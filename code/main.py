"""Entry point: build the evidence index, run every request in dataset/deletion_requests.csv and write output.csv at
the repo root. The default engine is the LLM investigation agent (code/agent) with the rule-based baseline as the
per-case fallback; --engine baseline runs the baseline alone with no model calls (screenshots still need a cache)."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from index.build import Builder  # noqa: E402
from index.store import Index  # noqa: E402
from planning.validator import validate_file  # noqa: E402
from agent.run import run_cases, usage_totals  # noqa: E402
from evaluation.usage import write_usage_report  # noqa: E402
from runner.baseline import run_case, write_rows  # noqa: E402
from runner.investigate import load_cases  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def ensure_screenshots() -> None:
    """Screenshots carry the state of 12 cases, so extract any that are not cached yet (needs OPENAI_API_KEY)."""
    from index import vision

    missing = vision.missing_cache()
    if not missing:
        return
    try:
        spent = vision.extract_all()
        print(f"vision: extracted {len(missing)} screenshots (tokens in/out {spent[0]}/{spent[1]})")
    except Exception as e:  # without a model the screenshot cases fall back to empty evidence
        print(f"WARNING: could not read {len(missing)} screenshots ({e}); their cases will be wrong", file=sys.stderr)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["sample", "eval"], default="eval")
    ap.add_argument("--out", help="default: output.csv at the repo root (eval) or runs/main_sample.csv (sample)")
    ap.add_argument("--engine", choices=["agent", "baseline"], default="agent")
    ap.add_argument("--link-engine", choices=["rules", "agent"], default="rules",
                    help="how activity-only primary accounts are linked; the agent linker is opt-in (see TRACKER.md, link audit)")
    ap.add_argument("--skip-index", action="store_true", help="reuse an existing build/index.sqlite")
    args = ap.parse_args()

    if not args.skip_index:
        ensure_screenshots()
        Builder().run()
    index = Index()
    if args.link_engine == "agent":
        from agent.link_agent import make_account_chooser
        from runner.investigate import set_account_chooser

        set_account_chooser(make_account_chooser())
    cases = load_cases("sample_deletion_requests.csv" if args.split == "sample" else "deletion_requests.csv")
    if args.engine == "agent":
        import llm_config

        model = llm_config.settings()["agent_model"]
        results = run_cases(index, cases, model)
        rows = [(cid, r["status"], r["steps"]) for cid, r in results.items()]
        if args.split == "eval" and not args.out:
            write_usage_report(REPO / "code" / "evaluation" / "usage_report.md", model, llm_config.settings()["vision_model"],
                               usage_totals(results), len(cases))
        fb = usage_totals(results)["fallbacks"]
        print(f"agent engine: {len(rows) - len(fb)} agent rows, {len(fb)} baseline fallbacks")
    else:
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
