from __future__ import annotations

import json
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Optional

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite


@dataclass
class StoreCreate:
    name: str
    alias: Optional[Any] = None
    address: Optional[str] = None
    branch_name: Optional[str] = None
    tel: Optional[str] = None
    fax: Optional[str] = None
    registration_number: Optional[str] = None
    country_code: Optional[str] = None


@dataclass
class StoreIdentity:
    name: str
    branch_name: Optional[str] = None
    registration_number: Optional[str] = None


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def reset_data(conn: SQLiteConnection) -> None:
    conn.execute("DELETE FROM stores")
    conn.execute("DELETE FROM sqlite_sequence WHERE name = 'stores'")
    conn.commit()


def _decode_alias(value: Any) -> Any:
    if value is None or not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _row_to_store(row) -> dict:
    return {
        "store_id": row[0],
        "name": row[1],
        "alias": _decode_alias(row[2]),
        "address": row[3],
        "branch_name": row[4],
        "tel": row[5],
        "fax": row[6],
        "registration_number": row[7],
        "country_code": row[8],
        "created_at": str(row[9]),
    }


def _build_identity_where(identity: StoreIdentity) -> tuple[str, list[Any]]:
    clauses = ["name = ?"]
    params: list[Any] = [identity.name]
    if identity.branch_name is None:
        clauses.append("branch_name IS NULL")
    else:
        clauses.append("branch_name = ?")
        params.append(identity.branch_name)
    if identity.registration_number is None:
        clauses.append("registration_number IS NULL")
    else:
        clauses.append("registration_number = ?")
        params.append(identity.registration_number)
    return " AND ".join(clauses), params


def create_store(conn: SQLiteConnection, store: StoreCreate) -> dict:
    alias_value = json.dumps(store.alias, ensure_ascii=False) if store.alias is not None else None
    row = conn.execute(
        """
        INSERT INTO stores (
            name, alias, address, branch_name, tel, fax, registration_number, country_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        RETURNING store_id, name, alias, address, branch_name, tel, fax,
                  registration_number, country_code, created_at
        """,
        (
            store.name,
            alias_value,
            store.address,
            store.branch_name,
            store.tel,
            store.fax,
            store.registration_number,
            store.country_code or "JP",
        ),
    ).fetchone()
    conn.commit()
    return _row_to_store(row)


def delete_store(conn: SQLiteConnection, identity: StoreIdentity) -> int:
    where_sql, params = _build_identity_where(identity)
    cursor = conn.execute(f"DELETE FROM stores WHERE {where_sql}", params)
    conn.commit()
    return cursor.rowcount


def _alias_values(value: Any) -> list[str]:
    decoded = _decode_alias(value)
    if decoded is None:
        return []
    if isinstance(decoded, list):
        return [str(item) for item in decoded]
    if isinstance(decoded, dict):
        return [str(item) for item in decoded.values()]
    return [str(decoded)]


def _similarity(left: Any, right: Any) -> float:
    if left is None or right is None:
        return 0.0
    a = str(left).strip().casefold()
    b = str(right).strip().casefold()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def find_store_similar_fields(
    conn: SQLiteConnection,
    q_name: Optional[str] = None,
    q_branch: Optional[str] = None,
    q_address: Optional[str] = None,
    q_alias: Optional[str] = None,
    q_reg: Optional[str] = None,
    per_field_threshold: float = 0.25,
    min_score: Optional[float] = None,
    limit: int = 5,
) -> list[dict]:
    rows = conn.execute(
        """
        SELECT store_id, name, alias, address, branch_name, tel, fax,
               registration_number, country_code, created_at
        FROM stores
        ORDER BY store_id
        """
    ).fetchall()

    ranked: list[dict] = []
    for row in rows:
        record = _row_to_store(row)
        alias_sim = max((_similarity(alias, q_alias) for alias in _alias_values(record["alias"])), default=0.0)
        name_sim = _similarity(record["name"], q_name)
        branch_sim = _similarity(record["branch_name"], q_branch)
        addr_sim = _similarity(record["address"], q_address)
        reg_sim = _similarity(record["registration_number"], q_reg)
        field_scores = [name_sim, alias_sim, branch_sim, addr_sim, reg_sim]
        if max(field_scores, default=0.0) < per_field_threshold:
            continue
        score = 0.20 * name_sim + 0.20 * alias_sim + 0.30 * branch_sim + 0.30 * addr_sim
        if min_score is not None and score < min_score:
            continue
        record.update(
            name_sim=name_sim,
            alias_sim=alias_sim,
            branch_sim=branch_sim,
            addr_sim=addr_sim,
            reg_sim=reg_sim,
            score=score,
        )
        ranked.append(record)
    ranked.sort(key=lambda value: (-value["score"], value["store_id"]))
    return ranked[: max(1, int(limit))]


def _alias_to_text(alias: Optional[Any]) -> Optional[str]:
    if alias is None:
        return None
    if isinstance(alias, str):
        return alias
    if isinstance(alias, (list, tuple)):
        return " ".join(str(value) for value in alias if value is not None)
    return json.dumps(alias, ensure_ascii=False)


def resolve_store_from_ocr(
    conn: SQLiteConnection,
    ocr_store: StoreCreate,
    accept_score: float = 0.7,
    per_field_threshold: float = 0.3,
) -> dict:
    candidates = find_store_similar_fields(
        conn,
        q_name=ocr_store.name,
        q_branch=ocr_store.branch_name,
        q_address=ocr_store.address,
        q_alias=_alias_to_text(ocr_store.alias),
        q_reg=ocr_store.registration_number,
        per_field_threshold=per_field_threshold,
        limit=5,
    )
    top = candidates[0] if candidates else None
    if top and top["score"] >= accept_score:
        return {
            "suggested_store": top,
            "draft_store": ocr_store,
            "candidates": candidates,
            "decision": "use_existing_candidate",
        }
    return {
        "suggested_store": None,
        "draft_store": ocr_store,
        "candidates": candidates,
        "decision": "use_ocr_raw",
    }


def strict_match_store_id(conn: SQLiteConnection, store: StoreCreate) -> Optional[int]:
    if store.address is None and store.branch_name is None:
        return None
    clauses = ["name = ?"]
    params: list[Any] = [store.name]
    if store.registration_number is not None:
        clauses.append("registration_number = ?")
        params.append(store.registration_number)
    alternatives: list[str] = []
    if store.address is not None:
        alternatives.append("address = ?")
        params.append(store.address)
    if store.branch_name is not None:
        alternatives.append("branch_name = ?")
        params.append(store.branch_name)
    clauses.append("(" + " OR ".join(alternatives) + ")")
    row = conn.execute(
        "SELECT store_id FROM stores WHERE " + " AND ".join(clauses) + " ORDER BY store_id LIMIT 1",
        params,
    ).fetchone()
    return int(row[0]) if row else None


def finalize_store_after_review(
    conn: SQLiteConnection,
    reviewed_store: StoreCreate,
    alias_to_add: Optional[str] = None,
) -> dict:
    matched_id = strict_match_store_id(conn, reviewed_store)
    if matched_id is None:
        if alias_to_add:
            reviewed_store.alias = [alias_to_add]
        return create_store(conn, reviewed_store)

    conn.execute(
        """
        UPDATE stores SET
            address = COALESCE(address, ?),
            branch_name = COALESCE(branch_name, ?),
            tel = COALESCE(tel, ?),
            fax = COALESCE(fax, ?),
            registration_number = COALESCE(registration_number, ?),
            country_code = COALESCE(country_code, ?)
        WHERE store_id = ?
        """,
        (
            reviewed_store.address,
            reviewed_store.branch_name,
            reviewed_store.tel,
            reviewed_store.fax,
            reviewed_store.registration_number,
            reviewed_store.country_code,
            matched_id,
        ),
    )
    if alias_to_add:
        alias_row = conn.execute("SELECT alias FROM stores WHERE store_id = ?", (matched_id,)).fetchone()
        aliases = _alias_values(alias_row[0] if alias_row else None)
        if alias_to_add not in aliases:
            aliases.append(alias_to_add)
            conn.execute(
                "UPDATE stores SET alias = ? WHERE store_id = ?",
                (json.dumps(aliases, ensure_ascii=False), matched_id),
            )
    row = conn.execute(
        """
        SELECT store_id, name, alias, address, branch_name, tel, fax,
               registration_number, country_code, created_at
        FROM stores WHERE store_id = ?
        """,
        (matched_id,),
    ).fetchone()
    conn.commit()
    return _row_to_store(row)
