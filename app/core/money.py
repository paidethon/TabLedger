"""Money helpers. All amounts are computed with Decimal and stored as integer cents."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from typing import Any

CENT = Decimal("0.01")
HONG_KONG_TZ = timezone(timedelta(hours=8))


def money(value: Any) -> Decimal:
    return Decimal(str(value or 0)).quantize(CENT, rounding=ROUND_HALF_UP)


def cents(value: Any) -> int:
    return int(money(value) * 100)


def cents_to_money(value: int) -> Decimal:
    return (Decimal(value) / Decimal(100)).quantize(CENT)


def as_float(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


@lru_cache(maxsize=None)
def parse_dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def now_hong_kong() -> str:
    return datetime.now(HONG_KONG_TZ).isoformat(timespec="seconds")


def amount_tier(amount: float) -> str:
    if amount < 20:
        return "小额"
    if amount <= 200:
        return "中额"
    return "大额"
