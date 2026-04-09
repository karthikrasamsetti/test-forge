"""LLM factory for test-forge."""

from __future__ import annotations

import os
from functools import lru_cache

# ── LangSmith must be configured BEFORE any LangChain import ──────────
# LangChain reads LANGCHAIN_* env vars at import time, not at call time.
from test_forge.config.settings import get_settings as _get_settings

_s = _get_settings()
if _s.LANGSMITH_API_KEY:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", _s.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGSMITH_PROJECT", "test-forge")

# ── Now safe to import LangChain ───────────────────────────────────────
from langchain_core.language_models import BaseChatModel  # noqa: E402


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Returns the configured LLM. Cached — one instance per process."""
    s = _get_settings()

    if s.LLM_PROVIDER == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=s.OPENAI_MODEL,
            api_key=s.OPENAI_API_KEY,  # type: ignore[arg-type]
            temperature=0.0,
        )

    elif s.LLM_PROVIDER == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model_name="claude-3-5-sonnet-20241022",
            api_key=s.ANTHROPIC_API_KEY,  # type: ignore[arg-type]
            temperature=0.0,
            timeout=60.0,
            stop=None,
        )

    elif s.LLM_PROVIDER == "bedrock":
        from langchain_aws import ChatBedrockConverse

        return ChatBedrockConverse(
            model=s.BEDROCK_MODEL_ID,
            region_name=s.AWS_REGION,
            temperature=0.0,
        )

    raise ValueError(f"Unknown LLM_PROVIDER: {s.LLM_PROVIDER}")
