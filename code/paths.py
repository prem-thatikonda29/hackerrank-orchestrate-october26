"""Where the dataset and the built index live. Defaults are the repo's dataset/ and build/index.sqlite; the custom
evaluation cases point DATASET_DIR / INDEX_PATH / VISION_CACHE_DIR at their own mini dataset."""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASET = Path(os.environ.get("DATASET_DIR") or REPO / "dataset")
INDEX_PATH = Path(os.environ.get("INDEX_PATH") or REPO / "build" / "index.sqlite")
VISION_CACHE = Path(os.environ.get("VISION_CACHE_DIR") or REPO / "cache" / "vision")
