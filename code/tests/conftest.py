"""Tests run against the generated custom dataset so they never touch the real dataset or build/index.sqlite."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
CASES = CODE / "evaluation" / "custom_cases"
_TMP = Path(tempfile.mkdtemp(prefix="ghost_tests_"))

subprocess.run([sys.executable, str(CASES / "build_cases.py")], check=True, capture_output=True)
os.environ["DATASET_DIR"] = str(CASES / "dataset")
os.environ["INDEX_PATH"] = str(_TMP / "index.sqlite")
os.environ["VISION_CACHE_DIR"] = str(_TMP / "vision")
sys.path.insert(0, str(CODE))

from index.build import Builder  # noqa: E402

Builder().run()
