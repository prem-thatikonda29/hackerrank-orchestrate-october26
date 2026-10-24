"""Check a case row and the final output file against the submission contract."""

import csv
import json
import re
from pathlib import Path

from index.vocab import OPERATION_ORDER, OPERATION_SPEC, PHASE_ORDER

STEP_FIELDS = {"step_no", "phase", "system_id", "resource_id", "operation", "state", "evidence_id"}
EVIDENCE_RE = re.compile(r"^(evidence|activity|audit)_\d+$")


def validate_row(status: str, steps: list[dict], audit_at: str | None = None, evidence_time=None) -> list[str]:
    """Return contract violations for one case. evidence_time(evidence_id) -> captured/event time or None."""
    errors: list[str] = []
    if status not in ("COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"):
        return [f"bad deletion_status {status!r}"]
    if status == "NOT_APPLICABLE":
        return [] if steps == [] else ["NOT_APPLICABLE must have steps_json=[]"]
    if not steps and status == "INCOMPLETE":
        errors.append("INCOMPLETE case has no steps")
    keys = []
    for i, s in enumerate(steps, start=1):
        if set(s) != STEP_FIELDS:
            errors.append(f"step {i}: fields {sorted(set(s) ^ STEP_FIELDS)} wrong")
            continue
        if s["step_no"] != i:
            errors.append(f"step {i}: step_no {s['step_no']} not consecutive")
        spec = OPERATION_SPEC.get(s["operation"])
        if not spec:
            errors.append(f"step {i}: unknown operation {s['operation']}")
            continue
        if spec != (s["phase"], s["state"]):
            errors.append(f"step {i}: {s['operation']} needs phase/state {spec}")
        ev = s["evidence_id"]
        if status == "INCOMPLETE" and s["phase"] != "FINDING" and ev:
            errors.append(f"step {i}: future {s['phase'].lower()} step must have empty evidence_id")
        if (status == "COMPLETE" or s["phase"] == "FINDING") and not ev:
            errors.append(f"step {i}: {s['operation']} needs an evidence_id")
        if ev and not EVIDENCE_RE.match(ev):
            errors.append(f"step {i}: malformed evidence_id {ev}")
        if ev and audit_at and evidence_time:
            t = evidence_time(ev)
            if t is None:
                errors.append(f"step {i}: unknown evidence_id {ev}")
            elif t > audit_at:
                errors.append(f"step {i}: evidence {ev} is dated after audit_at")
        if status == "COMPLETE" and s["phase"] != "VERIFICATION":
            errors.append(f"step {i}: COMPLETE cases contain only verification steps")
        keys.append((PHASE_ORDER.index(spec[0]), OPERATION_ORDER.index(s["operation"]), s["system_id"], s["resource_id"]))
    if keys != sorted(keys):
        errors.append("steps are not in phase/operation/system/resource order")
    if len(set(keys)) != len(keys):
        errors.append("duplicate steps")
    return errors


def validate_file(path: Path, expected_case_ids: list[str]) -> list[str]:
    errors = []
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = list(reader)
    if header != ["case_id", "deletion_status", "steps_json"]:
        errors.append(f"bad header {header}")
    ids = [r[0] for r in rows]
    if ids != sorted(expected_case_ids):
        errors.append(f"case ids differ from requests or are unsorted ({len(ids)} rows, expected {len(expected_case_ids)})")
    for case_id, status, steps_json in rows:
        try:
            steps = json.loads(steps_json)
        except json.JSONDecodeError as e:
            errors.append(f"{case_id}: steps_json not JSON ({e})")
            continue
        errors += [f"{case_id}: {e}" for e in validate_row(status, steps)]
    return errors
