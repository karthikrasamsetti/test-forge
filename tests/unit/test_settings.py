"""
Basic settings tests — verifies config loads correctly.
"""

from src.test_forge.config.settings import get_settings


def test_settings_loads():
    """Settings should load without errors."""
    settings = get_settings()
    assert settings is not None


def test_default_framework():
    """Default framework should be playwright."""
    settings = get_settings()
    assert settings.DEFAULT_FRAMEWORK == "playwright"


def test_default_base_url():
    """Default URL should be saucedemo."""
    settings = get_settings()
    assert settings.DEFAULT_BASE_URL == "https://www.saucedemo.com"


def test_default_llm_provider():
    """Default LLM provider should be openai."""
    settings = get_settings()
    assert settings.LLM_PROVIDER == "openai"
