from __future__ import annotations

from typing import Any

from category_helper.llm.base import Capabilities, GenerationOptions, LLMProvider, LLMResult
from category_helper.llm.errors import LLMResponseError
from category_helper.llm.providers._shared import import_sdk, translate_error, usage_to_dict
from category_helper.llm.registry import register_provider

# Claude's server-side web search tool. Newer Claude models support the
# dynamic-filtering variant below; older ones need "web_search_20250305".
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search"}

# Extended thinking is on by default on current Claude models, and `max_tokens`
# caps thinking + answer together. Classifying one receipt line needs no
# thinking, so we turn it off and keep the token budget for the JSON. When web
# search is enabled we leave thinking on (tool use is unreliable with thinking
# disabled) and raise the floor so the answer is not truncated by thinking.
MIN_TOKENS_WITH_THINKING = 4096


@register_provider
class AnthropicProvider(LLMProvider):
    """Anthropic Claude via the Messages API."""

    name = "anthropic"
    default_model = "claude-opus-5"
    capabilities = Capabilities(web_search=True, structured_output=True)
    api_key_env = ("ANTHROPIC_API_KEY", "CLAUDE_API_KEY")

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._cached_client: Any = None

    def _client(self) -> Any:
        if self._cached_client is None:
            anthropic = import_sdk("anthropic", "anthropic")
            kwargs: dict[str, Any] = {
                "api_key": self.resolve_api_key(),
                "max_retries": self.settings.max_retries,
            }
            if self.settings.base_url:
                kwargs["base_url"] = self.settings.base_url
            if self.settings.timeout_seconds:
                kwargs["timeout"] = self.settings.timeout_seconds
            self._cached_client = anthropic.Anthropic(**kwargs)
        return self._cached_client

    def generate(self, prompt: str, options: GenerationOptions) -> LLMResult:
        model = self.resolve_model(options)
        schema = self.structured_output_schema(options)
        thinking_enabled = options.use_web_search

        max_tokens = options.max_output_tokens
        if thinking_enabled:
            max_tokens = max(max_tokens, MIN_TOKENS_WITH_THINKING)

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if options.use_web_search:
            kwargs["tools"] = [WEB_SEARCH_TOOL]
        if not thinking_enabled:
            kwargs["thinking"] = {"type": "disabled"}

        output_config: dict[str, Any] = {}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        effort = options.extra.get("effort", "low" if not thinking_enabled else None)
        if effort:
            output_config["effort"] = effort
        if output_config:
            kwargs["output_config"] = output_config

        kwargs.update(
            {k: v for k, v in options.extra.items() if k not in {"effort"}}
        )

        try:
            response = self._client().messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - normalised below
            raise translate_error(exc, self.name) from exc

        usage = usage_to_dict(getattr(response, "usage", None))
        stop_reason = getattr(response, "stop_reason", None)

        # Safety classifiers return HTTP 200 with an empty content list, so this
        # must be checked before touching response.content.
        if stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            reason = getattr(details, "category", None) or "policy refusal"
            return LLMResult(
                text="",
                provider=self.name,
                model=model,
                refused=True,
                refusal_reason=str(reason),
                usage=usage,
                raw=response,
            )

        text = ""
        for block in getattr(response, "content", None) or []:
            if getattr(block, "type", None) == "text":
                text = (block.text or "").strip()
                break

        if stop_reason == "max_tokens":
            raise LLMResponseError(
                f"{self.name} hit max_tokens ({max_tokens}) before finishing; "
                "raise LLM_MAX_OUTPUT_TOKENS.",
                provider=self.name,
            )
        if not text:
            raise LLMResponseError(
                f"Empty response from {self.name} (model={model}, "
                f"stop_reason={stop_reason}).",
                provider=self.name,
            )

        return LLMResult(
            text=text,
            provider=self.name,
            model=model,
            usage=usage,
            raw=response,
        )
