"""Apply classification rules to a reconciled ledger and build Yimu import rows."""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.core.classification.rules import (
    EXPENSE_DISPOSITIONS,
    INCOME_DISPOSITIONS,
    NET_AMOUNT_DISPOSITIONS,
    SOURCE_DISPLAY,
    match_rule,
    validate_pair,
)
from app.core.context import EngineConfig
from app.core.money import amount_tier
from app.core.reconciliation.predicates import semantic_internal_income


def _quantize(value: Any) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def classify_record(
    merchant: str,
    item: str,
    direction: str,
    rules: list[dict[str, str]],
    overrides: Mapping[tuple[str, str], Mapping[str, str]],
    categories: Mapping[str, Any] | None = None,
) -> tuple[str, str, list[str], str]:
    """Resolve (category, subcategory, extra_tags, basis) for one record.

    A matched pair that is invalid for this direction (e.g. an expense-only
    dictionary entry applied to a refund income row) falls through to the
    review queue instead of blocking the export.
    """

    rule = match_rule(rules, direction, merchant, item)
    if rule:
        pair_ok = True
        if categories is not None:
            pair_ok = validate_pair(direction, rule["类别"], rule["子类"], categories)
        if pair_ok:
            extra_tags = [t for t in rule["标签"].split() if t]
            return rule["类别"], rule["子类"], extra_tags, f"分类词典：{rule['键']}"
    override = overrides.get((direction, merchant))
    if override:
        extra_tags = [t for t in (override.get("标签") or override.get("tags", "") or "").split() if t]
        return (
            override.get("类别") or override.get("category", ""),
            override.get("子类") or override.get("subcategory", ""),
            extra_tags,
            "当期分类",
        )
    return "", "", [], "待分类"


def build_import_rows(
    config: EngineConfig,
    ledger: Mapping[str, Any],
    categories: Mapping[str, Any],
    rules: list[dict[str, str]],
    overrides: Mapping[tuple[str, str], Mapping[str, str]] | None = None,
):
    """Build the 9-column Yimu rows from a reconciled ledger.

    Returns (rows, unmatched) where ``unmatched`` groups records that still
    need classification (for AI fallback and the review queue), keyed by
    (direction, merchant-or-item).
    """

    overrides = overrides or {}
    rows: list[dict[str, Any]] = []
    unmatched: dict[tuple[str, str], dict[str, Any]] = {}
    index = 0
    for record in sorted(ledger["all_records"], key=lambda r: (r["datetime"], r["source"])):
        disposition = str(record.get("disposition", ""))
        if disposition in INCOME_DISPOSITIONS:
            internal, _ = semantic_internal_income(record, config)
            if internal:
                continue
            direction = "收入"
            amount = _quantize(record["amount"])
        elif disposition in EXPENSE_DISPOSITIONS:
            direction = "支出"
            base = record["net_amount"] if disposition in NET_AMOUNT_DISPOSITIONS else record["amount"]
            amount = _quantize(base)
        else:
            continue
        if amount <= 0:
            continue
        index += 1
        merchant = str(record.get("merchant", "") or "").strip()
        item = str(record.get("item", "") or "").strip()
        category, subcategory, extra_tags, basis = classify_record(
            merchant, item, direction, rules, overrides, categories
        )
        if not category:
            key = (direction, merchant or item)
            slot = unmatched.setdefault(
                key,
                {
                    "匹配键": merchant or item,
                    "收支": direction,
                    "笔数": 0,
                    "金额合计": 0.0,
                    "示例商品": set(),
                },
            )
            slot["笔数"] += 1
            slot["金额合计"] = round(slot["金额合计"] + amount, 2)
            if len(slot["示例商品"]) < 3 and item:
                slot["示例商品"].add(item)
        tags = [SOURCE_DISPLAY.get(str(record["source"]), str(record["source"]))]
        if direction == "收入":
            tags.append(f"收入-{subcategory}" if subcategory else "收入")
        tags.append(amount_tier(amount))
        if record.get("refund_original_uid") or record.get("refund_record_uids"):
            tags.append("退款")
        if disposition == "导入-银行直连支出":
            tags.append("银行直连")
        if disposition == "保留-跨期退款收入":
            tags.append("待确认")
        if "&" in str(record.get("payment_method", "")):
            tags.append("复合支付")
        if disposition == "导入-提现手续费":
            tags.append("提现手续费")
        tags.extend(extra_tags)
        remark = (
            merchant
            if (not item or re.sub(r"\s+", "", merchant) == re.sub(r"\s+", "", item))
            else f"{merchant}｜{item}"
        )
        if disposition == "导入-提现手续费":
            remark = f"微信提现服务费｜原提现金额{record['amount']}"
        # 收支账户: withdrawal fee is deducted from WeChat balance (historical rule);
        # otherwise the record account, or a configured Yimu mapping.
        if disposition == "导入-提现手续费":
            account = "微信零钱"
        else:
            account = str(record.get("account", "") or "").strip()
            if not account:
                account = SOURCE_DISPLAY.get(str(record["source"]), str(record["source"]))
            for card in config.bank_cards:
                if account == card.display_name and card.yimu_account:
                    account = card.yimu_account
                    break
        rows.append(
            {
                "日期": str(record["datetime"])[:10],
                "收支类型": direction,
                "金额": amount,
                "类别": category,
                "子类": subcategory,
                "所属账本": config.ledger_name,
                "收支账户": account,
                "备注": remark,
                "标签": " ".join(dict.fromkeys(tags)),
                "_类别": category,
                "_子类": subcategory,
                "_依据": basis,
                "_record_uid": record.get("record_uid", ""),
                "_disposition": disposition,
            }
        )
    return rows, unmatched
