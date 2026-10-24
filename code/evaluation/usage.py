"""Write evaluation/usage_report.md for the final full run from recorded token counts. Prices in USD per 1M tokens."""

import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PRICES = {"gpt-6-luna": {"input": 0.10, "output": 0.50}}   # developers.openai.com/api/docs/pricing, 24 Oct 2026; cached input ignored (upper bound)


def vision_usage(model: str) -> dict:
    total = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    for f in sorted((REPO / "cache" / "vision").glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("model") == model:
            total["calls"] += 1
            total["input_tokens"] += d.get("usage", {}).get("input_tokens", 0)
            total["output_tokens"] += d.get("usage", {}).get("output_tokens", 0)
    return total


def cost(model: str, u: dict) -> float:
    p = PRICES.get(model, {"input": 0.0, "output": 0.0})
    return (u["input_tokens"] * p["input"] + u["output_tokens"] * p["output"]) / 1e6


def write_usage_report(path: Path, model: str, vision_model: str, agent: dict, n_requests: int) -> None:
    rows = [("Investigation agent (per-case)", model, agent), ("Screenshot extraction", vision_model, vision_usage(vision_model))]
    lines = ["# Usage report", "",
             f"Final full-dataset run: {n_requests} requests. Provider: OpenAI (https://api.openai.com/v1). Prices (USD per 1M tokens) "
             f"used for the estimate: {json.dumps(PRICES)}. Cached-input discounts are ignored, so the cost is an upper bound.", "",
             "| Role | Model | Model calls | Input tokens | Output tokens | Est. cost (USD) |", "|---|---|---:|---:|---:|---:|"]
    tot = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    total_cost = 0.0
    for role, m, u in rows:
        c = cost(m, u)
        total_cost += c
        for k in tot:
            tot[k] += u[k]
        lines.append(f"| {role} | {m} | {u['calls']} | {u['input_tokens']:,} | {u['output_tokens']:,} | {c:.4f} |")
    lines.append(f"| **Overall** | | {tot['calls']} | {tot['input_tokens']:,} | {tot['output_tokens']:,} | {total_cost:.4f} |")
    tokens = tot["input_tokens"] + tot["output_tokens"]
    lines += ["", f"- Total tokens: {tokens:,}; average per request: {tokens / n_requests:,.0f} tokens.",
              f"- Estimated total cost: ${total_cost:.4f}; per request: ${total_cost / n_requests:.5f}.",
              f"- Cases that fell back to the rule-based baseline (agent error or contract violation): {len(agent.get('fallbacks', []))}"
              + (f" ({', '.join(agent['fallbacks'])})" if agent.get("fallbacks") else "") + ".",
              "- Screenshot extraction is cached per image, so its counts are those of the extraction that produced the cache used by this run.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
