# HANDOFF: Ghost Data Still Exists

Written 24 Oct 2026, 23:55 IST, at the end of a local Claude Code session. Deadline: **25 Oct 2026, 18:00 IST**.
Read this first, then `TRACKER.md` (task list), then `code/docs/architecture.md` (agent design) and `code/docs/evidence_profile.md` (what the 94 evidence files contain).

Read `AGENTS.md` too. It requires appending a `log.txt` entry for every user turn, with the exact `tool=` and `model=` of the session writing it. Keep doing that in the new session; `log.txt` is the `chat_transcript` deliverable.

## 1. The task in one paragraph

For each of 518 rows in `dataset/deletion_requests.csv`, decide whether deletion was really requested (else `NOT_APPLICABLE`, `[]`), resolve current and historical identifiers, reconstruct every resource's state at `audit_at` from the evidence, and write `output.csv` (`case_id,deletion_status,steps_json`) with findings, remediation and verification steps in the contract order. Full contract: `problem_statement.md` and `AGENTS.md` section 6.

## 2. Design (agreed)

- **Agent produces facts, code produces steps.** An LLM agent (LangChain 1.x `create_agent`) investigates and records what it observed per resource. Deterministic code (`code/planning/planner.py`) turns recorded facts into sorted, numbered steps. The LLM never orders or numbers steps.
- Provider-agnostic: model and key come from `.env` through `code/llm_config.py`. The user has no Anthropic API access and will use their own OpenAI key.
- Guardrails and tracing plan: `code/docs/architecture.md` sections 3 and 4.
- A deterministic **baseline** (index + investigator + planner, no LLM) already exists and is the agent's fallback and the comparison row for the report.

## 3. State right now

Done (all in this repo, branch `handoff/baseline-pipeline`):

| Piece | Where |
|---|---|
| Venv and pins | `code/requirements.txt` (`.venv` is gitignored; recreate it) |
| Evidence index | `code/index/{build,store,vocab,logparse}.py` |
| Planner and validator | `code/planning/` (validator accepts all 32 golden rows) |
| Scorer | `code/evaluation/scorer.py` |
| Baseline and linking | `code/runner/{baseline,investigate,linking,intent}.py` |
| Provider config | `code/llm_config.py` (OpenAI by default) |
| Endpoint smoke test | `code/scripts/check_llm.py` |
| Vision extractor (partly run) | `code/index/vision.py` |
| Status command | `code/scripts/status.py` |

Baseline on the 32 samples: status accuracy **1.000**, step precision/recall/F1 **0.995 / 0.979 / 0.987**, field accuracy state 1.000, evidence_id 1.000, step_no 0.974, exact row **0.875** (28 of 32 rows exact). The 518-row baseline file validates (345 INCOMPLETE, 163 COMPLETE, 10 NOT_APPLICABLE).

Not done: screenshot/PDF observations in the index (B-section of the tracker), temporal state (export expiry etc.), the LangChain tools and agent, `code/main.py` (still an **empty file**), reports, final run, packaging, `agent-trace.zip`.

## 4. Resume in a new environment

```bash
git checkout handoff/baseline-pipeline
python3 -m venv .venv && .venv/bin/pip install -r code/requirements.txt
```

Do not rely on a global LangChain install: on the author's Mac it was broken (`langchain` 1.4.0 with `langgraph-prebuilt` 1.1.0). The pins in `code/requirements.txt` work together.

Create `.env` in the repo root (gitignored, never commit it), or just export the variable. Provider decision: **OpenAI directly** (the user's own key). `code/llm_config.py` is the single place that reads the config; every script goes through it:

```
OPENAI_API_KEY=<your OpenAI key>       # LLM_API_KEY also works and wins if both are set
AGENT_MODEL=gpt-6-luna                 # default if unset; confirm with check_llm.py --list
VISION_MODEL=gpt-6-luna                # must accept images; unverified, test with: vision.py --only account_admin
```

No base URL is needed (it defaults to `https://api.openai.com/v1`; set `LLM_BASE_URL` only for another OpenAI-compatible endpoint).

Then:

```bash
.venv/bin/python code/index/build.py                                       # builds build/index.sqlite (~seconds)
.venv/bin/python code/runner/baseline.py --split sample                    # runs/baseline_sample.csv
.venv/bin/python code/evaluation/scorer.py runs/baseline_sample.csv --verbose   # expect exact rows 0.875
.venv/bin/python code/scripts/status.py                                    # tracker progress
.venv/bin/python code/scripts/check_llm.py --list                          # list models, then run without --list for a tool-calling check
```

**Budget: all runs are now paid.** The user has about $3.60 of OpenAI credit and the free Groq tier from the local FreeLLMAPI router (Docker on the author's Mac, `http://127.0.0.1:3001/v1`) is gone, so there is no free development tier. Prices (developers.openai.com/api/docs/pricing, 24 Oct): `gpt-6-luna` $0.10 in / $0.01 cached / $0.50 out per 1M tokens. Rough costs with lean prompts (1 to 3 model calls, about 6K tokens per case): one 32-sample run about $0.05, the 11 screenshots under $0.05, the full 518-case run about $0.3 to 0.6. So: keep requests lean, cache per-case results on disk so reruns are free, and build the budget cap (`BUDGET_USD`, default 2.50, stop starting new cases past it and fall back to the baseline). Check the remaining credit in the OpenAI dashboard before the final run.

Old local-router facts, for reference only: it exposed Groq `gpt-oss-120b`, `gpt-oss-20b` and `qwen3.8-27b` (all passed tool calling; the last read images), with Groq limits of 30 RPM, 1K requests/day, 8K tokens/minute and 200K tokens/day per model.

`cache/`, `build/`, `runs/` and `.venv/` are gitignored. `cache/vision/` held 3 of 11 screenshot extractions, so re-run `code/index/vision.py` (it skips cached files and sleeps 12 s between calls; the sleep was for Groq's per-minute limit and can be lowered with OpenAI).

## 5. Data rules found so far (all heuristics from the 32 gold samples; they raised exact rows from 41% to 87.5%)

Code: `code/runner/investigate.py`, `code/runner/linking.py`, `code/index/build.py`.

1. **Match identifiers case-insensitively** (20 eval request emails differ from the identity table only by case). Match whole emails, not substrings (look-alike decoys such as `g.silva76.jr@` vs `g.silva76@`). No email is ever shared by two users.
2. **Best link is the user ID** (`customer_reference`, `subject_reference`, `user_id`). 94 of 107 historical emails appear in evidence; old phones and hashes almost never do.
3. **Stale evidence is dropped:** a candidate whose latest sighting predates `requested_at` is ignored (removes `account_6523` in case_17).
4. **Citations are `evidence_*` file IDs.** COMPLETE cases cite on every verification step; INCOMPLETE cases cite only on findings (remediation and verification use `""`). Never cite a file captured after `audit_at`. A file's lines dated before `audit_at` may still be used to discover a resource (case_09), never to cite.
5. **Absence by omission:** a deleted account has no record, so cite a snapshot that covers `account_service` and does not contain it, preferring a file the case already cites.
6. **Retained records:** audit records get `VERIFY_RECORD_PRESENT`; backups get only `REPORT_RESTORE_RISK` / `BLOCK_RESTORE` / `VERIFY_RESTORE_BLOCKED` (never a "present" check); data flows get disable/verify; support messages and email logs are redacted, analytics anonymized, tombstones redacted (stable `user_id` stays).
7. **Category follows the log event, not the ID prefix:** `contact_lookup_completed` on an `analytics_` ID is a marketing profile (delete); `contact_query_completed` / `deletion_marker_check` on a `support_` ID is a secondary identifier (delete).
8. **Activity-only resources** (primary account, identifier-free anonymized analytics) are linked through `deletion_worker` events near `reported_at`:
   - Account deletes sit in a window of -76 to -43 minutes before `reported_at`, and the event result mirrors the reported status (`COMPLETE`→`SUCCESS`, `PARTIALLY_COMPLETE`→`PARTIAL`, `NOT_STARTED`→`FAILED`). Hand-built story cases (01 to 10) use -5 to +10 minutes with any result.
   - Analytics anonymizations sit at -31 to -26 minutes and chain to the same case's support redaction (+390 or +449 s), flow disable (+839 s) and backup block (+1229 or +1288 s).
   - Resources already tied by evidence to any person are excluded; COMPLETE-looking cases prefer an account the evidence proves absent.
   - The status mapping only chooses between candidate records; it is never proof of state.
9. **Accept as lost:** `account_004`, `audit_record_6645`, `audit_record_5569` appear in no participant data for their cases. `account_410` (case_23) is a genuine three-way tie.

## 6. The screenshot / PDF family (next work)

12 `LINK_RESOURCE_IDENTITY` activity events (activity_4000 to 4011) tie a resource to a subject's email and user ID, with a parenthesised alias (a filename, a statement number, or the ID itself). The 11 PNGs and 4 PDFs under `dataset/evidence/` are the evidence for these cases. The link event identifies the subject; the image/PDF supplies the state. A link only counts when its `event_time` is at or before the case's `audit_at`.

| Link | Resource ← subject | Evidence file (captured) | What it shows | Case(s) |
|---|---|---|---|---|
| activity_4000 | account_5762 ← haruka.s1838@zoho.com, user_11 | account_admin_console_20260926.png (26 Sep) | profile exists, phone +81 90 7610 2507 | case_34 |
| activity_4001 | search_428 ← g.silva76@gmail.com, user_13 | search_index_inspector_20260901.png | hit with name and email; decoy search_1874 is `g.silva76.jr@` | case_35 |
| activity_4002 | data_flow_1121 ← jain.aditi@gmail.com, user_14 | connector_management_console_20260807.png | continuous sync to customer_analytics_warehouse, Healthy | case_39 |
| activity_4003 | analytics_7438 ← darwish.omar@icloud.com, user_20 | analytics_profile_view_20260713.png | identifiable profile (email shown) | case_50 |
| activity_4004 | export_1260 ← ingrid.dahl@outlook.com, user_18 | customer_export_console_20260618.png | Ready, created 18 Jun 06:42, **expires 25 Jun 2026 06:42 UTC** | case_52 (audit 19 Jun), case_33 (audit 19 Jul) |
| activity_4009 | export_1842 (NST-2026-0718-1842) ← ingrid.dahl, user_18 | account_history_report_20260718.pdf (18 Jul) | statement PDF; link time 18 Jul 11:07 | case_33 only (after case_52's audit_at) |
| activity_4005 | support_1034 ← kagyeman8947@fastmail.com, user_22 | support_ticket_console_20260524.png | ticket CS-5034 with name, email and shared order context | case_53 |
| activity_4006 | backup_9287 ← areed1433@gmail.com, user_16 | backup_restore_console_20260429.png | archive listed, **Retention end 26 Jul 2026**, decoy archives for other emails | case_55 |
| activity_4007 | object_19 (delivery_confirmation_19.pdf) ← priya.raman@gmail.com, user_07 | object_storage_browser_20260213.png | attachment object; catalog says it belongs only to this subject | case_61 (audit 14 Feb) |
| activity_4008 | marketing_001 ← kwame-mensah@proton.me, user_09 | marketing_profile_console.png (16 Sep) | live profile, email opt-in Yes; **image shows a different name (Soo-jin Park), so trust the link** | case_63 |
| activity_4010 | audit_record_003 ← oliver-bennett@gmail.com, user_03 | security_audit_explorer_20260905.png + security_retention_extract.pdf | closure event; retain until 25 Aug 2027 04:03 | case_45 |
| activity_4011 | tombstone_17 (user_17) ← leonie.g9352@outlook.com, user_17 | deleted_account_reference_20260310.png | "Retained reference" with only the stable user_17 and linked ORD/CS/FIN references | case_46 (audit 11 Mar) |

Expected semantics (hypotheses, not verified against gold):
- Present and in scope → `REPORT_REMAINING_RECORD` + delete (account, search entry, export, marketing profile, subject-only attachment `object_storage`).
- Identifiers visible in analytics or a support ticket → `REPORT_EXPOSED_PII` (anonymize / redact).
- Data flow enabled → `REPORT_ACTIVE_DATA_FLOW` + disable.
- Backup archive listed with no block → restore risk.
- `audit_record_003` retained within its period → `VERIFY_RECORD_PRESENT`. `tombstone_17` with only the stable reference → compliant, `VERIFY_RECORD_REDACTED`.
- An export or backup past its expiry or retention end at `audit_at` is gone or non-restorable (export_1260 for case_33).
- Which of audit_record_001 to 006 to include for case_45: use only the linked `audit_record_003` (the other five are filler rows in the screenshot; this is a guess).

Vision notes: `qwen3.8-27b` through the router read screenshots, but a free-form prompt misread a phone digit (1610 for 7610). `code/index/vision.py` asks for structured JSON and snaps emails, phones and resource IDs to known identities (edit distance), which fixed that. The three screenshots extracted locally looked correct. Check the remaining eight by eye against the table above.

Zero-step cases in the current baseline output: case_33, 34, 35, 39, 50, 52, 53, 55, 61, 63. They are deletion requests whose evidence is only these screenshots/PDFs.

## 7. Other facts worth knowing

- `system_activity.csv` is noisy: 721 FAILED and 376 PARTIAL results. `SYNC_RECORD` right after `DELETE_RECORD` (account_005) means the record is recreated. `CLASSIFY_REQUEST` events (10) are claims; they map to case_533 to case_542, the non-deletion requests.
- Verification also covers clean resources: the primary account always gets a `VERIFY_RECORD_ABSENT`.
- Log formats (6) are parsed by `code/index/logparse.py`; all 2211 lines parse. `trace_id` links a lookup line (email) to its completion line (resource).
- macOS note: `timeout` does not exist in the default shell.

## 8. Submission checklist (also in TRACKER.md section E)

`code.zip` (README.md + code/, no dataset, no `.env`, no nested archives, evaluation reports under `code/evaluation/`), `output.csv` (518 rows at repo root), `log.txt`, `agent-trace.zip` (include this handoff's Claude Code sessions and the claude.ai planning chat export), AI interview prep. Challenge and submission URL: https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists

## 9. Suggested order for the next session

1. Setup (section 4) and confirm the baseline reproduces 0.875.
2. B1 to B6 (finish vision, link events, temporal state, rescore).
3. C1 to C7 (agent) with a cheap model on the 32 samples.
4. D (reports and custom cases) while the final run executes.
5. E (final run, README, zips, trace).

If time runs short, the baseline already produces a valid `output.csv`: write it with `python code/runner/baseline.py --split eval --out output.csv`, then improve.
