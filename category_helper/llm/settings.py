from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from category_helper.config import ensure_env_loaded
from category_helper.llm.base import GenerationOptions, ProviderSettings
from category_helper.llm.errors import LLMConfigError
from category_helper.llm.registry import normalize_provider_name

DEFAULT_PROVIDER = "openai"
DEFAULT_MAX_OUTPUT_TOKENS = 1024

# Per-provider env fallbacks. `LLM_*` always wins; the legacy names are kept so
# existing .env files keep working after the refactor.
_API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY"),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openai_compatible": ("OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY"),
    "azure_openai": ("AZURE_OPENAI_API_KEY",),
}
_MODEL_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_MODEL",),
    "anthropic": ("ANTHROPIC_MODEL",),
    "gemini": ("GEMINI_MODEL",),
    "openai_compatible": ("OPENAI_COMPATIBLE_MODEL",),
    "azure_openai": ("AZURE_OPENAI_DEPLOYMENT",),
}
_BASE_URL_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_BASE_URL",),
    "openai_compatible": ("OPENAI_COMPATIBLE_BASE_URL",),
    "azure_openai": ("AZURE_OPENAI_ENDPOINT",),
}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    settings: ProviderSettings
    options: GenerationOptions


def _env_str(*names: str) -> str | None:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _env_int(*names: str, default: int, minimum: int = 1) -> int:
    raw = _env_str(*names)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError as exc:
        raise LLMConfigError(f"{names[0]} must be an integer, got {raw!r}") from exc


def _env_float(*names: str) -> float | None:
    raw = _env_str(*names)
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{names[0]} must be a number, got {raw!r}") from exc


def _env_bool(*names: str, default: bool = False) -> bool:
    raw = _env_str(*names)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "y", "on"}


def _env_json(name: str) -> dict[str, Any]:
    raw = _env_str(name)
    if raw is None:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise LLMConfigError(f"{name} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise LLMConfigError(f"{name} must be a JSON object")
    return parsed


def load_llm_config() -> LLMConfig:
    """Read the whole LLM configuration from the environment, once."""
    ensure_env_loaded()

    provider = normalize_provider_name(
        _env_str("LLM_PROVIDER") or DEFAULT_PROVIDER
    )

    extra = _env_json("LLM_EXTRA_JSON")
    if provider == "azure_openai":
        api_version = _env_str("AZURE_OPENAI_API_VERSION")
        if api_version:
            extra = {**extra, "api_version": api_version}

    settings = ProviderSettings(
        api_key=_env_str("LLM_API_KEY", *_API_KEY_ENV.get(provider, ())),
        base_url=_env_str("LLM_BASE_URL", *_BASE_URL_ENV.get(provider, ())),
        timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS"),
        # The worker already retries with its own backoff, so the SDK should not
        # retry underneath the SIGALRM task timeout.
        max_retries=_env_int("LLM_MAX_RETRIES", default=0, minimum=0),
        extra=extra,
    )

    options = GenerationOptions(
        model=_env_str("LLM_MODEL", *_MODEL_ENV.get(provider, ())),
        max_output_tokens=_env_int(
            "LLM_MAX_OUTPUT_TOKENS",
            "OPENAI_MAX_OUTPUT_TOKENS",
            default=DEFAULT_MAX_OUTPUT_TOKENS,
        ),
        use_web_search=_env_bool("LLM_USE_WEB_SEARCH", "OPENAI_USE_WEB_SEARCH"),
        extra=_env_json("LLM_OPTIONS_JSON"),
    )

    return LLMConfig(provider=provider, settings=settings, options=options)