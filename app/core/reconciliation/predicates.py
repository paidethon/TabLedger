"""Disposition predicates for the reconciliation engine.

The legacy code hard-coded the statement owner's name; owner-specific
detection now goes through :class:`EngineConfig` (``owner_names`` and
``extra_internal_tokens``).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any, Mapping

from app.core.context import EngineConfig
from app.core.money import money
from app.core.parsers.accounts import GENERIC_INTERNAL_TOKENS
from app.core.parsers.common import compact_text

BANK_SOURCES = frozenset(("工商银行", "中国银行"))
PLATFORM_SOURCES = frozenset(("wx", "zfb"))

SOURCE_TAG = {
    "wx": "微信",
    "zfb": "支付宝",
    "工商银行": "工商银行",
    "中国银行": "中国银行",
}


def uid(record: Mapping[str, Any]) -> str:
    return f"{record['source']}:{record['source_id']}"


def is_zfb_refund(record: Mapping[str, Any]) -> bool:
    return record.get("source") == "zfb" and record.get("status") == "退款成功"


def is_wx_refund_event(record: Mapping[str, Any]) -> bool:
    return (
        record.get("source") == "wx"
        and record.get("flow") == "收入"
        and ("退款" in str(record.get("status", "")) or "退款" in str(record.get("raw_type", "")))
    )


def is_platform_refund_event(record: Mapping[str, Any]) -> bool:
    return is_zfb_refund(record) or is_wx_refund_event(record)


def withdrawal_fee(record: Mapping[str, Any]) -> Decimal:
    if record.get("source") != "wx" or record.get("raw_type") != "零钱提现":
        return Decimal("0.00")
    raw = record.get("raw") or {}
    remark = str(raw.get("备注", "")) if isinstance(raw, Mapping) else ""
    match = re.search(r"服务费\s*[¥￥]?\s*([0-9]+(?:\.[0-9]+)?)", remark)
    return money(match.group(1)) if match else Decimal("0.00")


def is_zfb_internal(record: Mapping[str, Any]) -> bool:
    if record.get("source") != "zfb":
        return False
    if record.get("raw_type") in {"投资理财", "账户存取"}:
        return True
    text = f"{record.get('merchant', '')}|{record.get('item', '')}"
    return "支付宝小荷包" in text


def is_bank_internal(record: Mapping[str, Any], config: EngineConfig) -> bool:
    if record.get("source") not in BANK_SOURCES:
        return False
    raw_type = str(record.get("raw_type", ""))
    if raw_type in {"理财", "金融付款", "充值", "数字人民币兑出"}:
        return True
    text = f"{record.get('merchant', '')}|{record.get('item', '')}"
    tokens = list(GENERIC_INTERNAL_TOKENS) + list(config.extra_internal_tokens)
    for name in config.owner_names:
        if name:
            tokens.append(f"支付宝-{name}")
            tokens.append(f"微信-{name}")
    if any(token in text for token in tokens):
        return True
    # A transfer explicitly involving the statement owner is a self movement.
    if raw_type in {"转账", "跨行转账"} and config.is_owner_text(record.get("merchant")):
        return True
    return False


def semantic_internal_income(record: Mapping[str, Any], config: EngineConfig) -> tuple[bool, str]:
    """余额宝/零钱通/提现到卡等本人资金搬运，不作为收入导入。"""
    if record.get("flow") != "收入" or record.get("disposition") != "排除-收入":
        return False, ""
    text = f"{record.get('merchant', '')} {record.get('item', '')} {record.get('raw_type', '')}"
    signals = [
        "支付宝小荷包-转出", "支付宝小荷包转出", "转出到银行卡", "微信零钱提现",
        "零钱通提现", "支付宝余额提现", "余额宝提现", "自助存款", "atm存款", "无卡存款",
    ]
    for signal in signals:
        if signal in text:
            return True, f"本人账户资金搬运：{signal}"
    if config.owner_names and config.is_owner_text(text) and any(
        k in text for k in ("转账", "转入", "提现", "汇入", "存款", "入账")
    ):
        return True, "本人账户间互转"
    return False, ""


def platform_event_direction(record: Mapping[str, Any]) -> int:
    if is_platform_refund_event(record):
        return 1
    if record.get("flow") == "收入":
        return 1
    if record.get("source") == "wx":
        raw_type = str(record.get("raw_type", ""))
        if raw_type == "零钱提现" or ("转出-到" in raw_type and ("银行" in raw_type or "工商" in raw_type)):
            return 1
    return -1


def bank_event_direction(record: Mapping[str, Any]) -> int:
    return 1 if money(record.get("signed_amount")) > 0 else -1


def platform_match_eligible(
    record: Mapping[str, Any],
    refund_originals: set[str],
    config: EngineConfig,
) -> bool:
    if record.get("source") not in PLATFORM_SOURCES:
        return False
    if money(record.get("amount")) <= 0:
        return False
    if not config.canonical_bank_account(record.get("account")):
        return False
    if record.get("status") == "交易关闭" and uid(record) not in refund_originals:
        return False
    return True


def merchant_similarity(platform: Mapping[str, Any], bank: Mapping[str, Any]) -> int:
    p_source = platform.get("source")
    bank_text = compact_text(
        f"{bank.get('merchant', '')}|{bank.get('item', '')}|{bank.get('raw_type', '')}"
    )
    score = 0
    if p_source == "zfb" and "支付宝" in bank_text:
        score += 5
    if p_source == "wx" and ("财付通" in bank_text or "微信" in bank_text):
        score += 5
    p_merchant = compact_text(platform.get("merchant"))
    b_merchant = compact_text(bank.get("merchant"))
    if p_merchant and b_merchant and (p_merchant in b_merchant or b_merchant in p_merchant):
        score += 8
    return score


def bank_has_platform_hint(record: Mapping[str, Any]) -> bool:
    text = f"{record.get('merchant', '')}|{record.get('item', '')}|{record.get('raw_type', '')}"
    return any(token in text for token in ("支付宝", "财付通", "微信零钱", "微信转账"))


def dedupe(items) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            output.append(clean)
    return output
