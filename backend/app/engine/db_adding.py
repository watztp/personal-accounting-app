from __future__ import annotations

from typing import Any

from backend.app.db.db_ops import add_receipt_payload, ensure_schema, get_connection
from backend.app.engine.entity_matching import finalize_review_entities


def add_receipt_to_db(
    reviewed_payload: dict[str, Any],
    *,
    user_id: int,
    create_item_rows: bool = True,
    match_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Insert reviewed payload into DB.

    Args:
        reviewed_payload: Finalized receipt payload.
        user_id: User id.
        create_item_rows: Whether to create item rows.

    Returns:
        DB result dict containing created entities.
    """
    with get_connection() as conn:
        ensure_schema(conn)
        store, item_ids = finalize_review_entities(
            conn,
            reviewed_payload,
            context=match_context,
            user_id=user_id,
        )
        db_result = add_receipt_payload(
            conn,
            reviewed_payload,
            create_item_rows=create_item_rows,
            user_id=user_id,
            store_override=store,
            item_ids_override=item_ids,
        )
        conn.commit()
    return db_result
