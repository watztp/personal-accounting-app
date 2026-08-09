from __future__ import annotations

import asyncio
import mimetypes
import os
import secrets
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

from backend.app.db.sqlite_db import ensure_schema as ensure_accounting_schema
from backend.app.engine.db_adding import add_receipt_to_db
from backend.app.engine.receipt_processing import process_receipt_upload
from category_helper.worker import CategoryWatcher
from orchestra.app.worker_db import (
    clear_image_path,
    claim_review_for_finalization,
    create_duty_if_under_in_process_limit,
    ensure_schema as ensure_worker_schema,
    get_connection,
    get_duty_by_id,
    get_duty_by_token,
    list_cleanup_targets,
    lock_duty_for_worker,
    mark_review_timeouts,
    recover_pending_duties,
    restore_review_after_finalize_error,
    update_duty_failed,
    update_duty_finalized,
    update_duty_ready_for_review,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = Path(os.getenv("RECEIPT_UPLOAD_DIR", PROJECT_ROOT / "data" / "uploads")).resolve()
WORKER_COUNT = max(1, int(os.getenv("RECEIPT_WORKER_COUNT", "2")))
IN_PROCESS_LIMIT = max(1, int(os.getenv("RECEIPT_IN_PROCESS_LIMIT", "5")))
HOUSEKEEPER_INTERVAL = max(5, int(os.getenv("HOUSEKEEPER_INTERVAL_SEC", "60")))


class ApplicationOrchestrator:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self.tasks: list[asyncio.Task[Any]] = []
        self.housekeeper: asyncio.Task[Any] | None = None
        self.category_watcher = CategoryWatcher()

    async def start(self) -> None:
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        with get_connection() as conn:
            ensure_accounting_schema(conn)
            ensure_worker_schema(conn)
            pending = recover_pending_duties(conn)
        for duty in pending:
            await self.queue.put(str(duty["worker_id"]))
        self.tasks = [asyncio.create_task(self._worker(f"receipt-worker-{i + 1}")) for i in range(WORKER_COUNT)]
        self.housekeeper = asyncio.create_task(self._housekeeper())
        await self.category_watcher.start()

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        if self.housekeeper:
            self.housekeeper.cancel()
        await self.category_watcher.stop()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.housekeeper:
            with suppress(asyncio.CancelledError):
                await self.housekeeper

    async def submit(self, *, user_id: int, data: bytes, filename: str | None, content_type: str | None) -> dict[str, Any]:
        token = secrets.token_urlsafe(18)
        worker_id = str(uuid.uuid4())
        suffix = Path(filename or "").suffix.lower() or mimetypes.guess_extension(content_type or "") or ".bin"
        path = UPLOAD_DIR / f"{token}{suffix}"
        await asyncio.to_thread(path.write_bytes, data)
        try:
            with get_connection() as conn:
                duty, current = create_duty_if_under_in_process_limit(
                    conn,
                    worker_id=worker_id,
                    worker_encrypt_id=token,
                    user_id=user_id,
                    image_path=str(path),
                    in_process_limit=IN_PROCESS_LIMIT,
                )
            if duty is None:
                self._delete_upload(path)
                raise RuntimeError(f"at most {IN_PROCESS_LIMIT} active receipts are allowed per user (currently {current})")
        except Exception:
            self._delete_upload(path)
            raise
        await self.queue.put(worker_id)
        return {"worker_encrypt_id": token, "status": "queued", "user_id": user_id}

    def status(self, *, token: str, user_id: int) -> dict[str, Any] | None:
        duty = self._owned_duty(token, user_id)
        if duty is None:
            return None
        result = {
            "worker_encrypt_id": token,
            "status": duty["status"],
            "step": duty["step"],
            "last_error": duty.get("last_error"),
        }
        if duty["status"] == "ready_for_review":
            result["review_url"] = f"/review.html?token={token}"
        return result

    def review_data(self, *, token: str, user_id: int) -> dict[str, Any] | None:
        duty = self._owned_duty(token, user_id)
        if duty is None:
            return None
        return {
            "worker_encrypt_id": token,
            "status": duty["status"],
            "review_payload": (duty.get("draft_result") or {}).get("review_payload"),
            "backend": duty.get("draft_result"),
        }

    async def complete(
        self, *, token: str, user_id: int, reviewed_payload: dict[str, Any], create_item_rows: bool = True
    ) -> dict[str, Any] | None:
        duty = self._owned_duty(token, user_id)
        if duty is None or duty["status"] != "ready_for_review":
            return None
        with get_connection() as conn:
            claimed = claim_review_for_finalization(
                conn, worker_id=duty["worker_id"], lock_owner=f"finalize-user-{user_id}"
            )
        if not claimed:
            return None
        try:
            db_result = await asyncio.to_thread(
                add_receipt_to_db,
                reviewed_payload,
                user_id=user_id,
                create_item_rows=create_item_rows,
                match_context=(duty.get("draft_result") or {}).get("match_context"),
            )
        except Exception as exc:
            with get_connection() as conn:
                restore_review_after_finalize_error(conn, worker_id=duty["worker_id"], error=str(exc))
            raise
        with get_connection() as conn:
            update_duty_finalized(conn, worker_id=duty["worker_id"], final_result=db_result)
        self._delete_upload(Path(duty["image_path"]))
        with get_connection() as conn:
            clear_image_path(conn, worker_id=duty["worker_id"])
        return db_result

    def cancel(self, *, token: str, user_id: int, reason: str) -> bool:
        duty = self._owned_duty(token, user_id)
        if duty is None or duty["status"] not in {"queued", "running", "ready_for_review"}:
            return False
        with get_connection() as conn:
            update_duty_failed(conn, worker_id=duty["worker_id"], error=reason)
        self._delete_upload(Path(duty["image_path"]))
        with get_connection() as conn:
            clear_image_path(conn, worker_id=duty["worker_id"])
        return True

    def image_path(self, *, token: str, user_id: int) -> Path | None:
        duty = self._owned_duty(token, user_id)
        if not duty or not duty.get("image_path"):
            return None
        path = Path(duty["image_path"]).resolve()
        return path if path.is_file() and path.is_relative_to(UPLOAD_DIR) else None

    def _owned_duty(self, token: str, user_id: int) -> dict[str, Any] | None:
        with get_connection() as conn:
            duty = get_duty_by_token(conn, worker_encrypt_id=token)
        return duty if duty and int(duty.get("user_id") or 0) == user_id else None

    async def _worker(self, worker_name: str) -> None:
        while True:
            worker_id = await self.queue.get()
            try:
                with get_connection() as conn:
                    locked = lock_duty_for_worker(conn, worker_id=worker_id, lock_owner=worker_name)
                    duty = get_duty_by_id(conn, worker_id=worker_id) if locked else None
                if not duty:
                    continue
                image_bytes = await asyncio.to_thread(Path(duty["image_path"]).read_bytes)
                result = await process_receipt_upload(image_bytes, user_id=str(duty["user_id"]))
                if not result.get("review_payload"):
                    raise ValueError("receipt model output could not be converted into a review payload")
                with get_connection() as conn:
                    update_duty_ready_for_review(conn, worker_id=worker_id, draft_result=result)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                with get_connection() as conn:
                    update_duty_failed(conn, worker_id=worker_id, error=str(exc))
            finally:
                self.queue.task_done()

    async def _housekeeper(self) -> None:
        while True:
            await asyncio.sleep(HOUSEKEEPER_INTERVAL)
            with get_connection() as conn:
                timed_out = mark_review_timeouts(conn)
                targets = list_cleanup_targets(conn)
            for duty in [*timed_out, *targets]:
                self._delete_upload(Path(duty.get("image_path") or ""))
                with get_connection() as conn:
                    clear_image_path(conn, worker_id=duty["worker_id"])

    @staticmethod
    def _delete_upload(path: Path) -> None:
        try:
            resolved = path.resolve()
            if resolved.is_file() and resolved.is_relative_to(UPLOAD_DIR):
                resolved.unlink()
        except (OSError, ValueError):
            pass
