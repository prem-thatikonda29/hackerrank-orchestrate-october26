from planning.planner import RecordedResource, plan
from planning.validator import validate_row


def rec(rid, category, state, ev="evidence_01"):
    system = {"account": "account_service", "search": "search_service", "data_flow": "integration_service", "backup": "backup_service",
              "support": "support_platform", "analytics": "analytics_platform"}[rid.rsplit("_", 1)[0]]
    return RecordedResource(system, rid, category, state, ev)


def ops(steps):
    return [(s["phase"], s["operation"], s["resource_id"]) for s in steps]


def test_incomplete_orders_phases_and_disables_flow_before_deleting():
    status, steps = plan([rec("account_1", "USER_PROFILE", "PRESENT"), rec("data_flow_1", "DATA_FLOW", "FLOW_ACTIVE")])
    assert status == "INCOMPLETE"
    assert [s["step_no"] for s in steps] == list(range(1, len(steps) + 1))
    phases = [s["phase"] for s in steps]
    assert phases == sorted(phases, key=["FINDING", "REMEDIATION", "VERIFICATION"].index)
    remediation = [s["operation"] for s in steps if s["phase"] == "REMEDIATION"]
    assert remediation.index("DISABLE_DATA_FLOW") < remediation.index("DELETE_RECORD")


def test_incomplete_cites_only_findings():
    _, steps = plan([rec("search_1", "SEARCH_INDEX_ENTRY", "PRESENT")])
    assert [bool(s["evidence_id"]) for s in steps] == [True, False, False]


def test_complete_has_only_cited_verifications():
    status, steps = plan([rec("account_1", "USER_PROFILE", "ABSENT"), rec("support_1", "SUPPORT_MESSAGE", "CLEAN")])
    assert status == "COMPLETE"
    assert {s["phase"] for s in steps} == {"VERIFICATION"} and all(s["evidence_id"] for s in steps)
    assert ("VERIFICATION", "VERIFY_RECORD_REDACTED", "support_1") in ops(steps)


def test_retained_backup_is_never_verified_present():
    status, steps = plan([rec("backup_1", "BACKUP_ARCHIVE", "RESTORE_ALLOWED")])
    assert status == "INCOMPLETE"
    assert [s["operation"] for s in steps] == ["REPORT_RESTORE_RISK", "BLOCK_RESTORE", "VERIFY_RESTORE_BLOCKED"]


def test_redact_and_anonymize_choose_the_policy_action():
    _, steps = plan([rec("support_1", "SUPPORT_MESSAGE", "EXPOSED"), rec("analytics_1", "ANALYTICS_PROFILE", "EXPOSED")])
    assert {"REDACT_RECORD", "ANONYMIZE_RECORD"} <= {s["operation"] for s in steps}


def test_planner_output_passes_validator():
    status, steps = plan([rec("account_1", "USER_PROFILE", "PRESENT"), rec("backup_1", "BACKUP_ARCHIVE", "RESTORE_ALLOWED")])
    assert validate_row(status, steps) == []


def test_validator_rejects_contract_breaks():
    status, steps = plan([rec("account_1", "USER_PROFILE", "ABSENT")])
    assert validate_row(status, [{**steps[0], "evidence_id": ""}])                      # COMPLETE needs a citation
    assert validate_row(status, steps, "2026-01-01T00:00:00Z", lambda _e: "2026-02-01T00:00:00Z")   # cited after audit_at
    assert validate_row("NOT_APPLICABLE", steps)                                          # must be []
    assert validate_row("INCOMPLETE", [{**steps[0], "step_no": 2}])                       # numbering
    assert validate_row("WHATEVER", [])
