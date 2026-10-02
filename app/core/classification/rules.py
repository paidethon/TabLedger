"""Classification rules: matching, validation and the built-in income taxonomy.

Rules are stored in the database (migrated from the legacy 分类词典.csv);
this module is storage-agnostic and works on plain rule dicts with keys
键/收支/类别/子类/标签/来源.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

INCOME_CATEGORIES = [
    "AA款", "红包", "返利", "利息", "退税", "转账", "其他", "中奖", "理财盈利",
    "礼金人情", "借入", "奖金", "兼职外快", "工资", "二手闲置", "补贴", "报销",
]

SOURCE_DISPLAY = {
    "wx": "微信",
    "zfb": "支付宝",
    "工商银行": "工商银行",
    "中国银行": "中国银行",
}

EXPENSE_DISPOSITIONS = {
    "导入-支出", "导入-提现手续费", "导入-部分退款净额", "导入-银行直连支出", "排除-全额退款原单",
}
# 部分退款原单仍按毛额导入（退款另记一笔收入），仅提现手续费按净额。
NET_AMOUNT_DISPOSITIONS = {"导入-提现手续费"}
INCOME_DISPOSITIONS = {"排除-收入", "排除-退款入账", "保留-跨期退款收入"}


def match_rule(
    rules: Sequence[Mapping[str, str]], direction: str, *texts: str
) -> dict[str, str] | None:
    joined = " ".join(t for t in texts if t)
    exact = [r for r in rules if r["收支"] in ("", direction) and r["键"] in texts]
    if exact:
        return exact[0]
    for rule in rules:
        if rule["收支"] in ("", direction) and rule["键"] in joined:
            return rule
    return None


def validate_pair(
    direction: str, category: str, subcategory: str, categories: Mapping[str, Any]
) -> bool:
    if direction == "收入":
        return category == subcategory and category in INCOME_CATEGORIES
    return category in categories and subcategory in list(categories.get(category, []))


def rule_from_row(row: Mapping[str, str], origin: str) -> dict[str, str] | None:
    key = (row.get("键") or row.get("匹配键") or "").strip()
    if not key:
        return None
    return {
        "键": key,
        "收支": (row.get("收支") or "").strip(),
        "类别": (row.get("类别") or "").strip(),
        "子类": (row.get("子类") or "").strip(),
        "标签": (row.get("标签") or "").strip(),
        "来源": origin,
    }


def sort_rules_longest_first(rules: list[dict[str, str]]) -> list[dict[str, str]]:
    return sorted(rules, key=lambda r: len(r["键"]), reverse=True)


def rules_from_csv_rows(rows: Iterable[Mapping[str, str]], origin: str) -> list[dict[str, str]]:
    rules = []
    for row in rows:
        rule = rule_from_row(row, origin)
        if rule:
            rules.append(rule)
    return sort_rules_longest_first(rules)
