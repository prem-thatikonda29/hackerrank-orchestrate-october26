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
