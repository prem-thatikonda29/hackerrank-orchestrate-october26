"""Score a predictions CSV against dataset/sample_output.csv (or any golden CSV with the same columns).

Metrics:
  status accuracy   - deletion_status matches
  step P/R/F1       - steps keyed by (phase, operation, system_id, resource_id)
  field accuracy    - for matched steps, per-field agreement (state, evidence_id)
  sequence accuracy - full steps_json array equal after normalizing key order
  exact row         - status and sequence both exact

Usage: python code/evaluation/scorer.py PRED.csv [--gold dataset/sample_output.csv] [--verbose]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def load(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def step_key(s: dict) -> tuple:
    return (s["phase"], s["operation"], s["system_id"], s["resource_id"])


def score(pred: dict, gold: dict) -> dict:
    cases = sorted(gold)
    status_ok = tp = fp = fn = seq_ok = exact = 0
    field_hits = {"state": 0, "evidence_id": 0, "step_no": 0}
    matched = 0
    per_case = []
    for cid in cases:
        g_status, g_steps = gold[cid]
        p_status, p_steps = pred.get(cid, ("MISSING", []))
        s_ok = p_status == g_status
        status_ok += s_ok
        gk = {step_key(s): s for s in g_steps}
        pk = {step_key(s): s for s in p_steps}
        hit = set(gk) & set(pk)
        tp += len(hit)
        fp += len(set(pk) - hit)
        fn += len(set(gk) - hit)
        for k in hit:
            matched += 1
            for f in field_hits:
                field_hits[f] += gk[k][f] == pk[k][f]
        q_ok = json.dumps(p_steps, sort_keys=True) == json.dumps(g_steps, sort_keys=True)
        seq_ok += q_ok
        exact += s_ok and q_ok
        per_case.append({"case_id": cid, "status": (p_status, g_status), "missing": sorted(set(gk) - hit),
                         "extra": sorted(set(pk) - hit),
                         "field_diffs": [(k, f, pk[k][f], gk[k][f]) for k in sorted(hit) for f in field_hits if gk[k][f] != pk[k][f]]})
    n = len(cases)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    return {
        "cases": n,
        "status_accuracy": status_ok / n,
        "step_precision": precision,
        "step_recall": recall,
        "step_f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "field_accuracy": {f: (v / matched if matched else 0.0) for f, v in field_hits.items()},
        "sequence_accuracy": seq_ok / n,
        "exact_row_accuracy": exact / n,
        "per_case": per_case,
    }


def print_report(result: dict, verbose: bool) -> None:
    print(f"cases                {result['cases']}")
    print(f"status accuracy      {result['status_accuracy']:.3f}")
    print(f"step P / R / F1      {result['step_precision']:.3f} / {result['step_recall']:.3f} / {result['step_f1']:.3f}")
    print("field accuracy       " + ", ".join(f"{k}={v:.3f}" for k, v in result["field_accuracy"].items()))
    print(f"sequence accuracy    {result['sequence_accuracy']:.3f}")
    print(f"exact row accuracy   {result['exact_row_accuracy']:.3f}")
    if verbose:
        for c in result["per_case"]:
            if c["status"][0] != c["status"][1] or c["missing"] or c["extra"] or c["field_diffs"]:
                print(f"\n{c['case_id']}: status pred={c['status'][0]} gold={c['status'][1]}")
                for k in c["missing"]:
                    print(f"   missing {k}")
                for k in c["extra"]:
                    print(f"   extra   {k}")
                for k, f, p, g in c["field_diffs"]:
                    print(f"   {f} {k[1]} {k[3]}: pred={p!r} gold={g!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pred")
    ap.add_argument("--gold", default=str(REPO / "dataset" / "sample_output.csv"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    print_report(score(load(Path(args.pred)), load(Path(args.gold))), args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
