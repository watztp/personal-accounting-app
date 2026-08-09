from __future__ import annotations


class LLMError(Exception):
    """Base error raised by every provider adapter in this package."""

    # Whether the worker should back off and try the same item again later.
    retryable: bool = False

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class LLMConfigError(LLMError):
    """Missing or invalid configuration (unknown provider, no model, ...)."""


class LLMAuthError(LLMError):
    """Missing or rejected credentials."""


class LLMBadRequestError(LLMError):
    """The provider rejected the request payload."""


class LLMRateLimitError(LLMError):
    """Quota exhausted for now."""

    retryable = True


class LLMTemporaryError(LLMError):
    """Connection error, timeout, 5xx or 'overloaded'."""

    retryable = True


class LLMResponseError(LLMError):
    """The provider answered but the payload is unusable."""


# Errors that will never fix themselves by retrying the same item: the worker
# should log loudly and stop instead of spinning on every row in the queue.
FATAL_ERRORS = (LLMConfigError, LLMAuthError)
