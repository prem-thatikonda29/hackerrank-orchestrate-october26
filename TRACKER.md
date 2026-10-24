# TRACKER: Ghost Data Still Exists

Deadline: **25 Oct 2026, 18:00 IST** (set by the user, not in the repo). Freeze code by ~15:00 IST.
Print progress and the next task: `python code/scripts/status.py`

How to use: tick a box (`[x]`) when a task is done and add a one-line note. Keep IDs stable. `[~]` means started but unfinished. Every task names its verification, so "done" is checkable.

## A. Foundation (done)

- [x] A1 Venv and pinned requirements (`.venv`, `code/requirements.txt`). Verify: `.venv/bin/python -c "import langchain; print(langchain.__version__)"` prints 1.4.7.
- [x] A2 Evidence index of all 94 files (`code/index/build.py`, 2894 observations, 2040 resources). Verify: `python code/index/build.py`.
- [x] A3 Planner and validator (`code/planning/`). Verify: validator accepts all 32 golden rows.
- [x] A4 Scorer (`code/evaluation/scorer.py`).
- [x] A5 Rule-based baseline (`code/runner/baseline.py`). Result on 32 samples: status 1.000, step F1 0.987, exact rows 0.875. Verify: `python code/runner/baseline.py --split sample && python code/evaluation/scorer.py runs/baseline_sample.csv`.
- [x] A6 Baseline file for all 518 cases validates (`runs/baseline_eval.csv`: 345 INCOMPLETE, 163 COMPLETE, 10 NOT_APPLICABLE).
- [x] A7 Provider config (`code/llm_config.py`, OpenAI by default) and endpoint smoke test (`code/scripts/check_llm.py`). The local FreeLLMAPI router worked on the author's Mac but is not available in the cloud session.
- [x] A8 (done 24 Oct: tool calling PASS, vision works on gpt-6-luna, temperature default removed) In the new session: add `OPENAI_API_KEY`, run `python code/scripts/check_llm.py` (expect PASS) and check that `gpt-6-luna` accepts images (`python code/index/vision.py --only account_admin`). If it does not, set `AGENT_MODEL` / `VISION_MODEL` to a model that does.

## B. Screenshots, PDFs and the early-audit family (next)

- [x] B1 (done 24 Oct: all 11 extracted, checked against HANDOFF section 6; main.py now extracts missing ones) Vision extractor (`code/index/vision.py`) written. 3 of 11 screenshots extracted in a local gitignored cache. Remaining: run all 11 (`python code/index/vision.py`), then compare against the expected facts in HANDOFF.md section 6.
- [x] B2 (done: build.py load_links, link event pairs with the file captured 7 min earlier) Index `LINK_RESOURCE_IDENTITY` events as identity links (resource_id, alias, email, user_id, event_time). Match each link's alias to the extracted screenshot or PDF text. Candidates only count when the link event is at or before `audit_at`.
- [x] B3 (done, only the linked resource is observed; other resources on a screen are decoys) Turn screenshot and PDF content into observations with state: account present, search hit present, data flow active, analytics identifiable, export Ready, support message with identifiers, backup archive listed, marketing profile live, object attachment present, audit record retained, tombstone reference only.
- [x] B4 (done in store.state_at for link-derived dates; case_33 drops the stale export_1260, case_52 keeps it) Temporal state at `audit_at`: export `expires_at`, backup "Retention end", `retained_until` / `retention_until`. An export that expired before `audit_at` is absent. Verify: case_52 (audit 19 Jun) has export_1260 present, case_33 (audit 19 Jul) does not.
- [x] B5 (done: all 12 now have 1 to 3 plausible steps; gold unknown) Re-run the 10 zero-step eval cases (case_33, 34, 35, 39, 50, 52, 53, 55, 61, 63) plus case_45 and case_46, and eyeball each against the screenshot.
- [x] B6 (done: samples still status 1.000, exact rows 0.875) Re-score the 32 samples. Must stay at or above 0.875 exact rows, status 1.000.

## C. Agent layer (LangChain)

- [ ] C1 Case dossier prompt: identity, candidate resources with state at `audit_at`, primary-account match, in under about 2.5K tokens.
- [ ] C2 LangChain tools with Pydantic in/out (see `code/docs/architecture.md` section 2): `resolve_identity`, `search_evidence`, `get_resource_timeline`, `read_evidence`, `query_snapshot`, `get_activity`, `find_primary_account`, `record_resource`, `preview_plan`, `submit`. Case ID and `audit_at` come from `ToolRuntime.context`, never from the model.
- [ ] C3 `create_agent` with middleware: trace (JSONL), input guard, model/tool call limits, retry, fallback model, tool-error handling, context trimming, output guard (planner then validator, 2 repair rounds), budget cap.
- [ ] C4 Tracing: `runs/<run_id>/trace.jsonl`, per-case JSON, `usage.json`, `code/trace_view.py`. The agent must give a `rationale` on every recorded resource.
- [ ] C5 Wire `code/main.py` (still an empty starter file): build index, run cases (baseline fallback), write `output.csv` at the repo root, write `usage_report.md`.
- [ ] C6 Smoke-test the agent on case_05, case_10, case_26, case_32 with a cheap model; compare with the baseline row.
- [ ] C7 Agent run on all 32 samples scored. Agent must be at least as good as the baseline; otherwise use the baseline for those case families and document why.

## Agent status (25 Oct session note)

- [x] C-basic: `code/agent/{agent,run}.py` (LangChain `create_agent`, tools `get_observations` and `get_activity`, structured `Verdict`, merge guards, baseline fallback, versioned on-disk cache). `code/main.py` runs it by default (`--engine baseline` for no model calls).
- Result: agent engine output equals the baseline on all 518 rows and 32 samples (0.875 exact rows). The agent changes about 430 state labels but the planner maps them to the same steps; it only has leverage where a label crosses a violation boundary, an intent decision, or an omission. Repeatability: 0 differing rows between two independent runs of 100 cases (the first design without the merge guards flipped 4 of 60).
- Still open: C4 tracing (per-case JSONL), C3 middleware beyond the merge guard, D1 to D4 reports, E packaging. Overfitting: no held-out gold exists, so build the custom cases (D2) from patterns NOT used to tune the heuristics.

### Link audit (agent vs rules for activity-only primary accounts), for the evaluation report

- Rules link 30/32 sample accounts (tuned on them). Zero-shot agent linker (`code/agent/link_agent.py`, prompt l1) linked 26/32; after revising the prompt on its sample failures (l2, contaminated) 28/32.
- Label-free audit (`python code/evaluation/link_audit.py --agent`): of 272 eval cases needing a linked account, 157 (58%) have exactly one candidate under the tight rule and the agent agrees with the rules on 147 of them (94%). 105 (39%) have several candidates; there the two agree on only 14 (13%). 10 have none (the screenshot family).
- Conclusion: the rule is well supported where it is decisive; the 105 ambiguous cases are a real uncertainty that neither method can resolve from the data (activity IDs do not follow case order and gaps to a case's other events are not constant). Rules stay the default (`--link-engine rules`); the agent linker is opt-in. An error here costs one wrong account step per case, not the case status.

## D. Evaluation and reports

- [x] D1 (done: code/evaluation/evaluation_report.md, regenerate with make_report.py) `code/evaluation/evaluation_report.md`: case, step, field and sequence accuracy; baseline vs agent; failures found and fixes.
- [x] D2 (done: 7 cases, code/evaluation/custom_cases/, run with run_custom.py) At least 3 extra evaluation cases under `code/evaluation/custom_cases/` (sync recreating a record, tombstone with leftover hash, activity or evidence after `audit_at`, NOT_APPLICABLE despite reported COMPLETE, old email with a look-alike decoy).
- [x] D3 (done: 20 passed, 1 documented xfail; python -m pytest code/tests) Unit tests for planner, validator, log parser, linking (`pytest code/tests`).
- [x] D4 (done: generated by main.py from the final run) `code/evaluation/usage_report.md` generated from the final full run (providers, models, calls, tokens, cost).

## E. Final run and submission

- [x] E1 (done: agent engine, 518 rows valid, 0 fallbacks, byte-identical on a clean no-key clone) Final 518-case run on `gpt-6-luna` (about $0.3 to 0.6 expected). `output.csv` at repo root. Validate: `validate_file` in `code/planning/validator.py` (518 rows, sorted, valid JSON).
- [x] E2 (done: README.md rewritten) README.md rewritten with setup and run instructions (the starter README is still in place).
- [x] E3 (done: python code/scripts/package.py builds and checks code.zip; zips are gitignored, rebuild before upload) `code.zip`: `zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/.git/*" "*/.DS_Store"` (no dataset, no nested archives, no keys, no `.env`).
- [x] E4 (done: 27 entries, each with tool= and model=, no secrets; the six estimated headings were corrected in place on 24 Oct with the user permission; Mac-session entry times are unchanged and some run ahead of the real clock) `log.txt` complete and verified (every entry has `tool=` and `model=`).
- [x] E5 (scope decided by the user: this cloud session's transcript only, plus log.txt as the chat transcript; Mac sessions and the claude.ai planning chat are NOT included. Restage after the session ends: python code/scripts/stage_trace.py --label cloud) `agent-trace.zip` per `agent_trace_instructions.md` (Claude Code sessions plus the claude.ai planning chat export via Settings, Privacy, Export data).
- [ ] E6 Submit at https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists and prepare for the AI interview (architecture, investigation approach, remediation planning, evaluation).

## Decision log

| When (IST) | Decision | Why |
|---|---|---|
| 24 Oct | Agent produces facts, code produces steps | Exact ordering and numbering must not depend on the LLM |
| 24 Oct | LangChain 1.x `create_agent` + middleware, provider from `.env` | No Anthropic API access; provider-agnostic |
| 24 Oct | Dev on Groq `gpt-oss-120b` via FreeLLMAPI, final run on OpenAI `gpt-6-luna` | Groq free tier is 200K tokens/day per model, too small for the full run |
| 25 Oct | Superseded: OpenAI direct for everything (key in `.env`, `code/llm_config.py`) | FreeLLMAPI is local-only and unavailable in the cloud session; no free dev tier, so keep prompts lean, cache cases and cap spend (about $3.60 credit) |
| 24 Oct | Link activity-only resources by timing + reported-status/result consistency | Primary accounts and anonymized analytics appear in no evidence file |
| 24 Oct | Drop candidates whose latest evidence predates `requested_at` | Removed the one temporal decoy in the samples |
| 24 Oct | Use `LINK_RESOURCE_IDENTITY` events as identity links for screenshot/PDF cases | They carry email and user_id for resources that images only show by resource ID |
