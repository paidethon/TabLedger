"""Privacy sanitizer: build the minimal AI payload from transactions.

Need-to-know only: merchant, item, flow, coarse amount tier.  Bank cards,
phone numbers, account numbers, order ids, names, balances and exact
amounts/times never leave the machine.
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

# Stripped even from the merchant/item text before sending.
_PII_PATTERNS = [
    re.compile(r"\b1[3-9]\d{9}\b"),  # CN mobile numbers
    re.compile(r"\b\d{15}(\d{2}[0-9Xx])?\b"),  # ID / long numbers
    re.compile(r"\b\d{9,25}\b"),  # order/transaction ids & card numbers
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),  # emails
]


def sanitize_text(value: str) -> str:
    text = str(value or "")
    for pattern in _PII_PATTERNS:
        text = pattern.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def amount_tier(cents: int) -> str:
    if cents < 2000:
        return "small"
    if cents <= 20000:
        return "medium"
    return "large"


def build_ai_payload(unmatched_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the per-unique-merchant payload entries.

    Each unmatched item: {"id", "merchant", "item", "flow"} (+ tier).
    """

    payload: list[dict[str, Any]] = []
    for entry in unmatched_items:
        merchant = sanitize_text(entry.get("merchant", ""))
        item = sanitize_text(entry.get("item", ""))
        payload.append(
            {
                "id": entry.get("id", ""),
                "merchant": merchant,
                "item": item,
                "flow": entry.get("flow", "支出"),
            }
        )
    return [p for p in payload if p["merchant"] or p["item"]]


def normalize_key(merchant: str, item: str) -> str:
    """Canonical matching key: lowercase, whitespace/order-number stripped."""

    return sanitize_text(f"{merchant} {item}".lower()).strip()


def decimal_to_cents(value: Any) -> int:
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1")))
