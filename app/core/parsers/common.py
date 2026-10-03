"""Shared parser primitives: schema, text normalisation, record construction."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.core.context import EngineConfig
from app.core.money import HONG_KONG_TZ, CENT

SCHEMA_FIELDS = [
    "source",
    "source_row",
    "source_id",
    "datetime",
    "flow",
    "amount",
    "signed_amount",
    "balance",
    "status",
    "merchant",
    "item",
    "payment_method",
    "account",
    "raw_type",
    "raw_counterparty",
    "raw_account",
    "raw_location",
    "parse_flags",
]

PLATFORM_FIELDS = [
    *SCHEMA_FIELDS[:16],
    "merchant_order_id",
    *SCHEMA_FIELDS[16:],
    "raw",
]


PLACEHOLDER_RE = re.compile(r"^[\s\-_—]+$")


def normalize_text(value: Any) -> str:
    """Join visual line wraps while retaining meaningful in-line spaces."""

    if value is None:
        return ""
    lines = []
    for line in str(value).replace("\r", "\n").split("\n"):
        cleaned = re.sub(r"[\t\f\v ]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
    result = "".join(lines).strip()
    if not result or PLACEHOLDER_RE.fullmatch(result):
        return ""
    return result


def normalize_account(value: Any) -> str:
    text = normalize_text(value)
    if not text:
        return ""
    return re.sub(r"\s+", "", text)


def compact_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def parse_money(value: Any, *, position: str) -> Decimal:
    text = str(value or "").replace(",", "").replace("￥", "").replace("¥", "").strip()
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid money value at {position}: {value!r}") from exc
    return result.quantize(CENT, rounding=ROUND_HALF_UP)


def decimal_number(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def file_sha256(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_source_id(
    prefix: str,
    account: str,
    date_time: str,
    signed_amount: Decimal,
    balance: Decimal,
    raw_type: str,
    raw_counterparty: str,
    raw_account: str,
) -> str:
    parts = [
        account,
        date_time,
        f"{signed_amount:.2f}",
        f"{balance:.2f}",
        raw_type,
        raw_counterparty,
        raw_account,
    ]
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:24]
    return f"{prefix}:{digest}"


def make_record(
    config: EngineConfig,
    *,
    source: str,
    source_row: int,
    date_time: datetime,
    signed_amount: Decimal,
    balance: Decimal,
    merchant: str,
    item: str,
    payment_method: str,
    raw_type: str,
    raw_counterparty: str,
    raw_account: str,
    raw_location: str,
    parse_flags: list[str],
) -> dict[str, Any]:
    date_text = date_time.strftime("%Y-%m-%d %H:%M:%S")
    flow = "支出" if signed_amount < 0 else "收入"
    amount = abs(signed_amount)
    if signed_amount == 0:
        raise ValueError(f"Zero-amount bank transaction at {source} row {source_row}")
    merchant = merchant or raw_counterparty or item or raw_type or source
    item = item or raw_type or merchant
    payment_method = payment_method or "银行账户"
    account = config.source_account(source)

    record = {
        "source": source,
        "source_row": source_row,
        "source_id": make_source_id(
            config.source_id_prefix(source),
            account,
            date_text,
            signed_amount,
            balance,
            raw_type,
            raw_counterparty,
            raw_account,
        ),
        "datetime": date_text,
        "flow": flow,
        "amount": decimal_number(amount),
        "signed_amount": decimal_number(signed_amount),
        "balance": decimal_number(balance),
        "status": "已入账",
        "merchant": merchant,
        "item": item,
        "payment_method": payment_method,
        "account": account,
        "raw_type": raw_type,
        "raw_counterparty": raw_counterparty,
        "raw_account": raw_account,
        "raw_location": raw_location,
        "parse_flags": sorted(set(parse_flags)),
    }
    if list(record) != SCHEMA_FIELDS:
        raise AssertionError("Transaction schema order drifted")
    return record


def validate_schema(records: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids = [record["source_id"] for record in records]
    if len(ids) != len(set(ids)):
        errors.append(f"source_id is not unique: {len(ids) - len(set(ids))} duplicate(s)")
    for index, record in enumerate(records, start=1):
        if list(record) != SCHEMA_FIELDS:
            errors.append(f"record {index}: schema fields differ")
        if record["flow"] not in {"支出", "收入"}:
            errors.append(f"record {index}: invalid flow {record['flow']!r}")
        if Decimal(str(record["amount"])) <= 0:
            errors.append(f"record {index}: amount must be positive")
        signed = Decimal(str(record["signed_amount"]))
        if record["flow"] == "支出" and signed >= 0:
            errors.append(f"record {index}: expense signed_amount is not negative")
        if record["flow"] == "收入" and signed <= 0:
            errors.append(f"record {index}: income signed_amount is not positive")
        for key in ("source", "source_id", "datetime", "status", "merchant", "item", "payment_method", "account", "raw_type"):
            if record[key] in (None, ""):
                errors.append(f"record {index}: required field {key!r} is blank")
    return errors


def is_monotonic(records: list[dict[str, Any]]) -> bool:
    values = [record["datetime"] for record in records]
    return values == sorted(values)


def decimal_from_record(record: dict[str, Any], key: str) -> Decimal:
    return Decimal(str(record[key])).quantize(Decimal("0.01"))


def bank_datetime(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=HONG_KONG_TZ)
