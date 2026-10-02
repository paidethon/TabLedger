"""Unit tests for the deterministic engine: parsers, security, reconciliation, classification, export."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal
from pathlib import Path

import pytest

from app.core.classification.apply import build_import_rows, classify_record
from app.core.classification.rules import match_rule, rules_from_csv_rows, validate_pair
from app.core.context import BankCard, EngineConfig
from app.core.extraction import BillInput, detect_and_extract, extract_bank_all
from app.core.exporters.biff8 import build_biff_workbook, build_cfb, validate_xls
from app.core.exporters.yimu import finalize_rows, write_yimu_xls
from app.core.parsers.archive import ArchiveError, unzip_bill
from app.core.parsers.common import make_record, validate_schema
from app.core.parsers.xlsx import read_first_xlsx_sheet
from app.core.reconciliation.ledger import LedgerBuilder
from app.core.reconciliation.refunds import auto_direct_bank_refund_groups

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic"

CONFIG = EngineConfig(
    owner_names=("张测试",),
    bank_cards=(
        BankCard(source="工商银行", tail="0000", display_name="工行储蓄卡(0000)", id_prefix="ICBC0000"),
        BankCard(source="中国银行", tail="1111", display_name="中国银行储蓄卡(1111)", id_prefix="BOC1111"),
    ),
)

PLAIN_INPUTS = [
    BillInput(path=FIXTURES / "alipay.csv", name="alipay.csv"),
    BillInput(path=FIXTURES / "wechat.xlsx", name="wechat.xlsx"),
    BillInput(path=FIXTURES / "icbc.pdf", name="icbc.pdf"),
    BillInput(path=FIXTURES / "boc.pdf", name="boc.pdf"),
]


@pytest.fixture(scope="module")
def extracted():
    records_by_source, file_meta, errors = detect_and_extract(PLAIN_INPUTS, CONFIG)
    assert errors == []
    return records_by_source, file_meta


class TestParsers:
    def test_alipay_count_and_schema(self, extracted):
        records = extracted[0]["zfb"]
        assert len(records) == 11
        for r in records:
            assert r["flow"] in {"支出", "收入", "不计收支"}
            assert r["amount"] > 0
            assert r["source_id"]
        assert any(r["status"] == "退款成功" for r in records)

    def test_wechat_declared_summary_parsed(self, extracted):
        records = extracted[0]["wx"]
        assert len(records) == 8
        meta = [f for f in extracted[1]["files"] if f.get("source") == "wx"][0]
        assert meta["declared"]["count"] == 8

    def test_bank_records_balance_schema(self, extracted):
        icbc = extracted[0]["工商银行"]
        boc = extracted[0]["中国银行"]
        assert len(icbc) == 7 and len(boc) == 2
        _, qa = extract_bank_all(extracted[0])
        assert qa["errors"] == []
        assert all(v["balance_chain_ok"] for v in qa["banks"].values())

    def test_boc_statement_reversed_to_ascending(self, extracted):
        boc = extracted[0]["中国银行"]
        assert boc[0]["datetime"] < boc[1]["datetime"]

    def test_no_owner_name_in_sanitized_defaults(self):
        # Default config has no personal data; card matching is disabled.
        from app.core.context import default_engine_config

        cfg = default_engine_config()
        assert cfg.canonical_bank_account("工商银行储蓄卡(0000)") == ""
        assert cfg.owner_names == ()

    def test_card_matching_via_config(self):
        assert CONFIG.canonical_bank_account("工商银行储蓄卡(0000)") == "工行储蓄卡(0000)"
        assert CONFIG.canonical_bank_account("中国工商银行储蓄卡(0000)") == "工行储蓄卡(0000)"
        assert CONFIG.canonical_bank_account("中国银行储蓄卡(1111)") == "中国银行储蓄卡(1111)"
        assert CONFIG.canonical_bank_account("微信零钱") == ""
        assert CONFIG.canonical_bank_account("工商银行储蓄卡(9999)") == ""


class TestArchiveSecurity:
    def test_zip_with_correct_password(self, tmp_path):
        data, inner = unzip_bill(
            FIXTURES / "encrypted" / "alipay_encrypted.zip", ["test-zip-" + "pass-01"], "a.zip"
        )
        assert inner.endswith(".csv")

    def test_zip_wrong_password(self):
        with pytest.raises(ArchiveError):
            unzip_bill(FIXTURES / "encrypted" / "alipay_encrypted.zip", ["nope"], "a.zip")

    def test_zip_slip_rejected(self, tmp_path):
        evil = tmp_path / "evil.zip"
        with zipfile.ZipFile(evil, "w") as zf:
            zf.writestr("../escape.csv", "x")
        with pytest.raises(ArchiveError):
            unzip_bill(evil, [""], "evil.zip")

    def test_zip_bomb_ratio_rejected(self, tmp_path):
        bomb = tmp_path / "bomb.zip"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("huge.csv", b"0" * (10 * 1024 * 1024))
        with pytest.raises(ArchiveError):
            unzip_bill(bomb, [""], "bomb.zip")

    def test_nested_zip_rejected(self, tmp_path):
        inner = tmp_path / "inner.zip"
        with zipfile.ZipFile(inner, "w") as zf:
            zf.writestr("x.csv", "x")
        outer = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer, "w") as zf:
            zf.writestr("inner.zip", inner.read_bytes())
        with pytest.raises(ArchiveError):
            unzip_bill(outer, [""], "outer.zip")

    def test_multi_entry_zip_rejected(self, tmp_path):
        multi = tmp_path / "multi.zip"
        with zipfile.ZipFile(multi, "w") as zf:
            zf.writestr("a.csv", "x")
            zf.writestr("b.csv", "y")
        with pytest.raises(ArchiveError):
            unzip_bill(multi, [""], "multi.zip")

    def test_xlsx_reader_entity_safety(self):
        """Shared strings containing entity definitions must not expand."""

        class FakeZip:
            pass

        # read_first_xlsx_sheet uses defusedxml; a payload with entities must raise, not expand
        malicious = io.BytesIO()
        with zipfile.ZipFile(malicious, "w") as zf:
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                '<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa">]>'
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                '<sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>&a;&a;&a;</t></is></c></row></sheetData></worksheet>',
            )
        with pytest.raises(ValueError):
            read_first_xlsx_sheet(malicious.getvalue())


class TestReconciliation:
    def test_cross_source_match_counts(self, extracted):
        records = extracted[0]
        bank_records, _ = extract_bank_all(records)
        payapps = sorted(records["wx"] + records["zfb"], key=lambda r: r["datetime"])
        ledger = LedgerBuilder(CONFIG, payapps, bank_records).run()
        types = ledger["summary"]["match_type_counts"]
        assert types.get("跨源去重-同卡同额5分钟") == 2
        assert types.get("跨源去重-提现扣费净额") == 1
        assert types.get("退款回链-微信") == 1
        assert types.get("退款回链-支付宝") == 2

    def test_partial_refund_imports_gross_original(self, extracted):
        records = extracted[0]
        bank_records, _ = extract_bank_all(records)
        payapps = sorted(records["wx"] + records["zfb"], key=lambda r: r["datetime"])
        ledger = LedgerBuilder(CONFIG, payapps, bank_records).run()
        by_uid = {r["record_uid"]: r for r in ledger["all_records"]}
        original = next(r for r in ledger["all_records"] if r["source_id"] == "TESTZFB0001")
        assert original["disposition"] == "导入-部分退款净额"
        assert Decimal(str(original["amount"])) == Decimal("88.00")  # gross, by design
        assert Decimal(str(original["net_amount"])) == Decimal("58.00")

    def test_same_amount_different_merchant_not_deduped(self, extracted):
        records = extracted[0]
        bank_records, _ = extract_bank_all(records)
        payapps = sorted(records["wx"] + records["zfb"], key=lambda r: r["datetime"])
        ledger = LedgerBuilder(CONFIG, payapps, bank_records).run()
        direct = [
            r
            for r in ledger["all_records"]
            if r["merchant"] in {"测试A商店", "测试B商店"} and r["source"] == "工商银行"
        ]
        assert all(r["disposition"] == "导入-银行直连支出" for r in direct)
        assert len(direct) == 2

    def test_internal_transfers_excluded(self, extracted):
        records = extracted[0]
        bank_records, _ = extract_bank_all(records)
        payapps = sorted(records["wx"] + records["zfb"], key=lambda r: r["datetime"])
        ledger = LedgerBuilder(CONFIG, payapps, bank_records).run()
        internals = [r for r in ledger["all_records"] if r["disposition"] == "排除-内部资金搬运"]
        assert len(internals) == 3  # 余额宝转入 + 支付宝→银行卡转账 + 数币兑出

    def test_cross_period_bank_refund_flagged_for_review(self, extracted):
        records = extracted[0]
        bank_records, _ = extract_bank_all(records)
        payapps = sorted(records["wx"] + records["zfb"], key=lambda r: r["datetime"])
        ledger = LedgerBuilder(CONFIG, payapps, bank_records).run()
        cross = [r for r in ledger["all_records"] if r["disposition"] == "保留-跨期退款收入"]
        assert len(cross) == 1

    def test_bank_direct_refund_auto_pairing(self):
        banks = [
            {"source": "工商银行", "source_row": 1, "source_id": "a", "datetime": "2026-01-01 10:00:00", "flow": "支出", "amount": 100.0, "signed_amount": -100.0, "merchant": "测试商户", "raw_type": "消费"},
            {"source": "工商银行", "source_row": 2, "source_id": "b", "datetime": "2026-02-01 10:00:00", "flow": "收入", "amount": 40.0, "signed_amount": 40.0, "merchant": "测试商户", "raw_type": "退款"},
        ]
        groups = auto_direct_bank_refund_groups(banks)
        assert groups == [(("工商银行", 1), (("工商银行", 2),))]


class TestClassification:
    RULES = rules_from_csv_rows(
        [
            {"匹配键": "测试超市", "收支": "支出", "类别": "购物消费", "子类": "日常家居", "标签": ""},
            {"匹配键": "测试", "收支": "", "类别": "食品餐饮", "子类": "三餐", "标签": ""},
        ],
        "词典",
    )

    def test_long_key_first(self):
        rule = match_rule(self.RULES, "支出", "测试超市", "生活用品")
        assert rule["键"] == "测试超市"

    def test_direction_aware(self):
        # 支出-only rules must not apply to income rows; direction-"" rules apply to both.
        assert match_rule(self.RULES, "收入", "测试超市")["键"] == "测试"
        assert match_rule(self.RULES, "支出", "测试超市")["键"] == "测试超市"

    def test_invalid_pair_rejected(self):
        taxonomy = {"食品餐饮": ["三餐"]}
        assert validate_pair("支出", "食品餐饮", "三餐", taxonomy)
        assert not validate_pair("支出", "食品餐饮", "不存在的子类", taxonomy)
        assert not validate_pair("收入", "食品餐饮", "三餐", taxonomy)

    def test_expense_rule_not_applied_to_income(self):
        category, subcategory, _tags, basis = classify_record(
            "测试超市", "生活用品", "收入", self.RULES, {}, categories={"购物消费": ["日常家居"]}
        )
        assert category == "" and basis == "待分类"

    def test_unmatched_queued_for_ai(self, extracted):
        records = extracted[0]
        bank_records, _ = extract_bank_all(records)
        payapps = sorted(records["wx"] + records["zfb"], key=lambda r: r["datetime"])
        ledger = LedgerBuilder(CONFIG, payapps, bank_records).run()
        taxonomy = __import__("json").loads((FIXTURES.parent.parent.parent / "app/core/data/taxonomy.json").read_text())
        rows, unmatched = build_import_rows(CONFIG, ledger, taxonomy, [])
        assert unmatched
        assert any(key[1] == "神秘未知商户XYZ" for key in unmatched)


class TestExport:
    def _rows(self):
        return [
            {
                "日期": "2026-06-01", "收支类型": "支出", "金额": 12.34, "类别": "食品餐饮", "子类": "三餐",
                "所属账本": "日常账本", "收支账户": "支付宝余额", "备注": "测试", "标签": "小额",
                "_依据": "测试",
            }
        ]

    def test_biff8_readback(self, tmp_path):
        rows = self._rows()
        path = tmp_path / "out.xls"
        report = write_yimu_xls(rows, path)
        assert report["validation_status"] == "PASS"
        assert report["data_row_count"] == 1
        assert path.read_bytes()[:8] == bytes.fromhex("D0CF11E0A1B11AE1")

    def test_biff8_rejects_zero_amount(self, tmp_path):
        rows = [{**self._rows()[0], "金额": 0}]
        with pytest.raises(ValueError):
            build_cfb(build_biff_workbook(rows))

    def test_finalize_rejects_invalid_category(self, tmp_path):
        rows = [{**self._rows()[0], "类别": "不存在的类", "子类": "x"}]
        with pytest.raises(ValueError):
            finalize_rows(rows, {"食品餐饮": ["三餐"]})

    def test_formula_like_text_is_stored_as_string(self, tmp_path):
        rows = [{**self._rows()[0], "备注": "=HYPERLINK(\"http://evil.example\")"}]
        path = tmp_path / "out.xls"
        report = write_yimu_xls(rows, path)
        assert report["validation_status"] == "PASS"
