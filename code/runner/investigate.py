"""Deterministic investigation of a case: identity, linked resources and their state at audit_at.

The result is both the agent's starting dossier and the input of the rule-based baseline.
Stage 1 builds an evidence-only dossier per case. Stage 2 (linking.py) attaches the resources only the activity log
can connect to a person. Stage 3 assembles the final dossier.
"""

import csv
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path

from index.store import Index, ResourceState
from index.vocab import CATEGORY_ACTION, prefix_of, system_for, type_for
from planning.planner import RecordedResource, plan

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "dataset"


@dataclass
class Case:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    request_text: str


@dataclass
class Candidate:
    resource_id: str
    system_id: str
    category: str
    match_types: list[str]
    citable: bool
    state: ResourceState | None
    linked_by: str                      # "evidence" | "activity"
    other_owners: list[str] = field(default_factory=list)


@dataclass
class Dossier:
    case: Case
    user_id: str | None
    identity_summary: dict
    candidates: list[Candidate]
    primary_account: str | None
    primary_basis: str


def load_cases(name: str) -> list[Case]:
    with open(DATASET / name, encoding="utf-8") as fh:
        return [Case(**r) for r in csv.DictReader(fh)]


@lru_cache(maxsize=1)
def all_cases() -> tuple[Case, ...]:
    return tuple(load_cases("sample_deletion_requests.csv") + load_cases("deletion_requests.csv"))


def category_of(state: ResourceState | None, resource_id: str) -> str:
    rtype = (state.record_type if state else "") or type_for(resource_id)
    return rtype if rtype in CATEGORY_ACTION else type_for(resource_id)


def evidence_dossier(index: Index, case: Case) -> Dossier:
    """Stage 1: everything evidence ties to the subject, with state at audit_at."""
    ident = index.resolve_identity(case.email)
    if not ident:
        return Dossier(case, None, {}, [], None, "identity not found")
    hits = index.search(ident, case.audit_at)
    candidates: list[Candidate] = []
    for rid, hs in sorted(hits.items()):
        matches = sorted({h["match_type"] for h in hs})
        others = sorted(index.other_owners(rid, ident.user_id))
        if others and "user_id" not in matches:
            continue  # identifier collides with another known person and nothing ties it to this subject
        state = index.state_at(rid, case.audit_at)
        if state and max(o.captured_at for o in state.history) < case.requested_at:
            continue  # every sighting predates the request, so it says nothing about what the deletion left behind
        candidates.append(Candidate(rid, system_for(rid), category_of(state, rid), matches,
                                    any(h["citable"] for h in hs), state, "evidence", others))
    accounts = [c for c in candidates if prefix_of(c.resource_id) == "account"]
    summary = {
        "user_id": ident.user_id,
        "name": ident.name,
        "request_email_is": "current" if case.email.lower() == ident.current_email else "historical",
        "emails": sorted(ident.emails),
        "audit_ids": sorted({a for a in list(ident.emails.values()) + list(ident.phones.values()) + list(ident.hashes.values()) if a}),
    }
    return Dossier(case, ident.user_id, summary, candidates,
                   accounts[0].resource_id if accounts else None, "evidence" if accounts else "")


def baseline_state(c: Candidate) -> str:
    """Map an index state to a planner state for the candidate's category."""
    s = c.state
    if s is None:
        return "UNKNOWN"
    if c.category == "DATA_FLOW":
        return "FLOW_ACTIVE" if s.flow == "ACTIVE" else ("FLOW_DISABLED" if s.flow == "DISABLED" else "UNKNOWN")
    if c.category == "BACKUP_ARCHIVE":
        return "RESTORE_ALLOWED" if s.restore == "ALLOWED" else ("RESTORE_BLOCKED" if s.restore == "BLOCKED" else "UNKNOWN")
    if s.presence == "ABSENT":
        return "ABSENT"
    if CATEGORY_ACTION.get(c.category) in ("REDACT", "ANONYMIZE"):
        return "EXPOSED" if s.pii == "EXPOSED" else ("CLEAN" if s.pii == "CLEAN" else "UNKNOWN")
    return "PRESENT"


def _recorded(candidates: list[Candidate]) -> list[RecordedResource]:
    return [RecordedResource(c.system_id, c.resource_id, c.category, baseline_state(c), c.state.evidence_id if c.state else "")
            for c in candidates]


ACCOUNT_CHOOSER = None   # set to a callable (see linking.build_links) to let the agent link primary accounts


def set_account_chooser(chooser) -> None:
    global ACCOUNT_CHOOSER
    ACCOUNT_CHOOSER = chooser
    _dossiers.cache_clear()


@lru_cache(maxsize=2)
def _dossiers(index: Index) -> dict[str, Dossier]:
    from runner.linking import build_links  # local import: linking imports planner types

    cases = list(all_cases())
    base = {c.case_id: evidence_dossier(index, c) for c in cases}
    complete_like = {cid: plan(_recorded(d.candidates))[0] == "COMPLETE" for cid, d in base.items()}
    evidence_resources = {cid: {c.resource_id for c in d.candidates} for cid, d in base.items()}
    links = build_links(index, cases, base, complete_like, evidence_resources, ACCOUNT_CHOOSER)

    final: dict[str, Dossier] = {}
    for cid, d in base.items():
        extra: list[Candidate] = []
        primary, basis = d.primary_account, d.primary_basis
        if cid in links.account:
            rid, act = links.account[cid]
            extra.append(Candidate(rid, "account_service", "USER_PROFILE", ["activity_time"], False,
                                   index.state_at(rid, d.case.audit_at), "activity"))
            primary, basis = rid, f"activity {act}"
        if cid in links.analytics:
            rid, act = links.analytics[cid]
            extra.append(Candidate(rid, "analytics_platform", "ANALYTICS_PROFILE", ["activity_time"], False,
                                   index.state_at(rid, d.case.audit_at), "activity"))
        final[cid] = replace(d, candidates=d.candidates + extra, primary_account=primary, primary_basis=basis)
    return final


def investigate(index: Index, case: Case) -> Dossier:
    return _dossiers(index).get(case.case_id) or evidence_dossier(index, case)


def absence_evidence(index: Index, d: Dossier, cited: list[str]) -> str:
    """File proving a record absent by omission: a snapshot covering the system dated at/before audit_at.

    Prefer a file the case already cites elsewhere (one coherent witness), otherwise the newest covering snapshot.
    """
    covering = index.covering_files("account_service", d.case.audit_at)
    covering_ids = [f for f, _ in covering]
    for fid in sorted(set(cited), key=lambda f: (-cited.count(f), f)):
        if fid in covering_ids:
            return fid
    return covering_ids[0] if covering_ids else ""


def baseline_resources(d: Dossier, index: Index | None = None) -> list[RecordedResource]:
    out = _recorded(d.candidates)
    cited = [r.evidence_id for r in out if r.evidence_id]
    for i, r in enumerate(out):
        if r.category == "USER_PROFILE" and r.observed_state == "UNKNOWN" and index is not None:
            out[i] = RecordedResource(r.system_id, r.resource_id, r.category, "ABSENT", absence_evidence(index, d, cited))
        elif r.category == "ANALYTICS_PROFILE" and r.observed_state == "UNKNOWN":
            out[i] = RecordedResource(r.system_id, r.resource_id, r.category, "CLEAN", r.evidence_id)
    return out
