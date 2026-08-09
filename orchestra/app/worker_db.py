from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.app.db.sqlite_db import SQLiteConnection, get_connection as connect_sqlite

BASE_DIR = Path(__file__).resolve().parents[1]
SCHEMA_PATH = BASE_DIR / "orchestra_db.sql"


def get_connection() -> SQLiteConnection:
    return connect_sqlite()
def ensure_schema(conn: SQLiteConnection) -> None:
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.commit()


def create_duty(
    conn: SQLiteConnection,
    *,
    worker_id: str,
    worker_encrypt_id: str,
    user_id: int | None = None,
    image_path: str,
    status: str = "queued",
    step: str = "infer",
) -> dict[str, Any]:
    row = conn.execute(
        """
        INSERT INTO worker_duty (
            worker_id,
            worker_encrypt_id,
            user_id,
            status,
            step,
            image_path
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (worker_id, worker_encrypt_id, user_id, status, step, image_path),
    ).fetchone()
    conn.commit()
    return dict(row)


def create_duty_if_under_in_process_limit(
    conn: SQLiteConnection,
    *,
    worker_id: str,
    worker_encrypt_id: str,
    user_id: int | None,
    image_path: str,
    status: str = "queued",
    step: str = "infer",
    in_process_limit: int = 5,
) -> tuple[dict[str, Any] | None, int]:
    if user_id is None:
        created = create_duty(
            conn,
            worker_id=worker_id,
            worker_encrypt_id=worker_encrypt_id,
            user_id=user_id,
            image_path=image_path,
            status=status,
            step=step,
        )
        return created, 0

    count_row = conn.execute(
        """
        SELECT COUNT(*) AS in_process_count
        FROM worker_duty
        WHERE user_id = %s
          AND status IN ('queued', 'running', 'ready_for_review')
        """,
        (user_id,),
    ).fetchone()
    in_process_count = int(count_row["in_process_count"]) if count_row else 0

    if in_process_count >= in_process_limit:
        conn.rollback()
        return None, in_process_count

    row = conn.execute(
        """
        INSERT INTO worker_duty (
            worker_id,
            worker_encrypt_id,
            user_id,
            status,
            step,
            image_path
        )
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (worker_id, worker_encrypt_id, user_id, status, step, image_path),
    ).fetchone()
    conn.commit()
    return dict(row), in_process_count


def lock_duty_for_worker(
    conn: SQLiteConnection,
    *,
    worker_id: str,
    lock_owner: str,
) -> bool:
    row = conn.execute(
        """
        UPDATE worker_duty
        SET status = 'running',
            locked_by = %s,
            locked_at = now(),
            updated_at = now()
        WHERE worker_id = %s
          AND status = 'queued'
          AND locked_by IS NULL
        RETURNING worker_id
        """,
        (lock_owner, worker_id),
    ).fetchone()
    conn.commit()
    return row is not None


def release_duty_lock(conn: SQLiteConnection, *, worker_id: str) -> None:
    conn.execute(
        """
        UPDATE worker_duty
        SET locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE worker_id = %s
        """,
        (worker_id,),
    )
    conn.commit()


def get_duty_by_token(conn: SQLiteConnection, *, worker_encrypt_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT *
        FROM worker_duty
        WHERE worker_encrypt_id = %s
        """,
        (worker_encrypt_id,),
    ).fetchone()
    if not row:
        return None
    return _decode_duty(row)


def get_duty_by_id(conn: SQLiteConnection, *, worker_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM worker_duty WHERE worker_id = %s",
        (worker_id,),
    ).fetchone()
    return _decode_duty(row) if row else None


def recover_pending_duties(conn: SQLiteConnection) -> list[dict[str, Any]]:
    conn.execute(
        """
        UPDATE worker_duty
        SET status = 'ready_for_review',
            step = 'review',
            locked_by = NULL,
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP,
            last_error = 'finalization interrupted by restart; please submit review again'
        WHERE status = 'running' AND step = 'finalize'
        """
    )
    conn.execute(
        """
        UPDATE worker_duty
        SET status = 'queued',
            step = 'infer',
            locked_by = NULL,
            locked_at = NULL,
            updated_at = CURRENT_TIMESTAMP,
            last_error = 'recovered after restart'
        WHERE status IN ('queued', 'running') AND step <> 'finalize'
        """
    )
    rows = conn.execute(
        """
        SELECT * FROM worker_duty
        WHERE status = 'queued' AND image_path IS NOT NULL AND image_path <> ''
        ORDER BY created_at
        """
    ).fetchall()
    conn.commit()
    return [_decode_duty(row) for row in rows]


def _decode_duty(row) -> dict[str, Any]:
    result = dict(row)
    for key in ("draft_result", "final_result"):
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                pass
    return result


def update_duty_ready_for_review(
    conn: SQLiteConnection,
    *,
    worker_id: str,
    draft_result: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE worker_duty
        SET status = 'ready_for_review',
            step = 'review',
            draft_result = %s,
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now(),
            last_error = NULL
        WHERE worker_id = %s
        """,
        (draft_result, worker_id),
    )
    conn.commit()


def claim_review_for_finalization(conn: SQLiteConnection, *, worker_id: str, lock_owner: str) -> bool:
    row = conn.execute(
        """
        UPDATE worker_duty
        SET status = 'running',
            step = 'finalize',
            locked_by = %s,
            locked_at = now(),
            updated_at = now()
        WHERE worker_id = %s AND status = 'ready_for_review'
        RETURNING worker_id
        """,
        (lock_owner, worker_id),
    ).fetchone()
    conn.commit()
    return row is not None


def restore_review_after_finalize_error(conn: SQLiteConnection, *, worker_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE worker_duty
        SET status = 'ready_for_review',
            step = 'review',
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now(),
            last_error = %s
        WHERE worker_id = %s AND step = 'finalize'
        """,
        (error, worker_id),
    )
    conn.commit()


def update_duty_finalized(
    conn: SQLiteConnection,
    *,
    worker_id: str,
    final_result: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE worker_duty
        SET status = 'finalized',
            step = 'completed',
            final_result = %s,
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now(),
            last_error = NULL
        WHERE worker_id = %s
        """,
        (final_result, worker_id),
    )
    conn.commit()


def update_duty_failed(conn: SQLiteConnection, *, worker_id: str, error: str) -> None:
    conn.execute(
        """
        UPDATE worker_duty
        SET status = 'failed',
            step = 'failed',
            last_error = %s,
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE worker_id = %s
        """,
        (error, worker_id),
    )
    conn.commit()


def mark_review_timeouts(conn: SQLiteConnection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        UPDATE worker_duty
        SET status = 'timeout',
            step = 'timeout',
            last_error = 'review session timeout',
            locked_by = NULL,
            locked_at = NULL,
            updated_at = now()
        WHERE status = 'ready_for_review'
          AND updated_at < datetime('now', '-' || session_timeout_sec || ' seconds')
        RETURNING worker_id, worker_encrypt_id, image_path
        """
    ).fetchall()
    conn.commit()
    return [dict(row) for row in rows]


def list_cleanup_targets(
    conn: SQLiteConnection,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT worker_id, worker_encrypt_id, status, image_path
        FROM worker_duty
        WHERE status IN ('finalized', 'failed', 'timeout')
          AND image_path IS NOT NULL
          AND image_path <> ''
        ORDER BY updated_at ASC
        LIMIT %s
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def clear_image_path(conn: SQLiteConnection, *, worker_id: str) -> None:
    conn.execute(
        """
        UPDATE worker_duty
        SET image_path = '',
            updated_at = now()
        WHERE worker_id = %s
        """,
        (worker_id,),
    )
    conn.commit()
