"""Extract structured text from evidence screenshots with a vision LLM, ground it, and cache it per file.

Run once: python code/index/vision.py [--model MODEL] [--force]
Cache: cache/vision/<image stem>.json  (keyed by image SHA-256, model and prompt version)

Vision models misread characters (for example 7610 as 1610), so every extracted value is grounded against what the
dataset already knows: emails and phones are snapped to a known identity when within a small edit distance, and
resource IDs must match the known ID patterns.
"""

import argparse
import base64
import csv
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

REPO = Path(__file__).resolve().parents[2]
DATASET = REPO / "dataset"
CACHE = REPO / "cache" / "vision"
PROMPT_VERSION = "v2"

PROMPT = """You transcribe a screenshot of an internal admin console into JSON. Transcribe literally, character by character.
Never correct, complete, translate or invent text. Copy every digit exactly as printed. Use null for anything unreadable.

Return ONLY one JSON object with these keys:
{
  "console": "name of the application shown at top left",
  "page_title": "main heading",
  "subtitle": "line under the heading, if any",
  "header_fields": {"label": "value"},         // summary cards under the tabs (label and value of each card)
  "table_columns": ["column names"],
  "table_rows": [["cell", "cell", ...]],         // every row of the main table, one list per row, cells in column order
  "detail_title": "heading of the right-hand panel (often a resource id)",
  "detail_fields": {"label": "value"},           // every label/value pair of the right-hand panel
  "activity": ["each activity or timeline entry as one string"]
}"""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def edit_distance(a: str, b: str, limit: int = 3) -> int:
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Vocabulary:
    """Everything the dataset already knows, used to ground extracted strings."""

    def __init__(self) -> None:
        def rows(name):
            with open(DATASET / name, encoding="utf-8") as fh:
                return list(csv.DictReader(fh))

        ids, hist = rows("user_identities.csv"), rows("user_audit_log.csv")
        self.emails = {r["email"].lower() for r in ids} | {r[k].lower() for r in hist if r["field_name"] == "email" for k in ("old_value", "new_value")}
        self.phones = {re.sub(r"\D", "", r["phone"]): r["phone"] for r in ids}
        self.phones.update({re.sub(r"\D", "", r[k]): r[k] for r in hist if r["field_name"] == "phone" for k in ("old_value", "new_value")})
        self.names = {r["name"] for r in ids}
        self.resources = set()
        for r in rows("system_activity.csv"):
            self.resources |= set(re.findall(r"\b[a-z_]+_\d+\b", r["details"])) | {r["resource_id"]}

    def snap_email(self, value: str) -> str:
        v = value.strip().lower()
        if v in self.emails:
            return v
        best = sorted((edit_distance(v, e), e) for e in self.emails if abs(len(e) - len(v)) <= 2)[:2]
        if best and best[0][0] <= 2 and (len(best) == 1 or best[1][0] > best[0][0]):
            return best[0][1]
        return value.strip()

    def snap_phone(self, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if digits in self.phones:
            return self.phones[digits]
        best = sorted((edit_distance(digits, p), p) for p in self.phones if abs(len(p) - len(digits)) <= 1)[:2]
        if best and best[0][0] <= 1 and (len(best) == 1 or best[1][0] > best[0][0]):
            return self.phones[best[0][1]]
        return value.strip()

    def snap_resource(self, value: str) -> str:
        v = value.strip()
        if v in self.resources:
            return v
        prefix = v.rsplit("_", 1)[0]
        near = [r for r in self.resources if r.startswith(prefix + "_") and edit_distance(v, r, 1) <= 1]
        return near[0] if len(near) == 1 else v


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"\+\d[\d ()-]{7,}\d")
RESOURCE_RE = re.compile(r"\b(?:account|search|analytics|support|export|backup|data_flow|audit_record|tombstone|legal_hold|marketing|cache|email_delivery|object)_\d+\b")


def ground_text(text: str, vocab: Vocabulary) -> str:
    text = EMAIL_RE.sub(lambda m: vocab.snap_email(m.group(0)), text)
    text = PHONE_RE.sub(lambda m: vocab.snap_phone(m.group(0)), text)
    return RESOURCE_RE.sub(lambda m: vocab.snap_resource(m.group(0)), text)


def ground(node, vocab: Vocabulary):
    if isinstance(node, str):
        return ground_text(node, vocab)
    if isinstance(node, list):
        return [ground(x, vocab) for x in node]
    if isinstance(node, dict):
        return {k: ground(v, vocab) for k, v in node.items()}
    return node


def flatten(extraction: dict) -> str:
    """Plain-text transcript of an extraction, used for matching and as the observation excerpt."""
    parts = [extraction.get("console") or "", extraction.get("page_title") or "", extraction.get("subtitle") or ""]
    parts += [f"{k}: {v}" for k, v in (extraction.get("header_fields") or {}).items()]
    cols = extraction.get("table_columns") or []
    parts.append(" | ".join(map(str, cols)))
    parts += [" | ".join(map(str, row)) for row in extraction.get("table_rows") or []]
    parts.append(extraction.get("detail_title") or "")
    parts += [f"{k}: {v}" for k, v in (extraction.get("detail_fields") or {}).items()]
    parts += [str(a) for a in extraction.get("activity") or []]
    return "\n".join(p for p in parts if p)


def parse_json(text: str) -> dict:
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    return json.loads(m.group(0) if m else text)


def call_model(llm, image_path: Path, retries: int = 6) -> tuple[dict, dict]:
    b64 = base64.b64encode(image_path.read_bytes()).decode()
    messages = [("user", [{"type": "text", "text": PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}}])]
    for attempt in range(retries):
        try:
            reply = llm.invoke(messages)
            return parse_json(str(reply.content)), dict(reply.usage_metadata or {})
        except json.JSONDecodeError:
            time.sleep(2)
        except Exception as e:  # rate limits and transient router errors
            if "429" in str(e) or "503" in str(e) or "rate" in str(e).lower():
                time.sleep(min(60, 8 * (attempt + 1)))
            else:
                raise
    raise RuntimeError(f"vision extraction failed for {image_path.name}")


def extract_all(model: str | None = None, force: bool = False, only: str | None = None) -> tuple[int, int]:
    """Extract every screenshot that has no valid cache entry. Returns (input tokens, output tokens) spent."""
    import llm_config

    model = model or llm_config.settings()["vision_model"]
    llm = llm_config.chat_model(model, max_tokens=3000)
    vocab = Vocabulary()
    CACHE.mkdir(parents=True, exist_ok=True)
    with open(DATASET / "evidence_files.csv", encoding="utf-8") as fh:
        images = [(r["file_id"], DATASET / r["file_path"]) for r in csv.DictReader(fh) if r["file_path"].endswith(".png")]
    total_in = total_out = 0
    for file_id, path in images:
        if only and only not in path.name:
            continue
        out = CACHE / f"{path.stem}.json"
        digest = sha256_file(path)
        if out.exists() and not force:
            cached = json.loads(out.read_text())
            if cached.get("sha256") == digest and cached.get("prompt_version") == PROMPT_VERSION:
                print(f"cached   {path.name}")
                continue
        raw, usage = call_model(llm, path)
        grounded = ground(raw, vocab)
        out.write_text(json.dumps({"file_id": file_id, "sha256": digest, "model": model, "prompt_version": PROMPT_VERSION,
                                   "usage": usage, "raw": raw, "extraction": grounded, "text": flatten(grounded)},
                                  indent=1, ensure_ascii=False))
        total_in += usage.get("input_tokens", 0)
        total_out += usage.get("output_tokens", 0)
        print(f"extracted {path.name} in={usage.get('input_tokens')} out={usage.get('output_tokens')}")
        time.sleep(1)
    return total_in, total_out


def missing_cache() -> list[str]:
    """Screenshots without a valid cache entry for the current prompt version."""
    with open(DATASET / "evidence_files.csv", encoding="utf-8") as fh:
        images = [DATASET / r["file_path"] for r in csv.DictReader(fh) if r["file_path"].endswith(".png")]
    missing = []
    for path in images:
        out = CACHE / f"{path.stem}.json"
        try:
            cached = json.loads(out.read_text())
            ok = cached.get("sha256") == sha256_file(path) and cached.get("prompt_version") == PROMPT_VERSION
        except (OSError, json.JSONDecodeError):
            ok = False
        if not ok:
            missing.append(path.name)
    return missing


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", help="substring of the image file name")
    args = ap.parse_args()
    total_in, total_out = extract_all(args.model, args.force, args.only)
    print(f"tokens in={total_in} out={total_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
