# Architecture (as built)

An evidence-first deletion auditor. Deterministic code builds an index of all evidence and a per-case dossier; an LLM
agent decides intent and the state of each resource; deterministic code turns the decided facts into the ordered
`steps_json`. The model never writes, orders or numbers steps.

```
dataset/ ──► index/build.py ──► build/index.sqlite   (observations, identity keys, activity, coverage)
                                      │
 deletion_requests.csv ─► runner/investigate.py ─► Dossier (identity, candidate resources + raw observations at audit_at)
                                      │                  ▲ runner/linking.py   links activity-only primary accounts / analytics
                                      ▼
                           agent/agent.py  (LangChain create_agent, tools get_observations + get_activity)
                                      │  Verdict {authorizes_deletion, resources[category, observed_state, evidence_id, rationale]}
                                      ▼
                           merge_verdict  (guards) ─► planning/planner.py ─► planning/validator.py ─► output.csv
                                      └── any error or contract violation ─► runner/baseline.py row (no LLM)
```

## 1. Evidence index (`code/index`)
- Every evidence file (SQL snapshots with replicas, JSON bundles and ledgers, six log formats, text, PDFs, screenshots) is
  normalized into observations: resource, system, record type, presence, PII exposure, flow, restore, retention, file,
  capture time, and identifier keys (emails, user refs, hashes, phones).
- Identity resolution (`store.resolve_identity`) matches the request email, current or historical, case-insensitively and as
  a whole token; look-alike emails belong to other people and are excluded when evidence ties a resource to someone else.
- `state_at(resource, audit_at)` only uses files captured at or before `audit_at`; a file captured later is never citable.
- Screenshots are transcribed once by a vision model (`vision.py`), grounded against known identities (a phone digit was
  once misread), and cached in `code/vision_cache/`. Each `LINK_RESOURCE_IDENTITY` activity event is paired with the file
  captured 7 minutes earlier and only the linked resource is observed. Export expiry and backup retention end are read from
  the screens and applied at `audit_at`.

## 2. Dossier and linking (`code/runner`)
- Candidates are resources the evidence ties to the subject; a candidate whose latest sighting predates the request is dropped.
- A deleted primary account leaves no record, and anonymized analytics carry no identifiers. They are linked through the
  deletion worker's activity trail (timing window, result consistency, exclusivity, chained spacing). This is the least
  certain part of the system; see `evaluation_report.md` (link audit). An opt-in agent linker exists (`--link-engine agent`).

## 3. Agent (`code/agent`)
- `create_agent` with two read-only tools (observations of a resource, activity events of a resource, both cut at `audit_at`),
  structured output (`Verdict`), one case per run. Prompts are in `agent.py` (`SYSTEM_PROMPT`) and `link_agent.py` (`SYSTEM`).
- Guards in code (`merge_verdict`): the agent's category and state win; a dossier candidate the agent omits keeps the
  dossier's reading; resources the dossier never offered are ignored; a citation must be a file captured at or before
  `audit_at`, otherwise the dossier's citation is used. Every change versus the dossier is logged as an override.
- Results are cached per case in `cache/agent/<model>_<AGENT_VERSION>/` (stores verdict, overrides, token usage). This is
  the per-case trace; bump `AGENT_VERSION` when the prompt, tools or merge logic change.
- Failures (API error, recursion limit, validator errors) fall back to the rule-based baseline for that case.

## 4. Planner and validator (`code/planning`)
- `planner.plan`: pure function from `RecordedResource` (category, observed state, evidence) and `deletion_rules.csv` actions
  to steps: findings, then remediation (disable flows and block restores before changing records), then verification, each
  sorted by operation order, system and resource, numbered from 1. Only findings (and every verification of a `COMPLETE`
  case) carry an `evidence_id`.
- `validator`: checks the contract (fields, phase/state per operation, ordering, numbering, evidence rules, no evidence after `audit_at`).

## 5. Baseline (`code/runner/baseline.py`)
Same dossier and planner without the model. It is the fallback, the comparison row in the evaluation, and what `main.py`
uses when no API key is set.

## 6. What was deliberately not built
- No per-turn tracing middleware, budget middleware or model fallback chain: the run costs about $0.34 in total, so the
  per-case result cache and the usage report were judged sufficient.
- `SYNC_RECORD` events are not interpreted (measured exposure: none on the supplied data, `evaluation/sync_audit.py`).
- No retrieval over free text: evidence is indexed by identifiers and resource IDs, not by embeddings.

## 7. Models
OpenAI `gpt-6-luna` for the investigation agent and for screenshot transcription (configurable with `AGENT_MODEL` and
`VISION_MODEL`). The provider, key and base URL come from environment variables only (`code/llm_config.py`).
