from dataclasses import dataclass
from typing import Optional

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite

@dataclass
class ReceiptItemCreate:
    item_name_raw: str
    receipt_id: Optional[int] = None
    item_id: Optional[int] = None
    quantity: Optional[float] = None
    unit_price: Optional[float] = None
    price: Optional[float] = None
    line_total: Optional[float] = None
    line_no: Optional[int] = None
    category_id: Optional[int] = None


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def create_receipt_item(conn: SQLiteConnection, item: ReceiptItemCreate) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO receipt_items (
                receipt_id,
                item_id,
                item_name_raw,
                quantity,
                unit_price,
                price,
                line_total,
                line_no,
                category_id
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING
                receipt_item_id, receipt_id, item_id, item_name_raw, quantity,
                unit_price, price, line_total, line_no, category_id, created_at
            """,
            (
                item.receipt_id,
                item.item_id,
                item.item_name_raw,
                item.quantity,
                item.unit_price,
                item.price,
                item.line_total,
                item.line_no,
                item.category_id,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return {
        "receipt_item_id": row[0],
        "receipt_id": row[1],
        "item_id": row[2],
        "item_name_raw": row[3],
        "quantity": row[4],
        "unit_price": row[5],
        "price": row[6],
        "line_total": row[7],
        "line_no": row[8],
        "category_id": row[9],
        "created_at": row[10],
    }


def create_receipt_items(conn: SQLiteConnection, items: list[ReceiptItemCreate]) -> list[dict]:
    if not items:
        return []

    values_sql = ", ".join(["(%s, %s, %s, %s, %s, %s, %s, %s, %s)"] * len(items))
    params: list = []
    for item in items:
        params.extend(
            [
                item.receipt_id,
                item.item_id,
                item.item_name_raw,
                item.quantity,
                item.unit_price,
                item.price,
                item.line_total,
                item.line_no,
                item.category_id,
            ]
        )

    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO receipt_items (
                receipt_id,
                item_id,
                item_name_raw,
                quantity,
                unit_price,
                price,
                line_total,
                line_no,
                category_id
            )
            VALUES {values_sql}
            RETURNING
                receipt_item_id, receipt_id, item_id, item_name_raw, quantity,
                unit_price, price, line_total, line_no, category_id, created_at
            """,
            params,
        )
        rows = cur.fetchall()
    conn.commit()

    return [
        {
            "receipt_item_id": r[0],
            "receipt_id": r[1],
            "item_id": r[2],
            "item_name_raw": r[3],
            "quantity": r[4],
            "unit_price": r[5],
            "price": r[6],
            "line_total": r[7],
            "line_no": r[8],
            "category_id": r[9],
            "created_at": r[10],
        }
        for r in rows
    ]


def delete_receipt_item(conn: SQLiteConnection, receipt_item_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM receipt_items WHERE receipt_item_id = %s", (receipt_item_id,))
        deleted = cur.rowcount
    conn.commit()
    return deleted


def find_receipt_items(
    conn: SQLiteConnection,
    receipt_id: Optional[int] = None,
    item_id: Optional[int] = None,
    category_id: Optional[int] = None,
    item_name_raw: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses = []
    params: list = []

    if receipt_id is not None:
        clauses.append("receipt_id = %s")
        params.append(receipt_id)
    if item_id is not None:
        clauses.append("item_id = %s")
        params.append(item_id)
    if category_id is not None:
        clauses.append("category_id = %s")
        params.append(category_id)
    if item_name_raw is not None:
        clauses.append("item_name_raw ILIKE %s")
        params.append(f"%{item_name_raw}%")

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    sql = f"""
    SELECT
        receipt_item_id, receipt_id, item_id, item_name_raw, quantity,
        unit_price, price, line_total, line_no, category_id, created_at
    FROM receipt_items
    {where_sql}
    ORDER BY receipt_item_id
    LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        {
            "receipt_item_id": r[0],
            "receipt_id": r[1],
            "item_id": r[2],
            "item_name_raw": r[3],
            "quantity": r[4],
            "unit_price": r[5],
            "price": r[6],
            "line_total": r[7],
            "line_no": r[8],
            "category_id": r[9],
            "created_at": r[10],
        }
        for r in rows
    ]
