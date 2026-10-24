# Evidence Profile (dataset/ as of 24 Oct 2026)

Profiling notes that drive the tool and planner design. All numbers come from read-only scripts over `dataset/`.

## 1. Evidence by type

94 files: 24 `.sql`, 28 `.json`, 1 `.txt`, 26 `.log`, 4 `.pdf`, 11 `.png`. Every file in the manifest exists on disk, and nothing on disk is missing from the manifest.

| Type | What one file holds | How identifiers appear | Dating |
|---|---|---|---|
| SQL inventory (11, `customer_data_inventory_*`) | 8 typed tables (`customer_profiles`, `customer_search_documents`, `customer_analytics_profiles`, `support_records`, `customer_exports`, `connector_subscriptions`, `backup_restore_entries`, `security_audit_records`) plus `snapshot_scope(origin_system, captured_at)` | Columns: `customer_reference`/`subject_reference` = `user_id`; email, phone and email_hash are plain columns; `enabled` and `restore_allowed` are 0/1 | `snapshot_scope` lists which systems the capture covers. The 20260905 file covers only one system and is otherwise empty. |
| SQL replica pairs (12: read, regional, reporting, failover) | `customer_data_primary` plus one or more replica tables with the same schema `(resource_id, origin_system, collection_name, record_json, updated_at)` | Fields live inside `record_json` | Compare primary against each replica to find drift (rows differ by count, e.g. 24 vs 26). |
| SQL `customer_accounts_failover_replica_20260902` | `customer_profiles_primary` / `_failover_replica` with `user_id`, name, email, phone, status | Plain columns | Holds the account_005 sync-recreate trap and look-alike decoys (Karim El-Masri, Mateo Rios Gil). |
| SQL `customer_search_primary_20260910` | `search_documents_primary(resource_id, display_name, email, source_resource_id)` | `source_resource_id` links search → account | |
| JSON snapshot bundle (13) | `snapshots[]` = {captured_at, data_store, collection, records[]} | Same record shapes as `record_json`; also `operational_record_history` lookups `{system, resource_id, record_type, result, lookup_value}` | Each inner `captured_at` can be earlier than the file's capture time |
| JSON service export (10) | `service_exports.<system>.<collection>[]` with `observed_at` and a nulled-or-filled profile | `status_code` 404 means the record is absent; null fields mean redacted or anonymized | |
| JSON lookup ledgers (`connector_configurations`, `customer_export_manifest`, `security_audit_ledger`) | `records[] = {recorded_at, system, resource_id, record_type, result, subject_reference}` | `subject_reference` is an email (sometimes UPPERCASE) or a `user_id` | Each row has its own `recorded_at` |
| JSON `customer_analytics_report`, `customer_export_jobs` | One collection of records | Typed record fields | |
| TXT handover (1) | Pipe rows `time \| system \| resource \| RECORD_TYPE \| RESULT \| subject` | Email (often UPPERCASE) or `user_id` | Captured 2026-10-01 |
| Logs (26, six formats: bracketed, logfmt, pipe, JSON-per-line, syslog, cloud `requestId=`) | Service events | `lookup_key`, `match_key`, `subject_key`, `subject_ref` and `correlation_key` hold the identifier; the `*_completed` line with the same `trace_id` carries the `resource_id` and state (`rows`, `http_status`, `enabled`, `email=…`, `integrity_check`) | No line is later than its file's capture time |
| PDF (4) | Statement, support attachment, legal register, compliance journal | Text layer present (PyMuPDF), so no OCR is needed | Legal register links `ORD-…` + `user_NN` to `legal_hold_*` through `order_reference` |
| PNG (11) | Admin console screenshots, one subject per detail panel, with look-alike decoy rows | Header line `user_NN · resource_id · email`; the backup, connector and export consoles list several rows | Needs a vision pass, done once and cached |

Shared vocabulary: `record_type` uses the rule categories directly (USER_PROFILE, SEARCH_INDEX_ENTRY, ANALYTICS_PROFILE, SUPPORT_MESSAGE, DATA_FLOW, BACKUP_ARCHIVE, USER_EXPORT, SECURITY_AUDIT_LOG, LEGAL_HOLD_RECORD). `result` is one of FOUND, NOT_FOUND, IDENTIFIABLE, ANONYMIZED, REDACTED, ACTIVE, DISABLED, BLOCKED.

## 2. Origin vs system

`origin` in `evidence_files.csv` names the exporter, not the system the data belongs to. Most files span 5–9 systems. Take `system_id` from the record itself (`origin_system`, `data_store`, `system`, the log's `service`) or from the resource prefix:

| Prefix | system_id | Source |
|---|---|---|
| account_ | account_service | samples, SQL |
| search_ | search_service | samples, SQL |
| analytics_ | analytics_platform | samples, SQL |
| support_ | support_platform | samples, SQL (`support_conversations` = SUPPORT_MESSAGE, `ticket_attachments` = SUPPORT_ATTACHMENT) |
| export_ | export_service | samples, SQL |
| backup_ | backup_service | samples, SQL |
| data_flow_ | integration_service | samples, SQL |
| audit_record_ | security_service | samples, SQL |
| tombstone_ | identity_service | samples, SQL |
| legal_hold_ | legal_service | SQL `origin_system` (not in samples) |
| marketing_ | marketing_platform | one PNG (`marketing_001`, origin marketing_platform) |
| cache_, email_delivery_, object_ | cache_service, email_delivery_service, object_storage | activity log only; never in evidence |

## 3. Identity linking

- Most records carry `customer_reference`, `subject_reference` or `user_id` = the identities `user_id`, which is the strongest link. Only 3 references in the evidence are unknown users (`user_7204`, `user_8741`, `user_9116`), and they are decoys.
- Old emails matter: 94 of 107 historical emails appear in the evidence. Old phones (1 of 92) and old hashes (1 of 107) almost never do.
- Evidence uses UPPERCASE emails, so match case-insensitively. Look-alike decoys (`g.silva76.jr@` vs `g.silva76@`) mean the match must be an exact whole token, not a substring.

## 4. Where the sample answers' resources come from (193 steps)

- 168 are in an evidence file captured before `audit_at`.
- 6 appear only in a file captured after `audit_at`, though the log lines themselves fall before it (e.g. case_09 links account_3960 and audit_record_4902 through `trace_id` in evidence_06).
- 13 appear only in the activity log. These are all `account_*` primary profiles, linked by a `deletion_worker` DELETE_RECORD event shortly before the case's `reported_at`.
- 6 appear nowhere in participant data (e.g. audit_record_6645, audit_record_5569), so we accept losing them.

## 5. Sample-case traces

- **case_05 (INCOMPLETE).** Activity shows `account_005` deleted at 09:11:01 (SUCCESS), then a SYNC_RECORD to `account_failover_replica` 8s later. evidence_54 (failover replica, captured 2026-09-02) still has `account_005` ACTIVE with full PII, so the account is recreated. Finding from evidence: REMAINING_RECORD. The fix and the check (DELETE / VERIFY_ABSENT) come from the USER_PROFILE rule, DELETE.
- **case_10 (INCOMPLETE, historical email).** The request email `nadia.ionescu@` is user_10's old email (the current one is `nadia.petrescu@`). evidence_61 (support_replica.log): `lookup_key=nadia.ionescu@` gives `support_002`, and `deletion_marker_check marker_found=false` means it is still present. The activity log calls support_002 deleted, which is false. `account_010` is verification-only, linked by activity_3595 at the exact `reported_at`.
- **case_26 (COMPLETE, with retained records).** Every step cites evidence_92 (service export 2026-09-15): account 404 (ABSENT), audit_record present (RETAIN, so VERIFY_PRESENT), support fields nulled (REDACTED), analytics nulled (ANONYMIZED), data_flow `enabled=false` (DISABLED), backup `restore_allowed=false` (BLOCKED). evidence_02 also mentions this user but was captured 09-17, after `audit_at`, so it is ignored.
- **case_32 (INCOMPLETE, tombstone).** evidence_51 has `tombstone_9178` (user_542) still holding name, email, phone and hash, so EXPOSED_PII leads to REDACT. `account_8858` is linked only through activity.
- **case_09.** In evidence_93 (inventory SQL), `backup_4978` has `customer_email=k.appiah88@`, `restore_allowed=1`, which is a RESTORE_RISK. A backup is RETAIN, so it gets only BLOCK_RESTORE plus VERIFY_RESTORE_BLOCKED, never VERIFY_PRESENT.

How a COMPLETE case cites evidence: absence is proven either by an explicit 404 or NOT_FOUND, or by a snapshot whose scope covers the system but does not contain the record (evidence_54 proves account_001/002/004 absent).

## 6. Non-deletion requests

The 10 `CLASSIFY_REQUEST` accounts map, through the security audit ledger, to case_533–case_542. All ten request texts are explicit non-deletion requests (correct my phone number, send invoices, unsubscribe me, etc.). Text classification and the activity hint agree.
