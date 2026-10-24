"""Agent that links the primary account to a case when no evidence file names it.

A deleted account leaves no record, so only the deletion worker's activity trail can connect it to a person. The
baseline does this with fixed time windows tuned on the public samples. Here an LLM ranks the candidate delete events
for each case from the facts, using only generic priors (no tuned offsets and no result mapping), so its accuracy on the
samples is an honest estimate of how the approach generalizes. Code keeps the rest: structural exclusion of accounts
that evidence ties to someone else, and one-to-one assignment across cases.
"""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel, Field

import llm_config

REPO = Path(__file__).resolve().parents[2]
LINK_VERSION = "l2"   # l1 was the zero-shot prompt (26/32 sample accounts); l2 was revised after seeing its sample failures

SYSTEM = """You match a deletion request to the account-deletion event that belongs to it.
The deletion workflow reported a status for the request at reported_at. A background worker logs one DELETE_RECORD event per account it processes.
Candidates are such events near that time; most belong to other people. Activity results are claims, not proof.
Domain priors: the worker usually acts shortly before the workflow reports, but an event a few minutes after the report is also possible; it never precedes requested_at.
Its result tends to agree with the reported outcome (success, partial or failed), but the reported status itself is only a claim and can be wrong.
evidence_proves_absent_at_audit=true means evidence shows that account is gone. false only means no evidence either way: a deleted account normally leaves no trace, so false does not mean the account still exists.
other_records_look_compliant says whether evidence on this person's other records (search, support, exports, flows, backups) shows no policy violation. If it is true, the deletion was probably
carried out in full, so prefer a candidate whose account evidence proves absent; if false, evidence shows leftover data, so absence proof matters less.
Pick the single best candidate, then add up to two alternates. Use an empty list only if no candidate is plausible."""


class Ranking(BaseModel):
    ranked_activity_ids: list[str] = Field(default_factory=list)
    reason: str = Field(description="one short sentence")


def _prompt(case, cands: list[dict], info: dict) -> str:
    lines = [f"reported_status={case.reported_status} reported_at={case.reported_at} requested_at={case.requested_at} audit_at={case.audit_at} "
             f"other_records_look_compliant={str(info['complete_like']).lower()}",
             "candidates (offset_min is event time minus reported_at):"]
    for e in cands:
        lines.append(json.dumps({"activity_id": e["activity_id"], "resource_id": e["resource_id"], "offset_min": round(info["offset"][e["activity_id"]], 1),
                                 "result": e["reported_result"], "evidence_proves_absent_at_audit": info["witnessed"][e["activity_id"]]}))
    return "\n".join(lines)


def make_account_chooser(model: str | None = None, workers: int = 8, tag: str = ""):
    model = model or llm_config.settings()["agent_model"]
    cache_dir = REPO / "cache" / "agent" / f"link_{LINK_VERSION}_{model}{tag}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    stats = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "errors": 0}

    def choose(need: list[str], candidates: dict, info: dict) -> dict[str, list[str]]:
        llm = llm_config.chat_model(model, max_tokens=600).with_structured_output(Ranking, include_raw=True)

        def one(cid):
            cands = candidates[cid]
            if not cands:
                return cid, []
            prompt = _prompt(info[cid]["case"], cands, info[cid])
            f = cache_dir / f"{cid}.json"
            if f.exists():
                data = json.loads(f.read_text())
            else:
                try:
                    out = llm.invoke([("system", SYSTEM), ("user", prompt)])
                    usage = getattr(out["raw"], "usage_metadata", None) or {}
                    data = {"ranked": out["parsed"].ranked_activity_ids if out["parsed"] else [], "reason": out["parsed"].reason if out["parsed"] else "",
                            "usage": {"input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0)}}
                    f.write_text(json.dumps(data))
                except Exception:
                    stats["errors"] += 1
                    return cid, []
            stats["calls"] += 1
            stats["input_tokens"] += data["usage"]["input_tokens"]
            stats["output_tokens"] += data["usage"]["output_tokens"]
            allowed = {e["activity_id"] for e in cands}
            return cid, [a for a in data["ranked"] if a in allowed]

        with ThreadPoolExecutor(workers) as pool:
            return dict(pool.map(one, need))

    choose.stats = stats
    return choose
