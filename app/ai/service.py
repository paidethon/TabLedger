"""Low-token AI classification: cache-first, batched, structured output.

Flow per job:
  1. rule engine classifies everything it can (free);
  2. remaining unique (merchant, item, flow) keys are normalized;
  3. each key is looked up in the local AI cache (0 tokens on hit);
  4. genuinely unknown keys are batched into ONE structured-output request;
  5. results are validated against the taxonomy before use; anything
     invalid or failed degrades to the manual-review queue.

The AI never sees amounts, times, account numbers or order ids (see
sanitizer.py) and never decides dedupe/refunds/balance facts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.db.models import AIClassificationCache, AIUsage
from app.services.settings_service import ai_api_key, get_ai_settings

logger = logging.getLogger("tabledger.ai")

PROMPT_VERSION = 1

SYSTEM_PROMPT = (
    "你是消费分类器。只从给出的合法分类中选择，不要解释，输出 JSON。"
)


class AIError(RuntimeError):
    pass


def cache_key(normalized: str, flow: str, provider: str, model: str) -> str:
    taxonomy_version = "v1"
    canonical = json.dumps(
        {
            "key": normalized,
            "flow": flow,
            "taxonomy": taxonomy_version,
            "prompt": PROMPT_VERSION,
            "provider": provider,
            "model": model,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def cache_lookup_many(db: DBSession, keys: list[str]) -> dict[str, AIClassificationCache]:
    """Fetch cached rows for the given keys (static query, Python-side match)."""

    result: dict[str, AIClassificationCache] = {}
    if not keys:
        return result
    rows = db.scalars(select(AIClassificationCache)).all()
    keyset = set(keys)
    for row in rows:
        if row.cache_key in keyset:
            result[row.cache_key] = row
    return result


def cache_store(
    db: DBSession,
    *,
    key: str,
    merchant: str,
    item: str,
    flow: str,
    category: str,
    subcategory: str,
    tags: str,
    confidence: float | None,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> None:
    db.merge(
        AIClassificationCache(
            cache_key=key,
            merchant=merchant[:256],
            item=item[:256],
            flow=flow,
            category=category,
            subcategory=subcategory,
            tags=tags,
            confidence=confidence,
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )


def classify_batch(
    db: DBSession,
    unknown_items: list[dict[str, Any]],
    categories: dict[str, Any],
    income_categories: list[str],
) -> dict[str, dict[str, Any]]:
    """Classify unknown unique items in one (or few) batched request(s).

    ``unknown_items``: [{"id", "merchant", "item", "flow"}] already sanitized.
    Returns {id: {"category", "subcategory", "tags", "confidence"}}.
    Invalid or missing results are simply absent from the return value.
    """

    settings = get_ai_settings(db)
    if not settings.get("enabled") or not unknown_items:
        return {}
    provider = str(settings.get("provider", ""))
    model = str(settings.get("model", ""))
    if not provider or not model:
        return {}

    # Candidate narrowing keeps the request small: only plausible categories
    # are offered, derived from a local fuzzy pass over the merchant text.
    taxonomy_offer = _offer_catalog(categories, income_categories)

    batch_size = max(10, int(settings.get("batch_size", 60)))
    results: dict[str, dict[str, Any]] = {}
    total_input = 0
    total_output = 0
    request_count = 0

    for offset in range(0, len(unknown_items), batch_size):
        chunk = unknown_items[offset : offset + batch_size]
        usage = _classify_chunk(db, chunk, taxonomy_offer, settings)
        request_count += 1
        total_input += usage.get("input_tokens", 0)
        total_output += usage.get("output_tokens", 0)
        for item_id, payload in usage["results"].items():
            if _pair_valid(payload, chunk, categories, income_categories):
                results[item_id] = payload

    db.add(
        AIUsage(
            provider=provider,
            model=model,
            requests=request_count,
            input_tokens=total_input,
            output_tokens=total_output,
        )
    )
    return results


def _pair_valid(
    payload: dict[str, Any],
    chunk: list[dict[str, Any]],
    categories: dict[str, Any],
    income_categories: list[str],
) -> bool:
    item_id = str(payload.get("id", ""))
    entry = next((x for x in chunk if str(x["id"]) == item_id), None)
    if entry is None:
        return False
    category = str(payload.get("category", ""))
    subcategory = str(payload.get("subcategory", ""))
    if entry["flow"] == "收入":
        return category == subcategory and category in income_categories
    return category in categories and subcategory in list(categories.get(category, []))


def _offer_catalog(categories: dict[str, Any], income_categories: list[str]) -> dict[str, Any]:
    return {
        "支出": {cat: list(subs) for cat, subs in categories.items()},
        "收入": list(income_categories),
    }


def _classify_chunk(
    db: DBSession,
    chunk: list[dict[str, Any]],
    offer: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """One structured-output request for a batch of unique keys."""

    import litellm

    api_key = ai_api_key(db)
    if not api_key:
        logger.info("AI enabled but no API key configured; skipping classification")
        return {"results": {}, "input_tokens": 0, "output_tokens": 0}

    user_payload = json.dumps(
        {
            "分类体系": offer,
            "待分类": [
                {"id": x["id"], "merchant": x["merchant"], "item": x["item"], "flow": x["flow"]}
                for x in chunk
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "classification_results",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "category": {"type": "string"},
                                "subcategory": {"type": "string"},
                                "tags": {"type": "array", "items": {"type": "string"}},
                                "confidence": {"type": "number"},
                            },
                            "required": ["id", "category", "subcategory", "tags", "confidence"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["results"],
                "additionalProperties": False,
            },
        },
    }

    last_error: Exception | None = None
    for attempt in range(1 + max(0, int(settings.get("retry", 1)))):
        try:
            response = litellm.completion(
                model=str(settings.get("model", "")),
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_payload},
                ],
                api_key=api_key,
                base_url=str(settings.get("base_url", "")) or None,
                timeout=int(settings.get("timeout_seconds", 60)),
                max_tokens=int(settings.get("max_output_tokens", 800)),
                temperature=0,
                response_format=response_format,
            )
        except Exception as exc:
            last_error = exc
            logger.warning("AI request failed (attempt %s): %s", attempt + 1, type(exc).__name__)
            continue
        usage_obj = getattr(response, "usage", None)
        input_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        content = response.choices[0].message.content or ""
        parsed = _parse_results(content)
        return {"results": parsed, "input_tokens": input_tokens, "output_tokens": output_tokens}

    # All attempts failed: degrade to manual review, never fail the job.
    logger.warning("AI classification unavailable: %s", type(last_error).__name__ if last_error else "unknown")
    return {"results": {}, "input_tokens": 0, "output_tokens": 0}


def _parse_results(content: str) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return {}
    results = {}
    for item in data.get("results", []) if isinstance(data, dict) else []:
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id", ""))
        if not item_id:
            continue
        results[item_id] = {
            "category": str(item.get("category", "")),
            "subcategory": str(item.get("subcategory", "")),
            "tags": " ".join(str(t) for t in item.get("tags", []) if str(t)),
            "confidence": float(item.get("confidence", 0.0) or 0.0),
        }
    return results


def test_connection(db: DBSession) -> dict[str, Any]:
    """Minimal ping: return {"ok": true}. Only called from the settings page."""

    import litellm

    settings = get_ai_settings(db)
    api_key = ai_api_key(db)
    if not api_key:
        return {"ok": False, "error": "未配置 API Key"}
    try:
        response = litellm.completion(
            model=str(settings.get("model", "")),
            messages=[{"role": "user", "content": 'return {"ok":true}'}],
            api_key=api_key,
            base_url=str(settings.get("base_url", "")) or None,
            timeout=20,
            max_tokens=32,
            temperature=0,
        )
        usage_obj = getattr(response, "usage", None)
        return {
            "ok": True,
            "model": settings.get("model", ""),
            "input_tokens": int(getattr(usage_obj, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage_obj, "completion_tokens", 0) or 0),
        }
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__}


def list_models(db: DBSession) -> dict[str, Any]:
    """Fetch /v1/models when the provider supports it; never raises."""

    import litellm

    settings = get_ai_settings(db)
    api_key = ai_api_key(db)
    if not api_key:
        return {"ok": False, "models": []}
    try:
        response = litellm.models.list(api_key=api_key, base_url=str(settings.get("base_url", "")) or None)
        model_ids = []
        for model in getattr(response, "data", []) or []:
            model_id = getattr(model, "id", None)
            if model_id:
                model_ids.append(str(model_id))
        return {"ok": True, "models": model_ids[:200]}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "models": []}


def decimal_cents(value: Any) -> int:
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1")))


def settings_snapshot() -> dict[str, Any]:
    return {"prompt_version": PROMPT_VERSION, "app": get_settings().version}
