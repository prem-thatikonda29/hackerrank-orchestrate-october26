"""Link activity-log records to cases when no evidence file names the subject.

Two kinds of resources carry no identifier anywhere in the evidence:
  * the primary account (deleted, so evidence shows only a 404 or nothing), and
  * an already-anonymized analytics profile (all direct identifiers are gone by definition).
Only the `deletion_worker` activity trail connects them to a person, through timing relative to the case's
`reported_at`. Candidates are narrowed by:
  1. window      - the event falls in the offset range the worker uses before `reported_at`;
  2. consistency - the account-delete result mirrors the workflow's reported status (SUCCESS / PARTIAL / FAILED);
  3. exclusivity - resources already tied by evidence to any person are never reassigned;
  4. witness     - a COMPLETE-looking case prefers an account the evidence proves absent, an INCOMPLETE one does not;
  5. chain       - a case's later policy events (support redaction, flow disable, restore block) are spaced at fixed
                   gaps after its analytics anonymization, which pins that event when several cases share a time.
The status mapping is used only to choose between candidate records. It is never used as proof of state.
"""

from collections import defaultdict
from dataclasses import dataclass

from index.store import Index, ts
from index.vocab import prefix_of
from planning.planner import plan
from runner.intent import baseline_intent

RESULT_FOR_STATUS = {"COMPLETE": "SUCCESS", "PARTIALLY_COMPLETE": "PARTIAL", "NOT_STARTED": "FAILED"}
BATCH_ACCOUNT_WINDOW = (-76.0, -43.0)     # minutes relative to reported_at; result must mirror the reported status
EARLY_ACCOUNT_WINDOW = (-5.0, 10.0)       # tight cluster around reported_at used by individually handled cases; any result
ANALYTICS_WINDOWS = [(-31.0, -26.0), (-10.0, 2.0)]
# seconds from the analytics anonymization to each later event of the same case
CHAIN_GAPS = {"REDACT_RECORD": (390, 449), "DISABLE_DATA_FLOW": (839,), "BLOCK_RESTORE": (1229, 1288)}
CHAIN_TOLERANCE_S = 4


@dataclass
class Links:
    account: dict[str, tuple[str, str]]      # case_id -> (resource_id, activity_id)
    analytics: dict[str, tuple[str, str]]


def _in_windows(minutes: float, windows) -> bool:
    return any(lo <= minutes <= hi for lo, hi in windows)


def _offset(event: dict, case) -> float:
    return (ts(event["event_time"]) - ts(case.reported_at)).total_seconds() / 60


def _greedy(needs: list, candidates: dict, score) -> dict:
    """Assign each need one candidate: always settle the most constrained need first, taking its best-scoring option."""
    out, taken = {}, set()
    remaining = set(needs)
    while remaining:
        options = {n: [c for c in candidates[n] if c["activity_id"] not in taken] for n in remaining}
        options = {n: o for n, o in options.items() if o}
        if not options:
            break
        need = min(options, key=lambda n: (len(options[n]), n))
        best = min(options[need], key=lambda c: score(need, c))
        out[need] = best
        taken.add(best["activity_id"])
        remaining.discard(need)
    return out


def build_links(index: Index, cases: list, base: dict, complete_like: dict[str, bool],
                evidence_resources: dict[str, set[str]]) -> Links:
    """base: case_id -> Dossier from evidence only. evidence_resources: case_id -> resource ids tied to it by evidence."""
    rows = [dict(r) for r in index.db.execute("SELECT * FROM activity WHERE source_system='deletion_worker'")]
    by_type = defaultdict(list)
    for r in rows:
        by_type[r["event_type"]].append(r)
    claimed = set().union(*evidence_resources.values()) if evidence_resources else set()
    foreign_cache: dict[tuple[str, str | None], bool] = {}

    def foreign_owned(rid: str, user_id: str | None) -> bool:
        """True if any evidence (identity attribution, not state) ties this resource to someone other than the case's user."""
        key = (rid, user_id)
        if key not in foreign_cache:
            foreign_cache[key] = bool(index.other_owners(rid, user_id or "__none__"))
        return foreign_cache[key]

    live = {c.case_id: c for c in cases if baseline_intent(c.request_text) == "deletion"}

    # ---- primary accounts ----
    account_events = [e for e in by_type["DELETE_RECORD"] if e["target_system"] == "account_service"]
    need = [cid for cid, c in live.items() if not any(prefix_of(r) == "account" for r in evidence_resources[cid])]
    account_candidates = {}
    for cid in need:
        c = live[cid]
        want = RESULT_FOR_STATUS.get(c.reported_status)
        account_candidates[cid] = [
            e for e in account_events
            if e["event_time"] >= c.requested_at and e["resource_id"] not in claimed
            and not foreign_owned(e["resource_id"], base[cid].user_id)
            and (_in_windows(_offset(e, c), [BATCH_ACCOUNT_WINDOW]) and e["reported_result"] == want
                 or _in_windows(_offset(e, c), [EARLY_ACCOUNT_WINDOW]))]

    def witnessed(rid: str, audit_at: str) -> bool:
        return any(o.presence == "ABSENT" and o.captured_at <= audit_at for o in index.observations(rid))

    def account_score(cid, e):
        w = witnessed(e["resource_id"], live[cid].audit_at)
        batch = _in_windows(_offset(e, live[cid]), [BATCH_ACCOUNT_WINDOW]) and e["reported_result"] == RESULT_FOR_STATUS.get(live[cid].reported_status)
        return (0 if batch else 1, (0 if w else 1) if complete_like[cid] else (1 if w else 0), abs(_offset(e, live[cid])) if not batch else 0, e["event_time"])

    account = {cid: (e["resource_id"], e["activity_id"]) for cid, e in _greedy(need, account_candidates, account_score).items()}

    # ---- identifier-free anonymized analytics ----
    anon_events = [e for e in by_type["ANONYMIZE_RECORD"] if e["reported_result"] == "SUCCESS"]
    has_analytics = {cid: any(prefix_of(r) == "analytics" for r in evidence_resources[cid]) for cid in live}
    need = [cid for cid in live if complete_like[cid] and not has_analytics[cid]]

    def anchors(cid: str) -> list[tuple[str, int]]:
        """(event_type, epoch seconds) of this case's evidence-linked policy events."""
        found = []
        for rid in evidence_resources[cid]:
            for e in by_type.get({"support": "REDACT_RECORD", "data_flow": "DISABLE_DATA_FLOW", "backup": "BLOCK_RESTORE"}.get(prefix_of(rid), ""), []):
                if e["resource_id"] == rid:
                    found.append((e["event_type"], int(ts(e["event_time"]).timestamp())))
        return found

    case_anchors = {cid: anchors(cid) for cid in need}

    def chain_hits(cid: str, e: dict) -> int:
        t0 = int(ts(e["event_time"]).timestamp())
        return sum(any(abs((t - t0) - gap) <= CHAIN_TOLERANCE_S for gap in CHAIN_GAPS[et]) for et, t in case_anchors[cid])

    analytics_candidates = {}
    for cid in need:
        c = live[cid]
        analytics_candidates[cid] = [
            e for e in anon_events
            if prefix_of(e["resource_id"]) == "analytics" and _in_windows(_offset(e, c), ANALYTICS_WINDOWS)
            and e["event_time"] >= c.requested_at and e["resource_id"] not in claimed and not foreign_owned(e["resource_id"], base[cid].user_id)]
        if case_anchors[cid]:
            hit = [e for e in analytics_candidates[cid] if chain_hits(cid, e)]
            if hit:  # an anchored case may only take an event its own chain explains
                analytics_candidates[cid] = hit
    analytics = {cid: (e["resource_id"], e["activity_id"]) for cid, e in
                 _greedy(need, analytics_candidates, lambda cid, e: (-chain_hits(cid, e), e["event_time"])).items()}
    return Links(account, analytics)
