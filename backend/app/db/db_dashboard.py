from __future__ import annotations

from datetime import date
from typing import Any

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def fetch_receipts_daily(
    conn: SQLiteConnection,
    *,
    sso_id: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.receipt_date, SUM(r.amount) AS total_amount
        FROM receipts r
        JOIN users u ON u.user_id = r.user_id
        WHERE u.sso_id = ? AND r.receipt_date BETWEEN ? AND ?
        GROUP BY r.receipt_date
        ORDER BY r.receipt_date
        """,
        (sso_id, date_from, date_to),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_category_rows(
    conn: SQLiteConnection,
    *,
    sso_id: str,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.receipt_date AS row_date,
               COALESCE(ri.line_total, ri.unit_price * COALESCE(ri.quantity, 1)) AS price,
               ri.category_id,
               c.category_name,
               c.sub_category_name
        FROM receipt_items ri
        JOIN receipts r ON r.receipt_id = ri.receipt_id
        JOIN users u ON u.user_id = r.user_id
        LEFT JOIN categories c ON c.category_id = ri.category_id
        WHERE u.sso_id = ? AND r.receipt_date BETWEEN ? AND ?
        ORDER BY r.receipt_date
        """,
        (sso_id, date_from, date_to),
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_receipts_sum(
    conn: SQLiteConnection,
    *,
    sso_id: str,
    date_from: date,
    date_to: date,
) -> float:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(r.amount), 0) AS total_amount
        FROM receipts r
        JOIN users u ON u.user_id = r.user_id
        WHERE u.sso_id = ? AND r.receipt_date BETWEEN ? AND ?
        """,
        (sso_id, date_from, date_to),
    ).fetchone()
    return float(row["total_amount"] or 0)


def fetch_compact_rows_by_user_id(
    conn: SQLiteConnection,
    *,
    user_id: int,
    date_from: date,
    date_to: date,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT r.receipt_date AS row_date,
               COALESCE(ri.line_total, ri.unit_price * COALESCE(ri.quantity, 1)) AS amount_yen,
               c.category_name,
               c.sub_category_name
        FROM receipt_items ri
        JOIN receipts r ON r.receipt_id = ri.receipt_id
        LEFT JOIN categories c ON c.category_id = ri.category_id
        WHERE r.user_id = ?
          AND r.receipt_date BETWEEN ? AND ?
          AND COALESCE(ri.line_total, ri.unit_price * COALESCE(ri.quantity, 1)) IS NOT NULL
        ORDER BY r.receipt_date
        """,
        (user_id, date_from, date_to),
    ).fetchall()
    return [dict(row) for row in rows]
