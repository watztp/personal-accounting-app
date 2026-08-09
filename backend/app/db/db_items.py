from dataclasses import dataclass
from typing import Optional

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite

@dataclass
class ItemCreate:
    name: str
    normalized_name: Optional[str] = None
    category_id: Optional[int] = None
    needs_review: bool = False


@dataclass
class ItemIdentity:
    name: str
    normalized_name: Optional[str] = None
    category_id: Optional[int] = None
    needs_review: Optional[bool] = None


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def create_item(conn: SQLiteConnection, item: ItemCreate) -> dict:
    if item.normalized_name:
        existing = find_item_by_normalized_name(conn, item.normalized_name)
        if existing:
            return existing
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO items (name, normalized_name, category_id, needs_review)
            VALUES (%s, %s, %s, %s)
            RETURNING item_id, name, normalized_name, category_id, needs_review, created_at
            """,
            (item.name, item.normalized_name, item.category_id, item.needs_review),
        )
        row = cur.fetchone()
    conn.commit()
    return {
        "item_id": row[0],
        "name": row[1],
        "normalized_name": row[2],
        "category_id": row[3],
        "needs_review": row[4],
        "created_at": row[5],
    }


def create_items(conn: SQLiteConnection, items: list[ItemCreate]) -> list[dict]:
    if not items:
        return []

    normalized_names = [i.normalized_name for i in items if i.normalized_name]
    existing_by_norm = _fetch_existing_by_normalized_name(conn, normalized_names)

    output: list[dict | None] = [None] * len(items)
    insert_entries: list[tuple[str, ItemCreate]] = []
    pending_keys: set[str] = set()
    index_to_key: dict[int, str] = {}

    for idx, item in enumerate(items):
        norm = item.normalized_name
        if norm and norm in existing_by_norm:
            output[idx] = existing_by_norm[norm]
            continue

        if norm:
            key = norm
            if key not in pending_keys:
                pending_keys.add(key)
                insert_entries.append((key, item))
            index_to_key[idx] = key
        else:
            key = f"__idx_{idx}"
            insert_entries.append((key, item))
            index_to_key[idx] = key

    if not insert_entries:
        return [r for r in output if r is not None]

    values_sql = ", ".join(["(%s, %s, %s, %s)"] * len(insert_entries))
    params: list = []
    for _, item in insert_entries:
        params.extend([item.name, item.normalized_name, item.category_id, item.needs_review])

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO items (name, normalized_name, category_id, needs_review)
            VALUES {values_sql}
            RETURNING item_id, name, normalized_name, category_id, needs_review, created_at
            """,
            params,
        )
        rows = cur.fetchall()
    conn.commit()

    created_rows = [
        {
            "item_id": r[0],
            "name": r[1],
            "normalized_name": r[2],
            "category_id": r[3],
            "needs_review": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]

    created_by_key = {key: row for (key, _), row in zip(insert_entries, created_rows, strict=True)}
    for idx, key in index_to_key.items():
        output[idx] = created_by_key[key]

    return [r for r in output if r is not None]


def _build_identity_where(identity: ItemIdentity) -> tuple[str, list]:
    clauses = ["name = %s"]
    params: list = [identity.name]

    if identity.normalized_name is None:
        clauses.append("normalized_name IS NULL")
    else:
        clauses.append("normalized_name = %s")
        params.append(identity.normalized_name)

    if identity.category_id is None:
        clauses.append("category_id IS NULL")
    else:
        clauses.append("category_id = %s")
        params.append(identity.category_id)

    if identity.needs_review is not None:
        clauses.append("needs_review = %s")
        params.append(identity.needs_review)

    return " AND ".join(clauses), params


def delete_item(conn: SQLiteConnection, identity: ItemIdentity) -> int:
    where_sql, params = _build_identity_where(identity)
    with conn.cursor() as cur:
        cur.execute(f"DELETE FROM items WHERE {where_sql}", params)
        deleted = cur.rowcount
    conn.commit()
    return deleted


def find_items(
    conn: SQLiteConnection,
    name: Optional[str] = None,
    normalized_name: Optional[str] = None,
    category_id: Optional[int] = None,
    needs_review: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses = []
    params: list = []

    if name is not None:
        clauses.append("name ILIKE %s")
        params.append(f"%{name}%")
    if normalized_name is not None:
        clauses.append("normalized_name = %s")
        params.append(normalized_name)
    if category_id is not None:
        clauses.append("category_id = %s")
        params.append(category_id)
    if needs_review is not None:
        clauses.append("needs_review = %s")
        params.append(needs_review)

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    sql = f"""
    SELECT item_id, name, normalized_name, category_id, needs_review, created_at
    FROM items
    {where_sql}
    ORDER BY item_id
    LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        {
            "item_id": r[0],
            "name": r[1],
            "normalized_name": r[2],
            "category_id": r[3],
            "needs_review": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


def find_uncategorized_items(conn: SQLiteConnection, limit: int = 50) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                i.item_id,
                i.name,
                i.normalized_name,
                i.category_id,
                i.needs_review,
                i.created_at,
                (
                    SELECT s.name
                    FROM receipt_items ri
                    JOIN receipts r ON r.receipt_id = ri.receipt_id
                    JOIN stores s ON s.store_id = r.store_id
                    WHERE ri.item_id = i.item_id
                    ORDER BY r.receipt_date DESC, ri.receipt_item_id DESC
                    LIMIT 1
                ) AS store_name
            FROM items i
            WHERE i.category_id IS NULL AND i.needs_review = 0
            ORDER BY i.item_id
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    return [
        {
            "item_id": r[0],
            "name": r[1],
            "normalized_name": r[2],
            "category_id": r[3],
            "needs_review": r[4],
            "created_at": r[5],
            "store_name": r[6],
        }
        for r in rows
    ]


def _find_manageable_items(
    conn: SQLiteConnection,
    *,
    include_unassigned: bool,
    limit: int,
) -> list[dict]:
    where_sql = "i.needs_review = 1 OR i.category_id IS NULL" if include_unassigned else "i.needs_review = 1"
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                i.item_id,
                i.name,
                i.normalized_name,
                i.category_id,
                i.needs_review,
                i.created_at,
                c.category_name,
                c.sub_category_name
            FROM items i
            LEFT JOIN categories c ON c.category_id = i.category_id
            WHERE {where_sql}
            ORDER BY i.item_id
            LIMIT %s
            """,
            (limit,),
        )
        rows = cur.fetchall()

    return [
        {
            "item_id": row[0],
            "name": row[1],
            "normalized_name": row[2],
            "category_id": row[3],
            "needs_review": bool(row[4]),
            "created_at": row[5],
            "category_name": row[6],
            "sub_category_name": row[7],
        }
        for row in rows
    ]


def find_items_needing_review(conn: SQLiteConnection, limit: int = 200) -> list[dict]:
    return _find_manageable_items(conn, include_unassigned=False, limit=limit)


def find_items_for_manual_assignment(conn: SQLiteConnection, limit: int = 200) -> list[dict]:
    """Return review items plus uncategorized items while automatic assignment is disabled."""
    return _find_manageable_items(conn, include_unassigned=True, limit=limit)


def set_item_category(
    conn: SQLiteConnection,
    item_id: int,
    category_id: Optional[int],
    needs_review: Optional[bool] = None,
) -> bool:
    with conn.cursor() as cur:
        if needs_review is None:
            cur.execute(
                """
                UPDATE items
                SET category_id = %s
                WHERE item_id = %s
                """,
                (category_id, item_id),
            )
        else:
            cur.execute(
                """
                UPDATE items
                SET category_id = %s, needs_review = %s
                WHERE item_id = %s
                """,
                (category_id, needs_review, item_id),
            )
        updated = cur.rowcount
    conn.commit()
    return updated > 0


def find_item_by_normalized_name(conn: SQLiteConnection, normalized_name: str) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT item_id, name, normalized_name, category_id, needs_review, created_at
            FROM items
            WHERE normalized_name = %s
            ORDER BY item_id
            LIMIT 1
            """,
            (normalized_name,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "item_id": row[0],
        "name": row[1],
        "normalized_name": row[2],
        "category_id": row[3],
        "needs_review": row[4],
        "created_at": row[5],
    }


def find_item_by_id(conn: SQLiteConnection, item_id: int) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT item_id, name, normalized_name, category_id, needs_review, created_at
            FROM items
            WHERE item_id = %s
            LIMIT 1
            """,
            (item_id,),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "item_id": row[0],
        "name": row[1],
        "normalized_name": row[2],
        "category_id": row[3],
        "needs_review": bool(row[4]),
        "created_at": row[5],
    }


def _fetch_existing_by_normalized_name(
    conn: SQLiteConnection, normalized_names: list[str]
) -> dict[str, dict]:
    if not normalized_names:
        return {}
    placeholders = ", ".join(["?"] * len(normalized_names))
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT item_id, name, normalized_name, category_id, needs_review, created_at
            FROM items
            WHERE normalized_name IN ({placeholders})
            """,
            normalized_names,
        )
        rows = cur.fetchall()
    return {
        r[2]: {
            "item_id": r[0],
            "name": r[1],
            "normalized_name": r[2],
            "category_id": r[3],
            "needs_review": r[4],
            "created_at": r[5],
        }
        for r in rows
    }
