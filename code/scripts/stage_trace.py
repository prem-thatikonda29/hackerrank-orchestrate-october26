"""Stage Claude Code session traces for this repo into agent-trace/ and zip them as agent-trace.zip.

Run it after the session(s) are finished (quit the CLI first so the files are fully written), on each machine you used:
    python code/scripts/stage_trace.py [--label cloud|mac|...]
It copies only ~/.claude/projects/<folders whose name contains the repo directory name> (the original JSONL files and their
sub-folders), refuses to stage a file containing an API key or token, and writes agent-trace/claude-code[-label]/.
Add the other traces by hand as agent_trace_instructions.md describes (/export transcripts, claude.ai chat export).
"""

import argparse
import os
import re
import shutil
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KEY_RE = re.compile(rb"sk-[A-Za-z0-9_-]{20,}|(?i:bearer\s+[A-Za-z0-9._-]{30,})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="")
    args = ap.parse_args()
    projects = Path.home() / ".claude" / "projects"
    matches = [p for p in projects.glob("*") if REPO.name in p.name] if projects.exists() else []
    if not matches:
        print(f"no Claude Code project folder containing '{REPO.name}' under {projects}")
        return 1
    secrets = [v.encode() for k in ("OPENAI_API_KEY", "LLM_API_KEY") if (v := os.environ.get(k)) and len(v) > 8]
    dest = REPO / "agent-trace" / ("claude-code" + (f"-{args.label}" if args.label else ""))
    if dest.exists():
        shutil.rmtree(dest)
    staged = 0
    for folder in matches:
        for f in sorted(folder.rglob("*")):
            if not f.is_file():
                continue
            data = f.read_bytes()
            if KEY_RE.search(data) or any(s in data for s in secrets):
                print(f"REFUSED (possible secret): {f}")
                return 1
            target = dest / folder.name / f.relative_to(folder)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            staged += 1
    zip_path = REPO / "agent-trace.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((REPO / "agent-trace").rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(REPO).as_posix())
    print(f"staged {staged} files from {len(matches)} project folder(s) -> {dest.relative_to(REPO)}; wrote {zip_path.name} ({zip_path.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
