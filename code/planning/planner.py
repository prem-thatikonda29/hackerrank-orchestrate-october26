"""Turn recorded resource facts into the ordered steps_json contract. Pure code: no LLM, no I/O."""

from dataclasses import dataclass

from index.vocab import CATEGORY_ACTION, OPERATION_ORDER, OPERATION_SPEC, PHASE_ORDER

# Observed states a resource can be recorded with.
STATES = ("ABSENT", "PRESENT", "EXPOSED", "CLEAN", "FLOW_ACTIVE", "FLOW_DISABLED", "RESTORE_ALLOWED", "RESTORE_BLOCKED", "UNKNOWN")


@dataclass(frozen=True)
class RecordedResource:
    system_id: str
    resource_id: str
    category: str          # a deletion_rules.csv data_category, or DATA_FLOW
    observed_state: str    # one of STATES
    evidence_id: str = ""  # file_id proving observed_state at audit_at ("" if none is citable)


# category action -> (finding, remediation, verification) when the state violates policy; verification when compliant
VIOLATION = {
    ("DELETE", "PRESENT"): ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    ("DELETE", "EXPOSED"): ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    ("DELETE", "CLEAN"): ("REPORT_REMAINING_RECORD", "DELETE_RECORD", "VERIFY_RECORD_ABSENT"),
    ("REDACT", "EXPOSED"): ("REPORT_EXPOSED_PII", "REDACT_RECORD", "VERIFY_RECORD_REDACTED"),
    ("ANONYMIZE", "EXPOSED"): ("REPORT_EXPOSED_PII", "ANONYMIZE_RECORD", "VERIFY_RECORD_ANONYMIZED"),
    ("DISABLE", "FLOW_ACTIVE"): ("REPORT_ACTIVE_DATA_FLOW", "DISABLE_DATA_FLOW", "VERIFY_DATA_FLOW_DISABLED"),
    ("RETAIN", "RESTORE_ALLOWED"): ("REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"),
}

COMPLIANT = {
    "DELETE": "VERIFY_RECORD_ABSENT",
    "REDACT": "VERIFY_RECORD_REDACTED",
    "ANONYMIZE": "VERIFY_RECORD_ANONYMIZED",
    "DISABLE": "VERIFY_DATA_FLOW_DISABLED",
}


def verification_for(category: str, state: str) -> str | None:
    action = CATEGORY_ACTION.get(category)
    if category == "BACKUP_ARCHIVE":
        return "VERIFY_RESTORE_BLOCKED"
    if action == "RETAIN":
        return "VERIFY_RECORD_PRESENT" if state != "ABSENT" else None
    if action in ("REDACT", "ANONYMIZE") and state == "ABSENT":
        return "VERIFY_RECORD_ABSENT"
    return COMPLIANT.get(action)


def plan(resources: list[RecordedResource]) -> tuple[str, list[dict]]:
    """Return (deletion_status, steps) for an authorized deletion."""
    findings, remediation, verification = [], [], []
    seen = set()
    for r in resources:
        if r.resource_id in seen:
            continue
        seen.add(r.resource_id)
        action = CATEGORY_ACTION.get(r.category)
        violation = VIOLATION.get((action, r.observed_state)) if r.observed_state != "UNKNOWN" else None
        if violation:
            find_op, fix_op, verify_op = violation
            findings.append((find_op, r, r.evidence_id))
            remediation.append((fix_op, r, ""))
            verification.append((verify_op, r, ""))
        else:
            verify_op = verification_for(r.category, r.observed_state)
            if verify_op:
                verification.append((verify_op, r, r.evidence_id))

    status = "INCOMPLETE" if findings else "COMPLETE"
    rows = findings + remediation + verification
    if status == "INCOMPLETE":
        rows = [(op, r, ev if OPERATION_SPEC[op][0] == "FINDING" else "") for op, r, ev in rows]
    rows.sort(key=lambda x: (PHASE_ORDER.index(OPERATION_SPEC[x[0]][0]), OPERATION_ORDER.index(x[0]), x[1].system_id, x[1].resource_id))
    steps = []
    for i, (op, r, ev) in enumerate(rows, start=1):
        phase, state = OPERATION_SPEC[op]
        steps.append({"step_no": i, "phase": phase, "system_id": r.system_id, "resource_id": r.resource_id,
                      "operation": op, "state": state, "evidence_id": ev})
    return status, steps
