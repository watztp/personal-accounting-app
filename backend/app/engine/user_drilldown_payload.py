from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass
class DrilldownDateRange:
    date_from: date
    date_to: date


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def resolve_drilldown_range(
    *,
    date_from: str | None,
    date_to: str | None,
    year: int | None = None,
    month: int | None = None,
) -> DrilldownDateRange:
    parsed_from = _parse_date(date_from)
    parsed_to = _parse_date(date_to)

    if year is not None:
        if month is not None:
            if month < 1 or month > 12:
                raise ValueError("month must be between 1 and 12")
            start = date(year, month, 1)
            if month == 12:
                end = date(year + 1, 1, 1) - timedelta(days=1)
            else:
                end = date(year, month + 1, 1) - timedelta(days=1)
            parsed_from = start
            parsed_to = end
        else:
            parsed_from = date(year, 1, 1)
            parsed_to = date(year, 12, 31)

    today = date.today()
    fallback_from = today - timedelta(days=180)
    parsed_from = parsed_from or fallback_from
    parsed_to = parsed_to or today

    if parsed_from > parsed_to:
        raise ValueError("date_from must be <= date_to")

    return DrilldownDateRange(date_from=parsed_from, date_to=parsed_to)
