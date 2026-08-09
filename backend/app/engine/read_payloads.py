from __future__ import annotations

from typing import Any

from backend.app.db.db_dashboard import get_connection as get_dashboard_connection
from backend.app.db.db_user_drilldown import (
    fetch_user_drilldown_filters,
    fetch_user_drilldown_rows,
    fetch_user_receipt_date_bounds,
    get_connection as get_drilldown_connection,
)
from backend.app.engine import dashboard_payload as dashboard_engine
from backend.app.engine.user_drilldown_payload import resolve_drilldown_range


def dashboard(*, user_id: int, date_from: str | None, date_to: str | None) -> dict[str, Any]:
    date_range = dashboard_engine.resolve_range(date_from, date_to)
    with get_dashboard_connection() as conn:
        dashboard_engine.ensure_schema(conn)
        payload = dashboard_engine.build_compact_payload(
            conn, user_id=user_id, date_from=date_range.date_from, date_to=date_range.date_to
        )
    return {"user_id": user_id, "payload": payload}


def drilldown(
    *,
    user_id: int,
    date_from: str | None = None,
    date_to: str | None = None,
    year: int | None = None,
    month: int | None = None,
    category: str | None = None,
    sub_category: str | None = None,
    limit: int = 2000,
) -> dict[str, Any]:
    all_history = year is None and month is None and not date_from and not date_to
    date_range = resolve_drilldown_range(date_from=date_from, date_to=date_to, year=year, month=month)
    with get_drilldown_connection() as conn:
        if all_history:
            minimum, maximum = fetch_user_receipt_date_bounds(conn, user_id=user_id)
            date_range.date_from = minimum or date_range.date_from
            date_range.date_to = maximum or date_range.date_to
        rows = fetch_user_drilldown_rows(
            conn,
            user_id=user_id,
            date_from=date_range.date_from,
            date_to=date_range.date_to,
            category=category,
            sub_category=sub_category,
            limit=limit,
        )
    return {
        "user_id": user_id,
        "date_from": date_range.date_from.isoformat(),
        "date_to": date_range.date_to.isoformat(),
        "rows": rows,
    }


def drilldown_filters(*, user_id: int) -> dict[str, Any]:
    with get_drilldown_connection() as conn:
        filters = fetch_user_drilldown_filters(conn, user_id=user_id)
    return {"user_id": user_id, **filters}
