"""Rule-based baseline: deterministic investigation + planner, no LLM.

Used as (1) the agent's fallback, (2) a comparison row in the evaluation report, (3) a planner/validator check.
Usage: python code/runner/baseline.py [--split sample|eval] [--out PATH]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from index.store import Index  # noqa: E402
from planning.planner import plan  # noqa: E402
from planning.validator import validate_row  # noqa: E402
from runner.intent import baseline_intent  # noqa: E402
from runner.investigate import baseline_resources, investigate, load_cases  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

def run_case(index: Index, case) -> tuple[str, list[dict]]:
    if baseline_intent(case.request_text) == "non_deletion":
        return "NOT_APPLICABLE", []
    dossier = investigate(index, case)
    status, steps = plan(baseline_resources(dossier, index))
    errors = validate_row(status, steps, case.audit_at, index.file_captured)
    if errors:
        print(f"{case.case_id}: validator: {errors[:3]}", file=sys.stderr)
    return status, steps


def write_rows(path: Path, rows: list[tuple[str, str, list[dict]]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for cid, status, steps in sorted(rows):
            w.writerow([cid, status, json.dumps(steps, separators=(",", ":"), sort_keys=True, ensure_ascii=False)])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["sample", "eval"], default="sample")
    ap.add_argument("--out")
    args = ap.parse_args()
    index = Index()
    cases = load_cases("sample_deletion_requests.csv" if args.split == "sample" else "deletion_requests.csv")
    rows = [(c.case_id, *run_case(index, c)) for c in cases]
    out = Path(args.out) if args.out else REPO / "runs" / f"baseline_{args.split}.csv"
    write_rows(out, rows)
    print(f"wrote {len(rows)} rows -> {out.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
