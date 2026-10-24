"""Smoke-test an OpenAI-compatible endpoint (OpenAI by default) for tool calling.

Reads OPENAI_API_KEY / LLM_API_KEY, LLM_BASE_URL and AGENT_MODEL from the environment or the repo-root .env
(see code/llm_config.py).
Usage: python code/scripts/check_llm.py [--list] [--model MODEL]
"""

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from langchain_core.tools import tool

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import llm_config  # noqa: E402


def list_models(base_url: str, api_key: str) -> list[str]:
    req = urllib.request.Request(f"{base_url}/models", headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return sorted(m["id"] for m in json.load(resp).get("data", []))


@tool
def get_resource_state(resource_id: str) -> str:
    """Return the recorded state of a resource at the audit cutoff."""
    return json.dumps({"resource_id": resource_id, "state": "PRESENT", "evidence_id": "evidence_54"})


def check_tool_calling(model: str) -> bool:
    llm = llm_config.chat_model(model)
    bound = llm.bind_tools([get_resource_state])
    messages = [("user", "Use the tool to look up account_005, then reply with its state in one word.")]
    started = time.time()
    first = bound.invoke(messages)
    if not first.tool_calls:
        print(f"FAIL: no tool call returned. Text: {first.content!r:.200}")
        return False
    call = first.tool_calls[0]
    print(f"tool call: {call['name']}({call['args']})")
    result = get_resource_state.invoke(call["args"])
    final = bound.invoke(messages + [first, {"role": "tool", "content": result, "tool_call_id": call["id"]}])
    meta = final.response_metadata or {}
    print(f"final answer: {final.content!r:.200}")
    print(f"served model: {meta.get('model_name')}  usage: {final.usage_metadata}  latency: {time.time() - started:.1f}s")
    return "present" in str(final.content).lower()


def main() -> int:
    cfg = llm_config.settings()
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true", help="list models the endpoint exposes")
    parser.add_argument("--model", default=cfg["agent_model"])
    args = parser.parse_args()

    base_url, api_key = cfg["base_url"], cfg["api_key"]
    if not api_key:
        print("No API key: set OPENAI_API_KEY (or LLM_API_KEY) in the environment or the repo-root .env.")
        return 2

    if args.list:
        models = list_models(base_url, api_key)
        print(f"{len(models)} models:")
        print("\n".join(models))
        return 0

    ok = check_tool_calling(args.model)
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
