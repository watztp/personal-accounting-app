from __future__ import annotations

from typing import Any

from category_helper.llm.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTemporaryError,
)


def import_sdk(module_name: str, package_hint: str):
    """Import a provider SDK lazily with an actionable error message."""
    try:
        return __import__(module_name, fromlist=["__name__"])
    except ImportError as exc:
        raise LLMConfigError(
            f"Package {package_hint!r} is required for this provider "
            f"(pip install {package_hint})."
        ) from exc


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def translate_error(exc: Exception, provider: str) -> LLMError:
    """Map any SDK exception onto our retryable/fatal taxonomy.

    Deliberately duck-typed instead of importing each SDK's exception classes,
    so a minor SDK upgrade cannot silently turn a rate limit into a fatal error.
    """
    if isinstance(exc, LLMError):
        return exc

    message = f"{type(exc).__name__}: {exc}"
    status = _status_code(exc)

    if status in (401, 403):
        return LLMAuthError(message, provider=provider)
    if status == 429:
        return LLMRateLimitError(message, provider=provider)
    if status is not None and status >= 500:
        return LLMTemporaryError(message, provider=provider)
    if status in (400, 404, 413, 422):
        return LLMBadRequestError(message, provider=provider)

    name = type(exc).__name__.lower()
    if any(token in name for token in ("connection", "timeout", "overload")):
        return LLMTemporaryError(message, provider=provider)
    if "ratelimit" in name or "resourceexhausted" in name:
        return LLMRateLimitError(message, provider=provider)
    if "auth" in name or "permission" in name:
        return LLMAuthError(message, provider=provider)

    return LLMError(message, provider=provider)


def usage_to_dict(usage: Any) -> dict[str, Any] | None:
    if usage is None:
        return None
    for method in ("model_dump", "to_dict", "dict"):
        fn = getattr(usage, method, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001 - usage reporting must never fail a call
                break
    if isinstance(usage, dict):
        return usage
    return {"raw": repr(usage)}