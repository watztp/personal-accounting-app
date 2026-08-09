from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import replace
from pathlib import Path
from typing import Any

from category_helper.llm import GenerationOptions, LLMProvider, get_provider

# Single source of truth for the classification payload. It is embedded in the
# prompt *and* handed to providers that can constrain output at the API level.
#
# Kept inside the subset every provider accepts: `additionalProperties: false`,
# all keys listed in `required`, nullable fields written as anyOf (not
# `"type": [...]`), and no numeric/length constraints.
CATEGORY_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "item_name": {"type": "string"},
        "product_type": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "Short noun phrase describing the product.",
        },
        "category": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "One allowed category, or null when uncertain.",
        },
        "subcategory": {
            "anyOf": [{"type": "string"}, {"type": "null"}],
            "description": "One allowed subcategory under that category, or null.",
        },
        "confidence": {"type": "number", "description": "0.0 to 1.0."},
        "needs_review": {"type": "boolean"},
        "reason": {"type": "string", "description": "At most one short sentence."},
        "evidence_urls": {
            "type": "array",
            "items": {"type": "string"},
            "description": "0-3 URLs, only if web information was actually used.",
        },
    },
    "required": [
        "item_name",
        "product_type",
        "category",
        "subcategory",
        "confidence",
        "needs_review",
        "reason",
        "evidence_urls",
    ],
    "additionalProperties": False,
}

# Older prompts used different key names per provider; accept both so responses
# from an unconstrained model still parse.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "category": ("main_category",),
    "subcategory": ("sub_category",),
    "product_type": ("productType",),
    "needs_review": ("needsReview",),
    "evidence_urls": ("evidenceUrls", "sources"),
}

DEFAULT_CATEGORIES_PATH = Path(__file__).resolve().with_name("item_cat.json")


def normalize_item_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def load_categories(path: str | Path) -> dict:
    if not Path(path).exists():
        raise FileNotFoundError(f"Missing categories file: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("item_cat.json must be a JSON object at the top level")
    return data


def build_allowed_text(cat_tree: dict) -> str:
    lines = []
    for cat, value in cat_tree.items():
        subs = value.get("sub_categories") or []
        lines.append(f"- {cat}: {', '.join(subs)}")
    return "\n".join(lines)


def build_prompt(item_name: str, cat_tree: dict, store_name: str | None = None) -> str:
    """One prompt for every provider."""
    allowed_text = build_allowed_text(cat_tree)
    schema_text = json.dumps(CATEGORY_JSON_SCHEMA, ensure_ascii=False, indent=2)
    item = normalize_item_name(item_name)
    store = normalize_item_name(store_name or "")
    store_context = f"\nStore: {store}" if store else ""

    return f"""
You are a strict product categorizer for Japanese receipts.

You MUST choose category/subcategory ONLY from the allowed list below.
You MAY use web search if available to disambiguate.

Allowed categories and subcategories:
{allowed_text}

Return ONLY valid JSON (no markdown, no extra text) matching this JSON Schema:
{schema_text}

Rules:
- Keep it SHORT.
- If uncertain: category=null, subcategory=null, confidence<=0.4, needs_review=true.
- evidence_urls: include up to 3 URLs only if you actually used web info.

Item: {item}{store_context}
""".strip()


def prepare_prompt(
    item_name: str,
    categories_path: str | Path | None = None,
    store_name: str | None = None,
) -> tuple[str, str, dict]:
    path = Path(categories_path) if categories_path else DEFAULT_CATEGORIES_PATH
    cat_tree = load_categories(path)
    prompt = build_prompt(item_name, cat_tree, store_name)
    normalized_item = normalize_item_name(item_name)
    return prompt, normalized_item, cat_tree


def classify_item(
    item_name: str,
    *,
    provider: LLMProvider | str,
    options: GenerationOptions | None = None,
    categories_path: str | Path | None = None,
    store_name: str | None = None,
) -> dict:
    """Classify one item with any provider.

    `provider` accepts a ready LLMProvider (preferred - the SDK client is reused
    across calls) or a provider name for one-off use.
    """
    llm = get_provider(provider) if isinstance(provider, str) else provider
    opts = options or GenerationOptions()

    prompt, normalized_item, cat_tree = prepare_prompt(
        item_name,
        categories_path,
        store_name,
    )
    if opts.json_schema is None:
        opts = replace(opts, json_schema=CATEGORY_JSON_SCHEMA)

    result = llm.generate(prompt, opts)
    if result.refused:
        return _unresolved(
            normalized_item,
            f"Provider refused the request ({result.refusal_reason}).",
        )
    return parse_model_output(result.text, normalized_item, cat_tree)


def parse_model_output(raw: str, item_name: str, cat_tree: dict) -> dict:
    try:
        result = json.loads(raw)
    except Exception:
        return _unresolved(item_name, "Model output was not valid JSON.", raw=raw)

    if not isinstance(result, dict):
        return _unresolved(item_name, "Model output was not a JSON object.", raw=raw)

    result = _normalize_result_fields(result, item_name)
    return validate_result(result, cat_tree)


def validate_result(result: dict, cat_tree: dict) -> dict:
    result["confidence"] = _clamp_confidence(result.get("confidence", 0.0))

    category = result.get("category")
    if not isinstance(category, str) or not category.strip():
        category = None
    subcategory = result.get("subcategory")
    if not isinstance(subcategory, str) or not subcategory.strip():
        subcategory = None

    result["category"] = category
    result["subcategory"] = subcategory

    # If no category or low certainty, force review.
    if category is None:
        result["subcategory"] = None
        result["needs_review"] = True
        result["confidence"] = min(result["confidence"], 0.4)
        if not result.get("reason"):
            result["reason"] = "Model could not confidently classify this item."
        return result

    if category not in cat_tree:
        result["category"] = None
        result["subcategory"] = None
        result["needs_review"] = True
        result["confidence"] = min(result["confidence"], 0.4)
        result["reason"] = "Category not in allowed set."
        return result

    allowed_subs = set(cat_tree[category].get("sub_categories") or [])
    if subcategory not in allowed_subs:
        result["subcategory"] = None
        result["needs_review"] = True
        result["confidence"] = min(result["confidence"], 0.4)
        result["reason"] = "Subcategory not valid for selected category."
        return result

    if "needs_review" not in result:
        result["needs_review"] = False

    return result


def _unresolved(item_name: str, reason: str, raw: str | None = None) -> dict:
    result = {
        "item_name": item_name,
        "product_type": None,
        "category": None,
        "subcategory": None,
        "confidence": 0.0,
        "needs_review": True,
        "reason": reason,
        "evidence_urls": [],
    }
    if raw:
        result["raw"] = raw[:400]
    return result


def _normalize_result_fields(result: dict, item_name: str) -> dict:
    normalized = dict(result)

    for canonical, aliases in _FIELD_ALIASES.items():
        if normalized.get(canonical) in (None, ""):
            for alias in aliases:
                if normalized.get(alias) not in (None, ""):
                    normalized[canonical] = normalized[alias]
                    break

    if not normalized.get("item_name"):
        normalized["item_name"] = item_name
    normalized.setdefault("product_type", None)
    normalized.setdefault("category", None)
    normalized.setdefault("subcategory", None)
    normalized.setdefault("confidence", 0.0)
    normalized.setdefault("needs_review", False)
    normalized.setdefault("reason", "")

    normalized["evidence_urls"] = _normalize_evidence_urls(
        normalized.get("evidence_urls")
    )

    return normalized


def _normalize_evidence_urls(value: Any) -> list[str]:
    if not value or not isinstance(value, list):
        return []

    urls: list[str] = []
    for item in value:
        if isinstance(item, str):
            text = item.strip()
            if text:
                urls.append(text)

    return urls[:3]


def _clamp_confidence(value: Any) -> float:
    try:
        numeric = float(value)
    except Exception:
        return 0.0
    return max(0.0, min(1.0, numeric))
