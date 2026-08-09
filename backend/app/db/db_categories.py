from dataclasses import dataclass

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite

@dataclass
class CategoryIdentity:
    category_name: str
    sub_category_name: str

    def validate(self) -> None:
        if not self.category_name or not self.sub_category_name:
            raise ValueError("category_name and sub_category_name are required")


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def find_category(conn: SQLiteConnection, identity: CategoryIdentity) -> dict | None:
    identity.validate()
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT category_id, category_name, sub_category_name, created_at
            FROM categories
            WHERE category_name = %s AND sub_category_name = %s
            ORDER BY category_id
            LIMIT 1
            """,
            (identity.category_name, identity.sub_category_name),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {
        "category_id": row[0],
        "category_name": row[1],
        "sub_category_name": row[2],
        "created_at": row[3],
    }


def create_category(conn: SQLiteConnection, identity: CategoryIdentity) -> dict:
    identity.validate()
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO categories (category_name, sub_category_name)
            VALUES (%s, %s)
            RETURNING category_id, category_name, sub_category_name, created_at
            """,
            (identity.category_name, identity.sub_category_name),
        )
        row = cur.fetchone()
    conn.commit()
    return {
        "category_id": row[0],
        "category_name": row[1],
        "sub_category_name": row[2],
        "created_at": row[3],
    }


def get_or_create_category(conn: SQLiteConnection, identity: CategoryIdentity) -> dict:
    existing = find_category(conn, identity)
    if existing:
        return existing
    return create_category(conn, identity)


def delete_category(conn: SQLiteConnection, identity: CategoryIdentity) -> int:
    identity.validate()
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM categories
            WHERE category_name = %s AND sub_category_name = %s
            """,
            (identity.category_name, identity.sub_category_name),
        )
        deleted = cur.rowcount
    conn.commit()
    return deleted
