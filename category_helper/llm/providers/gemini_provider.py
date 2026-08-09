from __future__ import annotations

from typing import Any

from category_helper.llm.base import Capabilities, GenerationOptions, LLMProvider, LLMResult
from category_helper.llm.errors import LLMResponseError
from category_helper.llm.providers._shared import import_sdk, translate_error, usage_to_dict
from category_helper.llm.registry import register_provider

DEFAULT_THINKING_LEVEL = "MEDIUM"


@register_provider
class GeminiProvider(LLMProvider):
    """Google Gemini via google-genai.

    ``structured_output`` is False on purpose: Gemini's ``response_schema`` does
    not accept the ``anyOf``/null shape our schema uses, so we ask for JSON via
    ``response_mime_type`` and let the prompt carry the schema.
    """

    name = "gemini"
    default_model = "gemini-3-flash-preview"
    capabilities = Capabilities(web_search=False, structured_output=False)
    api_key_env = ("GEMINI_API_KEY", "GOOGLE_API_KEY")

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._cached_client: Any = None

    def _client(self) -> Any:
        if self._cached_client is None:
            genai = import_sdk("google.genai", "google-genai")
            kwargs: dict[str, Any] = {"api_key": self.resolve_api_key()}
            if self.settings.base_url or self.settings.timeout_seconds:
                http_options: dict[str, Any] = {}
                if self.settings.base_url:
                    http_options["base_url"] = self.settings.base_url
                if self.settings.timeout_seconds:
                    # google-genai expects milliseconds here.
                    http_options["timeout"] = int(self.settings.timeout_seconds * 1000)
                kwargs["http_options"] = http_options
            self._cached_client = genai.Client(**kwargs)
        return self._cached_client

    def generate(self, prompt: str, options: GenerationOptions) -> LLMResult:
        model = self.resolve_model(options)
        types = import_sdk("google.genai.types", "google-genai")

        thinking_level = options.extra.get("thinking_level", DEFAULT_THINKING_LEVEL)
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(thinking_level=thinking_level),
            response_mime_type="application/json",
            max_output_tokens=options.max_output_tokens,
        )
        contents = [
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=prompt)],
            )
        ]

        try:
            response = self._client().models.generate_content(
                model=model,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise translate_error(exc, self.name) from exc

        text = (getattr(response, "text", "") or "").strip()
        if not text:
            raise LLMResponseError(
                f"Empty response from {self.name} (model={model}).", provider=self.name
            )
        return LLMResult(
            text=text,
            provider=self.name,
            model=model,
            usage=usage_to_dict(getattr(response, "usage_metadata", None)),
            raw=response,
        )
