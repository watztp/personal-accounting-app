from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import suppress
from dataclasses import replace
from typing import Any

from backend.app.db.db_categories import CategoryIdentity, get_or_create_category
from backend.app.db.db_items import find_uncategorized_items, set_item_category
from backend.app.db.sqlite_db import ensure_schema, get_connection
from category_helper.config import ensure_env_loaded
from category_helper.llm import GenerationOptions, LLMProvider, get_provider, load_llm_config
from category_helper.llm.errors import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMResponseError,
)
from category_helper.utils.category_search_helper import classify_item


logger = logging.getLogger("category_worker")


def _requires_manual_assignment(exc: LLMError) -> bool:
    return not exc.retryable


def _positive_float(name: str, default: float) -> float:
    try:
        return max(0.1, float(os.getenv(name, str(default))))
    except ValueError:
        return default


def _positive_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


class CategoryWatcher:
    """Continuously categorizes items without affecting the web server lifecycle."""

    def __init__(self) -> None:
        ensure_env_loaded()
        self.poll_seconds = _positive_float("CATEGORY_POLL_SECONDS", 2)
        self.retry_seconds = _positive_float("CATEGORY_RETRY_SECONDS", 10)
        self.batch_size = _positive_int("CATEGORY_BATCH_SIZE", 10)
        self.task: asyncio.Task[Any] | None = None
        self.state = "stopped"
        self.assignment_mode = "review_only"
        self.manual_reason: str | None = None
        self.last_error: str | None = None
        self.attempts = 0
        self.processed = 0
        self.last_attempt_at: float | None = None

    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.state = "checking"
        self.assignment_mode = "review_only"
        self.manual_reason = None
        self.last_error = None
        self.task = asyncio.create_task(self._loop(), name="category-watcher")

    async def stop(self) -> None:
        if not self.task:
            self.state = "stopped"
            return
        self.task.cancel()
        with suppress(asyncio.CancelledError):
            await self.task
        self.state = "stopped"

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "assignment_mode": self.assignment_mode,
            "auto_available": self.state not in {"stopped", "checking", "manual"},
            "manual_reason": self.manual_reason,
            "restart_required": self.assignment_mode == "manual",
            "attempts": self.attempts,
            "processed": self.processed,
            "last_error": self.last_error,
            "retry_seconds": self.retry_seconds,
        }

    def manual_assignment_enabled(self) -> bool:
        return self.assignment_mode == "manual"

    async def _loop(self) -> None:
        try:
            await asyncio.to_thread(self._validate_configuration)
            self.state = "idle"
        except asyncio.CancelledError:
            raise
        except LLMError as exc:
            if _requires_manual_assignment(exc):
                self._enter_manual_mode(exc)
                await asyncio.Future()
            else:
                self._enter_retry_mode(exc)
                await asyncio.sleep(self.retry_seconds)

        while True:
            try:
                pending = await asyncio.to_thread(self._pending_count)
                if pending == 0:
                    self.state = "idle"
                    await asyncio.sleep(self.poll_seconds)
                    continue

                self.state = "processing"
                self.attempts += 1
                self.last_attempt_at = time.time()
                count = await asyncio.to_thread(self._process_batch)
                self.processed += count
                self.last_error = None
                self.state = "idle"
                await asyncio.sleep(0.1 if count else self.poll_seconds)
            except asyncio.CancelledError:
                raise
            except LLMError as exc:
                if _requires_manual_assignment(exc):
                    self._enter_manual_mode(exc)
                    await asyncio.Future()
                else:
                    self._enter_retry_mode(exc)
                    await asyncio.sleep(self.retry_seconds)
            except Exception as exc:
                self._enter_retry_mode(exc)
                await asyncio.sleep(self.retry_seconds)

    def _validate_configuration(self) -> None:
        config = load_llm_config()
        provider = get_provider(config.provider, config.settings)
        provider.resolve_model(config.options)
        provider.resolve_api_key()

    def _enter_manual_mode(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self.state = "manual"
        self.assignment_mode = "manual"
        if isinstance(exc, LLMAuthError):
            self.manual_reason = "The category provider credentials are missing or were rejected."
        elif isinstance(exc, LLMConfigError):
            self.manual_reason = "The category provider configuration is missing or invalid."
        elif isinstance(exc, LLMBadRequestError):
            self.manual_reason = "The category provider rejected the configured model or request."
        elif isinstance(exc, LLMResponseError):
            self.manual_reason = "The category provider returned an unusable response."
        else:
            self.manual_reason = "The category provider cannot process automatic assignments."
        logger.error(
            "automatic categorization is unavailable: %s; manual assignment is enabled until server restart",
            self.last_error,
        )

    def _enter_retry_mode(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"
        self.state = "retrying"
        logger.warning(
            "category worker failed: %s; retrying in %.0f seconds",
            self.last_error,
            self.retry_seconds,
        )

    def _pending_count(self) -> int:
        with get_connection() as conn:
            ensure_schema(conn)
            return len(find_uncategorized_items(conn, limit=1))

    def _process_batch(self) -> int:
        config = load_llm_config()
        provider = get_provider(config.provider, config.settings)
        provider.resolve_model(config.options)
        provider.resolve_api_key()
        options = config.options
        if options.use_web_search and not provider.capabilities.web_search:
            logger.warning("category provider %s does not support web search; continuing without it", provider.name)
            options = replace(options, use_web_search=False)

        processed = 0
        with get_connection() as conn:
            ensure_schema(conn)
            for item in find_uncategorized_items(conn, limit=self.batch_size):
                if _categorize_item(conn, item=item, provider=provider, options=options):
                    processed += 1
        return processed


def _categorize_item(
    conn,
    *,
    item: dict[str, Any],
    provider: LLMProvider,
    options: GenerationOptions,
) -> bool:
    result = classify_item(
        item["name"],
        provider=provider,
        options=options,
        store_name=item.get("store_name"),
    )
    category = result.get("category")
    subcategory = result.get("subcategory")
    needs_review = bool(result.get("needs_review", False))
    if not category or not subcategory:
        set_item_category(conn, item_id=item["item_id"], category_id=None, needs_review=True)
        logger.warning(
            "category unresolved for item_id=%s; tagged for manual review",
            item["item_id"],
        )
        return False

    category_row = get_or_create_category(
        conn,
        CategoryIdentity(category_name=str(category), sub_category_name=str(subcategory)),
    )
    updated = set_item_category(
        conn,
        item_id=item["item_id"],
        category_id=category_row["category_id"],
        needs_review=needs_review,
    )
    if updated:
        logger.info(
            "categorized item_id=%s -> %s / %s",
            item["item_id"],
            category,
            subcategory,
        )
    return updated
