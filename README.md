# Ghost Data Still Exists: deletion-audit agent

For every request in `dataset/deletion_requests.csv` the system decides whether deletion was really requested, reconstructs
the state of the person's data at `audit_at` from the supplied evidence, and writes `output.csv` (`case_id,deletion_status,steps_json`)
with findings, an ordered remediation plan and verification checks. Design: [`code/docs/architecture.md`](code/docs/architecture.md).
Results and limitations: [`code/evaluation/evaluation_report.md`](code/evaluation/evaluation_report.md). Token use and cost of the final run:
[`code/evaluation/usage_report.md`](code/evaluation/usage_report.md).

## How it works (short)
1. **Index** all evidence files (SQL snapshots, JSON, logs, PDFs, screenshots) into SQLite, keyed by identifiers and resource IDs.
2. **Dossier** per case: resolve the request email (current or historical, case-insensitive), find the resources evidence ties to the person, and read their state at `audit_at` (later evidence is never used or cited).
3. **LLM agent** (LangChain `create_agent`, model `gpt-6-luna`) decides whether deletion is requested and the state of each resource, with read-only tools over the index and the activity log. Code guards its output.
4. **Planner and validator** (pure code) turn the decided facts and `deletion_rules.csv` into ordered, numbered steps. The model never writes steps.
5. Any error falls back to a deterministic rule-based baseline for that case.

Only files under `dataset/` are used. Nothing in the code reads organizer-only files or hard-codes answers.

## Setup
Python 3.13 (developed and tested on 3.13.17).

```bash
python3 -m venv .venv
.venv/bin/pip install -r code/requirements.txt
```

## Run
From the repository root (`dataset/` must be next to `code/`):

```bash
export OPENAI_API_KEY=...            # or put it in an uncommitted .env at the repo root; LLM_API_KEY also works
.venv/bin/python code/main.py        # builds the index, runs all 518 cases, writes output.csv and code/evaluation/usage_report.md
```

- Optional environment: `AGENT_MODEL` (default `gpt-6-luna`), `VISION_MODEL` (default = `AGENT_MODEL`), `LLM_BASE_URL` (default `https://api.openai.com/v1`).
- A cold agent run makes about 1,000 model calls (about 2M input and 0.27M output tokens, about $0.34 at `gpt-6-luna` prices) and takes several minutes. Per-case results are cached under `cache/agent/`, so a rerun is free and repeatable.
- **No API key:** `code/main.py` prints a warning and uses the rule-based baseline (no model calls). The screenshot transcriptions are shipped in `code/vision_cache/`, so this still produces a complete `output.csv`. Force it with `--engine baseline`.
- `--split sample` runs the 32 public samples instead (output in `runs/main_sample.csv`).
- The final file is validated on write (518 rows, sorted, valid JSON, contract rules).

## Evaluate
```bash
.venv/bin/python code/runner/baseline.py --split sample                       # runs/baseline_sample.csv
.venv/bin/python code/agent/run.py --split sample --out runs/agent_sample.csv  # the agent engine on the samples
.venv/bin/python code/evaluation/scorer.py runs/agent_sample.csv --verbose     # status / step / field / sequence accuracy
.venv/bin/python code/evaluation/run_custom.py --engine agent                  # the 7 custom cases with hand-written gold
.venv/bin/python code/evaluation/link_audit.py --agent                         # label-free audit of primary-account linking
.venv/bin/python code/evaluation/sync_audit.py                                 # SYNC_RECORD exposure audit
.venv/bin/python -m pytest code/tests                                          # unit and pipeline tests
.venv/bin/python code/evaluation/make_report.py                                # regenerates evaluation_report.md
```

## Layout
```
code/main.py            entry point
code/index/             evidence index, log parsers, screenshot transcription (vision.py)
code/runner/            identity resolution, dossier, linking, baseline
code/agent/             LLM investigator (agent.py), account linker (link_agent.py), runner with cache and fallback (run.py)
code/planning/          planner and validator
code/evaluation/        scorer, custom cases, audits, report generators, evaluation_report.md, usage_report.md
code/tests/             pytest suite (runs on the generated custom fixtures, not on dataset/)
code/vision_cache/      transcriptions of the 11 evidence screenshots (so no vision call is needed)
code/docs/              architecture and evidence profile
```
Prompts are in `code/agent/agent.py` (`SYSTEM_PROMPT`), `code/agent/link_agent.py` (`SYSTEM`) and `code/index/vision.py` (`PROMPT`).

## Known limitations
- The rules were derived from the 32 public samples, so sample accuracy is in-sample; there is no gold for the 518 evaluation cases.
- About 105 primary-account links are tie-breaks the data cannot resolve (see the link audit).
- The ten screenshot-family cases assume each contains only its linked resource.
- `SYNC_RECORD` events are not interpreted; measured exposure on the supplied data is nil.

Secrets are read from the environment only; no key or credential is stored in the repository.
