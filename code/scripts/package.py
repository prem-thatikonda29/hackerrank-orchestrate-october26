"""Build code.zip (README.md + code/) and verify it against the submission rules. Usage: python code/scripts/package.py

Rules checked (README.md "Packaging code.zip"): no dataset/ folder, no virtual environment or cache, no nested archive,
the two evaluation reports present, no API key (any sk- token or the value of OPENAI_API_KEY / LLM_API_KEY) in any file.
"""

import os
import re
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".git", "dataset"}
ARCHIVES = (".zip", ".tar", ".gz", ".tgz", ".7z", ".rar")
REQUIRED = ["README.md", "code/main.py", "code/evaluation/evaluation_report.md", "code/evaluation/usage_report.md", "code/requirements.txt"]
KEY_RE = re.compile(rb"sk-[A-Za-z0-9_-]{20,}")


def collect() -> list[Path]:
    files = [REPO / "README.md"]
    for path in sorted((REPO / "code").rglob("*")):
        rel = path.relative_to(REPO)
        if path.is_dir() or SKIP_DIRS & set(rel.parts) or path.name == ".DS_Store":
            continue
        files.append(path)
    return files


def main() -> int:
    files = collect()
    problems = []
    secrets = [v.encode() for k in ("OPENAI_API_KEY", "LLM_API_KEY") if (v := os.environ.get(k)) and len(v) > 8]
    for f in files:
        rel = f.relative_to(REPO).as_posix()
        if f.suffix.lower() in ARCHIVES:
            problems.append(f"nested archive: {rel}")
        data = f.read_bytes()
        if KEY_RE.search(data) or any(s in data for s in secrets):
            problems.append(f"possible API key in {rel}")
    names = {f.relative_to(REPO).as_posix() for f in files}
    problems += [f"missing required file: {r}" for r in REQUIRED if r not in names]
    if problems:
        print("NOT packaged:\n  " + "\n  ".join(problems))
        return 1
    out = REPO / "code.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, f.relative_to(REPO).as_posix())
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None
        listed = z.namelist()
    assert not any("dataset/" in n.split("evaluation/custom_cases/", 1)[-1] or n.startswith("dataset/") for n in listed)
    print(f"wrote {out.name}: {len(listed)} files, {out.stat().st_size / 1024:.0f} KB; reports present, no dataset folder, no nested archives, no keys found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
