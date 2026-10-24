"""Turn .env / environment variables into chat models. Works with OpenAI directly or any OpenAI-compatible endpoint.

Environment (all optional except a key):
  OPENAI_API_KEY or LLM_API_KEY   API key (LLM_API_KEY wins if both are set)
  LLM_BASE_URL or OPENAI_BASE_URL endpoint, default https://api.openai.com/v1
  AGENT_MODEL                     model for the investigation agent, default DEFAULT_MODEL
  VISION_MODEL                    model for screenshot extraction, default AGENT_MODEL

Secrets are read from the environment or the repo-root .env only; never hardcode or log them.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-6-luna"  # cheapest current OpenAI model on 24 Oct 2026; confirm with scripts/check_llm.py --list


def load_env() -> None:
    env_file = REPO / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def settings() -> dict:
    load_env()
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    agent_model = os.environ.get("AGENT_MODEL", DEFAULT_MODEL)
    return {
        "base_url": os.environ.get("LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or DEFAULT_BASE_URL,
        "api_key": api_key,
        "agent_model": agent_model,
        "vision_model": os.environ.get("VISION_MODEL", agent_model),
    }


def chat_model(model: str | None = None, **kwargs):
    """A LangChain ChatOpenAI client for the configured endpoint. Raises if no API key is configured."""
    from langchain_openai import ChatOpenAI

    cfg = settings()
    if not cfg["api_key"]:
        raise RuntimeError("No API key: set OPENAI_API_KEY (or LLM_API_KEY) in the environment or the repo-root .env")
    kwargs.setdefault("timeout", 120)
    return ChatOpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"], model=model or cfg["agent_model"], **kwargs)
