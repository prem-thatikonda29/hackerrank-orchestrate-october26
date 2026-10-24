"""Run the LLM agent over a split with per-case disk caching and a baseline fallback.

Usage: python code/agent/run.py [--split sample|eval] [--limit N] [--model M] [--tag T] [--out PATH]
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_config  # noqa: E402
from agent.agent import AGENT_VERSION, DB_LOCK, run_agent_case  # noqa: E402
from index.store import Index  # noqa: E402
from runner.baseline import run_case as baseline_case  # noqa: E402
from runner.baseline import write_rows  # noqa: E402
from runner.investigate import load_cases  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def run_cases(index: Index, cases: list, model: str, workers: int = 8, tag: str = "") -> dict[str, dict]:
    """case_id -> result. A case whose agent run errors or breaks the output contract gets the rule-based baseline row
    instead (result['fallback'] holds the reason). Agent results are cached on disk so a rerun is free and repeatable."""
    cache_dir = REPO / "cache" / "agent" / f"{model}_{AGENT_VERSION}{tag}"
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(case):
        f = cache_dir / f"{case.case_id}.json"
        if f.exists():
            res = json.loads(f.read_text())
        else:
            try:
                res = run_agent_case(index, case, model)
            except Exception as e:  # never raised: the case falls back to the baseline below
                res = {"status": "ERROR", "steps": [], "errors": [repr(e)[:300]], "usage": {}, "overrides": []}
            if res["status"] != "ERROR":
                f.write_text(json.dumps(res, ensure_ascii=False))
        if res["status"] == "ERROR" or res["errors"]:
            with DB_LOCK:
                status, steps = baseline_case(index, case)
            res = {**res, "status": status, "steps": steps, "fallback": "; ".join(map(str, res["errors"]))[:200]}
        return case.case_id, res

    with ThreadPoolExecutor(workers) as pool:
        return dict(pool.map(one, cases))


def usage_totals(results: dict[str, dict]) -> dict:
    return {"cases": len(results), "calls": sum(r["usage"].get("calls", 0) for r in results.values()),
            "input_tokens": sum(r["usage"].get("input_tokens", 0) for r in results.values()),
            "output_tokens": sum(r["usage"].get("output_tokens", 0) for r in results.values()),
            "fallbacks": sorted(cid for cid, r in results.items() if r.get("fallback"))}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["sample", "eval"], default="sample")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out")
    ap.add_argument("--tag", default="", help="separate cache directory, e.g. for a repeatability rerun")
    args = ap.parse_args()
    model = args.model or llm_config.settings()["agent_model"]
    index = Index()
    cases = load_cases("sample_deletion_requests.csv" if args.split == "sample" else "deletion_requests.csv")[: args.limit]
    results = run_cases(index, cases, model, args.workers, args.tag)
    print(json.dumps(usage_totals(results)))
    out = Path(args.out) if args.out else REPO / "runs" / f"agent_{args.split}.csv"
    write_rows(out, [(cid, r["status"], r["steps"]) for cid, r in results.items()])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
