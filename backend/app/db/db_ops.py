from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from backend.app.db.db_categories import ensure_schema as ensure_categories_schema
from backend.app.db.db_items import ItemCreate, create_items, ensure_schema as ensure_items_schema
from backend.app.db.db_receipt_items import (
    ReceiptItemCreate,
    create_receipt_items,
    ensure_schema as ensure_receipt_items_schema,
)
from backend.app.db.db_receipts import ReceiptCreate, create_receipt, ensure_schema as ensure_receipts_schema
from backend.app.db.db_stores import StoreCreate, finalize_store_after_review, ensure_schema as ensure_stores_schema
from backend.app.db.sqlite_db import SQLiteConnection, get_connection as connect_sqlite


@dataclass
class ReceiptPayload:
    receipt_main_info: dict[str, Any]
    items: list[dict[str, Any]]
    summary: dict[str, Any]


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_categories_schema(conn)
    ensure_stores_schema(conn)
    ensure_items_schema(conn)
    ensure_receipts_schema(conn)
    ensure_receipt_items_schema(conn)


def add_receipt_payload(
    conn: SQLiteConnection,
    payload: dict[str, Any],
    *,
    create_item_rows: bool = True,
    currency_default: str = "JPY",
    user_id: int,
    store_override: dict[str, Any] | None = None,
    item_ids_override: list[int] | None = None,
) -> dict:
    """
    Insert receipt data from payload into DB.
    Returns created ids and rows for quick use.
    """
    data = ReceiptPayload(
        receipt_main_info=payload.get("receipt_main_info") or {},
        items=payload.get("items") or [],
        summary=payload.get("summary") or {},
    )

    store = store_override or _upsert_store(conn, data.receipt_main_info)
    receipt = _create_receipt(
        conn,
        store_id=store["store_id"],
        main=data.receipt_main_info,
        summary=data.summary,
        currency_default=currency_default,
        user_id=user_id,
    )

    created_items: list[dict] = []
    receipt_items: list[dict] = []

    if data.items:
        if item_ids_override is not None:
            if len(item_ids_override) != len(data.items):
                raise ValueError("item_ids_override must match payload item count")
            item_ids = item_ids_override
            created_items = []
            item_category_ids = []
            for item_id in item_ids:
                row = conn.execute(
                    "SELECT item_id, name, normalized_name, category_id, created_at FROM items WHERE item_id = ?",
                    (item_id,),
                ).fetchone()
                if not row:
                    raise ValueError(f"item_id not found: {item_id}")
                record = dict(row)
                created_items.append(record)
                item_category_ids.append(record.get("category_id"))
        elif create_item_rows:
            items_to_create = [
                ItemCreate(
                    name=item.get("name"),
                    normalized_name=_normalize_name(item.get("name")),
                    category_id=None,
                )
                for item in data.items
            ]
            created_items = create_items(conn, items_to_create)
            item_ids = [r["item_id"] for r in created_items]
            item_category_ids = [r.get("category_id") for r in created_items]
        else:
            item_ids = [None] * len(data.items)
            item_category_ids = [None] * len(data.items)

        receipt_items_to_create = []
        for idx, (item, item_id, item_category_id) in enumerate(
            zip(data.items, item_ids, item_category_ids, strict=True), start=1
        ):
            quantity = _to_decimal(item.get("quantity"))
            unit_price = _to_decimal(item.get("unit_price"))
            price = _to_decimal(item.get("price"))
            discount = _to_decimal(item.get("discount_amount"))

            line_total = None
            if price is not None:
                line_total = price - discount if discount is not None else price
            elif quantity is not None and unit_price is not None:
                line_total = quantity * unit_price

            receipt_items_to_create.append(
                ReceiptItemCreate(
                    receipt_id=receipt["receipt_id"],
                    item_id=item_id,
                    item_name_raw=item.get("name"),
                    quantity=float(quantity) if quantity is not None else None,
                    unit_price=float(unit_price) if unit_price is not None else None,
                    price=float(price) if price is not None else None,
                    line_total=float(line_total) if line_total is not None else None,
                    line_no=idx,
                    category_id=item_category_id,
                )
            )

        receipt_items = create_receipt_items(conn, receipt_items_to_create)

    return {
        "store": store,
        "receipt": receipt,
        "items": created_items,
        "receipt_items": receipt_items,
    }


def _upsert_store(conn: SQLiteConnection, main: dict[str, Any]) -> dict:
    store_name = main.get("store_name")
    if not store_name:
        raise ValueError("receipt_main_info.store_name is required")

    store = StoreCreate(
        name=store_name,
        branch_name=main.get("store_branch"),
        address=main.get("store_addr"),
        tel=main.get("store_tel"),
        fax=main.get("store_fax"),
        registration_number=main.get("store_reg_num"),
        country_code=None,
    )
    return finalize_store_after_review(conn, store, alias_to_add=None)


def _create_receipt(
    conn: SQLiteConnection,
    *,
    store_id: int,
    main: dict[str, Any],
    summary: dict[str, Any],
    currency_default: str,
    user_id: int,
) -> dict:
    receipt_date = _parse_date(main.get("datetime"))
    amount = _to_decimal(summary.get("total")) or _to_decimal(summary.get("sub_total"))
    if amount is None:
        raise ValueError("summary.total or summary.sub_total is required")

    receipt = ReceiptCreate(
        receipt_date=receipt_date,
        amount=float(amount),
        currency=summary.get("currency") or currency_default,
        source=None,
        payment_method=summary.get("payment_method"),
        user_id=user_id,
        store_id=store_id,
    )
    return create_receipt(conn, receipt)


def _normalize_name(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return " ".join(name.strip().lower().split())


def _to_decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if text == "":
        return None
    return Decimal(text)


def _parse_date(value: Any) -> date:
    if value is None:
        raise ValueError("receipt_main_info.datetime is required")
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        return date.fromisoformat(text)
