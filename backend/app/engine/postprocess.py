from __future__ import annotations

import json
import re
from typing import Any


def _strip_unknown_tags(text: str) -> str:
    # Some model outputs include <unk> tokens which break XML-like parsing.
    return text.replace("<unk>", "")


def _find_block(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    return match.group(1).strip() if match else None


def _find_all(text: str, tag: str) -> list[str]:
    return [m.strip() for m in re.findall(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)]


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", value)
    if cleaned == "":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _to_str(value: float | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _split_item_rows(item_block: str) -> list[str]:
    # The model separates item rows with <sep/>. Keep parsing per row to avoid index drift.
    parts = re.split(r"<sep\s*/?>|</sep>", item_block, flags=re.IGNORECASE)
    return [p.strip() for p in parts if p and p.strip()]


def _first_present(block: str, tags: list[str]) -> str | None:
    for tag in tags:
        val = _find_block(block, tag)
        if val is not None and val.strip() != "":
            return val.strip()
    return None


def _parse_discount_amount(
    raw_discount: str | None,
    *,
    unit_price: float | None,
) -> float:
    if raw_discount is None:
        return 0.0

    text = raw_discount.strip()
    if text == "":
        return 0.0

    is_percent = bool(re.search(r"(%|％|パーセント|percent)", text, flags=re.IGNORECASE))
    numeric = _parse_number(text)
    if numeric is None:
        # Handle noisy OCR formats like "-371-" by extracting the numeric token.
        extracted = re.search(r"\d+(?:\.\d+)?", text)
        if extracted:
            numeric = float(extracted.group(0))

    if numeric is None:
        return 0.0

    numeric = abs(numeric)
    if is_percent:
        # Percent discount must be converted to amount from unit_price.
        if unit_price is None:
            return 0.0
        return (unit_price * numeric) / 100.0

    return numeric


def build_review_payload(
    model_output: dict[str, Any],
    *,
    user_id: str,
) -> dict[str, Any]:
    """Convert raw model output into review payload.

    Args:
        model_output: Raw model output dict.
        user_id: User id as string.

    Returns:
        Review payload dict.
    """
    text = model_output.get("text") or model_output.get("raw") or ""
    text = _strip_unknown_tags(text)

    store_block = _find_block(text, "store") or ""
    summary_block = _find_block(text, "summary") or ""
    item_block = _find_block(text, "item") or ""

    store_name = _find_block(store_block, "store_nm")
    store_branch = _find_block(store_block, "branch")
    store_addr = _find_block(store_block, "addr") or _find_block(store_block, "store_addr")
    store_tel = _find_block(store_block, "tel")
    store_fax = _find_block(store_block, "fax")
    store_reg_num = _find_block(store_block, "reg_num")
    store_datetime = _find_block(store_block, "datetime")

    items: list[dict[str, Any]] = []
    for row in _split_item_rows(item_block):
        name = _find_block(row, "nm")
        qty_val = _parse_number(_first_present(row, ["cnt", "qty", "quantity"]))
        price_val = _parse_number(_first_present(row, ["price", "item_price"]))
        unit_val = _parse_number(_first_present(row, ["unitprice", "unit_price"]))
        raw_discount = _first_present(
            row,
            [
                "discount_amount",
                "discount",
                "disc",
                "dc_amount",
                "dcprice",
                "discountprice",
                "discount_price",
            ],
        )

        if name is None and qty_val is None and price_val is None and unit_val is None:
            continue

        if qty_val is None:
            qty_val = 1
        if unit_val is None and price_val is not None and qty_val:
            unit_val = price_val / qty_val
        discount_val = _parse_discount_amount(raw_discount, unit_price=unit_val)

        items.append(
            {
                "name": name,
                "quantity": _to_str(qty_val),
                "unit_price": _to_str(unit_val),
                "price": _to_str(price_val),
                "discount_amount": _to_str(discount_val),
            }
        )

    total_quantity = _parse_number(_find_block(summary_block, "menuqty_cnt"))
    sub_total = _parse_number(_find_block(summary_block, "subtotal_price"))
    total = _parse_number(_find_block(summary_block, "total_price"))
    payment_method = _find_block(summary_block, "pay_method")
    paid_amount = _parse_number(_find_block(summary_block, "paidamount"))
    change_amount = _parse_number(_find_block(summary_block, "changeprice"))

    return {
        "user_id": user_id,
        "receipt_main_info": {
            "store_name": store_name,
            "store_branch": store_branch,
            "store_addr": store_addr,
            "store_tel": store_tel,
            "store_fax": store_fax,
            "store_reg_num": store_reg_num,
            "datetime": store_datetime,
        },
        "items": items,
        "summary": {
            "total_quantity": _to_str(total_quantity),
            "sub_total": _to_str(sub_total),
            "total": _to_str(total),
            "payment_method": payment_method,
            "paid_amount": _to_str(paid_amount),
            "change_amount": _to_str(change_amount),
        },
    }


def build_review_payload_from_json(
    json_text: str,
    *,
    user_id: str,
) -> dict[str, Any]:
    """Parse JSON text and build review payload.

    Args:
        json_text: JSON string from model.
        user_id: User id as string.

    Returns:
        Review payload dict.
    """
    data = json.loads(json_text)
    return build_review_payload(data, user_id=user_id)
