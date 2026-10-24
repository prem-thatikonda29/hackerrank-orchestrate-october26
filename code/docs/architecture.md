# Architecture Plan: Ghost Data Deletion-Audit Agent

Status: plan, not yet implemented. Stack checked against `langchain==1.4.7` and `langgraph==1.2.17` in a clean venv on 24 Oct 2026.

## 1. Principles

1. **The agent produces facts, code produces steps.** The LLM investigates and records what it observed about each resource. Deterministic code turns those observations into ordered `steps_json`.
2. **Guardrails sit at every boundary:** input, each tool call, each model turn, final output, and the whole run.
3. **Everything is traced.** Every model turn, tool call, guardrail decision and token count is written to disk and can be replayed.
4. **Provider-agnostic.** The model comes from config through `init_chat_model`, so changing provider is a single `.env` edit.
5. **Reproducible.** The index is built from `dataset/` alone. Per-case results are cached and keyed by input and prompt version, and output ordering and serialization are fixed.

## 2. Layers

```
dataset/ ──► [L1 Index builder] ──► build/index.sqlite  (+ cache/vision/*.json)
                                         │
deletion_requests.csv ──► [L5 Runner] ──► per case ──► [L3 Agent: LangChain create_agent]
                                         │                 │  tools (L2) read index only
                                         │                 ▼
                                         │           ledger (recorded facts) + verdict
                                         │                 ▼
                                         │           [L4 Planner → Validator]  (pure code)
                                         ▼                 ▼
                                    runs/<run_id>/  ◄── traces, usage, case files
                                         ▼
                                    output.csv, evaluation/*.md
```

### L1. Evidence index (deterministic, built once)

- **Loaders**, one per format:
  - SQL: executed into SQLite. Inventory tables and primary/replica tables are kept per database copy.
  - JSON: bundle, service export and ledger shapes.
  - TXT.
  - Logs: six formats normalized to key=value, then grouped by `trace_id`.
  - PDF: `pymupdf` text.
  - PNG: vision model, run once, cached by file SHA-256.
- **Normalized tables** in `build/index.sqlite`:
  - `identities`, `identity_history`: current identity snapshot and change history, with `audit_id`.
  - `files`: `file_id`, path, origin, `captured_at`.
  - `observations`: one row per sighting of a resource in a file. Columns: `resource_id`, `system_id`, `record_type`, `copy` (primary, read_replica, failover, …), `observed_at`, `file_id`, the identifier fields (`user_ref`, emails, phone, hash, name), `state_hint` (present, absent, redacted, anonymized, enabled, disabled, restore_allowed, …), and the raw excerpt.
  - `observation_keys`: lowercased identifier → observation, with `match_type`.
  - `activity`: `system_activity.csv` with parsed resource references.
  - `rules`: `deletion_rules.csv` plus the per-category verification table.
  - `account_assignment`: one global matching of `deletion_worker` primary-profile events to cases, computed in a single pass across all cases.
- **System ID** comes from the record's own field (`origin_system`, `data_store`, `system`, the log `service`), falling back to the prefix table. It never comes from the manifest `origin` or the activity log's `target_system`.

### L2. Tools (LangChain `@tool`, Pydantic in and out)

Every tool:

- uses a Pydantic `args_schema` with `extra="forbid"` and enum-typed fields;
- returns a Pydantic result model serialized to compact JSON;
- reads the case binding (`case_id`, `audit_at`, request email) from `ToolRuntime.context`, so the LLM never passes or changes the cutoff;
- raises `ToolInputError` for bad input, which `ToolErrorMiddleware` turns into an error `ToolMessage` the model can recover from. Internal exceptions are not exposed.

| Tool | Args | Result | Notes |
|---|---|---|---|
| `resolve_identity` | `email` | `user_id`, current and old emails, phones and hashes, each with `audit_id` and time range; or `null` | Matches case-insensitively |
| `search_evidence` | `identifiers[]`, `systems[]?` | hits grouped by resource: `system_id`, `resource_id`, `record_type`, `match_type`, `file_id`, `observed_at`, `citable` | Hits from files captured after `audit_at` are excluded, or marked `citable=false` if policy allows discovery from them |
| `get_resource_timeline` | `resource_id` | every sighting up to `audit_at`, per copy, newest first, plus related activity hints and a computed `state_at_audit` | The main tool for deciding state |
| `read_evidence` | `file_id`, `resource_id?` | bounded excerpt, or cached vision text for images | Hard cap on output size |
| `query_snapshot` | `file_id`, `sql` | rows (≤ 50) | Read-only connection, SELECT only, file must be dated before `audit_at` |
| `get_activity` | `resource_ids[]` or `window` | activity rows, marked "hint, not citable" | |
| `find_primary_account` | none | precomputed account, with match basis and confidence | |
| `record_resource` | `system_id`, `resource_id`, `category`, `observed_state`, `evidence_id`, `rationale` | `accepted`, or a list of guardrail errors | Writes to the agent state ledger through LangGraph `Command(update=…)` |
| `preview_plan` | none | the code-built steps and validator errors | |

The final answer is a structured response, `response_format=ToolStrategy(AuditVerdict)`:

```python
class AuditVerdict(BaseModel):
    intent: Literal["deletion", "non_deletion"]
    intent_rationale: str
    confidence: Literal["high", "medium", "low"]
    notes: str
```

### L3. Agent (LangChain 1.x `create_agent`, a LangGraph state graph)

```python
agent = create_agent(
    model=init_chat_model(cfg.agent_model, model_provider=cfg.provider, temperature=0),
    tools=TOOLS,
    system_prompt=SYSTEM_PROMPT,          # role, rules table, step contract, investigation checklist
    state_schema=AuditState,              # AgentState + ledger: list[RecordedResource]
    context_schema=CaseContext,           # case_id, audit_at, email, request_text (immutable)
    response_format=ToolStrategy(AuditVerdict),
    middleware=[
        TraceMiddleware(run_dir),                       # wrap_model_call / wrap_tool_call → JSONL
        InputGuardrail(),                               # before_agent
        ModelCallLimitMiddleware(run_limit=25, exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=60),
        ToolCallLimitMiddleware(tool_name="query_snapshot", run_limit=8),
        ModelRetryMiddleware(max_retries=3, backoff_factor=2.0),
        ToolRetryMiddleware(max_retries=1, tools=["read_evidence"]),
        ModelFallbackMiddleware(cfg.fallback_model),    # stronger model on hard failure
        ToolErrorMiddleware(on_error=to_model_message), # ToolInputError → error ToolMessage
        ContextEditingMiddleware(edits=[ClearToolUsesEdit(trigger=60_000, keep=6)]),
        OutputGuardrail(),                              # after_model: verdict → planner → validator
    ],
)
```

The execution loop per case is LangGraph's: model → tool calls → tool results → model … → structured `AuditVerdict`. `OutputGuardrail` then runs the planner and validator. If they report errors, it appends them as a message and jumps back to the model, at most 2 repair rounds. If the case still fails, it falls back to a deterministic verdict (§5) and flags the case.

### L4. Planner and validator (pure code, unit-tested)

- **Planner.** The ledger plus the rules give, for each resource, a treatment and a finding operation:
  - remaining record → DELETE
  - exposed PII → REDACT or ANONYMIZE
  - active data flow → DISABLE_DATA_FLOW
  - restore risk → BLOCK_RESTORE

  Every resource then gets a verification step from the per-category table. Backups get only VERIFY_RESTORE_BLOCKED. Retained records get VERIFY_RECORD_PRESENT. The primary account always gets a check. Steps are sorted by phase, then operation order, then `system_id`, then `resource_id`, and numbered from 1. The evidence rule: COMPLETE cases cite on every step; INCOMPLETE cases cite on findings only.
- **Validator.**
  - Schema and enums.
  - Phase and operation order, and consecutive numbering.
  - Every finding has a matching remediation and verification step.
  - Every cited `evidence_id` exists, is dated at or before `audit_at`, and mentions the resource.
  - NOT_APPLICABLE cases have `[]`.
  - No duplicate steps.
- **File validator.** Exactly 518 rows, header exact, sorted, every `steps_json` parses.

### L5. Runner

- `python code/main.py [--cases case_01,case_10] [--split sample|eval] [--concurrency 6] [--resume]`
- A thread pool with a configurable concurrency. Each case is idempotent: results are cached at `cache/cases/<case_id>-<input_hash>-<prompt_version>.json`, so a re-run resumes where it stopped.
- Rows are written in `case_id` order, and JSON is serialized with sorted keys and compact separators.

## 3. Guardrails

| Layer | Guardrail | Mechanism |
|---|---|---|
| Input | Request text is data, not instructions | Text is wrapped in tags and the system prompt says to treat it as quoted customer text; an `InputGuardrail` (`before_agent`) flags injection patterns in the trace |
| Input | Email normalization | Lowercased and trimmed before lookup |
| Tool | Time cutoff | `audit_at` comes from the immutable context; every query filters `captured_at ≤ audit_at` |
| Tool | Evidence integrity | `record_resource` rejects evidence that is late, doesn't exist, or doesn't mention the resource |
| Tool | Identity link | `record_resource` rejects a resource with no identifier chain to the subject (user_id, current or old email, hash, phone) |
| Tool | Schema | Pydantic `extra="forbid"`, enums for category, state and system |
| Tool | Read-only SQL | SQLite opened `mode=ro`, SELECT only, row cap |
| Runtime | Budgets | Model-call and tool-call limits, per-case wall-clock timeout |
| Runtime | Resilience | Model retry with backoff, model fallback, tool error → model-visible message |
| Runtime | Context size | Old tool outputs are cleared once the context passes a size threshold |
| Output | Structured verdict | `ToolStrategy(AuditVerdict)` with validation errors fed back to the model |
| Output | Contract | Planner and validator run after the model; at most 2 repair rounds |
| Output | Fail-safe | Deterministic baseline verdict if the agent fails, flagged `fallback=true` in the trace and counted in the report |
| Secrets | No keys in code or logs | `.env` loaded through `pydantic-settings`; the tracer redacts any key-like strings |

## 4. Logging and tracing

- **`TraceMiddleware`** (`wrap_model_call` / `wrap_tool_call`) writes one JSONL event per action to `runs/<run_id>/trace.jsonl`:
  - `case_start` and `case_end`
  - `model_call` (input message count and a hash, output text, any reasoning or thinking content the provider returns, tool calls requested, `usage_metadata`, latency, model name)
  - `tool_call` (name, args, result summary, status, latency)
  - `guardrail` (name, pass/fail, detail)
  - `repair`, `fallback`, `error`
- **Thoughts are made explicit.** Hidden chain of thought is provider-specific, so the agent must put a `rationale` field on every `record_resource` and on the verdict. That reasoning is always captured, whatever the provider. Provider reasoning summaries are captured as well when they exist.
- **Per-case file** `runs/<run_id>/cases/<case_id>.json`: full message transcript, ledger, verdict, planned steps, validator report, tokens, and cost.
- **Usage roll-up** `runs/<run_id>/usage.json`: per model and overall calls, input and output tokens, cost, from `AIMessage.usage_metadata` and the `UsageMetadataCallbackHandler`. It is rendered into `evaluation/usage_report.md`.
- **Trace viewer** `python code/trace_view.py <run_id> <case_id>`: prints a readable turn-by-turn timeline. It is used to put example traces into the evaluation report.
- **Optional LangSmith** if `LANGSMITH_API_KEY` is set in the environment. It's off by default, and the local JSONL trace is the source of truth.
- Python `logging` gives a concise console progress line per case, with full detail at DEBUG.

## 5. Deterministic baseline

The same index and planner, with no LLM. Rules decide each resource's state directly from `state_at_audit`. It serves three purposes:

1. the fallback when the agent fails;
2. a comparison row in the evaluation report (baseline vs agent);
3. a test that the planner and validator are correct on the 32 samples.

## 6. Evaluation

- `code/evaluation/scorer.py` scores any `output.csv` against `sample_output.csv`:
  - case-status accuracy;
  - step precision, recall and F1, keyed by (phase, operation, system, resource);
  - field accuracy per field;
  - sequence accuracy (exact array order);
  - evidence citation accuracy.
- Custom cases live in `code/evaluation/custom_cases/`. Each is a small fixture dataset plus the expected row:
  1. a sync that recreates a deleted record;
  2. a tombstone with a leftover hash;
  3. evidence or activity after `audit_at` that must be ignored;
  4. a NOT_APPLICABLE request reported as COMPLETE;
  5. an old email whose look-alike decoy is also present.
- `scorer.py --write-report` produces `evaluation/evaluation_report.md`: metrics, baseline vs agent, failures found, improvements made.

## 7. Repository layout

```
code/
  main.py                 # CLI entry: build index → run cases → write output.csv + reports
  config.py               # pydantic-settings: provider, models, limits, paths
  requirements.txt        # pinned
  index/                  # loaders/{sql,json,logs,pdf,images}.py, build.py, schema.sql
  agent/                  # tools.py, schemas.py, prompts.md, middleware/{trace,guardrails}.py, build.py
  planning/               # planner.py, validator.py, rules.py
  runner/                 # runner.py, cache.py, baseline.py
  trace_view.py
  evaluation/             # scorer.py, custom_cases/, evaluation_report.md, usage_report.md
  tests/                  # planner/validator/loaders unit tests
  docs/                   # evidence_profile.md, architecture.md
```

## 8. Models and budget

Constraints: about $3.60 of OpenAI credit, plus a Gemini API free tier (per-project RPM/RPD caps, shown in AI Studio). A consumer Gemini subscription does not raise API limits.

| Role | Primary | Price per 1M tokens (in / cached in / out) | Alternative |
|---|---|---|---|
| Development runs (32 samples, many times) | Gemini free-tier Flash or Flash-Lite | free, rate-limited | gpt-6-luna |
| Final 518-case run | `gpt-6-luna` | $0.10 / $0.01 / $0.50 | Gemini free tier, run over 2 days if RPD allows |
| One-time screenshot extraction (11 images) | same as the run model | well under $0.01 total | |

Prices are from developers.openai.com/api/docs/pricing as of 24 Oct 2026.

**Design for few calls per case.** Before the agent starts, deterministic retrieval builds a compact case dossier: the identity, candidate resources with their `state_at_audit`, and the primary-account match. The agent receives it in its first message and calls tools only to check or dig further. Target: 3–5 model calls and about 25k input tokens per case.

**Estimate.** 518 × (25k input at a ~60% cache-hit rate, plus about 1.5k output) comes to roughly $0.6–1.2 for the final run. Each 32-sample development run costs about $0.05.

**Budget guardrail.** A `BudgetMiddleware` keeps a running USD total from `usage_metadata` and a price table. It stops scheduling new cases once spend reaches the cap (`BUDGET_USD`, default 2.50). Cases cut off this way use the deterministic baseline and are flagged.

The provider is switched entirely in `.env` (`LLM_PROVIDER`, `AGENT_MODEL`); the code never changes.

**Update 25 Oct: the plan is now OpenAI directly (no FreeLLMAPI, no free development tier).** `code/llm_config.py` reads `OPENAI_API_KEY` (or `LLM_API_KEY`), optional `LLM_BASE_URL`, `AGENT_MODEL` and `VISION_MODEL`; the default model is `gpt-6-luna`. Costs are in HANDOFF.md section 4. Budget cap and lean prompts matter more than before. The notes below record what was tested against the local router on 24 Oct.

**Local FreeLLMAPI router** (`~/freellmapi`, Docker on Colima, `http://127.0.0.1:3001/v1`). It is OpenAI-compatible and used through `ChatOpenAI(base_url=LLM_BASE_URL, api_key=LLM_API_KEY)`.

Tested 24 Oct 2026 with `code/scripts/check_llm.py`:

| Model | Served via | Tool calling |
|---|---|---|
| `gpt-oss-120b` | groq | PASS (1.2s) |
| `gpt-oss-20b` | groq | PASS (0.9s) |
| `qwen3.8-27b` | groq | PASS (0.4s) |

Everything else failed: the stored Google key returns 402 "prepayment credits depleted", and no provider key is configured for the other models. `auto` also works, but spends about 127s trying dead routes first, so always pin a model.

Groq's free-tier limit per model is 30 RPM, 1K RPD, **8K TPM** and **200K TPD**. That gives about 600K tokens a day across the three models.

Consequences for the design:

- **Every request stays under about 6K tokens.** The system prompt is ≤ 2K, the case dossier ≤ 2.5K, and tool results are trimmed. Old tool results are cleared aggressively (`ClearToolUsesEdit` with a low trigger).
- **Target 1–3 model calls per case, about 6K tokens.** The full run then needs about 3M tokens, which is more than the Groq free tier gives in the time left.
- **Use per provider:**

  | Purpose | Provider and model |
  |---|---|
  | Development and sample runs | FreeLLMAPI → `gpt-oss-120b` (Groq, $0) |
  | Final 518-case run | OpenAI `gpt-6-luna` direct: about 3M input (largely cached) + 0.5M output ≈ **$0.3–0.6** |

  More free capacity could come from adding keys in the FreeLLMAPI dashboard (for example a Google AI Studio key on a project without billing, or other free providers listed at freellmapi.co/models).
- Pin one model per run. Log the served model from `response_metadata` and the `X-Routed-Via` header.

## 9. Environment

The global Python on this machine has a broken LangChain install: `langchain` 1.4.0 fails to import with `langgraph-prebuilt` 1.1.0. The project therefore uses its own venv:

```
python3 -m venv .venv && .venv/bin/pip install -r code/requirements.txt
```

Pins: `langchain==1.4.7`, `langgraph==1.2.17`, `langchain-core==1.7.0`, the chosen provider package, `pymupdf`, `pydantic-settings`, `python-dotenv`, `pytest`.
