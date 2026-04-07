"""
Configuration — all settings via environment variables.
Same pattern as langgraph_framework.
"""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── LLM ───────────────────────────────────────────────────────────────────
    LLM_PROVIDER: Literal["openai", "anthropic", "bedrock"] = "openai"
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o"
    ANTHROPIC_API_KEY: str = ""

    # ── AWS Bedrock ───────────────────────────────────────────────────────────
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"

    # ── Crawler ───────────────────────────────────────────────────────────────
    CRAWLER_HEADLESS: bool = True
    CRAWLER_TIMEOUT_MS: int = 30000
    CRAWLER_MAX_ELEMENTS: int = 200

    # ── Generator ─────────────────────────────────────────────────────────────
    DEFAULT_FRAMEWORK: Literal["playwright", "selenium", "pytest_api"] = "playwright"
    OUTPUT_DIR: str = "./outputs"

    # ── API ───────────────────────────────────────────────────────────────────
    API_HOST: str = "0.0.0.0"  # nosec B104 — intentional server binding address
    API_PORT: int = 8000
    API_KEY: str = ""

    # ── Observability ─────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "test-forge"

    # ── Target application ────────────────────────────────────────────────────
    DEFAULT_BASE_URL: str = "https://www.saucedemo.com"
    APP_USERNAME: str = "standard_user"
    APP_PASSWORD: str = "secret_sauce"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    return Settings()
