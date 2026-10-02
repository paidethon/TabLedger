"""Bank-direct refund pairing (cross-period refunds with no platform record)."""

from __future__ import annotations

import re
from collections import defaultdict
from decimal import Decimal
from typing import Any, Mapping

from app.core.money import money, parse_dt
from app.core.reconciliation.predicates import uid


def auto_direct_bank_refund_groups(banks: list[dict]) -> list:
    """Conservative automatic pairing: same source, same merchant, amount within
    the original, within 60 days, unique candidate; bank rows already shadowed
    by a platform match never participate."""

    def norm_merchant(r: Mapping[str, Any]) -> str:
        return re.sub(r"\s+", "", str(r.get("merchant") or ""))

    shadowed: set[str] = set()
    for bank in banks:
        if bank.get("flow") != "支出":
            continue
        b_dt = str(bank["datetime"])[:10]
        for other in banks:
            if other is bank or other["source"] != bank["source"]:
                continue
            if str(other["datetime"])[:10] == b_dt and money(other["amount"]) == money(bank["amount"]):
                shadowed.add(uid(bank))
                break

    refunds = [
        b
        for b in banks
        if uid(b) not in shadowed
        and b.get("flow") == "收入"
        and "退款" in str(b.get("raw_type", ""))
    ]
    originals = [b for b in banks if uid(b) not in shadowed and b.get("flow") == "支出"]
    by_uid_map = {uid(b): b for b in banks}
    assign: dict[str, str] = {}
    for refund in refunds:
        r_dt = parse_dt(str(refund["datetime"]))
        candidates = [
            o
            for o in originals
            if o["source"] == refund["source"]
            and norm_merchant(o)
            and norm_merchant(o) == norm_merchant(refund)
            and money(o["amount"]) >= money(refund["amount"])
            and 0 <= (r_dt - parse_dt(str(o["datetime"]))).days <= 60
        ]
        if len(candidates) == 1:
            assign[uid(refund)] = uid(candidates[0])
    grouped: dict[str, list[str]] = defaultdict(list)
    for r_uid, o_uid in assign.items():
        grouped[o_uid].append(r_uid)
    result = []
    for o_uid, r_uids in grouped.items():
        total = sum((money(by_uid_map[r]["amount"]) for r in r_uids), Decimal("0"))
        if total <= money(by_uid_map[o_uid]["amount"]):
            result.append(
                (
                    (by_uid_map[o_uid]["source"], int(by_uid_map[o_uid]["source_row"])),
                    tuple(
                        sorted(
                            (by_uid_map[r]["source"], int(by_uid_map[r]["source_row"]))
                            for r in r_uids
                        )
                    ),
                )
            )
    return result
