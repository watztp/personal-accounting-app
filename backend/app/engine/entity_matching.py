from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable

from backend.app.db.db_items import ItemCreate, create_items
from backend.app.db.db_stores import StoreCreate, create_store
from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema, get_connection


FUZZY_THRESHOLD = 0.90


def _text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def normalize_name(value: Any) -> str:
    return re.sub(r"[\W_]+", "", _text(value), flags=re.UNICODE)


def normalize_exact(value: Any) -> str:
    return re.sub(r"[\s\-‐‑‒–—―ー・,，.．/／()（）]+", "", _text(value))


def normalize_phone(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    prefix = "+" if text.strip().startswith("+") else ""
    return prefix + "".join(re.findall(r"\d", text))


def normalize_registration(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", _text(value))


def normalize_address(value: Any) -> str:
    return normalize_exact(value)


def _same(left: Any, right: Any, normalizer: Callable[[Any], str]) -> bool:
    return normalizer(left) == normalizer(right)


def _fingerprint(kind: str, payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{kind}:{encoded}".encode("utf-8")).hexdigest()


def _store_input_key(store: dict[str, Any]) -> str:
    return _fingerprint(
        "store",
        {
            "name": normalize_name(store.get("store_name")),
            "branch": normalize_exact(store.get("store_branch")),
            "tel": normalize_phone(store.get("store_tel")),
            "registration": normalize_registration(store.get("store_reg_num")),
            "address": normalize_address(store.get("store_addr")),
        },
    )


def _item_input_key(store_id: int, name: Any) -> str:
    return _fingerprint("item", {"store_id": store_id, "name": normalize_name(name)})


def _feedback_ignored(
    conn: SQLiteConnection,
    *,
    table: str,
    candidate_column: str,
    input_key: str,
    candidate_id: int,
    user_id: int,
) -> bool:
    rows = conn.execute(
        f"""
        SELECT user_id, outcome, COUNT(*) AS count
        FROM {table}
        WHERE input_key = ? AND {candidate_column} = ?
        GROUP BY user_id, outcome
        """,
        (input_key, candidate_id),
    ).fetchall()
    global_confirm = sum(int(row["count"]) for row in rows if row["outcome"] == "confirm")
    global_reject = sum(int(row["count"]) for row in rows if row["outcome"] == "reject")
    if global_reject >= 3 and global_reject >= global_confirm * 2:
        return True
    if global_confirm >= 3 and global_confirm >= global_reject * 2:
        return False
    user_confirm = sum(
        int(row["count"]) for row in rows if int(row["user_id"]) == user_id and row["outcome"] == "confirm"
    )
    user_reject = sum(
        int(row["count"]) for row in rows if int(row["user_id"]) == user_id and row["outcome"] == "reject"
    )
    return user_reject > user_confirm


def _store_rows(conn: SQLiteConnection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT store_id, name, alias, address, branch_name, tel, fax,
               registration_number, country_code, created_at
        FROM stores ORDER BY store_id
        """
    ).fetchall()
    aliases = conn.execute(
        "SELECT store_id, alias_value FROM store_name_aliases ORDER BY alias_id"
    ).fetchall()
    addresses = conn.execute(
        "SELECT store_id, alias_value FROM store_address_aliases ORDER BY alias_id"
    ).fetchall()
    aliases_by_store: dict[int, list[str]] = {}
    addresses_by_store: dict[int, list[str]] = {}
    for row in aliases:
        aliases_by_store.setdefault(int(row["store_id"]), []).append(str(row["alias_value"]))
    for row in addresses:
        addresses_by_store.setdefault(int(row["store_id"]), []).append(str(row["alias_value"]))
    output: list[dict[str, Any]] = []
    for row in rows:
        record = dict(row)
        store_id = int(record["store_id"])
        name_aliases = aliases_by_store.get(store_id, [])
        legacy = record.get("alias")
        if isinstance(legacy, str) and legacy:
            try:
                decoded = json.loads(legacy)
                name_aliases.extend(decoded if isinstance(decoded, list) else [str(decoded)])
            except json.JSONDecodeError:
                name_aliases.append(legacy)
        record["name_aliases"] = name_aliases
        record["address_aliases"] = addresses_by_store.get(store_id, [])
        output.append(record)
    return output


def _store_fields_match(candidate: dict[str, Any], incoming: dict[str, Any]) -> bool:
    if not _same(candidate.get("branch_name"), incoming.get("store_branch"), normalize_exact):
        return False
    if not _same(candidate.get("tel"), incoming.get("store_tel"), normalize_phone):
        return False
    if not _same(
        candidate.get("registration_number"), incoming.get("store_reg_num"), normalize_registration
    ):
        return False
    incoming_address = normalize_address(incoming.get("store_addr"))
    if normalize_address(candidate.get("address")) == incoming_address:
        return True
    return any(normalize_address(alias) == incoming_address for alias in candidate.get("address_aliases", []))


def _store_snapshot(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "store_name": candidate.get("name"),
        "store_branch": candidate.get("branch_name"),
        "store_addr": candidate.get("address"),
        "store_tel": candidate.get("tel"),
        "store_fax": candidate.get("fax"),
        "store_reg_num": candidate.get("registration_number"),
    }


def _find_fuzzy_store(
    conn: SQLiteConnection, *, incoming: dict[str, Any], user_id: int
) -> tuple[dict[str, Any], float, str] | None:
    incoming_name = normalize_name(incoming.get("store_name"))
    if not incoming_name:
        return None
    input_key = _store_input_key(incoming)
    matches: list[tuple[dict[str, Any], float, str]] = []
    for candidate in _store_rows(conn):
        best_value = SequenceMatcher(
            None, incoming_name, normalize_name(candidate.get("name"))
        ).ratio()
        best_source = "name"
        if best_value <= FUZZY_THRESHOLD:
            best_value = max(
                (
                    SequenceMatcher(None, incoming_name, normalize_name(alias)).ratio()
                    for alias in candidate.get("name_aliases", [])
                ),
                default=0.0,
            )
            best_source = "alias"
        if best_value <= FUZZY_THRESHOLD or not _store_fields_match(candidate, incoming):
            continue
        if _feedback_ignored(
            conn,
            table="store_match_feedback",
            candidate_column="candidate_store_id",
            input_key=input_key,
            candidate_id=int(candidate["store_id"]),
            user_id=user_id,
        ):
            continue
        matches.append((candidate, best_value, best_source))
    return max(matches, key=lambda value: (value[1], -int(value[0]["store_id"])), default=None)


def _item_candidates(conn: SQLiteConnection, *, store_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT DISTINCT i.item_id, i.name
        FROM items i
        LEFT JOIN receipt_items ri ON ri.item_id = i.item_id
        LEFT JOIN receipts r ON r.receipt_id = ri.receipt_id
        LEFT JOIN item_aliases ia ON ia.item_id = i.item_id AND ia.store_id = ?
        WHERE r.store_id = ? OR ia.store_id = ?
        ORDER BY i.item_id
        """,
        (store_id, store_id, store_id),
    ).fetchall()
    aliases = conn.execute(
        "SELECT item_id, alias_value FROM item_aliases WHERE store_id = ? ORDER BY alias_id",
        (store_id,),
    ).fetchall()
    by_item: dict[int, list[str]] = {}
    for row in aliases:
        by_item.setdefault(int(row["item_id"]), []).append(str(row["alias_value"]))
    return [{**dict(row), "aliases": by_item.get(int(row["item_id"]), [])} for row in rows]


def _find_fuzzy_item(
    conn: SQLiteConnection, *, store_id: int, incoming_name: Any, user_id: int
) -> tuple[dict[str, Any], float, str] | None:
    normalized = normalize_name(incoming_name)
    if not normalized:
        return None
    input_key = _item_input_key(store_id, incoming_name)
    matches: list[tuple[dict[str, Any], float, str]] = []
    for candidate in _item_candidates(conn, store_id=store_id):
        values = [(candidate.get("name"), "name")]
        values.extend((alias, "alias") for alias in candidate.get("aliases", []))
        score, source = max(
            ((SequenceMatcher(None, normalized, normalize_name(value)).ratio(), source) for value, source in values),
            default=(0.0, "name"),
        )
        if score <= FUZZY_THRESHOLD:
            continue
        if _feedback_ignored(
            conn,
            table="item_match_feedback",
            candidate_column="candidate_item_id",
            input_key=input_key,
            candidate_id=int(candidate["item_id"]),
            user_id=user_id,
        ):
            continue
        matches.append((candidate, score, source))
    return max(matches, key=lambda value: (value[1], -int(value[0]["item_id"])), default=None)


def resolve_review_entities(payload: dict[str, Any], *, user_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    reviewed = json.loads(json.dumps(payload, ensure_ascii=False))
    original_store = dict(reviewed.get("receipt_main_info") or {})
    context: dict[str, Any] = {
        "original_store": original_store,
        "original_items": [dict(item) for item in (reviewed.get("items") or [])],
        "store_match": None,
        "item_matches": [],
    }
    with get_connection() as conn:
        ensure_schema(conn)
        store_match = _find_fuzzy_store(conn, incoming=original_store, user_id=user_id)
        if not store_match:
            return reviewed, context
        candidate, score, source = store_match
        reviewed["receipt_main_info"] = {**original_store, **_store_snapshot(candidate)}
        store_id = int(candidate["store_id"])
        context["store_match"] = {
            "candidate_store_id": store_id,
            "input_key": _store_input_key(original_store),
            "score": score,
            "source": source,
        }
        for index, item in enumerate(reviewed.get("items") or []):
            original_name = item.get("name")
            item_match = _find_fuzzy_item(
                conn, store_id=store_id, incoming_name=original_name, user_id=user_id
            )
            if not item_match:
                continue
            item_candidate, item_score, item_source = item_match
            item["name"] = item_candidate.get("name")
            context["item_matches"].append(
                {
                    "index": index,
                    "candidate_store_id": store_id,
                    "candidate_item_id": int(item_candidate["item_id"]),
                    "input_key": _item_input_key(store_id, original_name),
                    "original_name": original_name,
                    "score": item_score,
                    "source": item_source,
                }
            )
    return reviewed, context


def _find_exact_store(conn: SQLiteConnection, incoming: dict[str, Any]) -> dict[str, Any] | None:
    incoming_name = normalize_name(incoming.get("store_name"))
    if not incoming_name:
        return None
    for candidate in _store_rows(conn):
        names = [candidate.get("name"), *candidate.get("name_aliases", [])]
        if not any(normalize_name(value) == incoming_name for value in names):
            continue
        if _store_fields_match(candidate, incoming):
            return candidate
    return None


def _find_exact_item(
    conn: SQLiteConnection, *, store_id: int, name: Any
) -> dict[str, Any] | None:
    normalized = normalize_name(name)
    for candidate in _item_candidates(conn, store_id=store_id):
        values = [candidate.get("name"), *candidate.get("aliases", [])]
        if any(normalize_name(value) == normalized for value in values):
            return candidate
    return None


def _add_alias(
    conn: SQLiteConnection,
    *,
    table: str,
    owner_columns: tuple[str, ...],
    owner_values: tuple[Any, ...],
    value: Any,
    normalizer: Callable[[Any], str],
) -> None:
    normalized = normalizer(value)
    if not normalized:
        return
    columns = ", ".join((*owner_columns, "alias_value", "alias_normalized"))
    placeholders = ", ".join("?" for _ in range(len(owner_values) + 2))
    conn.execute(
        f"INSERT OR IGNORE INTO {table} ({columns}) VALUES ({placeholders})",
        (*owner_values, str(value).strip(), normalized),
    )
    conn.commit()


def _record_feedback(
    conn: SQLiteConnection,
    *,
    table: str,
    candidate_column: str,
    user_id: int,
    input_key: str,
    candidate_id: int,
    outcome: str,
) -> None:
    conn.execute(
        f"INSERT INTO {table} (user_id, input_key, {candidate_column}, outcome) VALUES (?, ?, ?, ?)",
        (user_id, input_key, candidate_id, outcome),
    )
    conn.commit()


def finalize_review_entities(
    conn: SQLiteConnection,
    payload: dict[str, Any],
    *,
    context: dict[str, Any] | None,
    user_id: int,
) -> tuple[dict[str, Any], list[int]]:
    ensure_schema(conn)
    main = payload.get("receipt_main_info") or {}
    matched_store = _find_exact_store(conn, main)
    if matched_store:
        store = matched_store
    else:
        store = create_store(
            conn,
            StoreCreate(
                name=main.get("store_name"),
                branch_name=main.get("store_branch"),
                address=main.get("store_addr"),
                tel=main.get("store_tel"),
                fax=main.get("store_fax"),
                registration_number=main.get("store_reg_num"),
                country_code="JP",
            ),
        )
    store_id = int(store["store_id"])
    original_store = (context or {}).get("original_store") or {}
    if normalize_name(original_store.get("store_name")) != normalize_name(store.get("name")):
        _add_alias(
            conn,
            table="store_name_aliases",
            owner_columns=("store_id",),
            owner_values=(store_id,),
            value=original_store.get("store_name"),
            normalizer=normalize_name,
        )
    if normalize_address(original_store.get("store_addr")) != normalize_address(store.get("address")):
        _add_alias(
            conn,
            table="store_address_aliases",
            owner_columns=("store_id",),
            owner_values=(store_id,),
            value=original_store.get("store_addr"),
            normalizer=normalize_address,
        )
    store_match = (context or {}).get("store_match")
    if store_match:
        _record_feedback(
            conn,
            table="store_match_feedback",
            candidate_column="candidate_store_id",
            user_id=user_id,
            input_key=store_match["input_key"],
            candidate_id=int(store_match["candidate_store_id"]),
            outcome="confirm" if int(store_match["candidate_store_id"]) == store_id else "reject",
        )

    matches_by_index = {
        int(match["index"]): match for match in ((context or {}).get("item_matches") or [])
    }
    original_items = (context or {}).get("original_items") or []
    item_ids: list[int] = []
    for index, item in enumerate(payload.get("items") or []):
        exact = _find_exact_item(conn, store_id=store_id, name=item.get("name"))
        if exact:
            item_id = int(exact["item_id"])
        else:
            created = create_items(
                conn,
                [
                    ItemCreate(
                        name=item.get("name"),
                        normalized_name=normalize_name(item.get("name")),
                        category_id=None,
                    )
                ],
            )[0]
            item_id = int(created["item_id"])
        item_ids.append(item_id)
        original_name = (
            (original_items[index].get("name") if index < len(original_items) else None)
            or matches_by_index.get(index, {}).get("original_name")
            or item.get("name")
        )
        item_row = conn.execute("SELECT name FROM items WHERE item_id = ?", (item_id,)).fetchone()
        if item_row and normalize_name(original_name) != normalize_name(item_row["name"]):
            _add_alias(
                conn,
                table="item_aliases",
                owner_columns=("item_id", "store_id"),
                owner_values=(item_id, store_id),
                value=original_name,
                normalizer=normalize_name,
            )
        prior = matches_by_index.get(index)
        if prior:
            _record_feedback(
                conn,
                table="item_match_feedback",
                candidate_column="candidate_item_id",
                user_id=user_id,
                input_key=prior["input_key"],
                candidate_id=int(prior["candidate_item_id"]),
                outcome=(
                    "confirm"
                    if int(prior["candidate_item_id"]) == item_id
                    and int(prior["candidate_store_id"]) == store_id
                    else "reject"
                ),
            )
    return store, item_ids
