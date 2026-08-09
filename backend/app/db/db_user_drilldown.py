from __future__ import annotations

from datetime import date
from typing import Any

from backend.app.db.sqlite_db import SQLiteConnection, get_connection as connect_sqlite


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def fetch_user_drilldown_rows(
    conn: SQLiteConnection,
    *,
    user_id: int,
    date_from: date,
    date_to: date,
    category: str | None = None,
    sub_category: str | None = None,
    limit: int = 2000,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 5000))
    clauses = ["r.user_id = ?", "r.receipt_date BETWEEN ? AND ?"]
    params: list[Any] = [user_id, date_from, date_to]
    if category and category.strip():
        clauses.append("LOWER(COALESCE(c.category_name, 'uncategorized')) = ?")
        params.append(category.strip().lower())
    if sub_category and sub_category.strip():
        clauses.append("LOWER(COALESCE(c.sub_category_name, 'uncategorized')) = ?")
        params.append(sub_category.strip().lower())
    clauses.append("COALESCE(ri.line_total, ri.unit_price * COALESCE(ri.quantity, 1)) IS NOT NULL")
    params.append(safe_limit)

    rows = conn.execute(
        f"""
        SELECT r.receipt_date AS row_date,
               COALESCE(NULLIF(TRIM(ri.item_name_raw), ''), i.name, '(no name)') AS item_name,
               COALESCE(ri.quantity, 1) AS quantity,
               COALESCE(ri.line_total, ri.unit_price * COALESCE(ri.quantity, 1)) AS amount_yen,
               COALESCE(NULLIF(TRIM(c.category_name), ''), 'uncategorized') AS category_name,
               COALESCE(NULLIF(TRIM(c.sub_category_name), ''), 'uncategorized') AS sub_category_name
        FROM receipt_items ri
        JOIN receipts r ON r.receipt_id = ri.receipt_id
        LEFT JOIN items i ON i.item_id = ri.item_id
        LEFT JOIN categories c ON c.category_id = COALESCE(ri.category_id, i.category_id)
        WHERE {' AND '.join(clauses)}
        ORDER BY r.receipt_date DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def fetch_user_receipt_date_bounds(
    conn: SQLiteConnection,
    *,
    user_id: int,
) -> tuple[date | None, date | None]:
    row = conn.execute(
        "SELECT MIN(receipt_date) AS min_date, MAX(receipt_date) AS max_date FROM receipts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row or row["min_date"] is None:
        return (None, None)
    return (date.fromisoformat(str(row["min_date"])), date.fromisoformat(str(row["max_date"])))


def fetch_user_drilldown_filters(
    conn: SQLiteConnection,
    *,
    user_id: int,
) -> dict[str, Any]:
    filter_rows = conn.execute(
        """
        SELECT CAST(strftime('%Y', r.receipt_date) AS INTEGER) AS year,
               CAST(strftime('%m', r.receipt_date) AS INTEGER) AS month,
               COALESCE(NULLIF(TRIM(c.category_name), ''), 'uncategorized') AS category_name,
               COALESCE(NULLIF(TRIM(c.sub_category_name), ''), 'uncategorized') AS sub_category_name
        FROM receipt_items ri
        JOIN receipts r ON r.receipt_id = ri.receipt_id
        LEFT JOIN items i ON i.item_id = ri.item_id
        LEFT JOIN categories c ON c.category_id = COALESCE(ri.category_id, i.category_id)
        WHERE r.user_id = ?
        GROUP BY year, month, category_name, sub_category_name
        ORDER BY year DESC, month DESC, category_name, sub_category_name
        """,
        (user_id,),
    ).fetchall()
    ym_rows = conn.execute(
        """
        SELECT CAST(strftime('%Y', receipt_date) AS INTEGER) AS year,
               CAST(strftime('%m', receipt_date) AS INTEGER) AS month
        FROM receipts WHERE user_id = ?
        GROUP BY year, month ORDER BY year DESC, month DESC
        """,
        (user_id,),
    ).fetchall()
    cat_rows = conn.execute(
        """
        SELECT COALESCE(NULLIF(TRIM(c.category_name), ''), 'uncategorized') AS category_name,
               COALESCE(NULLIF(TRIM(c.sub_category_name), ''), 'uncategorized') AS sub_category_name
        FROM receipt_items ri
        JOIN receipts r ON r.receipt_id = ri.receipt_id
        LEFT JOIN items i ON i.item_id = ri.item_id
        LEFT JOIN categories c ON c.category_id = COALESCE(ri.category_id, i.category_id)
        WHERE r.user_id = ?
        GROUP BY category_name, sub_category_name ORDER BY category_name, sub_category_name
        """,
        (user_id,),
    ).fetchall()
    latest_row = conn.execute("SELECT MAX(receipt_date) AS latest_date FROM receipts WHERE user_id = ?", (user_id,)).fetchone()

    months_by_year: dict[str, list[int]] = {}
    years: list[int] = []
    for row in ym_rows:
        year, month = int(row["year"]), int(row["month"])
        key = str(year)
        if key not in months_by_year:
            months_by_year[key] = []
            years.append(year)
        months_by_year[key].append(month)

    categories: list[str] = []
    subcategories_by_category: dict[str, list[str]] = {}
    for row in cat_rows:
        category_name = str(row["category_name"])
        subcategory_name = str(row["sub_category_name"])
        if category_name not in subcategories_by_category:
            subcategories_by_category[category_name] = []
            categories.append(category_name)
        subcategories_by_category[category_name].append(subcategory_name)

    latest_date = None
    if latest_row and latest_row["latest_date"]:
        latest_date = date.fromisoformat(str(latest_row["latest_date"]))

    return {
        "years": years,
        "months_by_year": months_by_year,
        "categories": categories,
        "subcategories_by_category": subcategories_by_category,
        "filter_rows": [
            {
                "year": int(row["year"]),
                "month": int(row["month"]),
                "category": str(row["category_name"]),
                "subcategory": str(row["sub_category_name"]),
            }
            for row in filter_rows
        ],
        "default_year": latest_date.year if latest_date else None,
        "default_month": latest_date.month if latest_date else None,
    }
