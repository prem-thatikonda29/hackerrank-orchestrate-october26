"""Quick project status: tracker progress, next task, baseline score, cache state.

Usage: python code/scripts/status.py
Reads TRACKER.md checkboxes ([x] done, [~] started, [ ] todo). Safe to run without the venv's heavy packages.
"""

import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TASK_RE = re.compile(r"^- \[([ x~])\] (\w\d+) (.*)$")


def tracker() -> list[tuple[str, str, str]]:
    tasks = []
    for line in (REPO / "TRACKER.md").read_text(encoding="utf-8").splitlines():
        m = TASK_RE.match(line)
        if m:
            tasks.append((m.group(1), m.group(2), m.group(3)))
    return tasks


def main() -> int:
    tasks = tracker()
    done = sum(1 for s, _, _ in tasks if s == "x")
    started = [t for t in tasks if t[0] == "~"]
    todo = [t for t in tasks if t[0] == " "]
    print(f"Tracker: {done}/{len(tasks)} done, {len(started)} started, {len(todo)} todo")
    by_section: dict[str, list[str]] = {}
    for s, tid, _ in tasks:
        by_section.setdefault(tid[0], []).append(s)
    for sec, states in sorted(by_section.items()):
        print(f"  {sec}: {states.count('x')}/{len(states)}")
    nxt = (started + todo)[:3]
    print("Next:")
    for _, tid, text in nxt:
        print(f"  {tid} {text[:110]}")

    print("\nState:")
    for label, path in [("index", "build/index.sqlite"), ("baseline (sample)", "runs/baseline_sample.csv"),
                        ("baseline (eval)", "runs/baseline_eval.csv"), ("output.csv", "output.csv"), (".env", ".env")]:
        print(f"  {label:18s} {'present' if (REPO / path).exists() else 'MISSING'}")
    vision = REPO / "cache" / "vision"
    n = len(list(vision.glob("*.json"))) if vision.exists() else 0
    print(f"  vision cache       {n}/11 screenshots extracted")

    sample = REPO / "runs" / "baseline_sample.csv"
    scorer = REPO / "code" / "evaluation" / "scorer.py"
    if sample.exists():
        out = subprocess.run([sys.executable, str(scorer), str(sample)], capture_output=True, text=True, cwd=REPO)
        print("\nBaseline on 32 samples:\n" + "\n".join("  " + line for line in out.stdout.splitlines()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
