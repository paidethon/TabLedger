"""WeChat (微信) XLSX bill parser."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from app.core.context import EngineConfig
from app.core.money import CENT
from app.core.parsers.accounts import (
    clean_placeholder,
    clean_text,
    normalize_flow,
    normalize_payment_method,
    normalize_platform_account,
)
from app.core.parsers.xlsx import parse_excel_datetime, read_first_xlsx_sheet

EXPECTED_HEADERS = [
    "交易时间",
    "交易类型",
    "交易对方",
    "商品",
    "收/支",
    "金额(元)",
    "支付方式",
    "当前状态",
    "交易单号",
    "商户单号",
    "备注",
]

HEADER_ROW = 18
FIRST_DATA_ROW = 19


def parse_bill_money(value, *, field: str, location: str) -> Decimal:
    text = clean_text(value).replace(",", "").replace("￥", "").replace("¥", "")
    try:
        result = Decimal(text).quantize(CENT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid {field} at {location}: {value!r}") from exc
    if result < 0:
        raise ValueError(f"Negative {field} at {location}: {value!r}")
    return result


def json_money(value: Decimal) -> float:
    return float(value.quantize(CENT, rounding=ROUND_HALF_UP))


def signed_money(flow: str, amount: Decimal) -> float:
    """Use bookkeeping flow semantics: expense -, income +, neutral 0."""
    if amount == 0:
        return 0.0
    if flow == "支出":
        return json_money(-amount)
    if flow == "收入":
        return json_money(amount)
    return 0.0


def parse_declared_summary(lines, *, neutral_label: str) -> dict[str, Any]:
    import re

    text = "\n".join(lines)
    result: dict[str, Any] = {}
    labels = {"收入": "收入", "支出": "支出", neutral_label: "不计收支"}
    for label, flow in labels.items():
        pattern = rf"{re.escape(label)}：\s*(\d+)笔\s+([\d.]+)元"
        match = re.search(pattern, text)
        if not match:
            raise ValueError(f"Could not parse declared {label} summary")
        result[flow] = {"count": int(match.group(1)), "amount": match.group(2)}
    total = re.search(r"共\s*(\d+)笔记录", text)
    if not total:
        raise ValueError("Could not parse declared record count (共N笔记录)")
    result["count"] = int(total.group(1))
    return result


def extract_wx(path_or_bytes, config: EngineConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows, _ = read_first_xlsx_sheet(path_or_bytes)
    if HEADER_ROW not in rows:
        raise ValueError("WeChat header row 18 is missing")
    headers = [clean_text(value) for value in rows[HEADER_ROW]]
    if headers[: len(EXPECTED_HEADERS)] != EXPECTED_HEADERS:
        raise ValueError(f"Unexpected WeChat columns: {headers!r}")

    transactions: list[dict[str, Any]] = []
    for row_number in sorted(number for number in rows if number >= FIRST_DATA_ROW):
        values = rows[row_number] + [""] * max(0, len(EXPECTED_HEADERS) - len(rows[row_number]))
        values = values[: len(EXPECTED_HEADERS)]
        if not any(clean_text(value) for value in values):
            continue
        raw = dict(zip(EXPECTED_HEADERS, values, strict=False))
        location = f"Sheet1!A{row_number}:K{row_number}"
        flags: list[str] = []

        flow, flow_flags = normalize_flow("wx", raw["收/支"])
        flags.extend(flow_flags)
        amount = parse_bill_money(raw["金额(元)"], field="amount", location=location)
        if amount == 0:
            flags.append("zero_amount_source_record")
        payment_method, payment_flags = normalize_payment_method(raw["支付方式"])
        flags.extend(payment_flags)
        status = clean_text(raw["当前状态"])
        raw_type = clean_text(raw["交易类型"])
        account, account_flags = normalize_platform_account(
            config,
            "wx",
            payment_method,
            status=status,
            raw_type=raw_type,
            flow=flow,
        )
        flags.extend(account_flags)

        raw_source_id = str(raw["交易单号"])
        source_id = clean_text(raw_source_id)
        if source_id != raw_source_id:
            flags.append("source_id_whitespace_trimmed")
        raw_merchant_order_id = str(raw["商户单号"])
        merchant_order_id = clean_placeholder(raw_merchant_order_id)
        if merchant_order_id != raw_merchant_order_id:
            flags.append("merchant_order_id_whitespace_or_placeholder_cleaned")

        merchant = clean_placeholder(raw["交易对方"])
        item = clean_placeholder(raw["商品"])
        record = {
            "source": "wx",
            "source_row": row_number,
            "source_id": source_id,
            "datetime": parse_excel_datetime(raw["交易时间"], location=location),
            "flow": flow,
            "amount": json_money(amount),
            "signed_amount": signed_money(flow, amount),
            "balance": None,
            "status": status,
            "merchant": merchant,
            "item": item,
            "payment_method": payment_method,
            "account": account,
            "raw_type": raw_type,
            "raw_counterparty": clean_text(raw["交易对方"]),
            "raw_account": "",
            "merchant_order_id": merchant_order_id,
            "raw_location": location,
            "parse_flags": sorted(set(flags)),
            "raw": raw,
        }
        transactions.append(record)

    declared = parse_declared_summary(
        (rows[number][0] for number in (7, 8, 9, 10)), neutral_label="中性交易"
    )
    metadata = {
        "encoding": "OOXML UTF-8",
        "sheet": "Sheet1",
        "header_row": HEADER_ROW,
        "first_data_row": FIRST_DATA_ROW,
        "declared": declared,
    }
    return transactions, metadata
