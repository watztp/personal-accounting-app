from __future__ import annotations

import importlib
import os
from typing import Type

from category_helper.llm.base import LLMProvider, ProviderSettings
from category_helper.llm.errors import LLMConfigError

_REGISTRY: dict[str, Type[LLMProvider]] = {}
_BUILTINS_LOADED = False

_ALIASES: dict[str, str] = {
    "gpt": "openai",
    "chatgpt": "openai",
    "claude": "anthropic",
    "google": "gemini",
    "google_gemini": "gemini",
    "azure": "azure_openai",
    "compatible": "openai_compatible",
}


def normalize_provider_name(name: str | None) -> str:
    key = (name or "").strip().lower().replace("-", "_")
    return _ALIASES.get(key, key)


def register_provider(cls: Type[LLMProvider]) -> Type[LLMProvider]:
    """Class decorator: make a provider reachable by name."""
    if not getattr(cls, "name", ""):
        raise LLMConfigError(f"{cls.__name__} must define a non-empty `name`")
    _REGISTRY[cls.name] = cls
    return cls


def _load_builtins() -> None:
    global _BUILTINS_LOADED
    if _BUILTINS_LOADED:
        return
    # Importing the package registers every bundled provider. Adapters import
    # their SDK lazily, so a missing SDK only fails when that provider is used.
    importlib.import_module(".providers", package=__package__)
    _BUILTINS_LOADED = True


def load_provider_from_path(path: str) -> Type[LLMProvider]:
    """Import `pkg.module:ClassName` (or `pkg.module.ClassName`)."""
    spec = path.strip()
    if ":" in spec:
        module_name, _, attr = spec.partition(":")
    else:
        module_name, _, attr = spec.rpartition(".")
    if not module_name or not attr:
        raise LLMConfigError(
            f"Invalid provider path {path!r}; expected 'package.module:ClassName'."
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise LLMConfigError(f"Cannot import {module_name!r}: {exc}") from exc
    cls = getattr(module, attr, None)
    if not isinstance(cls, type) or not issubclass(cls, LLMProvider):
        raise LLMConfigError(f"{path!r} is not an LLMProvider subclass.")
    return cls


def _load_custom(key: str) -> None:
    """Resolve a provider that is not bundled with the package."""
    path = key if ("." in key or ":" in key) else (os.getenv("LLM_CUSTOM_PROVIDER") or "")
    if not path.strip():
        return
    cls = load_provider_from_path(path)
    register_provider(cls)
    _REGISTRY.setdefault(key, cls)


def available_providers() -> list[str]:
    _load_builtins()
    return sorted(_REGISTRY)


def get_provider_class(name: str) -> Type[LLMProvider]:
    key = normalize_provider_name(name)
    if not key:
        raise LLMConfigError("No LLM provider selected; set LLM_PROVIDER.")
    _load_builtins()
    if key not in _REGISTRY:
        _load_custom(key)
    if key not in _REGISTRY:
        raise LLMConfigError(
            f"Unknown LLM provider {name!r}. "
            f"Available: {', '.join(sorted(_REGISTRY))}. "
            "For your own adapter set LLM_PROVIDER=custom and "
            "LLM_CUSTOM_PROVIDER=package.module:ClassName."
        )
    return _REGISTRY[key]


def get_provider(name: str, settings: ProviderSettings | None = None) -> LLMProvider:
    return get_provider_class(name)(settings)