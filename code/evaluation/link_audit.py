"""Label-free audit of primary-account linking, for the evaluation report.

For every deletion case whose account is not named by evidence, count the delete events that satisfy the baseline's
tight rule (batch window, result mirrors the reported status, not owned by another person). A unique candidate is strong
support that the rule describes the data; several candidates mean the pick rests on tie-breaking. With --agent, compare
the agent linker's choice with the baseline's in each group. Usage: python code/evaluation/link_audit.py [--agent]
"""

import argparse
import collections
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from index.store import Index, ts  # noqa: E402
from index.vocab import prefix_of  # noqa: E402
from runner import investigate as inv  # noqa: E402
from runner.intent import baseline_intent  # noqa: E402
from runner.investigate import evidence_dossier, load_cases  # noqa: E402
from runner.linking import BATCH_ACCOUNT_WINDOW, RESULT_FOR_STATUS  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", action="store_true")
    args = ap.parse_args()
    idx = Index()
    base = {c: d.primary_account for c, d in inv._dossiers(idx).items()}
    agent = {}
    if args.agent:
        from agent.link_agent import make_account_chooser

        inv.set_account_chooser(make_account_chooser())
        agent = {c: d.primary_account for c, d in inv._dossiers(idx).items()}
    events = [dict(r) for r in idx.db.execute(
        "SELECT * FROM activity WHERE source_system='deletion_worker' AND event_type='DELETE_RECORD' AND target_system='account_service'")]
    groups: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    for name, split in [("sample_deletion_requests.csv", "sample"), ("deletion_requests.csv", "eval")]:
        for c in load_cases(name):
            if baseline_intent(c.request_text) != "deletion":
                continue
            d = evidence_dossier(idx, c)
            if any(prefix_of(x.resource_id) == "account" for x in d.candidates):
                continue
            want = RESULT_FOR_STATUS[c.reported_status]
            k = sum(1 for e in events if e["event_time"] >= c.requested_at
                    and BATCH_ACCOUNT_WINDOW[0] <= (ts(e["event_time"]) - ts(c.reported_at)).total_seconds() / 60 <= BATCH_ACCOUNT_WINDOW[1]
                    and e["reported_result"] == want and not idx.other_owners(e["resource_id"], d.user_id))
            g = groups[f"{split}:{'unique' if k == 1 else 'ambiguous' if k > 1 else 'none'}"]
            g["cases"] += 1
            if args.agent:
                g["agent==baseline"] += agent[c.case_id] == base[c.case_id]
    for key, g in sorted(groups.items()):
        print(key, dict(g))
    return 0


if __name__ == "__main__":
    sys.exit(main())
