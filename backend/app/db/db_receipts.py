from dataclasses import dataclass
from datetime import date
from typing import Optional

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite

@dataclass
class ReceiptCreate:
    receipt_date: date
    amount: float
    currency: str = "JPY"
    source: Optional[str] = None
    payment_method: Optional[str] = None
    user_id: Optional[int] = None
    store_id: Optional[int] = None


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def create_receipt(conn: SQLiteConnection, receipt: ReceiptCreate) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO receipts (
                user_id, store_id, receipt_date, amount, currency, source, payment_method
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING
                receipt_id, user_id, store_id, receipt_date, amount, currency, source,
                payment_method, created_at
            """,
            (
                receipt.user_id,
                receipt.store_id,
                receipt.receipt_date,
                receipt.amount,
                receipt.currency,
                receipt.source,
                receipt.payment_method,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    return {
        "receipt_id": row[0],
        "user_id": row[1],
        "store_id": row[2],
        "receipt_date": row[3],
        "amount": row[4],
        "currency": row[5],
        "source": row[6],
        "payment_method": row[7],
        "created_at": row[8],
    }


def delete_receipt(conn: SQLiteConnection, receipt_id: int) -> int:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM receipts WHERE receipt_id = %s", (receipt_id,))
        deleted = cur.rowcount
    conn.commit()
    return deleted


def find_receipts(
    conn: SQLiteConnection,
    user_id: Optional[int] = None,
    store_id: Optional[int] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    min_amount: Optional[float] = None,
    max_amount: Optional[float] = None,
    currency: Optional[str] = None,
    source: Optional[str] = None,
    payment_method: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    clauses = []
    params: list = []

    if user_id is not None:
        clauses.append("user_id = %s")
        params.append(user_id)
    if store_id is not None:
        clauses.append("store_id = %s")
        params.append(store_id)
    if date_from is not None:
        clauses.append("receipt_date >= %s")
        params.append(date_from)
    if date_to is not None:
        clauses.append("receipt_date <= %s")
        params.append(date_to)
    if min_amount is not None:
        clauses.append("amount >= %s")
        params.append(min_amount)
    if max_amount is not None:
        clauses.append("amount <= %s")
        params.append(max_amount)
    if currency is not None:
        clauses.append("currency = %s")
        params.append(currency)
    if source is not None:
        clauses.append("source ILIKE %s")
        params.append(f"%{source}%")
    if payment_method is not None:
        clauses.append("payment_method ILIKE %s")
        params.append(f"%{payment_method}%")

    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    sql = f"""
    SELECT
        receipt_id, user_id, store_id, receipt_date, amount,
        currency, source, payment_method, created_at
    FROM receipts
    {where_sql}
    ORDER BY receipt_id
    LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()

    return [
        {
            "receipt_id": r[0],
            "user_id": r[1],
            "store_id": r[2],
            "receipt_date": r[3],
            "amount": r[4],
            "currency": r[5],
            "source": r[6],
            "payment_method": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]
