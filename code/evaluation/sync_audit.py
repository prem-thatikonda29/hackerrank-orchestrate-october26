"""Audit whether SYNC_RECORD activity events contradict the evidence-derived state of any case resource.

The pipeline does not interpret SYNC_RECORD. This counts the places where it would matter:
  * an account judged ABSENT that has a successful sync after its successful delete (a recreation evidence missed);
  * a data flow judged DISABLED that has any sync (a flow that is still copying data);
and reports how many sync events touch no case resource at all (decoys). Usage: python code/evaluation/sync_audit.py
"""

import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from index.store import Index  # noqa: E402
from runner import investigate as inv  # noqa: E402
from runner.intent import baseline_intent  # noqa: E402
from runner.investigate import all_cases, baseline_resources  # noqa: E402


def main() -> int:
    idx = Index()
    dossiers = inv._dossiers(idx)
    seen: collections.Counter = collections.Counter()
    contradictions = []
    for case in all_cases():
        if baseline_intent(case.request_text) != "deletion":
            continue
        d = dossiers[case.case_id]
        state = {r.resource_id: r.observed_state for r in baseline_resources(d, idx)}
        for cand in d.candidates:
            rid, prefix = cand.resource_id, cand.resource_id.rsplit("_", 1)[0]
            events = sorted(idx.activity_for(rid, before=case.audit_at), key=lambda a: a["event_time"])
            sync = [a for a in events if a["event_type"] == "SYNC_RECORD" and a["reported_result"] == "SUCCESS"]
            deleted = [a for a in events if a["event_type"] == "DELETE_RECORD" and a["reported_result"] == "SUCCESS"]
            if prefix == "account" and deleted and sync and max(s["event_time"] for s in sync) > min(x["event_time"] for x in deleted):
                seen[("account sync after delete", state[rid])] += 1
                if state[rid] == "ABSENT":
                    contradictions.append((case.case_id, rid))
            if prefix == "data_flow" and any(a["event_type"] == "SYNC_RECORD" for a in events):
                seen[("data_flow with sync", state[rid])] += 1
                if state[rid] == "FLOW_DISABLED":
                    contradictions.append((case.case_id, rid))
    candidates = {c.resource_id for d in dossiers.values() for c in d.candidates}
    rows = idx.db.execute("SELECT resource_id, COUNT(*) FROM activity WHERE event_type='SYNC_RECORD' GROUP BY resource_id").fetchall()
    total = sum(n for _, n in rows)
    touched = sum(n for r, n in rows if r in candidates)
    for k, v in sorted(seen.items()):
        print(k, v)
    print(f"sync events {total}; on a resource that belongs to some case {touched}; decoys {total - touched}")
    print(f"contradictions with evidence-derived state: {len(contradictions)} {contradictions[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
