"""Copy this file to write your own provider.

Two ways to plug it in:

1. Keep it in this package
       cp _template.py my_provider.py      # edit it
       # then add `my_provider` to the import list in llm/providers/__init__.py
       LLM_PROVIDER=my_provider

2. Keep it anywhere on the Python path - no change to this repo
       LLM_PROVIDER=custom
       LLM_CUSTOM_PROVIDER=my_package.my_module:MyProvider

The only contract is: `generate()` returns the model's raw text. Parsing and
validation stay in utils/category_search_helper.py, so every provider shares
one prompt and one parser.
"""

from __future__ import annotations

from typing import Any

from category_helper.llm.base import Capabilities, GenerationOptions, LLMProvider, LLMResult
from category_helper.llm.errors import LLMResponseError
from category_helper.llm.providers._shared import translate_error
from category_helper.llm.registry import register_provider


@register_provider
class MyProvider(LLMProvider):
    # Value used for LLM_PROVIDER.
    name = "my_provider"
    # Used when LLM_MODEL is empty; None forces the operator to set one.
    default_model = "my-model-v1"
    # Declare only what you actually implement below. When structured_output is
    # True, `self.structured_output_schema(options)` hands you the JSON schema
    # to enforce at the API level (and returns None when web search is on).
    capabilities = Capabilities(web_search=False, structured_output=False)
    # Env vars checked when LLM_API_KEY is empty.
    api_key_env = ("MY_PROVIDER_API_KEY",)
    # Set False for local runtimes that need no credentials.
    requires_api_key = True

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._cached_client: Any = None

    def _client(self) -> Any:
        if self._cached_client is None:
            # Import the SDK here, not at module import time, so this provider
            # stays optional for everyone who does not use it.
            import httpx

            self._cached_client = httpx.Client(
                base_url=self.settings.base_url or "https://api.example.com",
                headers={"Authorization": f"Bearer {self.resolve_api_key()}"},
                timeout=self.settings.timeout_seconds or 60.0,
            )
        return self._cached_client

    def generate(self, prompt: str, options: GenerationOptions) -> LLMResult:
        model = self.resolve_model(options)
        try:
            response = self._client().post(
                "/v1/generate",
                json={
                    "model": model,
                    "prompt": prompt,
                    "max_tokens": options.max_output_tokens,
                },
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            # Maps HTTP status codes onto retryable / fatal error classes so the
            # worker can tell "try again later" from "stop, config is broken".
            raise translate_error(exc, self.name) from exc

        text = (payload.get("output") or "").strip()
        if not text:
            raise LLMResponseError(
                f"Empty response from {self.name} (model={model}).", provider=self.name
            )
        return LLMResult(text=text, provider=self.name, model=model, raw=payload)
