"""Run the LLM agent over a split with per-case disk caching. Usage: python code/agent/run.py [--split sample|eval] [--limit N] [--model M]"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_config  # noqa: E402
from agent.agent import run_agent_case  # noqa: E402
from index.store import Index  # noqa: E402
from runner.baseline import write_rows  # noqa: E402
from runner.investigate import load_cases  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["sample", "eval"], default="sample")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--model")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out")
    ap.add_argument("--tag", default="", help="separate cache directory, e.g. for a repeatability rerun")
    args = ap.parse_args()
    model = args.model or llm_config.settings()["agent_model"]
    index = Index()
    cases = load_cases("sample_deletion_requests.csv" if args.split == "sample" else "deletion_requests.csv")[: args.limit]
    cache_dir = REPO / "cache" / "agent" / (model + args.tag)
    cache_dir.mkdir(parents=True, exist_ok=True)

    def one(case):
        f = cache_dir / f"{case.case_id}.json"
        if f.exists():
            return case.case_id, json.loads(f.read_text())
        try:
            res = run_agent_case(index, case, model)
        except Exception as e:  # recorded, never raised: the caller scores a missing row as wrong
            return case.case_id, {"status": "ERROR", "steps": [], "errors": [repr(e)[:300]], "usage": {}}
        f.write_text(json.dumps(res, ensure_ascii=False))
        return case.case_id, res

    with ThreadPoolExecutor(args.workers) as pool:
        results = dict(pool.map(one, cases))
    ti = sum(r["usage"].get("input_tokens", 0) for r in results.values())
    to = sum(r["usage"].get("output_tokens", 0) for r in results.values())
    errs = {cid: r["errors"] for cid, r in results.items() if r["errors"] or r["status"] == "ERROR"}
    print(f"cases={len(results)} tokens in={ti} out={to} calls={sum(r['usage'].get('calls', 0) for r in results.values())} problems={len(errs)}")
    for cid, e in list(errs.items())[:10]:
        print(" ", cid, str(e)[:200])
    out = Path(args.out) if args.out else REPO / "runs" / f"agent_{args.split}.csv"
    write_rows(out, [(cid, r["status"], r["steps"]) for cid, r in results.items()])
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
