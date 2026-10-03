"""Alipay (支付宝) CSV bill parser (GB18030 encoded)."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Any

from app.core.context import EngineConfig
from app.core.parsers.accounts import (
    clean_placeholder,
    clean_text,
    normalize_flow,
    normalize_payment_method,
    normalize_platform_account,
)
from app.core.parsers.wechat import json_money, parse_bill_money, parse_declared_summary, signed_money

EXPECTED_HEADERS = [
    "交易时间",
    "交易分类",
    "交易对方",
    "对方账号",
    "商品说明",
    "收/支",
    "金额",
    "收/付款方式",
    "交易状态",
    "交易订单号",
    "商家订单号",
    "备注",
]


def decode_alipay_csv(data: bytes) -> str:
    try:
        return data.decode("gb18030")
    except UnicodeDecodeError as exc:
        raise ValueError("Alipay CSV is not valid GB18030") from exc


def extract_zfb(path_or_bytes, config: EngineConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if isinstance(path_or_bytes, (bytes, bytearray)):
        text = decode_alipay_csv(bytes(path_or_bytes))
        location_name = "alipay.csv"
    else:
        text = decode_alipay_csv(open(path_or_bytes, "rb").read())
        location_name = path_or_bytes.name

    reader = csv.reader(io.StringIO(text, newline=""))
    header: list[str] | None = None
    header_line = 0
    preamble: list[str] = []
    transactions: list[dict[str, Any]] = []

    for row in reader:
        line_number = reader.line_num
        if header is None:
            if row and clean_text(row[0]) == "交易时间":
                header = [clean_text(value) for value in row]
                header_line = line_number
                if header[: len(EXPECTED_HEADERS)] != EXPECTED_HEADERS:
                    raise ValueError(f"Unexpected Alipay columns: {header!r}")
            else:
                preamble.append(",".join(row))
            continue

        if not row or not clean_text(row[0]):
            continue
        padded = row + [""] * max(0, len(EXPECTED_HEADERS) - len(row))
        values = padded[: len(EXPECTED_HEADERS)]
        raw = dict(zip(EXPECTED_HEADERS, values, strict=False))
        location = f"{location_name}#line={line_number}"
        flags: list[str] = []

        flow, flow_flags = normalize_flow("zfb", raw["收/支"])
        flags.extend(flow_flags)
        amount = parse_bill_money(raw["金额"], field="amount", location=location)
        if amount == 0:
            flags.append("zero_amount_source_record")
        payment_method, payment_flags = normalize_payment_method(raw["收/付款方式"])
        flags.extend(payment_flags)
        status = clean_text(raw["交易状态"])
        raw_type = clean_text(raw["交易分类"])
        account, account_flags = normalize_platform_account(
            config,
            "zfb",
            payment_method,
            status=status,
            raw_type=raw_type,
            flow=flow,
        )
        flags.extend(account_flags)

        raw_source_id = str(raw["交易订单号"])
        source_id = raw_source_id.strip()
        if source_id != raw_source_id:
            flags.append("source_id_whitespace_trimmed")
        raw_merchant_order_id = str(raw["商家订单号"])
        merchant_order_id = clean_placeholder(raw_merchant_order_id)
        if merchant_order_id != raw_merchant_order_id:
            flags.append("merchant_order_id_whitespace_or_placeholder_cleaned")

        merchant = clean_placeholder(raw["交易对方"])
        item = clean_placeholder(raw["商品说明"])
        try:
            parsed_datetime = datetime.strptime(
                clean_text(raw["交易时间"]), "%Y-%m-%d %H:%M:%S"
            ).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(
                f"Invalid Alipay datetime at {location}: {raw['交易时间']!r}"
            ) from exc

        record = {
            "source": "zfb",
            "source_row": line_number,
            "source_id": source_id,
            "datetime": parsed_datetime,
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
            "raw_account": clean_placeholder(raw["对方账号"]),
            "merchant_order_id": merchant_order_id,
            "raw_location": location,
            "parse_flags": sorted(set(flags)),
            "raw": raw,
        }
        transactions.append(record)

    if header is None:
        raise ValueError("Alipay CSV header was not found")
    declared = parse_declared_summary(preamble, neutral_label="不计收支")
    metadata = {
        "encoding": "GB18030",
        "header_row": header_line,
        "first_data_row": header_line + 1,
        "declared": declared,
    }
    return transactions, metadata
