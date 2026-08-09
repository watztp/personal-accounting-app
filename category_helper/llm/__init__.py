"""Provider-agnostic LLM access for the category worker.

Add a provider by dropping a module into ``llm/providers/`` and decorating the
class with ``@register_provider`` (see ``llm/providers/_template.py``), or point
``LLM_CUSTOM_PROVIDER`` at ``package.module:ClassName`` without touching this
package at all.
"""

from __future__ import annotations

from category_helper.llm.base import (
    Capabilities,
    GenerationOptions,
    LLMProvider,
    LLMResult,
    ProviderSettings,
)
from category_helper.llm.errors import (
    FATAL_ERRORS,
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    LLMTemporaryError,
)
from category_helper.llm.registry import (
    available_providers,
    get_provider,
    get_provider_class,
    load_provider_from_path,
    normalize_provider_name,
    register_provider,
)
from category_helper.llm.settings import LLMConfig, load_llm_config

__all__ = [
    "Capabilities",
    "FATAL_ERRORS",
    "GenerationOptions",
    "LLMAuthError",
    "LLMBadRequestError",
    "LLMConfig",
    "LLMConfigError",
    "LLMError",
    "LLMProvider",
    "LLMRateLimitError",
    "LLMResponseError",
    "LLMResult",
    "LLMTemporaryError",
    "ProviderSettings",
    "available_providers",
    "get_provider",
    "get_provider_class",
    "load_llm_config",
    "load_provider_from_path",
    "normalize_provider_name",
    "register_provider",
]