"""Basic LLM investigator. The agent decides intent and the observed state of each resource from raw evidence;
deterministic code (planning.planner) turns those facts into ordered steps. The agent never writes or numbers steps.

The starting dossier lists the candidate resources the index tied to the subject, with their raw observations and no
baseline verdict. Two tools let the agent look further: the activity log for a resource, and all observations of one.
"""

import json
import threading
from typing import Literal

from langchain.agents import create_agent
from langchain_core.tools import tool
from pydantic import BaseModel, Field

import llm_config
from index.store import Index
from index.vocab import CATEGORY_ACTION, system_for
from planning.planner import STATES, RecordedResource, plan
from planning.validator import validate_row
from runner.investigate import Case, Dossier, absence_evidence, investigate

CATEGORIES = tuple(CATEGORY_ACTION)
DB_LOCK = threading.Lock()   # the agent runs parallel tool calls in threads; the index is one sqlite connection

SYSTEM_PROMPT = f"""You audit whether a person's data deletion was carried out, using only the evidence given at or before audit_at.
Workflow statuses and activity results are claims, not proof. State comes from evidence observations.

Decide:
1. authorizes_deletion: false if the request is about access, export, correction, marketing opt-out or anything other than deleting the data.
2. For each resource that belongs to the subject, its category and observed_state at audit_at.
   Treatment required per category: {json.dumps(CATEGORY_ACTION)}.
   observed_state: ABSENT (record gone), PRESENT (record exists), EXPOSED (record exists and direct identifiers such as name/email/phone are visible),
   CLEAN (record exists with identifiers removed), FLOW_ACTIVE / FLOW_DISABLED (data flows), RESTORE_ALLOWED / RESTORE_BLOCKED (backups), UNKNOWN (no usable evidence).
3. evidence_id: the file_id of the observation that proves the state ('' if none). Only cite file_ids that appear in the dossier or tool results.

Rules: a resource tied to a different person must be left out. Look-alike emails (one extra character) are different people.
A record about the subject that policy requires to be retained (audit logs, legal holds, backups) is not a violation by existing.
Resources labelled 'linked by timing' have no identifiers in evidence; keep them as the primary account or analytics profile only if their activity trail fits the case, and report ABSENT/UNKNOWN or CLEAN unless an observation says otherwise.
Be concise. Do not invent resources or file ids."""


class Rec(BaseModel):
    resource_id: str
    category: Literal[CATEGORIES]
    observed_state: Literal[STATES]
    evidence_id: str = ""
    rationale: str = Field(description="one short sentence naming the observation that supports the state")


class Verdict(BaseModel):
    authorizes_deletion: bool
    resources: list[Rec] = Field(default_factory=list)


def _obs_view(o) -> dict:
    return {"file_id": o.file_id, "captured_at": o.captured_at, "copy": o.copy, "presence": o.presence, "pii": o.pii,
            "flow": o.flow, "restore": o.restore, "retention_until": o.retention_until, "excerpt": o.excerpt[:200]}


def build_prompt(index: Index, d: Dossier) -> str:
    c = d.case
    lines = [f"case {c.case_id}; audit_at {c.audit_at}; reported_status {c.reported_status} at {c.reported_at}; requested_at {c.requested_at}",
             f"request_text: {c.request_text}", f"subject: {json.dumps(d.identity_summary)}", "candidates:"]
    cited: list[str] = []
    for cand in d.candidates:
        history = cand.state.history[-3:] if cand.state else []
        cited += [o.file_id for o in history]
        entry = {"resource_id": cand.resource_id, "default_category": cand.category, "matched_by": cand.match_types,
                 "other_owners": cand.other_owners, "linked": "by timing" if cand.linked_by == "activity" else "by evidence",
                 "observations": [_obs_view(o) for o in history] or "none at or before audit_at"}
        if cand.linked_by == "activity":
            entry["absence_witness_file"] = absence_evidence(index, d, cited)
        lines.append(json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines)


def make_tools(index: Index, case: Case):
    @tool
    def get_observations(resource_id: str) -> str:
        """All evidence observations of a resource captured at or before audit_at (file_id, state fields, excerpt)."""
        with DB_LOCK:
            st = index.state_at(resource_id, case.audit_at)
            return json.dumps([_obs_view(o) for o in (st.history if st else [])][-8:], ensure_ascii=False)

    @tool
    def get_activity(resource_id: str) -> str:
        """Activity-log events mentioning a resource at or before audit_at. Results are claims, not proof of state."""
        with DB_LOCK:
            rows = index.activity_for(resource_id, before=case.audit_at)[-8:]
        return json.dumps([{k: r[k] for k in ("activity_id", "event_time", "event_type", "target_system", "reported_result")} for r in rows])

    return [get_observations, get_activity]


def run_agent_case(index: Index, case: Case, model: str | None = None) -> dict:
    """Returns {status, steps, verdict, usage, errors}. Falls back to nothing: the caller decides on fallback."""
    with DB_LOCK:
        d = investigate(index, case)
        prompt = build_prompt(index, d)
    llm = llm_config.chat_model(model, max_tokens=4000)
    agent = create_agent(llm, make_tools(index, case), system_prompt=SYSTEM_PROMPT, response_format=Verdict)
    result = agent.invoke({"messages": [("user", prompt)]}, {"recursion_limit": 12})
    verdict: Verdict = result["structured_response"]
    usage = {"input_tokens": 0, "output_tokens": 0, "calls": 0}
    for m in result["messages"]:
        u = getattr(m, "usage_metadata", None)
        if u:
            usage["input_tokens"] += u.get("input_tokens", 0)
            usage["output_tokens"] += u.get("output_tokens", 0)
            usage["calls"] += 1
    if not verdict.authorizes_deletion:
        return {"status": "NOT_APPLICABLE", "steps": [], "verdict": verdict.model_dump(), "usage": usage, "errors": []}
    known = {c.resource_id for c in d.candidates}
    with DB_LOCK:
        return _finish(index, case, verdict, usage, known)


def _finish(index, case, verdict, usage, known) -> dict:
    recs = [RecordedResource(system_for(r.resource_id), r.resource_id, r.category, r.observed_state,
                             r.evidence_id if index.file_captured(r.evidence_id) and index.file_captured(r.evidence_id) <= case.audit_at else "")
            for r in verdict.resources if r.resource_id]
    status, steps = plan(recs)
    errors = validate_row(status, steps, case.audit_at, index.file_captured)
    return {"status": status, "steps": steps, "verdict": verdict.model_dump(), "usage": usage, "errors": errors,
            "invented": sorted({r.resource_id for r in verdict.resources} - known)}
