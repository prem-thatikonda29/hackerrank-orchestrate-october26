"""Where the dataset, the built index and the screenshot-extraction cache live. Defaults are the repo's dataset/ and build/index.sqlite; the custom
evaluation cases point DATASET_DIR / INDEX_PATH / VISION_CACHE_DIR at their own mini dataset."""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATASET = Path(os.environ.get("DATASET_DIR") or REPO / "dataset")
INDEX_PATH = Path(os.environ.get("INDEX_PATH") or REPO / "build" / "index.sqlite")
VISION_CACHE = Path(os.environ.get("VISION_CACHE_DIR") or Path(__file__).resolve().parent / "vision_cache")

# code/vision_cache is committed on purpose: it holds the transcription of the 11 evidence screenshots, so a run needs no vision model
# call (and no API key) unless an image or the extraction prompt changes.
