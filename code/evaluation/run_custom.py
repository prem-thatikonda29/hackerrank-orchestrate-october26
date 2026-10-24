"""Run the pipeline on the custom evaluation dataset and score it against its hand-written gold.

Usage: python code/evaluation/run_custom.py [--engine agent|baseline] [--verbose]
Runs main.py in a subprocess with DATASET_DIR / INDEX_PATH pointing at code/evaluation/custom_cases/dataset.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CASES = REPO / "code" / "evaluation" / "custom_cases"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", choices=["agent", "baseline"], default="agent")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    subprocess.run([sys.executable, str(CASES / "build_cases.py")], check=True)
    out = REPO / "runs" / f"custom_{args.engine}.csv"
    env = {**os.environ, "DATASET_DIR": str(CASES / "dataset"), "INDEX_PATH": str(REPO / "build" / "custom_index.sqlite"),
           "VISION_CACHE_DIR": str(REPO / "cache" / "vision_custom")}
    subprocess.run([sys.executable, str(REPO / "code" / "main.py"), "--engine", args.engine, "--out", str(out)], check=True, env=env)
    cmd = [sys.executable, str(REPO / "code" / "evaluation" / "scorer.py"), str(out), "--gold", str(CASES / "dataset" / "expected_output.csv")]
    return subprocess.run(cmd + (["--verbose"] if args.verbose else [])).returncode


if __name__ == "__main__":
    sys.exit(main())
