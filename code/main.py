"""Entry point: build the evidence index, run every request in dataset/deletion_requests.csv and write output.csv at
the repo root. For now every case goes through the rule-based baseline; the agent layer will slot in here."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from index.build import Builder  # noqa: E402
from index.store import Index  # noqa: E402
from planning.validator import validate_file  # noqa: E402
from runner.baseline import run_case, write_rows  # noqa: E402
from runner.investigate import load_cases  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["sample", "eval"], default="eval")
    ap.add_argument("--out", help="default: output.csv at the repo root (eval) or runs/main_sample.csv (sample)")
    ap.add_argument("--skip-index", action="store_true", help="reuse an existing build/index.sqlite")
    args = ap.parse_args()

    if not args.skip_index:
        Builder().run()
    index = Index()
    cases = load_cases("sample_deletion_requests.csv" if args.split == "sample" else "deletion_requests.csv")
    rows = [(c.case_id, *run_case(index, c)) for c in cases]

    default = REPO / "output.csv" if args.split == "eval" else REPO / "runs" / "main_sample.csv"
    out = Path(args.out) if args.out else default
    write_rows(out, rows)
    errors = validate_file(out, [c.case_id for c in cases])
    for e in errors[:20]:
        print(f"invalid: {e}", file=sys.stderr)
    print(f"wrote {len(rows)} rows -> {out}" + (f" ({len(errors)} validation errors)" if errors else " (valid)"))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
