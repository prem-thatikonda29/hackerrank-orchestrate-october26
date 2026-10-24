"""Generate the custom evaluation dataset: seven fictional cases in the real dataset formats, with hand-written gold.

The cases target behaviours the public samples do not tune the pipeline on: a sync that recreates a deleted record,
a tombstone that still holds an email hash, evidence dated after audit_at, a non-English non-deletion request, a
non-English deletion request, a historical email next to a look-alike decoy, and a retained audit record next to a
restorable backup. All people, emails and IDs are fictional. Gold is written here by hand, never produced by the pipeline.

Run: python code/evaluation/custom_cases/build_cases.py   (writes code/evaluation/custom_cases/dataset/)
"""

import csv
import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "dataset"
REAL = HERE.parents[2] / "dataset"
HASH = "9f2c1e7a" * 8   # 64 hex characters


def write_csv(name: str, header: list[str], rows: list[list]) -> None:
    with open(OUT / name, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def ledger(captured_at: str, origin: str, rows: list[tuple]) -> dict:
    """rows: (system, resource_id, record_type, result, subject_reference)"""
    return {"exported_at": captured_at, "origin": origin, "record_count": len(rows),
            "records": [{"recorded_at": captured_at, "system": s, "resource_id": r, "record_type": t, "result": res, "subject_reference": sub}
                        for s, r, t, res, sub in rows]}


USERS = [  # user_id, name, email, phone
    ("user_901", "Nadia Okafor", "nadia.okafor@example.org", "+234 802 555 0191"),
    ("user_902", "Daniel Reyes", "daniel.reyes@example.org", "+52 55 5550 0192"),
    ("user_903", "Marta Lindqvist", "marta.lindqvist@example.org", "+46 70 555 0193"),
    ("user_904", "Hugo Marchand", "hugo.marchand@example.org", "+33 6 55 50 01 94"),
    ("user_905", "Tomas Novak", "tomas.novak.work@example.org", "+420 601 555 195"),
    ("user_906", "Ines Carvalho", "ines.carvalho@example.org", "+351 912 555 196"),
    ("user_907", "Tomas Novakova", "tomas.novak1@example.org", "+420 601 555 197"),
    ("user_908", "Camille Roux", "camille.roux@example.org", "+33 6 55 50 01 98"),
]

T = "2026-10-01T08:00:00Z"
REQUESTS = [  # case_id, email, requested_at, reported_status, reported_at, audit_at, text
    ("custom_01", "nadia.okafor@example.org", T, "COMPLETE", "2026-10-02T02:00:00Z", "2026-10-03T12:00:00Z",
     "Please delete my account and every copy of my personal data. Confirm when the review is complete."),
    ("custom_02", "daniel.reyes@example.org", T, "COMPLETE", "2026-10-02T03:00:00Z", "2026-10-03T10:00:00Z",
     "I want my account erased. Follow the retention rules for anything you must keep."),
    ("custom_03", "marta.lindqvist@example.org", T, "COMPLETE", "2026-10-02T04:00:00Z", "2026-10-03T12:00:00Z",
     "Please permanently delete the account registered to this address."),
    ("custom_04", "hugo.marchand@example.org", T, "COMPLETE", "2026-10-02T05:00:00Z", "2026-10-03T12:00:00Z",
     "Bonjour, je souhaite uniquement corriger l'orthographe de mon nom sur mon compte. Merci de ne pas supprimer mon compte ni mes données."),
    ("custom_05", "camille.roux@example.org", T, "COMPLETE", "2026-10-02T06:00:00Z", "2026-10-03T12:00:00Z",
     "Je vous prie de supprimer définitivement mon compte ainsi que toutes les données personnelles associées."),
    ("custom_06", "tomas.novak@example.org", T, "PARTIALLY_COMPLETE", "2026-10-02T07:00:00Z", "2026-10-03T12:00:00Z",
     "Please erase the account that used this email address. Include records linked to earlier identifiers."),
    ("custom_07", "ines.carvalho@example.org", T, "NOT_STARTED", "2026-10-02T08:00:00Z", "2026-10-03T12:00:00Z",
     "I would like my account and personal data deleted under the standard procedure."),
]


def step(n, phase, system, resource, op, state, ev):
    return {"step_no": n, "phase": phase, "system_id": system, "resource_id": resource, "operation": op, "state": state, "evidence_id": ev}


def numbered(rows: list[tuple]) -> list[dict]:
    return [step(i, *r) for i, r in enumerate(rows, 1)]


F, R, V = "FINDING", "REMEDIATION", "VERIFICATION"
GOLD = {
    "custom_01": ("INCOMPLETE", numbered([
        (F, "account_service", "account_9101", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_01"),
        (F, "integration_service", "data_flow_9201", "REPORT_ACTIVE_DATA_FLOW", "ACTIVE", "evidence_01"),
        (R, "integration_service", "data_flow_9201", "DISABLE_DATA_FLOW", "DISABLED", ""),
        (R, "account_service", "account_9101", "DELETE_RECORD", "ABSENT", ""),
        (V, "account_service", "account_9101", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        (V, "integration_service", "data_flow_9201", "VERIFY_DATA_FLOW_DISABLED", "DISABLED", "")])),
    "custom_02": ("INCOMPLETE", numbered([
        (F, "identity_service", "tombstone_9302", "REPORT_EXPOSED_PII", "PRESENT", "evidence_03"),
        (R, "identity_service", "tombstone_9302", "REDACT_RECORD", "REDACTED", ""),
        (V, "account_service", "account_9102", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        (V, "identity_service", "tombstone_9302", "VERIFY_RECORD_REDACTED", "REDACTED", "")])),
    "custom_03": ("COMPLETE", numbered([
        (V, "account_service", "account_9103", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_04"),
        (V, "search_service", "search_9403", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_04")])),
    "custom_04": ("NOT_APPLICABLE", []),
    "custom_05": ("COMPLETE", numbered([
        (V, "account_service", "account_9108", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_06"),
        (V, "search_service", "search_9408", "VERIFY_RECORD_ABSENT", "ABSENT", "evidence_06")])),
    "custom_06": ("INCOMPLETE", numbered([
        (F, "search_service", "search_9405", "REPORT_REMAINING_RECORD", "PRESENT", "evidence_07"),
        (R, "search_service", "search_9405", "DELETE_RECORD", "ABSENT", ""),
        (V, "account_service", "account_9105", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        (V, "search_service", "search_9405", "VERIFY_RECORD_ABSENT", "ABSENT", "")])),
    "custom_07": ("INCOMPLETE", numbered([
        (F, "backup_service", "backup_9606", "REPORT_RESTORE_RISK", "ACTIVE", "evidence_08"),
        (R, "backup_service", "backup_9606", "BLOCK_RESTORE", "BLOCKED", ""),
        (V, "account_service", "account_9106", "VERIFY_RECORD_ABSENT", "ABSENT", ""),
        (V, "security_service", "audit_record_9506", "VERIFY_RECORD_PRESENT", "PRESENT", ""),
        (V, "backup_service", "backup_9606", "VERIFY_RESTORE_BLOCKED", "BLOCKED", "")])),
}

# custom_07's audit record and backup are cited only if the case is incomplete, so their evidence ids stay empty in verification.

EVIDENCE = {   # file_id -> (path, origin, captured_at, content)
    "evidence_01": ("evidence/records/account_sync_check_20261002.json", "account_service", "2026-10-02T10:00:00Z",
                    ledger("2026-10-02T10:00:00Z", "account_service", [
                        ("account_service", "account_9101", "USER_PROFILE", "FOUND", "nadia.okafor@example.org"),
                        ("integration_service", "data_flow_9201", "DATA_FLOW", "ACTIVE", "nadia.okafor@example.org")])),
    "evidence_02": ("evidence/records/account_lookup_20261002.json", "account_service", "2026-10-02T09:00:00Z",
                    ledger("2026-10-02T09:00:00Z", "account_service", [
                        ("account_service", "account_9102", "USER_PROFILE", "NOT_FOUND", "daniel.reyes@example.org")])),
    "evidence_03": ("evidence/records/deleted_references_20261002.json", "identity_service", "2026-10-02T09:30:00Z",
                    {"captured_at": "2026-10-02T09:30:00Z", "source": "identity_reference_store", "collection": "deleted_customer_references",
                     "records": [{"resource_id": "tombstone_9302", "user_id": "user_902", "email_hash": HASH, "name": None, "phone": None}]}),
    "evidence_04": ("evidence/records/account_search_check_20261002.json", "account_service", "2026-10-02T11:00:00Z",
                    ledger("2026-10-02T11:00:00Z", "account_service", [
                        ("account_service", "account_9103", "USER_PROFILE", "NOT_FOUND", "marta.lindqvist@example.org"),
                        ("search_service", "search_9403", "SEARCH_INDEX_ENTRY", "NOT_FOUND", "marta.lindqvist@example.org")])),
    "evidence_05": ("evidence/records/search_recheck_20261005.json", "search_service", "2026-10-05T11:00:00Z",
                    ledger("2026-10-05T11:00:00Z", "search_service", [
                        ("search_service", "search_9403", "SEARCH_INDEX_ENTRY", "FOUND", "marta.lindqvist@example.org")])),
    "evidence_06": ("evidence/records/account_search_check_20261002b.json", "account_service", "2026-10-02T12:00:00Z",
                    ledger("2026-10-02T12:00:00Z", "account_service", [
                        ("account_service", "account_9108", "USER_PROFILE", "NOT_FOUND", "camille.roux@example.org"),
                        ("search_service", "search_9408", "SEARCH_INDEX_ENTRY", "NOT_FOUND", "camille.roux@example.org")])),
    "evidence_07": ("evidence/records/identity_lookup_20261002.json", "search_service", "2026-10-02T09:00:00Z",
                    ledger("2026-10-02T09:00:00Z", "search_service", [
                        ("account_service", "account_9105", "USER_PROFILE", "NOT_FOUND", "user_905"),
                        ("search_service", "search_9405", "SEARCH_INDEX_ENTRY", "FOUND", "tomas.novak@example.org"),
                        ("search_service", "search_9407", "SEARCH_INDEX_ENTRY", "FOUND", "tomas.novak1@example.org")])),
    "evidence_08": ("evidence/records/retention_check_20261002.json", "backup_service", "2026-10-02T09:00:00Z",
                    ledger("2026-10-02T09:00:00Z", "backup_service", [
                        ("account_service", "account_9106", "USER_PROFILE", "NOT_FOUND", "ines.carvalho@example.org"),
                        ("security_service", "audit_record_9506", "SECURITY_AUDIT_LOG", "FOUND", "user_906"),
                        ("backup_service", "backup_9606", "BACKUP_ARCHIVE", "ACTIVE", "ines.carvalho@example.org")])),
}

ACTIVITY = [  # activity_id, event_time, event_type, source_system, target_system, resource_id, result, details
    ("activity_9001", "2026-10-02T01:10:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9101", "SUCCESS", "Deletion worker removed account_9101."),
    ("activity_9002", "2026-10-02T01:20:00Z", "SYNC_RECORD", "integration_service", "account_service", "account_9101", "SUCCESS", "Customer profile synchronization processed account_9101 from data_flow_9201."),
    ("activity_9003", "2026-10-02T02:30:00Z", "DELETE_RECORD", "deletion_worker", "account_service", "account_9102", "SUCCESS", "Deletion worker removed account_9102."),
    ("activity_9004", "2026-10-01T22:00:00Z", "INDEX_RECORD", "account_service", "search_service", "search_9999", "SUCCESS", "Customer search-index refresh processed search_9999."),
]


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    write_csv("user_identities.csv", ["user_id", "name", "email", "phone", "email_hash", "updated_at"],
              [[u, n, e, p, f"{i:02x}" * 32, "2026-06-01T00:00:00Z"] for i, (u, n, e, p) in enumerate(USERS, 1)])
    write_csv("user_audit_log.csv", ["audit_id", "user_id", "event_time", "field_name", "old_value", "new_value"],
              [["audit_9001", "user_905", "2026-08-01T10:00:00Z", "email", "tomas.novak@example.org", "tomas.novak.work@example.org"]])
    write_csv("system_activity.csv", ["activity_id", "event_time", "event_type", "source_system", "target_system", "resource_id", "reported_result", "details"], ACTIVITY)
    shutil.copy(REAL / "deletion_rules.csv", OUT / "deletion_rules.csv")
    header = ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"]
    write_csv("deletion_requests.csv", header, REQUESTS)
    write_csv("sample_deletion_requests.csv", header, [])
    manifest = []
    for fid, (path, origin, captured, body) in EVIDENCE.items():
        (OUT / path).parent.mkdir(parents=True, exist_ok=True)
        (OUT / path).write_text(json.dumps(body, indent=1), encoding="utf-8")
        manifest.append([fid, path, origin, captured])
    write_csv("evidence_files.csv", ["file_id", "file_path", "origin", "captured_at"], manifest)
    write_csv("expected_output.csv", ["case_id", "deletion_status", "steps_json"],
              [[cid, st, json.dumps(steps, separators=(",", ":"), sort_keys=True)] for cid, (st, steps) in sorted(GOLD.items())])
    print(f"wrote {len(REQUESTS)} cases, {len(EVIDENCE)} evidence files -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
