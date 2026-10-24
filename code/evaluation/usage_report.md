# Usage report

Final full-dataset run: 518 requests. Provider: OpenAI (https://api.openai.com/v1). Prices (USD per 1M tokens) used for the estimate: {"gpt-6-luna": {"input": 0.1, "output": 0.5}}. Cached-input discounts are ignored, so the cost is an upper bound.

| Role | Model | Model calls | Input tokens | Output tokens | Est. cost (USD) |
|---|---|---:|---:|---:|---:|
| Investigation agent (per-case) | gpt-6-luna | 1009 | 1,949,140 | 265,639 | 0.3277 |
| Screenshot extraction | gpt-6-luna | 11 | 28,677 | 10,306 | 0.0080 |
| **Overall** | | 1020 | 1,977,817 | 275,945 | 0.3358 |

- Total tokens: 2,253,762; average per request: 4,351 tokens.
- Estimated total cost: $0.3358; per request: $0.00065.
- Cases that fell back to the rule-based baseline (agent error or contract violation): 0.
- The per-case counts are read from the result cache (`cache/agent/<model>_<AGENT_VERSION>/`) that the real model calls of this configuration wrote; rerunning `code/main.py` with that cache present makes no new calls and reproduces the same counts and the same `output.csv`. A cold run (empty cache) makes these calls again.
- Screenshot extraction is cached per image (`code/vision_cache/`), so its counts are those of the extraction that produced the cache used by this run.
