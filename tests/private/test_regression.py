"""Private regression: new engine vs the legacy pipeline's committed artifacts.

Runs ONLY on the user's machine against real bill data (pytest -m private).
Reads configuration from private/regression/config.json (gitignored):

    {
      "legacy_dir": "/home/.../Tab-private/legacy",
      "periods": {"20260815-20260906": {"passwords": {...}}},
      "owner_names": ["..."],
      "cards": [{"source": "工商银行", "tail": "....", "display_name": "...", "id_prefix": "..."}]
    }

Compares against the legacy outputs (ledger.json + 一木 xls) and prints only
PASS/FAIL and difference counts — never amounts or merchant names.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from pathlib import Path

import pytest

CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "private" / "regression" / "config.json"

pytestmark = pytest.mark.private


def _config() -> dict:
    if not CONFIG_PATH.exists():
        pytest.skip("private/regression/config.json 不存在；跳过私密回归")
    cfg = json.loads(CONFIG_PATH.read_text())
    # Prefer the freshly regenerated baseline (current legacy code output)
    # over the stored artifacts, which may come from an older legacy version.
    rebuilt = Path("/tmp/legacy-run/data")
    if (rebuilt / "20260815-20260906" / "ledger.json").exists():
        cfg["baseline_data_dir"] = str(rebuilt)
    else:
        cfg["baseline_data_dir"] = str(Path(cfg["legacy_dir"]) / "data")
    return cfg


def _engine_config(cfg: dict):
    from app.core.context import BankCard, EngineConfig

    cards = tuple(
        BankCard(
            source=c["source"],
            tail=c["tail"],
            display_name=c["display_name"],
            id_prefix=c.get("id_prefix", ""),
        )
        for c in cfg.get("cards", [])
    )
    return EngineConfig(owner_names=tuple(cfg.get("owner_names", [])), bank_cards=cards)


def _legacy_entries(legacy_dir: Path, period: str) -> dict:
    return json.loads((legacy_dir / "data" / period / "entries.json").read_text())


def _legacy_ledger(legacy_dir: Path, period: str) -> dict:
    return json.loads((legacy_dir / "data" / period / "ledger.json").read_text())


@pytest.mark.parametrize("period", ["20260101-20260719", "20260815-20260906"])
def test_reconciliation_matches_legacy(period: str):
    cfg = _config()
    legacy_dir = Path(cfg["legacy_dir"])
    if not (legacy_dir / "data" / period / "entries.json").exists():
        pytest.skip(f"缺少 {period} 的真实数据")

    from app.core.extraction import BillInput, detect_and_extract, extract_bank_all
    from app.core.reconciliation.ledger import LedgerBuilder
    from app.core.reconciliation.refunds import auto_direct_bank_refund_groups

    config = _engine_config(cfg)
    raw_dir = legacy_dir / "data" / period / "原始"
    period_cfg = cfg["periods"][period]
    passwords = list(period_cfg["passwords"].values())

    inputs = [BillInput(path=raw_dir / name, name=name) for name in period_cfg["files"]]
    records_by_source, _meta, errors = detect_and_extract(inputs, config, passwords)
    assert errors == [], f"新引擎解析错误: {errors[:3]}"

    new_banks, bank_qa = extract_bank_all(records_by_source)
    assert bank_qa["errors"] == []
    payapps = sorted(records_by_source["wx"] + records_by_source["zfb"], key=lambda r: r["datetime"])

    # Bank-direct refunds: use the legacy audited override when present so the
    # comparison isolates engine logic, not human adjudication.
    override_path = legacy_dir / "data" / period / "退款回链.json"
    if override_path.exists():
        raw = json.loads(override_path.read_text())
        direct_groups = [tuple(g) for g in raw]
    else:
        direct_groups = auto_direct_bank_refund_groups(new_banks)

    builder = LedgerBuilder(config, payapps, new_banks, direct_refund_groups=direct_groups)
    new_ledger = builder.run()

    legacy = json.loads((Path(cfg["baseline_data_dir"]) / period / "ledger.json").read_text())

    # -- compare (aggregate counts only; no amounts printed) ----------------
    new_disp = new_ledger["summary"]["disposition_counts"]
    old_disp = legacy["summary"]["disposition_counts"]
    diffs = {k: (old_disp.get(k, 0), new_disp.get(k, 0)) for k in set(old_disp) | set(new_disp) if old_disp.get(k, 0) != new_disp.get(k, 0)}

    new_records = {r["record_uid"]: r for r in new_ledger["all_records"]}
    old_records = {r["record_uid"]: r for r in legacy["all_records"]}
    uid_diffs = len(set(new_records) ^ set(old_records))
    mismatched = [
        uid
        for uid in set(new_records) & set(old_records)
        if new_records[uid]["disposition"] != old_records[uid]["disposition"]
    ]

    # expense/income totals must match exactly
    def totals(records):
        expense = sum((Decimal(str(r["amount"])) for r in records.values() if str(r["disposition"]).startswith("导入-") and r["flow"] != "收入"), Decimal("0"))
        income = sum((Decimal(str(r["amount"])) for r in records.values() if r["disposition"] in {"排除-收入", "保留-跨期退款收入", "排除-退款入账"}), Decimal("0"))
        return expense, income

    new_exp, new_inc = totals(new_records)
    old_exp, old_inc = totals(old_records)

    report = {
        "disposition_diffs": diffs,
        "record_set_diffs": uid_diffs,
        "disposition_mismatches": len(mismatched),
        "totals_match": (new_exp == old_exp and new_inc == old_inc),
    }
    print(f"\n[{period}] PASS={not diffs and uid_diffs == 0 and not mismatched and report['totals_match']} diffs={json.dumps({**report, 'mismatched': mismatched[:5]}, ensure_ascii=False, default=str)}")

    assert not diffs, f"处置分布与旧版不一致: {diffs}"
    assert uid_diffs == 0, f"记录集合不一致: {uid_diffs} 条"
    assert not mismatched, f"处置不一致: {mismatched[:5]}"
    assert report["totals_match"]


@pytest.mark.parametrize("period", ["20260101-20260719", "20260815-20260906"])
def test_extraction_counts_match_legacy_entries(period: str):
    cfg = _config()
    legacy_dir = Path(cfg["legacy_dir"])
    if not (legacy_dir / "data" / period / "entries.json").exists():
        pytest.skip(f"缺少 {period} 的真实数据")

    from app.core.extraction import BillInput, detect_and_extract

    config = _engine_config(cfg)
    raw_dir = legacy_dir / "data" / period / "原始"
    period_cfg = cfg["periods"][period]
    passwords = list(period_cfg["passwords"].values())
    inputs = [BillInput(path=raw_dir / name, name=name) for name in period_cfg["files"]]
    records_by_source, _meta, errors = detect_and_extract(inputs, config, passwords)
    assert errors == []

    legacy = json.loads((Path(cfg["baseline_data_dir"]) / period / "entries.json").read_text())
    legacy_counts = {
        "wx": sum(1 for r in legacy["payapps"] if r["source"] == "wx"),
        "zfb": sum(1 for r in legacy["payapps"] if r["source"] == "zfb"),
    }
    for source in ("工商银行", "中国银行"):
        legacy_counts[source] = sum(1 for r in legacy["banks"] if r["source"] == source)

    for source, expected in legacy_counts.items():
        actual = len(records_by_source.get(source, []))
        assert actual == expected, f"{source}: 新 {actual} vs 旧 {expected}"
