# Evaluation report

Everything below was produced by the commands in the last section. Gold exists only for the 32 public samples, so there is no gold accuracy for the 518 evaluation cases; for those the report measures agreement between engines and the share of cases that depend on fragile heuristics.

## 1. Accuracy on the 32 public samples and 7 custom cases

Engines: **baseline** = deterministic rules (index, investigator, linker, planner); **agent** = the default submission engine (LLM investigator with guards over the same dossier; the planner still writes every step). Steps are matched on (phase, operation, system_id, resource_id). Field accuracy is over matched steps.

| Run | Cases | Status acc. | Step P / R / F1 | Field acc. state / evidence_id / step_no | Sequence acc. | Exact row |
|---|---:|---:|---|---|---:|---:|
| Samples, baseline | 32 | 1.000 | 0.995 / 0.979 / 0.987 | 1.000 / 1.000 / 0.974 | 0.875 | 0.875 |
| Samples, agent | 32 | 1.000 | 0.995 / 0.979 / 0.987 | 1.000 / 1.000 / 0.974 | 0.875 | 0.875 |
| Custom, baseline | 7 | 0.857 | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 1.000 | 0.857 |
| Custom, agent | 7 | 1.000 | 1.000 / 1.000 / 1.000 | 1.000 / 1.000 / 1.000 | 1.000 | 1.000 |

**Read the sample row with care.** The baseline's rules were derived from these same 32 samples, so 1.000 status accuracy is an in-sample number and overstates what to expect on unseen data. The custom cases were written after the rules were fixed (but by the same author, so they are a generalization check, not an independent benchmark).

### Remaining sample failures (both engines)

- `case_04`: missing VERIFY_RECORD_ABSENT account_004; step_no of analytics_901 1 vs gold 2
- `case_18`: missing VERIFY_RECORD_PRESENT audit_record_6645; step_no of data_flow_4890 13 vs gold 14; step_no of analytics_037 12 vs gold 13; step_no of support_5569 11 vs gold 12
- `case_20`: missing VERIFY_RECORD_PRESENT audit_record_5569; step_no of data_flow_1770 7 vs gold 8
- `case_23`: missing VERIFY_RECORD_ABSENT account_410; extra VERIFY_RECORD_ABSENT account_7015

Four primary-account or audit-record IDs (`account_004`, `audit_record_6645`, `audit_record_5569`, `account_410`) appear in no participant-visible data for their cases (`account_410` is a genuine three-way tie), so they are unrecoverable from the supplied files.

## 2. Custom evaluation cases

Seven fictional cases in `code/evaluation/custom_cases/` (generator `build_cases.py`, hand-written gold, same file formats as the real dataset):

| Case | What it tests | Baseline | Agent |
|---|---|---|---|
| custom_01 | a sync recreated a deleted account and its data flow is still active (finding order, disable-before-delete); the recreation is visible in evidence, the pipeline does not read `SYNC_RECORD` events itself | pass | pass |
| custom_02 | tombstone that still holds an email hash (exposed PII on a REDACT record) | pass | pass |
| custom_03 | evidence captured after `audit_at` must be ignored (a later re-sync must not change a COMPLETE answer) | pass | pass |
| custom_04 | French request to correct a name and explicitly not delete (NOT_APPLICABLE despite reported COMPLETE) | FAIL | pass |
| custom_05 | French deletion request, clean evidence (COMPLETE) | pass | pass |
| custom_06 | historical email with a look-alike decoy owned by another person | pass | pass |
| custom_07 | retained audit record next to a restorable backup (restore risk, retained record verified present) | pass | pass |

### Custom case details

Fictional people and IDs; evidence uses the same ledger and snapshot formats as the real dataset (`fixtures/` holds the generated files).

#### custom_01

- Request email: `nadia.okafor@example.org`; requested 2026-10-01T08:00:00Z; reported status **COMPLETE** at 2026-10-02T02:00:00Z; audit_at 2026-10-03T12:00:00Z.
- Request text: "Please delete my account and every copy of my personal data. Confirm when the review is complete."
- Evidence:
  - `evidence_01` captured 2026-10-02T10:00:00Z: account_9101 FOUND (nadia.okafor@example.org); data_flow_9201 ACTIVE (nadia.okafor@example.org)
- Expected: **INCOMPLETE**

  | # | Phase | Operation | System | Resource | Evidence |
  |---:|---|---|---|---|---|
  | 1 | FINDING | REPORT_REMAINING_RECORD | account_service | account_9101 | evidence_01 |
  | 2 | FINDING | REPORT_ACTIVE_DATA_FLOW | integration_service | data_flow_9201 | evidence_01 |
  | 3 | REMEDIATION | DISABLE_DATA_FLOW | integration_service | data_flow_9201 | (empty) |
  | 4 | REMEDIATION | DELETE_RECORD | account_service | account_9101 | (empty) |
  | 5 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_9101 | (empty) |
  | 6 | VERIFICATION | VERIFY_DATA_FLOW_DISABLED | integration_service | data_flow_9201 | (empty) |

#### custom_02

- Request email: `daniel.reyes@example.org`; requested 2026-10-01T08:00:00Z; reported status **COMPLETE** at 2026-10-02T03:00:00Z; audit_at 2026-10-03T10:00:00Z.
- Request text: "I want my account erased. Follow the retention rules for anything you must keep."
- Evidence:
  - `evidence_02` captured 2026-10-02T09:00:00Z: account_9102 NOT_FOUND (daniel.reyes@example.org)
  - `evidence_03` captured 2026-10-02T09:30:00Z: tombstone_9302 still holds an email_hash (user_902)
- Expected: **INCOMPLETE**

  | # | Phase | Operation | System | Resource | Evidence |
  |---:|---|---|---|---|---|
  | 1 | FINDING | REPORT_EXPOSED_PII | identity_service | tombstone_9302 | evidence_03 |
  | 2 | REMEDIATION | REDACT_RECORD | identity_service | tombstone_9302 | (empty) |
  | 3 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_9102 | (empty) |
  | 4 | VERIFICATION | VERIFY_RECORD_REDACTED | identity_service | tombstone_9302 | (empty) |

#### custom_03

- Request email: `marta.lindqvist@example.org`; requested 2026-10-01T08:00:00Z; reported status **COMPLETE** at 2026-10-02T04:00:00Z; audit_at 2026-10-03T12:00:00Z.
- Request text: "Please permanently delete the account registered to this address."
- Evidence:
  - `evidence_04` captured 2026-10-02T11:00:00Z: account_9103 NOT_FOUND (marta.lindqvist@example.org); search_9403 NOT_FOUND (marta.lindqvist@example.org)
  - `evidence_05` captured 2026-10-05T11:00:00Z: search_9403 FOUND (marta.lindqvist@example.org)
- Expected: **COMPLETE**

  | # | Phase | Operation | System | Resource | Evidence |
  |---:|---|---|---|---|---|
  | 1 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_9103 | evidence_04 |
  | 2 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_9403 | evidence_04 |

#### custom_04

- Request email: `hugo.marchand@example.org`; requested 2026-10-01T08:00:00Z; reported status **COMPLETE** at 2026-10-02T05:00:00Z; audit_at 2026-10-03T12:00:00Z.
- Request text: "Bonjour, je souhaite uniquement corriger l'orthographe de mon nom sur mon compte. Merci de ne pas supprimer mon compte ni mes données."
- Evidence:
  - (none: the request text alone decides this case)
- Expected: **NOT_APPLICABLE**, `steps_json = []`

#### custom_05

- Request email: `camille.roux@example.org`; requested 2026-10-01T08:00:00Z; reported status **COMPLETE** at 2026-10-02T06:00:00Z; audit_at 2026-10-03T12:00:00Z.
- Request text: "Je vous prie de supprimer définitivement mon compte ainsi que toutes les données personnelles associées."
- Evidence:
  - `evidence_06` captured 2026-10-02T12:00:00Z: account_9108 NOT_FOUND (camille.roux@example.org); search_9408 NOT_FOUND (camille.roux@example.org)
- Expected: **COMPLETE**

  | # | Phase | Operation | System | Resource | Evidence |
  |---:|---|---|---|---|---|
  | 1 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_9108 | evidence_06 |
  | 2 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_9408 | evidence_06 |

#### custom_06

- Request email: `tomas.novak@example.org`; requested 2026-10-01T08:00:00Z; reported status **PARTIALLY_COMPLETE** at 2026-10-02T07:00:00Z; audit_at 2026-10-03T12:00:00Z.
- Request text: "Please erase the account that used this email address. Include records linked to earlier identifiers."
- Evidence:
  - `evidence_07` captured 2026-10-02T09:00:00Z: account_9105 NOT_FOUND (user_905); search_9405 FOUND (tomas.novak@example.org); search_9407 FOUND (tomas.novak1@example.org)
- Expected: **INCOMPLETE**

  | # | Phase | Operation | System | Resource | Evidence |
  |---:|---|---|---|---|---|
  | 1 | FINDING | REPORT_REMAINING_RECORD | search_service | search_9405 | evidence_07 |
  | 2 | REMEDIATION | DELETE_RECORD | search_service | search_9405 | (empty) |
  | 3 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_9105 | (empty) |
  | 4 | VERIFICATION | VERIFY_RECORD_ABSENT | search_service | search_9405 | (empty) |

#### custom_07

- Request email: `ines.carvalho@example.org`; requested 2026-10-01T08:00:00Z; reported status **NOT_STARTED** at 2026-10-02T08:00:00Z; audit_at 2026-10-03T12:00:00Z.
- Request text: "I would like my account and personal data deleted under the standard procedure."
- Evidence:
  - `evidence_08` captured 2026-10-02T09:00:00Z: account_9106 NOT_FOUND (ines.carvalho@example.org); audit_record_9506 FOUND (user_906); backup_9606 ACTIVE (ines.carvalho@example.org)
- Expected: **INCOMPLETE**

  | # | Phase | Operation | System | Resource | Evidence |
  |---:|---|---|---|---|---|
  | 1 | FINDING | REPORT_RESTORE_RISK | backup_service | backup_9606 | evidence_08 |
  | 2 | REMEDIATION | BLOCK_RESTORE | backup_service | backup_9606 | (empty) |
  | 3 | VERIFICATION | VERIFY_RECORD_ABSENT | account_service | account_9106 | (empty) |
  | 4 | VERIFICATION | VERIFY_RECORD_PRESENT | security_service | audit_record_9506 | (empty) |
  | 5 | VERIFICATION | VERIFY_RESTORE_BLOCKED | backup_service | backup_9606 | (empty) |

## 3. The 518 evaluation cases (no gold)

- Agent output equals the baseline output on 518 of 518 rows; no case needed the baseline fallback.
- The agent changes many internal state labels (for example `PRESENT` to `EXPOSED`) but the planner maps equivalent states to the same steps, so steps rarely change. Its measurable contribution is language-independent intent detection (custom_04) and a second reading of each resource.
- Repeatability: two independent agent runs over the same 100 cases produced 0 differing rows. Before the merge guards were added, a rerun of 60 cases differed on 4 rows (6.7%) and one case failed with an error; this is why the guards exist.

### Primary-account linking audit (label-free)

Activity-only primary accounts are linked by the rules (default) from the deletion worker's activity trail. `link_audit.py --agent` compares the rules with an independent LLM linker that uses only generic priors:

```
eval:ambiguous {'cases': 105, 'agent==baseline': 14}
eval:none {'cases': 10, 'agent==baseline': 10}
eval:unique {'cases': 157, 'agent==baseline': 147}
sample:ambiguous {'cases': 3, 'agent==baseline': 2}
sample:none {'cases': 7, 'agent==baseline': 6}
sample:unique {'cases': 9, 'agent==baseline': 7}
```

Where the rule yields exactly one candidate the independent linker agrees (about 94% of the time in the evaluation split), which supports the rule. Where it yields several (about 39% of the cases that need a linked account) the two disagree almost always: the data does not contain the answer, so those links are guesses and are expected to cost one account step per affected case, not the case status.

## 4. Failures found and improvements made

| # | Failure found | Improvement |
|---|---|---|
| 1 | Evidence older than the request appeared as a leftover (a stale `account_6523` in case_17) | Drop candidates whose latest sighting predates `requested_at` |
| 2 | Look-alike emails (`g.silva76.jr@` vs `g.silva76@`) matched as substrings | Whole-token, case-insensitive identifier matching; evidence tied to another user is excluded |
| 3 | Deleted accounts leave no record, so COMPLETE cases had no citation | Cite a snapshot that covers `account_service` and omits the account (absence by omission) |
| 4 | Primary accounts and anonymized analytics appear in no evidence | Link through the deletion worker's activity trail by timing, result consistency and exclusivity (the audit above measures how far this can be trusted) |
| 5 | Ten screenshot/PDF cases produced COMPLETE with no steps | Vision extraction with grounding to known identities; each `LINK_RESOURCE_IDENTITY` event is paired with the file captured 7 minutes earlier and only the linked resource is observed |
| 6 | Feeding whole screenshots into the index reduced sample exact rows from 0.875 to 0.844 and pulled in decoys (three exports, two backups) | Link-based observation of a single resource per screenshot |
| 7 | Export expiry and backup retention end were ignored | Dates read from the screens; an export past expiry is absent at `audit_at` (case_33 vs case_52) |
| 8 | `gpt-6-luna` rejects `temperature=0` | Removed the temperature default |
| 9 | The agent's parallel tool calls raced on one SQLite connection | Tool bodies and index access serialized with a lock |
| 10 | A result cache keyed by model name served stale results after the agent logic changed | Cache directory is versioned by `AGENT_VERSION` |
| 11 | The agent returned blank evidence IDs and dropped or invented resources, and was non-repeatable | Merge guards: dossier fills omitted candidates, invented resources are ignored, citations must be captured at or before `audit_at`, baseline fallback on any error |
| 12 | English-only intent regex missed non-English non-deletion requests (custom_04) | Intent is decided by the agent from the request text; the regex remains only for the linker and the fallback |

## 5. Known limitations

- No gold for the 518 evaluation cases; sample accuracy is in-sample for the rules.
- About 105 primary-account links are tie-breaks (section 3). The ten screenshot-family cases assume each case contains only its linked resource, which is a hypothesis, not a verified gold fact.
- The custom cases are author-written. They test behaviors, not independent ground truth.
- `SYNC_RECORD` activity events are not interpreted: a recreated record is detected only if an evidence file shows it. `python code/evaluation/sync_audit.py` measures the exposure on the supplied data: all 21 accounts with a successful sync after their delete are already present through evidence, sync events appear on 29 of 119 active data flows and on none of the 36 disabled ones, 361 of 447 sync events touch no case resource (decoys), and there are 0 contradictions with the evidence-derived states. The gap is therefore latent here, not costly, but a recreation visible only in the activity stream would be missed.

## 6. How to reproduce

```bash
python code/main.py                                   # index + agent engine -> output.csv, code/evaluation/usage_report.md
python code/runner/baseline.py --split sample         # runs/baseline_sample.csv
python code/agent/run.py --split sample --out runs/agent_sample.csv
python code/agent/run.py --split eval --out runs/agent_eval.csv && python code/runner/baseline.py --split eval
python code/evaluation/run_custom.py --engine baseline && python code/evaluation/run_custom.py --engine agent
python code/evaluation/scorer.py runs/agent_sample.csv --verbose
python code/evaluation/make_report.py                # rewrites this file
```
