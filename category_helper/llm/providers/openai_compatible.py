from __future__ import annotations

import os
from typing import Any

from category_helper.llm.base import Capabilities, GenerationOptions, LLMProvider, LLMResult
from category_helper.llm.errors import LLMConfigError, LLMResponseError
from category_helper.llm.providers._shared import import_sdk, translate_error, usage_to_dict
from category_helper.llm.registry import register_provider

# How to ask for JSON back:
#   "schema" - response_format json_schema (OpenAI, Azure, Fireworks, vLLM, ...)
#   "object" - response_format json_object (widest support; default)
#   "off"    - prompt only (some local runtimes reject response_format entirely)
DEFAULT_JSON_MODE = "object"
_JSON_MODES = {"schema", "object", "off"}


class ChatCompletionsProvider(LLMProvider):
    """Base adapter for anything speaking the OpenAI Chat Completions API."""

    capabilities = Capabilities(web_search=False, structured_output=True)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._cached_client: Any = None

    def _client_kwargs(self) -> dict[str, Any]:
        if not self.settings.base_url:
            raise LLMConfigError(
                f"Provider {self.name!r} needs a base URL; set LLM_BASE_URL "
                "(e.g. https://api.groq.com/openai/v1, "
                "https://api.deepseek.com, http://localhost:11434/v1).",
                provider=self.name,
            )
        kwargs: dict[str, Any] = {
            # Local runtimes accept any non-empty key.
            "api_key": self.resolve_api_key() or "not-needed",
            "base_url": self.settings.base_url,
            "max_retries": self.settings.max_retries,
        }
        if self.settings.timeout_seconds:
            kwargs["timeout"] = self.settings.timeout_seconds
        return kwargs

    def _build_client(self) -> Any:
        openai = import_sdk("openai", "openai")
        return openai.OpenAI(**self._client_kwargs())

    def _client(self) -> Any:
        if self._cached_client is None:
            self._cached_client = self._build_client()
        return self._cached_client

    def _json_mode(self, options: GenerationOptions) -> str:
        mode = str(
            options.extra.get("json_mode")
            or os.getenv("LLM_JSON_MODE")
            or DEFAULT_JSON_MODE
        ).strip().lower()
        if mode not in _JSON_MODES:
            raise LLMConfigError(
                f"LLM_JSON_MODE must be one of {sorted(_JSON_MODES)}, got {mode!r}",
                provider=self.name,
            )
        return mode

    def generate(self, prompt: str, options: GenerationOptions) -> LLMResult:
        model = self.resolve_model(options)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": options.max_output_tokens,
        }

        schema = self.structured_output_schema(options)
        mode = self._json_mode(options)
        if schema is not None and mode == "schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "item_classification",
                    "schema": schema,
                    "strict": True,
                },
            }
        elif mode in {"schema", "object"}:
            kwargs["response_format"] = {"type": "json_object"}

        kwargs.update(
            {k: v for k, v in options.extra.items() if k != "json_mode"}
        )

        try:
            response = self._client().chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise translate_error(exc, self.name) from exc

        usage = usage_to_dict(getattr(response, "usage", None))
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise LLMResponseError(
                f"No choices returned by {self.name} (model={model}).",
                provider=self.name,
            )

        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "content_filter":
            return LLMResult(
                text="",
                provider=self.name,
                model=model,
                refused=True,
                refusal_reason="content_filter",
                usage=usage,
                raw=response,
            )
        if finish_reason == "length":
            raise LLMResponseError(
                f"{self.name} hit max_tokens ({options.max_output_tokens}) before "
                "finishing; raise LLM_MAX_OUTPUT_TOKENS.",
                provider=self.name,
            )

        text = (getattr(choice.message, "content", "") or "").strip()
        if not text:
            raise LLMResponseError(
                f"Empty response from {self.name} (model={model}).", provider=self.name
            )
        return LLMResult(
            text=text,
            provider=self.name,
            model=model,
            usage=usage,
            raw=response,
        )


@register_provider
class OpenAICompatibleProvider(ChatCompletionsProvider):
    """Groq, DeepSeek, xAI, Mistral, Together, Fireworks, OpenRouter, Ollama,
    LM Studio, vLLM - anything exposing /v1/chat/completions."""

    name = "openai_compatible"
    default_model = None  # must be set explicitly; vendors share no default
    api_key_env = ("OPENAI_COMPATIBLE_API_KEY", "OPENAI_API_KEY")
    requires_api_key = False


@register_provider
class AzureOpenAIProvider(ChatCompletionsProvider):
    """Azure OpenAI. `model` is the *deployment* name, not the base model."""

    name = "azure_openai"
    default_model = None
    api_key_env = ("AZURE_OPENAI_API_KEY",)
    default_api_version = "2024-10-21"

    def _build_client(self) -> Any:
        if not self.settings.base_url:
            raise LLMConfigError(
                "Azure OpenAI needs AZURE_OPENAI_ENDPOINT "
                "(e.g. https://<resource>.openai.azure.com).",
                provider=self.name,
            )
        openai = import_sdk("openai", "openai")
        kwargs: dict[str, Any] = {
            "api_key": self.resolve_api_key(),
            "azure_endpoint": self.settings.base_url,
            "api_version": self.settings.extra.get(
                "api_version", self.default_api_version
            ),
            "max_retries": self.settings.max_retries,
        }
        if self.settings.timeout_seconds:
            kwargs["timeout"] = self.settings.timeout_seconds
        return openai.AzureOpenAI(**kwargs)

    def resolve_model(self, options: GenerationOptions) -> str:
        model = (options.model or "").strip()
        if not model:
            raise LLMConfigError(
                "Azure OpenAI needs a deployment name; set "
                "AZURE_OPENAI_DEPLOYMENT (or LLM_MODEL).",
                provider=self.name,
            )
        return model
