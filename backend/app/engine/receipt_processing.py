from __future__ import annotations

import asyncio
from typing import Any

from backend.app.core.img_auth import verify_and_reencode
from backend.app.engine.postprocess import build_review_payload
from backend.app.engine.receipt_reader import read_receipt
from backend.app.engine.entity_matching import resolve_review_entities
_INFERENCE_METADATA_FIELDS = {
    "model_id",
    "model_version",
    "model_name",
    "model_type",
    "model_vendor",
}


def _without_inference_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key not in _INFERENCE_METADATA_FIELDS}


async def call_receipt_model(image_bytes: bytes) -> dict[str, Any]:
    """Call receipt model inference API.

    Args:
        image_bytes: Raw image bytes.

    Returns:
        Model output as dict.
    """
    processed_bytes = await asyncio.to_thread(verify_and_reencode, image_bytes)

    payload = await read_receipt(processed_bytes)
    return _without_inference_metadata(payload)


def postprocess_model_output(
    model_output: dict[str, Any],
    *,
    user_id: str | None,
) -> dict[str, Any] | None:
    """Build review payload from model output when possible.

    Args:
        model_output: Raw model output dict.
        user_id: User id as string or None.

    Returns:
        Review payload dict or None if not enough info.
    """
    if not user_id:
        return None

    if "receipt_main_info" in model_output and "items" in model_output and "summary" in model_output:
        return model_output

    if "text" in model_output or "raw" in model_output:
        return build_review_payload(model_output, user_id=user_id)

    return None


async def process_receipt_upload(
    image_bytes: bytes,
    *,
    user_id: str | None,
) -> dict[str, Any]:
    """Process receipt upload: model call + optional postprocess.

    Args:
        image_bytes: Raw image bytes.
        user_id: User id as string or None.

    Returns:
        Processing result dict with model_output and review_payload.
    """
    model_output = await call_receipt_model(image_bytes)
    review_payload = postprocess_model_output(model_output, user_id=user_id)
    match_context: dict[str, Any] | None = None
    if review_payload is not None and user_id is not None:
        review_payload, match_context = await asyncio.to_thread(
            resolve_review_entities,
            review_payload,
            user_id=int(user_id),
        )

    return {
        "status": "processed",
        "model_output": model_output,
        "review_payload": review_payload,
        "match_context": match_context,
        "user_id": int(user_id) if user_id is not None else None,
    }
