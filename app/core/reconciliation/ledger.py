"""Cross-source reconciliation engine.

Ported from the legacy single-file pipeline with identical matching
semantics; user-specific lookups now go through :class:`EngineConfig`.
Every match records type, confidence, reason, participants and rule so the
review UI can explain and override each decision.
"""

from __future__ import annotations

import copy
import itertools
from collections import Counter, defaultdict
from decimal import Decimal
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from app.core.context import EngineConfig
from app.core.money import as_float, cents, money, parse_dt, now_hong_kong
from app.core.reconciliation.predicates import (
    BANK_SOURCES,
    PLATFORM_SOURCES,
    bank_event_direction,
    bank_has_platform_hint,
    dedupe,
    is_bank_internal,
    is_platform_refund_event,
    is_wx_refund_event,
    is_zfb_internal,
    is_zfb_refund,
    merchant_similarity,
    platform_event_direction,
    platform_match_eligible,
    uid,
    withdrawal_fee,
)
from app.core.parsers.common import compact_text

INCOME_DISPOSITIONS = {"排除-收入", "排除-退款入账", "保留-跨期退款收入"}


class LedgerBuilder:
    def __init__(
        self,
        config: EngineConfig,
        payapps: Sequence[Mapping],
        banks: Sequence[Mapping],
        direct_refund_groups: Sequence = (),
    ) -> None:
        self.config = config
        self.direct_refund_groups_input = tuple(
            ((str(o[0]), int(o[1])), tuple((str(r[0]), int(r[1])) for r in refunds))
            for o, refunds in direct_refund_groups
        )
        self.payapps: List[Dict[str, Any]] = [copy.deepcopy(dict(x)) for x in payapps]
        self.banks: List[Dict[str, Any]] = [copy.deepcopy(dict(x)) for x in banks]
        self.records: List[Dict[str, Any]] = self.payapps + self.banks
        self.by_uid: Dict[str, Dict[str, Any]] = {}
        self.state: Dict[str, Dict[str, Any]] = {}
        for record in self.records:
            record_uid = uid(record)
            if record_uid in self.by_uid:
                raise ValueError(f"重复 record_uid: {record_uid}")
            self.by_uid[record_uid] = record
            self.state[record_uid] = {
                "disposition": None,
                "disposition_reason": "",
                "net_amount": Decimal("0.00"),
                "matched_record_uids": [],
                "match_ids": [],
                "refund_original_uid": None,
                "refund_uids": [],
                "refund_total": Decimal("0.00"),
                "classification": None,
                "import_index": None,
                "review_reasons": [],
            }
        self.matches: List[Dict[str, Any]] = []
        self.refund_groups: Dict[str, List[str]] = defaultdict(list)
        self.refund_reverse: Dict[str, str] = {}
        self.reliable_platform_used: set[str] = set()
        self.reliable_bank_used: set[str] = set()
        self.tentative_bank: Dict[str, str] = {}
        self.direct_bank_refund_groups: Dict[str, List[str]] = {}
        self.direct_bank_refund_reverse: Dict[str, str] = {}

    def add_match(
        self,
        match_type: str,
        confidence: str,
        reason: str,
        platform_uids: Sequence[str] = (),
        bank_uids: Sequence[str] = (),
        original_uids: Sequence[str] = (),
        refund_uids: Sequence[str] = (),
        account: str = "",
        amount: Optional[Decimal] = None,
        time_delta_seconds: Optional[int] = None,
        decision: str = "exclude_duplicate_or_refund",
    ) -> str:
        match_id = f"M{len(self.matches) + 1:04d}"
        involved = dedupe((*platform_uids, *bank_uids, *original_uids, *refund_uids))
        payload: Dict[str, Any] = {
            "match_id": match_id,
            "match_type": match_type,
            "confidence": confidence,
            "decision": decision,
            "reason": reason,
            "platform_record_uids": list(platform_uids),
            "bank_record_uids": list(bank_uids),
            "original_record_uids": list(original_uids),
            "refund_record_uids": list(refund_uids),
            "account": account,
            "amount": as_float(amount) if amount is not None else None,
            "time_delta_seconds": time_delta_seconds,
        }
        self.matches.append(payload)
        for record_uid in involved:
            if record_uid not in self.state:
                raise KeyError(f"匹配引用未知 record_uid: {record_uid}")
            self.state[record_uid]["match_ids"].append(match_id)
            peers = [x for x in involved if x != record_uid]
            self.state[record_uid]["matched_record_uids"] = dedupe(
                (*self.state[record_uid]["matched_record_uids"], *peers)
            )
        return match_id

    def set_disposition(self, record_uid: str, disposition: str, reason: str, net_amount: Any = 0) -> None:
        state = self.state[record_uid]
        if state["disposition"] is not None:
            raise ValueError(
                f"record_uid 被重复 disposition: {record_uid} / "
                f"{state['disposition']} -> {disposition}"
            )
        state["disposition"] = disposition
        state["disposition_reason"] = reason
        state["net_amount"] = money(net_amount)

    def link_platform_refunds(self) -> None:
        zfb_by_id = {
            str(record["source_id"]): record
            for record in self.payapps
            if record.get("source") == "zfb"
        }
        for refund in [x for x in self.payapps if is_zfb_refund(x)]:
            refund_uid = uid(refund)
            original_id = str(refund["source_id"]).split("_", 1)[0]
            original = zfb_by_id.get(original_id)
            if original is None or original is refund:
                raise ValueError(f"支付宝退款无法按订单前缀回链: {refund_uid}")
            original_uid = uid(original)
            self.refund_groups[original_uid].append(refund_uid)
            self.refund_reverse[refund_uid] = original_uid

        wx_refunds = sorted(
            (x for x in self.payapps if is_wx_refund_event(x)),
            key=lambda x: parse_dt(str(x["datetime"])),
        )
        wx_expenses = [
            x for x in self.payapps if x.get("source") == "wx" and x.get("flow") == "支出"
        ]
        for refund in wx_refunds:
            refund_time = parse_dt(str(refund["datetime"]))
            refund_amount = money(refund["amount"])
            refund_account = str(refund.get("account", ""))
            refund_item = compact_text(str(refund.get("item", "")).replace("退款-", ""))
            refund_merchant = compact_text(refund.get("merchant"))
            candidates: List[Tuple[int, float, Dict[str, Any]]] = []
            for original in wx_expenses:
                original_uid = uid(original)
                original_time = parse_dt(str(original["datetime"]))
                if original_time >= refund_time or str(original.get("account", "")) != refund_account:
                    continue
                already_refunded = sum(
                    (money(self.by_uid[x]["amount"]) for x in self.refund_groups.get(original_uid, [])),
                    Decimal("0.00"),
                )
                if already_refunded + refund_amount > money(original["amount"]):
                    continue
                status = str(original.get("status", ""))
                score = 0
                if any(token in status for token in ("退款", "退还")):
                    score += 100
                if money(original["amount"]) == refund_amount:
                    score += 30
                original_item = compact_text(str(original.get("item", "")).replace("转账备注", ""))
                if refund_item and original_item and (
                    refund_item == original_item or refund_item in original_item or original_item in refund_item
                ):
                    score += 35
                original_merchant = compact_text(original.get("merchant"))
                if refund_merchant and original_merchant and (
                    refund_merchant == original_merchant
                    or refund_merchant in original_merchant
                    or refund_merchant in refund_merchant
                ):
                    score += 35
                if score >= 100:
                    candidates.append((score, -(refund_time - original_time).total_seconds(), original))
            if not candidates:
                raise ValueError(f"微信退款无法回链: {uid(refund)}")
            candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            original = candidates[0][2]
            original_uid = uid(original)
            self.refund_groups[original_uid].append(uid(refund))
            self.refund_reverse[uid(refund)] = original_uid

        for original_uid, refund_uids in sorted(self.refund_groups.items()):
            refund_uids.sort(key=lambda x: parse_dt(str(self.by_uid[x]["datetime"])))
            refund_total = sum(
                (money(self.by_uid[x]["amount"]) for x in refund_uids), Decimal("0.00")
            )
            original_amount = money(self.by_uid[original_uid]["amount"])
            if refund_total > original_amount:
                raise ValueError(f"退款大于原单: {original_uid}")
            self.state[original_uid]["refund_uids"] = list(refund_uids)
            self.state[original_uid]["refund_total"] = refund_total
            for refund_uid in refund_uids:
                self.state[refund_uid]["refund_original_uid"] = original_uid
            source = self.by_uid[original_uid]["source"]
            self.add_match(
                match_type="退款回链-支付宝" if source == "zfb" else "退款回链-微信",
                confidence="high",
                reason=(
                    "支付宝退款交易号以前缀精确回链原订单"
                    if source == "zfb"
                    else "微信退款按退款状态、账户、金额及商户/商品回链最近前序原单"
                ),
                original_uids=(original_uid,),
                refund_uids=refund_uids,
                account=str(self.by_uid[original_uid].get("account", "")),
                amount=refund_total,
            )

    def assign_platform_dispositions(self) -> None:
        for record in self.payapps:
            record_uid = uid(record)
            amount = money(record.get("amount"))
            if record_uid in self.refund_reverse:
                self.set_disposition(
                    record_uid,
                    "排除-退款入账",
                    f"退款已回链原单 {self.refund_reverse[record_uid]}，不作为收入或负支出导入",
                )
                continue
            if record_uid in self.refund_groups:
                refunded = self.state[record_uid]["refund_total"]
                net = amount - refunded
                if net == 0:
                    self.set_disposition(
                        record_uid,
                        "排除-全额退款原单",
                        f"原金额 {amount:.2f}，累计退款 {refunded:.2f}，净额为零",
                    )
                else:
                    self.set_disposition(
                        record_uid,
                        "导入-部分退款净额",
                        f"原金额 {amount:.2f}，累计退款 {refunded:.2f}，仅导入净支出 {net:.2f}",
                        net,
                    )
                continue
            if amount <= 0:
                self.set_disposition(
                    record_uid,
                    "排除-零金额优惠",
                    "交易金额为0.00（全额优惠/立减），保留审计但不得导入",
                )
                continue
            if record.get("status") == "交易关闭":
                self.set_disposition(record_uid, "排除-交易关闭", "支付宝交易状态为交易关闭")
                continue
            if record.get("flow") == "收入":
                self.set_disposition(record_uid, "排除-收入", "本次导入仅包含实际支出")
                continue
            if record.get("source") == "wx" and record.get("flow") == "不计收支":
                fee = withdrawal_fee(record)
                if fee > 0:
                    self.set_disposition(
                        record_uid,
                        "导入-提现手续费",
                        f"提现本金属于资金搬运，仅导入备注中的真实服务费 {fee:.2f}",
                        fee,
                    )
                else:
                    self.set_disposition(
                        record_uid,
                        "排除-内部资金搬运",
                        "微信零钱、零钱通与本人银行卡之间的提现/充值/转存",
                    )
                continue
            if is_zfb_internal(record):
                self.set_disposition(
                    record_uid,
                    "排除-内部资金搬运",
                    "余额宝/基金申购、账户存取或支付宝小荷包转存，不是消费",
                )
                continue
            if record.get("flow") == "支出" or (
                record.get("source") == "zfb"
                and record.get("flow") == "不计收支"
                and record.get("status") in {"交易成功", "等待确认收货"}
            ):
                self.set_disposition(record_uid, "导入-支出", "有效的实际支出交易", amount)
                continue
            self.set_disposition(
                record_uid,
                "排除-非支出",
                f"未满足实际支出条件：flow={record.get('flow')} status={record.get('status')}",
            )

    def exact_cross_source_matches(self, refund_originals: set[str]) -> None:
        platforms = [
            record
            for record in self.payapps
            if platform_match_eligible(record, refund_originals, self.config)
        ]
        edges: List[Tuple[int, int, str, str]] = []
        for platform in platforms:
            p_uid = uid(platform)
            p_time = parse_dt(str(platform["datetime"]))
            p_account = self.config.canonical_bank_account(platform.get("account"))
            p_direction = platform_event_direction(platform)
            p_cents = cents(platform["amount"])
            for bank in self.banks:
                if bank.get("account") != p_account or bank_event_direction(bank) != p_direction:
                    continue
                if cents(bank["amount"]) != p_cents:
                    continue
                delta = abs(int((parse_dt(str(bank["datetime"])) - p_time).total_seconds()))
                if delta <= 300:
                    edges.append((delta, -merchant_similarity(platform, bank), p_uid, uid(bank)))
        for delta, negative_similarity, p_uid, b_uid in sorted(edges):
            if p_uid in self.reliable_platform_used or b_uid in self.reliable_bank_used:
                continue
            platform = self.by_uid[p_uid]
            bank = self.by_uid[b_uid]
            self.reliable_platform_used.add(p_uid)
            self.reliable_bank_used.add(b_uid)
            self.add_match(
                match_type="跨源去重-同卡同额5分钟",
                confidence="high",
                reason=(
                    "同一真实银行卡、同方向、同金额且时间差不超过5分钟；"
                    f"平台记录优先（商户线索评分 {-negative_similarity}）"
                ),
                platform_uids=(p_uid,),
                bank_uids=(b_uid,),
                account=str(bank["account"]),
                amount=money(platform["amount"]),
                time_delta_seconds=delta,
            )

    def withdrawal_net_matches(self, refund_originals: set[str]) -> None:
        for platform in self.payapps:
            p_uid = uid(platform)
            fee = withdrawal_fee(platform)
            if (
                fee <= 0
                or p_uid in self.reliable_platform_used
                or not platform_match_eligible(platform, refund_originals, self.config)
            ):
                continue
            expected_bank_cents = cents(money(platform["amount"]) - fee)
            p_time = parse_dt(str(platform["datetime"]))
            account = self.config.canonical_bank_account(platform.get("account"))
            candidates = []
            for bank in self.banks:
                b_uid = uid(bank)
                if b_uid in self.reliable_bank_used:
                    continue
                delta = abs(int((parse_dt(str(bank["datetime"])) - p_time).total_seconds()))
                if (
                    bank.get("account") == account
                    and bank_event_direction(bank) == 1
                    and cents(bank["amount"]) == expected_bank_cents
                    and delta <= 300
                ):
                    candidates.append((delta, bank))
            if len(candidates) != 1:
                continue
            delta, bank = candidates[0]
            b_uid = uid(bank)
            self.reliable_platform_used.add(p_uid)
            self.reliable_bank_used.add(b_uid)
            self.add_match(
                match_type="跨源去重-提现扣费净额",
                confidence="high",
                reason="微信提现金额减明确服务费后，与银行卡净入账金额一致",
                platform_uids=(p_uid,),
                bank_uids=(b_uid,),
                account=account,
                amount=money(bank["amount"]),
                time_delta_seconds=delta,
            )

    def subset_cross_source_matches(self, refund_originals: set[str]) -> None:
        eligible = [
            record
            for record in self.payapps
            if uid(record) not in self.reliable_platform_used
            and platform_match_eligible(record, refund_originals, self.config)
        ]
        for bank in sorted(self.banks, key=lambda x: parse_dt(str(x["datetime"]))):
            b_uid = uid(bank)
            if b_uid in self.reliable_bank_used:
                continue
            b_time = parse_dt(str(bank["datetime"]))
            direction = bank_event_direction(bank)
            account = str(bank.get("account", ""))
            bank_cents = cents(bank["amount"])
            nearby = [
                record
                for record in eligible
                if uid(record) not in self.reliable_platform_used
                and self.config.canonical_bank_account(record.get("account")) == account
                and platform_event_direction(record) == direction
                and abs((parse_dt(str(record["datetime"])) - b_time).total_seconds()) <= 300
                and cents(record["amount"]) < bank_cents
            ]
            solutions: List[Tuple[Dict[str, Any], ...]] = []
            for source in PLATFORM_SOURCES:
                source_rows = [x for x in nearby if x.get("source") == source]
                for size in range(2, min(4, len(source_rows)) + 1):
                    for combination in itertools.combinations(source_rows, size):
                        times = [parse_dt(str(x["datetime"])) for x in combination]
                        if (max(times) - min(times)).total_seconds() > 2:
                            continue
                        if sum(cents(x["amount"]) for x in combination) == bank_cents:
                            solutions.append(combination)
            unique_sets = {
                tuple(sorted(uid(x) for x in solution)): solution for solution in solutions
            }
            if len(unique_sets) != 1:
                continue
            combination = next(iter(unique_sets.values()))
            platform_uids = tuple(uid(x) for x in combination)
            max_delta = max(
                abs(int((parse_dt(str(x["datetime"])) - b_time).total_seconds()))
                for x in combination
            )
            confidence = "high" if max_delta <= 120 else "medium"
            self.reliable_platform_used.update(platform_uids)
            self.reliable_bank_used.add(b_uid)
            self.add_match(
                match_type="跨源去重-1对多子集和",
                confidence=confidence,
                reason=(
                    f"{len(platform_uids)}条同平台同秒订单金额合计与一笔银行扣款一致；"
                    + ("时间差不超过2分钟" if max_delta <= 120 else "银行入账延迟超过2分钟但不超过5分钟")
                ),
                platform_uids=platform_uids,
                bank_uids=(b_uid,),
                account=account,
                amount=money(bank["amount"]),
                time_delta_seconds=max_delta,
            )

    def composite_cross_source_matches(self, refund_originals: set[str]) -> None:
        for platform in self.payapps:
            p_uid = uid(platform)
            method = str(platform.get("payment_method", ""))
            if (
                p_uid in self.reliable_platform_used
                or "&" not in method
                or not platform_match_eligible(platform, refund_originals, self.config)
            ):
                continue
            p_time = parse_dt(str(platform["datetime"]))
            account = self.config.canonical_bank_account(platform.get("account"))
            direction = platform_event_direction(platform)
            p_cents = cents(platform["amount"])
            candidates: List[Tuple[int, Dict[str, Any]]] = []
            for bank in self.banks:
                b_uid = uid(bank)
                if b_uid in self.reliable_bank_used:
                    continue
                delta = abs(int((parse_dt(str(bank["datetime"])) - p_time).total_seconds()))
                b_cents = cents(bank["amount"])
                if (
                    bank.get("account") == account
                    and bank_event_direction(bank) == direction
                    and 0 < b_cents < p_cents
                    and delta <= 600
                ):
                    candidates.append((delta, bank))
            if len(candidates) != 1:
                continue
            delta, bank = candidates[0]
            b_uid = uid(bank)
            difference = money(platform["amount"]) - money(bank["amount"])
            self.reliable_platform_used.add(p_uid)
            self.reliable_bank_used.add(b_uid)
            self.add_match(
                match_type="跨源去重-复合付款金额差",
                confidence="medium",
                reason=(
                    f"付款方式含多个资金来源，唯一银行卡候选少扣 {difference:.2f}；"
                    "差额由余额/红包/优惠等复合资金解释"
                ),
                platform_uids=(p_uid,),
                bank_uids=(b_uid,),
                account=account,
                amount=money(bank["amount"]),
                time_delta_seconds=delta,
            )

    def delayed_internal_matches(self, refund_originals: set[str]) -> None:
        """Match same-day internal movements whose bank posting is unusually late.

        These are never used for ordinary consumption.  The relaxed same-day
        window is allowed only when the platform row already has the audited
        disposition ``排除-内部资金搬运`` and there is one unique same-card,
        same-direction, same-amount candidate.
        """

        internal_platforms = [
            record
            for record in self.payapps
            if uid(record) not in self.reliable_platform_used
            and self.state[uid(record)]["disposition"] == "排除-内部资金搬运"
            and platform_match_eligible(record, refund_originals, self.config)
        ]
        for bank in self.banks:
            b_uid = uid(bank)
            if b_uid in self.reliable_bank_used or not bank_has_platform_hint(bank):
                continue
            b_time = parse_dt(str(bank["datetime"]))
            candidates = [
                platform
                for platform in internal_platforms
                if uid(platform) not in self.reliable_platform_used
                and self.config.canonical_bank_account(platform.get("account")) == bank.get("account")
                and platform_event_direction(platform) == bank_event_direction(bank)
                and cents(platform["amount"]) == cents(bank["amount"])
                and str(platform["datetime"])[:10] == str(bank["datetime"])[:10]
                and abs((parse_dt(str(platform["datetime"])) - b_time).total_seconds()) <= 12 * 60 * 60
            ]
            if len(candidates) != 1:
                continue
            platform = candidates[0]
            p_uid = uid(platform)
            delta = abs(int((parse_dt(str(platform["datetime"])) - b_time).total_seconds()))
            self.reliable_platform_used.add(p_uid)
            self.reliable_bank_used.add(b_uid)
            self.add_match(
                match_type="跨源去重-内部搬运延迟入账",
                confidence="medium",
                reason="平台行已明确为内部资金搬运；同日唯一同卡同方向同额银行行虽延迟入账，仍作为影子排除",
                platform_uids=(p_uid,),
                bank_uids=(b_uid,),
                account=str(bank["account"]),
                amount=money(bank["amount"]),
                time_delta_seconds=delta,
            )

    def link_direct_bank_refunds(self) -> None:
        by_source_row = {(x["source"], int(x["source_row"])): x for x in self.banks}
        for original_key, refund_keys in self.direct_refund_groups_input:
            original = by_source_row.get(original_key)
            refunds = [by_source_row.get(key) for key in refund_keys]
            if original is None or any(x is None for x in refunds):
                raise ValueError(f"银行直连退款固定核对行缺失: {original_key} -> {refund_keys}")
            original_uid = uid(original)
            refund_uids = [uid(x) for x in refunds if x is not None]
            if original_uid in self.reliable_bank_used or any(
                x in self.reliable_bank_used for x in refund_uids
            ):
                raise ValueError(f"银行直连退款组意外与平台匹配冲突: {original_uid}")
            refund_total = sum((money(x["amount"]) for x in refunds if x is not None), Decimal("0.00"))
            if refund_total > money(original["amount"]):
                raise ValueError(f"银行直连退款大于原支出: {original_uid}")
            self.direct_bank_refund_groups[original_uid] = refund_uids
            for refund_uid in refund_uids:
                self.direct_bank_refund_reverse[refund_uid] = original_uid
            self.state[original_uid]["refund_uids"] = list(refund_uids)
            self.state[original_uid]["refund_total"] = refund_total
            for refund_uid in refund_uids:
                self.state[refund_uid]["refund_original_uid"] = original_uid
            self.add_match(
                match_type="退款回链-银行直连",
                confidence="high",
                reason="银行余额链、商户、时间与退款金额人工精确核对；无平台账单对应",
                bank_uids=(original_uid, *refund_uids),
                original_uids=(original_uid,),
                refund_uids=refund_uids,
                account=str(original["account"]),
                amount=refund_total,
            )

    def mark_tentative_bank_duplicates(self, refund_originals: set[str]) -> None:
        eligible_platforms = [
            x
            for x in self.payapps
            if uid(x) not in self.reliable_platform_used
            and platform_match_eligible(x, refund_originals, self.config)
        ]
        protected = set(self.direct_bank_refund_groups) | set(self.direct_bank_refund_reverse)
        for bank in self.banks:
            b_uid = uid(bank)
            if (
                b_uid in self.reliable_bank_used
                or b_uid in protected
                or bank.get("flow") != "支出"
                or is_bank_internal(bank, self.config)
                or not bank_has_platform_hint(bank)
            ):
                continue
            b_time = parse_dt(str(bank["datetime"]))
            candidates = [
                x
                for x in eligible_platforms
                if self.config.canonical_bank_account(x.get("account")) == bank.get("account")
                and platform_event_direction(x) == -1
                and cents(x["amount"]) == cents(bank["amount"])
                and abs((parse_dt(str(x["datetime"])) - b_time).total_seconds()) <= 3 * 60 * 60
            ]
            if len(candidates) == 1:
                candidate = candidates[0]
                reason = "银行摘要明确指向支付平台，且3小时内存在唯一同卡同额平台候选；超出严格5分钟窗口，暂缓复核"
                platform_uids = (uid(candidate),)
                delta = abs(int((parse_dt(str(candidate["datetime"])) - b_time).total_seconds()))
            else:
                reason = "银行摘要明确指向支付宝/财付通，但严格匹配未唯一命中；为防重复暂缓复核"
                platform_uids = tuple(uid(x) for x in candidates[:10])
                delta = None
            self.tentative_bank[b_uid] = reason
            self.state[b_uid]["review_reasons"].append(reason)
            self.add_match(
                match_type="跨源去重-疑似平台重复",
                confidence="low",
                reason=reason,
                platform_uids=platform_uids,
                bank_uids=(b_uid,),
                account=str(bank["account"]),
                amount=money(bank["amount"]),
                time_delta_seconds=delta,
                decision="hold_for_review",
            )

    def assign_bank_dispositions(self) -> None:
        for record in self.banks:
            record_uid = uid(record)
            amount = money(record["amount"])
            if record_uid in self.reliable_bank_used:
                self.set_disposition(
                    record_uid,
                    "影子重复-平台记录优先",
                    "已与微信/支付宝平台记录可靠匹配；平台信息更丰富，银行行不重复导入",
                )
                continue
            if record_uid in self.direct_bank_refund_reverse:
                self.set_disposition(
                    record_uid,
                    "排除-退款入账",
                    f"银行直连退款已回链原支出 {self.direct_bank_refund_reverse[record_uid]}",
                )
                continue
            if record_uid in self.direct_bank_refund_groups:
                refunded = self.state[record_uid]["refund_total"]
                net = amount - refunded
                if net == 0:
                    self.set_disposition(
                        record_uid,
                        "排除-全额退款原单",
                        f"银行直连原支出 {amount:.2f} 已全额退款",
                    )
                else:
                    self.set_disposition(
                        record_uid,
                        "导入-部分退款净额",
                        f"银行直连原支出 {amount:.2f}，累计退款 {refunded:.2f}，净支出 {net:.2f}",
                        net,
                    )
                continue
            if record.get("flow") == "收入":
                refund_text = f"{record.get('raw_type', '')}{record.get('item', '')}"
                if "退款" in refund_text:
                    self.set_disposition(
                        record_uid,
                        "保留-跨期退款收入",
                        "银行退款在本期无对应原支出，按本期实际到账收入保留（待确认）",
                    )
                else:
                    self.set_disposition(record_uid, "排除-收入", "本次导入仅包含实际支出")
                continue
            if is_bank_internal(record, self.config):
                self.set_disposition(
                    record_uid,
                    "排除-内部资金搬运",
                    f"银行交易类型 {record.get('raw_type')} 属于理财申购、充值或本人账户搬运",
                )
                continue
            if record_uid in self.tentative_bank:
                self.set_disposition(
                    record_uid,
                    "暂缓-疑似平台重复",
                    self.tentative_bank[record_uid],
                )
                continue
            self.set_disposition(
                record_uid,
                "导入-银行直连支出",
                "未与平台账单匹配，按独立银行卡直连实际支出保留",
                amount,
            )

    def output_records(self) -> List[Dict[str, Any]]:
        output = []
        for record in self.records:
            record_uid = uid(record)
            state = self.state[record_uid]
            row = copy.deepcopy(record)
            row.update(
                {
                    "record_uid": record_uid,
                    "disposition": state["disposition"],
                    "disposition_reason": state["disposition_reason"],
                    "net_amount": as_float(state["net_amount"]),
                    "refund_original_uid": state["refund_original_uid"],
                    "refund_record_uids": list(state["refund_uids"]),
                    "refund_total": as_float(state["refund_total"]),
                    "matched_record_uids": list(state["matched_record_uids"]),
                    "match_ids": list(state["match_ids"]),
                    "classification": copy.deepcopy(state["classification"]),
                    "import_index": state["import_index"],
                    "review_reasons": dedupe(state["review_reasons"]),
                }
            )
            output.append(row)
        return output

    def summary(self) -> Dict[str, Any]:
        source_stats: Dict[str, Any] = {}
        for source in sorted({str(x["source"]) for x in self.records}):
            rows = [x for x in self.records if x["source"] == source]
            source_stats[source] = {
                "record_count": len(rows),
                "date_min": min(str(x["datetime"]) for x in rows),
                "date_max": max(str(x["datetime"]) for x in rows),
                "flow_counts": dict(Counter(str(x.get("flow", "")) for x in rows)),
                "amount_by_flow": {
                    flow: as_float(
                        sum(
                            (money(x["amount"]) for x in rows if x.get("flow") == flow),
                            Decimal("0.00"),
                        )
                    )
                    for flow in sorted({str(x.get("flow", "")) for x in rows})
                },
            }
        dispositions = Counter(self.state[uid(x)]["disposition"] for x in self.records)
        return {
            "timezone": "UTC+08:00 Asia/Hong_Kong",
            "original_record_count": len(self.records),
            "source_stats": source_stats,
            "disposition_counts": dict(dispositions),
            "match_count": len(self.matches),
            "match_type_counts": dict(Counter(x["match_type"] for x in self.matches)),
            "refunds": {
                "zfb_refund_records": sum(1 for x in self.payapps if is_zfb_refund(x)),
                "zfb_original_orders": sum(
                    1 for x in self.refund_groups if self.by_uid[x]["source"] == "zfb"
                ),
                "wx_refund_records": sum(1 for x in self.payapps if is_wx_refund_event(x)),
                "wx_original_orders": sum(
                    1 for x in self.refund_groups if self.by_uid[x]["source"] == "wx"
                ),
                "direct_bank_original_orders": len(self.direct_bank_refund_groups),
                "partial_refund_orders": sum(
                    1
                    for o in (*self.refund_groups.keys(), *self.direct_bank_refund_groups.keys())
                    if self.state[o]["net_amount"] > 0
                ),
            },
            "withdrawal_fee": {
                "record_count": sum(1 for x in self.payapps if withdrawal_fee(x) > 0),
                "total": as_float(sum((withdrawal_fee(x) for x in self.payapps), Decimal("0.00"))),
            },
        }

    def run(self) -> Dict[str, Any]:
        self.link_platform_refunds()
        self.assign_platform_dispositions()
        refund_originals = set(self.refund_groups)
        self.exact_cross_source_matches(refund_originals)
        self.withdrawal_net_matches(refund_originals)
        self.subset_cross_source_matches(refund_originals)
        self.composite_cross_source_matches(refund_originals)
        self.delayed_internal_matches(refund_originals)
        self.link_direct_bank_refunds()
        self.mark_tentative_bank_duplicates(refund_originals)
        self.assign_bank_dispositions()
        return {
            "schema_version": "2.0",
            "generated_at": now_hong_kong(),
            "all_records": self.output_records(),
            "matches": self.matches,
            "summary": self.summary(),
        }
