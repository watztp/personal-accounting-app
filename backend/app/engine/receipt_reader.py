from __future__ import annotations

import importlib
import inspect
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "backend" / "config" / "receipt_reader.json"


@dataclass(frozen=True)
class ReceiptReaderConfig:
    type: str
    http: dict[str, Any] = field(default_factory=dict)
    callable: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


def config_path() -> Path:
    configured = os.getenv("RECEIPT_READER_CONFIG", "").strip()
    path = Path(configured) if configured else DEFAULT_CONFIG_PATH
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def load_config() -> ReceiptReaderConfig:
    path = config_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"receipt reader config not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid receipt reader config JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("receipt reader config must be a JSON object")
    reader_type = str(payload.get("type") or "").strip()
    if reader_type not in {"http_donut", "python_callable"}:
        raise ValueError("receipt reader type must be 'http_donut' or 'python_callable'")
    return ReceiptReaderConfig(
        type=reader_type,
        http=payload.get("http") if isinstance(payload.get("http"), dict) else {},
        callable=str(payload.get("callable") or "").strip() or None,
        options=payload.get("options") if isinstance(payload.get("options"), dict) else {},
    )


async def read_receipt(image_bytes: bytes) -> dict[str, Any]:
    config = load_config()
    if config.type == "http_donut":
        return await _call_http_donut(image_bytes, config.http)
    return await _call_python_pipeline(image_bytes, config)


async def _call_http_donut(image_bytes: bytes, settings: dict[str, Any]) -> dict[str, Any]:
    url = str(settings.get("url") or "").strip()
    if not url:
        raise ValueError("receipt reader config requires http.url")
    field = str(settings.get("file_field") or "file").strip()
    timeout_seconds = float(settings.get("timeout_seconds", 60))
    connect_seconds = float(settings.get("connect_timeout_seconds", 10))
    files = {field: ("upload.jpg", image_bytes, "image/jpeg")}
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=connect_seconds)) as client:
        response = await client.post(url, files=files)
    response.raise_for_status()
    try:
        payload = response.json()
    except ValueError as exc:
        raise ValueError("receipt reader must return a JSON object") from exc
    if not isinstance(payload, dict):
        raise ValueError("receipt reader must return a JSON object")
    return payload


async def _call_python_pipeline(image_bytes: bytes, config: ReceiptReaderConfig) -> dict[str, Any]:
    if not config.callable or ":" not in config.callable:
        raise ValueError("python_callable requires 'callable': 'package.module:function'")
    module_name, function_name = config.callable.split(":", 1)
    function: Callable[..., Any] = getattr(importlib.import_module(module_name), function_name)
    result: Any | Awaitable[Any] = function(image_bytes=image_bytes, options=config.options)
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, dict):
        raise ValueError("custom receipt pipeline must return a dict")
    return result
