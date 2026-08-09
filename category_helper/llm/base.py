from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar, Mapping, NamedTuple

from category_helper.llm.errors import LLMAuthError, LLMConfigError


class Capabilities(NamedTuple):
    """What a provider can do natively, so callers stop guessing."""

    web_search: bool = False
    structured_output: bool = False


@dataclass(frozen=True)
class ProviderSettings:
    """Client-level settings (used when the SDK client is constructed)."""

    api_key: str | None = None
    base_url: str | None = None
    timeout_seconds: float | None = None
    max_retries: int = 0
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerationOptions:
    """Per-call settings."""

    model: str | None = None
    max_output_tokens: int = 1024
    use_web_search: bool = False
    json_schema: dict[str, Any] | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMResult:
    text: str
    provider: str
    model: str
    refused: bool = False
    refusal_reason: str | None = None
    usage: Mapping[str, Any] | None = None
    raw: Any = None


class LLMProvider(ABC):
    """Every provider returns raw text; parsing stays in the caller."""

    name: ClassVar[str] = ""
    default_model: ClassVar[str | None] = None
    capabilities: ClassVar[Capabilities] = Capabilities()
    # Env vars checked (in order) when settings.api_key is empty.
    api_key_env: ClassVar[tuple[str, ...]] = ()
    # Set to False for providers that talk to a local runtime (e.g. Ollama).
    requires_api_key: ClassVar[bool] = True

    def __init__(self, settings: ProviderSettings | None = None) -> None:
        self.settings = settings or ProviderSettings()

    @abstractmethod
    def generate(self, prompt: str, options: GenerationOptions) -> LLMResult:
        """Send `prompt` and return the model's text response."""

    # -- helpers shared by adapters -------------------------------------

    def resolve_model(self, options: GenerationOptions) -> str:
        model = (options.model or self.default_model or "").strip()
        if not model:
            raise LLMConfigError(
                f"Provider {self.name!r} has no default model; set LLM_MODEL.",
                provider=self.name,
            )
        return model

    def resolve_api_key(self) -> str | None:
        import os

        key = (self.settings.api_key or "").strip()
        if key:
            return key
        for env_name in self.api_key_env:
            value = (os.getenv(env_name) or "").strip()
            if value:
                return value
        if self.requires_api_key:
            hint = " or ".join(self.api_key_env) or "LLM_API_KEY"
            raise LLMAuthError(
                f"Missing API key for provider {self.name!r}; set {hint}.",
                provider=self.name,
            )
        return None

    def structured_output_schema(
        self, options: GenerationOptions
    ) -> dict[str, Any] | None:
        """Schema to enforce at the API level, or None to rely on the prompt."""
        if not self.capabilities.structured_output:
            return None
        # Web search results carry citations, which conflict with schema-
        # constrained output on several providers. Fall back to the prompt.
        if options.use_web_search:
            return None
        return options.json_schema