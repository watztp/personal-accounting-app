from __future__ import annotations

from typing import Any

from category_helper.llm.base import Capabilities, GenerationOptions, LLMProvider, LLMResult
from category_helper.llm.errors import LLMResponseError
from category_helper.llm.providers._shared import import_sdk, translate_error, usage_to_dict
from category_helper.llm.registry import register_provider


@register_provider
class OpenAIProvider(LLMProvider):
    """OpenAI via the Responses API."""

    name = "openai"
    default_model = "gpt-4o"
    capabilities = Capabilities(web_search=True, structured_output=True)
    api_key_env = ("OPENAI_API_KEY",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._cached_client: Any = None

    def _client(self) -> Any:
        if self._cached_client is None:
            openai = import_sdk("openai", "openai")
            kwargs: dict[str, Any] = {
                "api_key": self.resolve_api_key(),
                "max_retries": self.settings.max_retries,
            }
            if self.settings.base_url:
                kwargs["base_url"] = self.settings.base_url
            if self.settings.timeout_seconds:
                kwargs["timeout"] = self.settings.timeout_seconds
            self._cached_client = openai.OpenAI(**kwargs)
        return self._cached_client

    def generate(self, prompt: str, options: GenerationOptions) -> LLMResult:
        model = self.resolve_model(options)
        kwargs: dict[str, Any] = {
            "model": model,
            "input": prompt,
            "max_output_tokens": options.max_output_tokens,
        }
        if options.use_web_search:
            kwargs["tools"] = [{"type": "web_search"}]

        schema = self.structured_output_schema(options)
        if schema is not None:
            kwargs["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "item_classification",
                    "schema": schema,
                    "strict": True,
                }
            }
        kwargs.update(options.extra)

        try:
            response = self._client().responses.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise translate_error(exc, self.name) from exc

        text = (getattr(response, "output_text", "") or "").strip()
        if not text:
            raise LLMResponseError(
                f"Empty response from {self.name} (model={model}).", provider=self.name
            )
        return LLMResult(
            text=text,
            provider=self.name,
            model=model,
            usage=usage_to_dict(getattr(response, "usage", None)),
            raw=response,
        )
