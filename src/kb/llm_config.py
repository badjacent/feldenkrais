"""LLM configuration and factory.

Contains provider configs (model names, API keys, token limits)
and the get_llm() factory function with kill-switch check.
"""

import os

from llm import LLM

_db = None


def init(db):
    """Initialize with a Database instance (needed for kill-switch check)."""
    global _db
    _db = db


LLM_CONFIGS = {
    "gemini": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 65536,
    },
    "gemini-review": {
        "provider": "gemini",
        "model": "gemini-3-flash-preview",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 65536,
        "thinking_level": "MEDIUM",
    },
    "gemini-2.0-flash": {
        "provider": "gemini",
        "model": "gemini-2.0-flash",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 8192,
    },
    "gemini-3.5-flash": {
        "provider": "gemini",
        "model": "gemini-3.5-flash",
        "api_key_env": "GOOGLE_API_KEY",
        "default_max_tokens": 65536,
    },
    "openai": {
        "provider": "openai",
        "model": "gpt-5",
        "api_key_env": "OPENAI_API_KEY",
        "default_max_tokens": 128000,
    },
    "openai-luna": {
        "provider": "openai",
        "model": "gpt-5.6-luna",
        "api_key_env": "OPENAI_API_KEY",
        "default_max_tokens": 128000,
    },
    "codex-astra": {
        "provider": "codex",
        "model": "gpt-6-astra",
        "default_max_tokens": 65536,
        "reasoning_effort": "low",
    },
    "anthropic": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_max_tokens": 64000,
    },
    "anthropic-sonnet-4.5": {
        "provider": "anthropic",
        "model": "claude-sonnet-4-5-20250929",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_max_tokens": 64000,
    },
    "anthropic-haiku": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5-20251001",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_max_tokens": 64000,
    },
    "zai": {
        "provider": "zai",
        "model": "glm-4.6",
        "api_key_env": "ZAI_API_KEY",
        "default_max_tokens": 32000,
    },
    "zai-air": {
        "provider": "zai",
        "model": "glm-4.5-air",
        "api_key_env": "ZAI_API_KEY",
        "default_max_tokens": 16000,
    },
}

DEFAULT_LLM = "gemini"


def get_llm(provider_key: str | None = None) -> LLM:
    """Get configured LLM instance.

    Args:
        provider_key: A key from LLM_CONFIGS. Defaults to DEFAULT_LLM.
    """
    if not _db:
        raise RuntimeError(
            "LLM calls are disabled (no database connection to check kill switch)."
        )
    settings = _db.get_cache("settings:llm_enabled")
    if not settings or not settings.get("enabled", False):
        raise RuntimeError(
            "LLM calls are disabled. Run /enable-llm to enable."
        )
    key = provider_key or DEFAULT_LLM
    config = LLM_CONFIGS.get(key) or _profile_config(key)
    if not config:
        raise ValueError(f"Unknown LLM provider: {key}. Choose from: {list(LLM_CONFIGS.keys())}")

    api_key_env = config.get("api_key_env")
    api_key = os.environ.get(api_key_env) if api_key_env else None
    if api_key_env and not api_key:
        raise ValueError(f"{api_key_env} environment variable not set")

    return LLM(
        provider=config["provider"],
        model=config["model"],
        api_key=api_key,
        default_max_tokens=config["default_max_tokens"],
        thinking_level=config.get("thinking_level"),
        reasoning_effort=config.get("reasoning_effort"),
        schema_contract=config.get("schema_contract", 2),
    )


# Credentials and output limits per provider, for LLM clients built from an
# analysis profile row rather than an LLM_CONFIGS entry.
_PROVIDER_DEFAULTS = {
    "gemini": {"api_key_env": "GOOGLE_API_KEY", "default_max_tokens": 65536},
    "anthropic": {"api_key_env": "ANTHROPIC_API_KEY", "default_max_tokens": 64000},
    "openai": {"api_key_env": "OPENAI_API_KEY", "default_max_tokens": 128000},
    "codex": {"default_max_tokens": 65536},  # uses the machine's `codex login` session
}


def _profile_config(provider_key: str) -> dict | None:
    """Build an LLM config from the analysis profile with this provider key.

    Analysis steps receive a profile's provider key (for example
    ``c3:astra-medium``). Historical keys such as ``gemini`` and ``codex-astra``
    are in LLM_CONFIGS; newer profiles live only in the analysis_profile table.
    """
    profile = _db.get_profile_by_provider_key(provider_key)
    if not profile:
        return None
    defaults = _PROVIDER_DEFAULTS.get(profile["provider"], {})
    params = profile.get("params") or {}
    return {
        "provider": profile["provider"],
        "model": profile["model"],
        "api_key_env": defaults.get("api_key_env"),
        "default_max_tokens": params.get("max_tokens", defaults.get("default_max_tokens", 16000)),
        "thinking_level": params.get("thinking_level"),
        "reasoning_effort": params.get("reasoning_effort") or params.get("effort"),
        "schema_contract": profile["contract_version"],
    }
