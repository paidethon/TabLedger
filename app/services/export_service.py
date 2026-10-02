"""Export service: build Yimu files from reviewed job state."""

from __future__ import annotations

import io
import zipfile
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DBSession

from app.config import get_settings
from app.core.classification.rules import validate_pair
from app.core.exporters.biff8 import build_biff_workbook, build_cfb, validate_xls
from app.db.models import Job, Match, Transaction
from app.services import settings_service


def _quantize(value: Any) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def collect_import_rows(db: DBSession, job_id: str, config) -> tuple[list[dict], dict[str, dict]]:
    """Assemble the final 9-column rows from persisted transactions.

    Import eligibility follows the legacy dispositions: expense dispositions
    (gross for full-refund originals, net for withdrawal fees) plus the
    income dispositions.  A transaction with no category stays behind and is
    reported through ``missing``.
    """

    transactions = db.scalars(select(Transaction).where(Transaction.job_id == job_id).order_by(Transaction.datetime, Transaction.source)).all()
    EXPENSE_OK = {"导入-支出", "导入-提现手续费", "导入-部分退款净额", "导入-银行直连支出", "排除-全额退款原单"}
    INCOME_OK = {"排除-收入", "排除-退款入账", "保留-跨期退款收入"}
    rows: list[dict] = []
    missing: dict[str, dict] = {}
    index = 0
    for tx in transactions:
        if tx.disposition in INCOME_OK:
            direction = "收入"
            amount = _quantize(tx.amount_cents / 100)
        elif tx.disposition in EXPENSE_OK:
            direction = "支出"
            base = tx.net_amount_cents if tx.disposition == "导入-提现手续费" else tx.amount_cents
            amount = _quantize(base / 100)
        else:
            continue
        if amount <= 0:
            continue
        if not tx.category:
            key = f"{direction}|{tx.merchant or tx.item}"
            slot = missing.setdefault(
                key,
                {"匹配键": tx.merchant or tx.item, "收支": direction, "笔数": 0, "金额合计": 0.0, "示例商品": []},
            )
            slot["笔数"] += 1
            slot["金额合计"] = round(slot["金额合计"] + amount, 2)
            if tx.item and tx.item not in slot["示例商品"] and len(slot["示例商品"]) < 3:
                slot["示例商品"].append(tx.item)
            continue
        index += 1
        account = tx.account or ""
        for card in config.bank_cards:
            if account == card.display_name and card.yimu_account:
                account = card.yimu_account
                break
        rows.append(
            {
                "日期": tx.datetime[:10],
                "收支类型": direction,
                "金额": amount,
                "类别": tx.category,
                "子类": tx.subcategory,
                "所属账本": config.ledger_name,
                "收支账户": "微信零钱" if tx.disposition == "导入-提现手续费" else (account or tx.source),
                "备注": tx.merchant if not tx.item or _same_text(tx.merchant, tx.item) else f"{tx.merchant}｜{tx.item}",
                "标签": tx.tags,
                "_record_uid": tx.record_uid,
                "_import_index": index,
            }
        )
    return rows, missing


def _same_text(a: str, b: str) -> bool:
    import re

    strip = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    return strip(a) == strip(b)


def build_workbook_bytes(rows: list[dict]) -> bytes:
    return build_cfb(build_biff_workbook(rows))


def validate_rows(rows: list[dict], taxonomy: dict) -> None:
    invalid = [
        (r["收支类型"], r["类别"], r["子类"])
        for r in rows
        if not validate_pair(r["收支类型"], r["类别"], r["子类"], taxonomy)
    ]
    if invalid:
        raise ValueError(f"分类不合法（对照分类体系）：{invalid[:10]}")


def export_job(db: DBSession, job_id: str) -> dict[str, Any]:
    """Run the export for a reviewed job. Returns artifact descriptors."""

    job = db.get(Job, job_id)
    if job is None:
        raise ValueError("任务不存在")
    config = settings_service.engine_config(db)
    taxonomy = settings_service.load_taxonomy()
    rows, missing = collect_import_rows(db, job_id, config)
    if missing:
        raise ValueError(f"还有 {len(missing)} 个待分类键，请先完成审核")

    validate_rows(rows, taxonomy)
    report = _write_and_validate(db, job_id, rows, "bill", f"一木记账_账单_{job_id}.xls")

    # Summary counts
    expense_rows = [r for r in rows if r["收支类型"] == "支出"]
    income_rows = [r for r in rows if r["收支类型"] == "收入"]
    stats = {
        "original_records": job.stats.get("original_records", 0) if job.stats else 0,
        "export_rows": len(rows),
        "expense_rows": len(expense_rows),
        "income_rows": len(income_rows),
        "expense_total": _quantize(sum(Decimal(str(r["金额"])) for r in expense_rows)),
        "income_total": _quantize(sum(Decimal(str(r["金额"])) for r in income_rows)),
        "deduped": sum(
            1
            for tx in db.scalars(select(Transaction).where(Transaction.job_id == job_id))
            if tx.disposition in {"影子重复-平台记录优先"}
        ),
        "internal_transfers_excluded": sum(
            1
            for tx in db.scalars(select(Transaction).where(Transaction.job_id == job_id))
            if tx.disposition == "排除-内部资金搬运"
        ),
        "refunds": sum(
            1
            for tx in db.scalars(select(Transaction).where(Transaction.job_id == job_id))
            if tx.refund_original_uid or tx.refund_uids
        ),
        "validation": report,
    }
    job.stats = {**(job.stats or {}), **stats}
    job.status = "done"
    db.commit()
    return {"file": report["file_name"], "stats": stats, "validation": report}


def _write_and_validate(db: DBSession, job_id: str, rows: list[dict], kind: str, file_name: str) -> dict[str, Any]:
    artifacts = get_settings().data_dir / "jobs" / job_id / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    path = artifacts / file_name
    stream = build_biff_workbook(rows)
    path.write_bytes(build_cfb(stream))
    report = validate_xls(path, len(rows))
    if report.get("validation_status") != "PASS":
        raise ValueError("回读校验未通过，已中止导出")
    report["file_name"] = file_name
    return report


def build_review_workbook(db: DBSession, job_id: str) -> bytes:
    """A human-checkable zip: 待分类清单 + transactions CSV."""

    config = settings_service.engine_config(db)
    rows, missing = collect_import_rows(db, job_id, config)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("说明.txt", "TabLedger 核对包：导出行与待分类清单（CSV）。\n")
        import csv as csv_mod

        out = io.StringIO()
        writer = csv_mod.writer(out)
        writer.writerow(["日期", "收支类型", "金额", "类别", "子类", "所属账本", "收支账户", "备注", "标签"])
        for r in rows:
            writer.writerow([r["日期"], r["收支类型"], f"{r['金额']:.2f}", r["类别"], r["子类"], r["所属账本"], r["收支账户"], r["备注"], r["标签"]])
        zf.writestr("导入行.csv", out.getvalue().encode("utf-8-sig"))

        out2 = io.StringIO()
        writer2 = csv_mod.writer(out2)
        writer2.writerow(["匹配键", "收支", "笔数", "金额合计", "示例商品"])
        for slot in missing.values():
            writer2.writerow([slot["匹配键"], slot["收支"], slot["笔数"], f"{slot['金额合计']:.2f}", "；".join(slot["示例商品"])])
        zf.writestr("待分类清单.csv", out2.getvalue().encode("utf-8-sig"))
    return buffer.getvalue()


def match_explanations(db: DBSession, job_id: str) -> list[dict[str, Any]]:
    matches = db.scalars(select(Match).where(Match.job_id == job_id).order_by(Match.id)).all()
    return [
        {
            "match_id": m.match_id,
            "match_type": m.match_type,
            "confidence": m.confidence,
            "decision": m.decision,
            "reason": m.reason,
            "platform_uids": m.platform_uids,
            "bank_uids": m.bank_uids,
            "original_uids": m.original_uids,
            "refund_uids": m.refund_uids,
            "account": m.account,
            "amount_cents": m.amount_cents,
            "time_delta_seconds": m.time_delta_seconds,
        }
        for m in matches
    ]
