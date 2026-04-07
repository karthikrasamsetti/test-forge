"""
LLM factory for test-forge.
Same pattern as langgraph_framework/llm/factory.py.
"""

from functools import lru_cache

from langchain_core.language_models import BaseChatModel

from src.test_forge.config.settings import get_settings

settings = get_settings()


@lru_cache(maxsize=1)
def get_llm() -> BaseChatModel:
    """Returns the configured LLM. Cached — one instance per process."""
    s = get_settings()

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
            model_name="claude-3-5-sonnet-20241022",  # ← model_name not model
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
