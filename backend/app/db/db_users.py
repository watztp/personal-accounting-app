from dataclasses import dataclass
from typing import Optional

from backend.app.db.sqlite_db import SQLiteConnection, ensure_schema as ensure_core_schema, get_connection as connect_sqlite

@dataclass
class UserIdentity:
    login_email: Optional[str]
    sso_id: Optional[str]

    def validate_at_least_one(self) -> None:
        if not (self.login_email or self.sso_id):
            raise ValueError("Provide at least one of login_email or sso_id")


def ensure_schema(conn: SQLiteConnection) -> None:
    ensure_core_schema(conn)


def create_user(conn: SQLiteConnection, identity: UserIdentity) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO users (login_email, sso_id)
            VALUES (%s, %s)
            RETURNING user_id, login_email, sso_id, created_at
            """,
            (identity.login_email, identity.sso_id),
        )
        row = cur.fetchone()
    conn.commit()
    return {
        "user_id": row[0],
        "login_email": row[1],
        "sso_id": row[2],
        "created_at": row[3],
    }


def delete_user(conn: SQLiteConnection, identity: UserIdentity) -> int:
    identity.validate_at_least_one()
    with conn.cursor() as cur:
        clauses = []
        params = []
        if identity.login_email:
            clauses.append("login_email = %s")
            params.append(identity.login_email)
        if identity.sso_id:
            clauses.append("sso_id = %s")
            params.append(identity.sso_id)
        cur.execute(f"DELETE FROM users WHERE {' OR '.join(clauses)}", params)
        deleted = cur.rowcount
    conn.commit()
    return deleted


def get_connection() -> SQLiteConnection:
    return connect_sqlite()


def get_or_create_user_id(conn: SQLiteConnection, identity: UserIdentity) -> int:
    identity.validate_at_least_one()
    with conn.cursor() as cur:
        clauses = []
        params = []
        if identity.sso_id:
            clauses.append("sso_id = %s")
            params.append(identity.sso_id)
        if identity.login_email:
            clauses.append("login_email = %s")
            params.append(identity.login_email)

        cur.execute(
            f"""
            SELECT user_id, login_email, sso_id
            FROM users
            WHERE {' OR '.join(clauses)}
            ORDER BY user_id
            LIMIT 1
            """,
            params,
        )
        row = cur.fetchone()

        if row:
            user_id, login_email, sso_id = row
            if identity.sso_id and sso_id and sso_id != identity.sso_id:
                raise ValueError("Existing user has different sso_id")
            if identity.login_email and login_email and login_email != identity.login_email:
                raise ValueError("Existing user has different login_email")

            if (identity.login_email and not login_email) or (identity.sso_id and not sso_id):
                cur.execute(
                    """
                    UPDATE users
                    SET login_email = COALESCE(login_email, %s),
                        sso_id = COALESCE(sso_id, %s)
                    WHERE user_id = %s
                    """,
                    (identity.login_email, identity.sso_id, user_id),
                )
            return user_id

        created = create_user(conn, identity)
        return created["user_id"]
