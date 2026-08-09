from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock
from typing import Any

from backend.app.db.db_dashboard import (
    ensure_schema as ensure_dashboard_schema,
    fetch_compact_rows_by_user_id,
)
from backend.app.db.db_users import ensure_schema as ensure_users_schema

_SCHEMA_LOCK = Lock()
_SCHEMA_READY = False


def ensure_schema(conn) -> None:
    """Ensure dashboard + users schemas exist.

    Args:
        conn: Open DB connection.
    """
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    with _SCHEMA_LOCK:
        if _SCHEMA_READY:
            return
        ensure_users_schema(conn)
        ensure_dashboard_schema(conn)
        _SCHEMA_READY = True


def parse_date(value: str | None) -> date | None:
    """Parse ISO date string to date.

    Args:
        value: ISO date string or None.

    Returns:
        date or None.
    """
    if not value:
        return None
    return date.fromisoformat(value)


def default_range(days: int = 365 * 5) -> tuple[date, date]:
    """Get default date range ending today.

    Args:
        days: Number of days back from today.

    Returns:
        (start_date, end_date).
    """
    end = date.today()
    start = end - timedelta(days=days)
    return start, end


@dataclass
class DateRange:
    date_from: date
    date_to: date


def resolve_range(date_from: str | None, date_to: str | None) -> DateRange:
    """Resolve date range, applying default if missing.

    - If both dates are missing, fallback to the last 5 years.
    - If one side is missing, fill only that side with default boundary.
    """
    parsed_from = parse_date(date_from)
    parsed_to = parse_date(date_to)
    fallback_from, fallback_to = default_range()
    if parsed_from is None and parsed_to is None:
        parsed_from = fallback_from
        parsed_to = fallback_to
    else:
        parsed_from = parsed_from or fallback_from
        parsed_to = parsed_to or fallback_to

    if parsed_from > parsed_to:
        raise ValueError("date_from must be <= date_to")

    return DateRange(date_from=parsed_from, date_to=parsed_to)


def _to_int_yen(value: Any) -> int:
    amount = Decimal(str(value or 0))
    return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def build_compact_payload(
    conn,
    *,
    user_id: int,
    date_from: date,
    date_to: date,
    tz: str = "Asia/Tokyo",
) -> dict[str, Any]:
    """Build compact dashboard payload from DB rows."""
    rows = fetch_compact_rows_by_user_id(
        conn,
        user_id=user_id,
        date_from=date_from,
        date_to=date_to,
    )

    cats: list[str] = []
    cat_to_id: dict[str, int] = {}
    subcats: list[dict[str, Any]] = []
    subcat_key_to_id: dict[tuple[int, str], int] = {}

    day: list[int] = []
    subcat_id: list[int] = []
    amount_yen: list[int] = []

    for row in rows:
        row_date = row["row_date"]
        cat_name = (row.get("category_name") or "uncategorized").strip().lower()
        sub_name = (row.get("sub_category_name") or "uncategorized").strip().lower()

        if cat_name not in cat_to_id:
            cat_to_id[cat_name] = len(cats)
            cats.append(cat_name)
        cat_id = cat_to_id[cat_name]

        sub_key = (cat_id, sub_name)
        if sub_key not in subcat_key_to_id:
            subcat_key_to_id[sub_key] = len(subcats)
            subcats.append({"name": sub_name, "cat_id": cat_id})
        sid = subcat_key_to_id[sub_key]

        day.append((row_date - date_from).days)
        subcat_id.append(sid)
        amount_yen.append(_to_int_yen(row.get("amount_yen")))

    return {
        "v": 1,
        "tz": tz,
        "base_date": date_from.isoformat(),
        "cats": cats,
        "subcats": subcats,
        "data": {
            "day": day,
            "subcat_id": subcat_id,
            "amount_yen": amount_yen,
        },
    }
