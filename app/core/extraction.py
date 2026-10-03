"""In-memory extraction pipeline: detect bill sources, parse, validate.

This replaces the legacy ``run_extract``: instead of writing entries.json
into a period folder, it returns the parsed records and QA report, letting
the job service persist them where it wants.  Inputs are server-side paths
with random IDs; user filenames are used only as weak detection signals.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.core.context import EngineConfig
from app.core.parsers import archive
from app.core.parsers.alipay import extract_zfb
from app.core.parsers.boc import extract_boc
from app.core.parsers.common import file_sha256, validate_schema
from app.core.parsers.icbc import extract_icbc
from app.core.parsers.wechat import extract_wx

BANK_EXTRACTORS = {"工商银行": extract_icbc, "中国银行": extract_boc}
BANK_ORDER = ("工商银行", "中国银行")

MAX_TOTAL_RECORDS = 20000


@dataclass
class BillInput:
    """One uploaded bill staged on disk under a server-generated name."""

    path: Any  # Path
    name: str  # original display name (weak signal only)
    password: str | None = None


def _suffix(name: str) -> str:
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


def _read_bytes(path) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def _detect_zip_inner(data: bytes, inner_name: str) -> str | None:
    low = inner_name.lower()
    if low.endswith(".csv") and archive.sniff_payapp_csv(data):
        return "zfb"
    if low.endswith(".xlsx") and archive.sniff_wx_xlsx(data):
        return "wx"
    # Filename is only a weak signal; content decides when sniffing works.
    if archive.sniff_payapp_csv(data):
        return "zfb"
    if archive.sniff_wx_xlsx(data):
        return "wx"
    return None


def detect_and_extract(
    inputs: list[BillInput], config: EngineConfig, passwords: list[str] | None = None
) -> tuple[dict[str, list[dict]], dict[str, Any], list[str]]:
    """Detect and extract every bill.  Returns (records_by_source, metadata, errors).

    ``records_by_source`` keys: "wx", "zfb", "工商银行", "中国银行".
    Passwords passed here are merged with per-file passwords and stay in memory.
    """

    passwords = list(passwords or [])
    records_by_source: dict[str, list[dict]] = {}
    metadata: dict[str, Any] = {"files": []}
    errors: list[str] = []

    for item in inputs:
        suffix = _suffix(item.name)
        if suffix not in archive.SUPPORTED_SUFFIXES:
            errors.append(f"{item.name}: 不支持的文件类型")
            metadata["files"].append({"name": item.name, "status": "rejected", "reason": "unsupported type"})
            continue
        file_passwords = ([item.password] if item.password else []) + passwords
        try:
            if suffix == ".zip":
                data, inner_name = archive.unzip_bill(item.path, file_passwords, item.name)
                kind = _detect_zip_inner(data, inner_name)
                if kind == "zfb":
                    records, meta = extract_zfb(data, config)
                    records_by_source.setdefault("zfb", []).extend(records)
                    metadata["files"].append(
                        {"name": item.name, "inner": inner_name, "source": "zfb", "status": "ok", **meta}
                    )
                elif kind == "wx":
                    records, meta = extract_wx(data, config)
                    records_by_source.setdefault("wx", []).extend(records)
                    metadata["files"].append(
                        {"name": item.name, "inner": inner_name, "source": "wx", "status": "ok", **meta}
                    )
                else:
                    raise ValueError(f"无法识别压缩包内容：{inner_name}")
            elif suffix == ".csv":
                if not archive.sniff_payapp_csv(_read_bytes(item.path)):
                    raise ValueError("CSV 内容不是可识别的支付宝账单")
                records, meta = extract_zfb(item.path, config)
                records_by_source.setdefault("zfb", []).extend(records)
                metadata["files"].append({"name": item.name, "source": "zfb", "status": "ok", **meta})
            elif suffix == ".xlsx":
                if not archive.sniff_wx_xlsx(_read_bytes(item.path)):
                    raise ValueError("XLSX 内容不是可识别的微信账单")
                records, meta = extract_wx(item.path, config)
                records_by_source.setdefault("wx", []).extend(records)
                metadata["files"].append({"name": item.name, "source": "wx", "status": "ok", **meta})
            elif suffix == ".pdf":
                source, _pdf, matched_password = archive.sniff_bank_pdf(item.path, file_passwords)
                records, meta = BANK_EXTRACTORS[source](item.path, matched_password, config)
                records_by_source.setdefault(source, []).extend(records)
                metadata["files"].append(
                    {"name": item.name, "source": source, "status": "ok", "password_matched": bool(matched_password), **meta}
                )
        except Exception as exc:
            errors.append(f"{item.name}: {exc}")
            metadata["files"].append({"name": item.name, "status": "error", "reason": str(exc)})

    total = sum(len(v) for v in records_by_source.values())
    if total > MAX_TOTAL_RECORDS:
        errors.append(f"记录总数超过上限: {total}")
    return records_by_source, metadata, errors


def extract_bank_all(records_by_source: dict[str, list[dict]]) -> tuple[list[dict], dict[str, Any]]:
    """Validate balance chains for bank records and merge, sorted by time."""

    all_records: list[dict] = []
    per_source: dict[str, Any] = {}
    errors: list[str] = []
    for source in BANK_ORDER:
        records = records_by_source.get(source, [])
        if not records:
            continue
        expense = -sum(
            (Decimal(str(r["signed_amount"])) for r in records if r["flow"] == "支出"), Decimal("0")
        )
        income = sum(
            (Decimal(str(r["signed_amount"])) for r in records if r["flow"] == "收入"), Decimal("0")
        )
        chain_bad: list[int] = []
        prev = None
        for r in records:
            if prev is not None:
                expected = (
                    Decimal(str(prev["balance"])) + Decimal(str(r["signed_amount"]))
                ).quantize(Decimal("0.01"))
                if expected != Decimal(str(r["balance"])):
                    chain_bad.append(r["source_row"])
            prev = r
        per_source[source] = {
            "record_count": len(records),
            "expense": str(expense),
            "income": str(income),
            "balance_chain_ok": not chain_bad,
            "chain_bad_rows": chain_bad[:10],
        }
        if chain_bad:
            errors.append(f"{source} 余额链断裂行：{chain_bad[:10]}")
        errors.extend(validate_schema(records))
        all_records.extend(records)
    all_records.sort(key=lambda r: (r["datetime"], r["source"]))
    return all_records, {"banks": per_source, "errors": errors}


def reconcile_payapps(records: list[dict], metadata: dict[str, Any]) -> dict[str, Any]:
    """Declared-summary reconciliation: WeChat must match exactly; the Alipay
    gap caused by 交易关闭 orders is informational, not blocking.

    (The legacy pipeline parsed the declared summary but never wired it into
    this comparison; TabLedger performs the actual comparison.)
    """

    declared_by_source: dict[str, dict] = {}
    for f in metadata.get("files", []):
        if f.get("status") == "ok" and f.get("declared"):
            declared_by_source.setdefault(f["source"], f["declared"])

    report: dict[str, Any] = {}
    for source in ("wx", "zfb"):
        rows = [r for r in records if r["source"] == source]
        flows: dict[str, dict[str, str]] = {}
        for flow in ("收入", "支出", "不计收支"):
            total = sum((Decimal(str(r["amount"])) for r in rows if r["flow"] == flow), Decimal("0"))
            flows[flow] = {
                "count": sum(1 for r in rows if r["flow"] == flow),
                "amount": f"{total:.2f}",
            }
        declared = declared_by_source.get(source, {})
        entry: dict[str, Any] = {
            "record_count": len(rows),
            "flows": flows,
            "declared": declared,
        }
        if declared:
            checks = {}
            for flow in ("收入", "支出", "不计收支"):
                d_amt = Decimal(str(declared.get(flow, {}).get("amount", "0")))
                a_amt = Decimal(flows[flow]["amount"])
                d_cnt = declared.get(flow, {}).get("count", 0)
                a_cnt = flows[flow]["count"]
                checks[flow] = {
                    "declared_count": d_cnt,
                    "actual_count": a_cnt,
                    "declared_amount": f"{d_amt:.2f}",
                    "actual_amount": f"{a_amt:.2f}",
                    "count_match": d_cnt == a_cnt,
                    "amount_match": abs(d_amt - a_amt) <= Decimal("0.005"),
                }
            entry["declared_check"] = checks
        report[source] = entry
    return report


def file_fingerprint(item: BillInput) -> str:
    """Stable content hash for duplicate-upload detection (no filename in it)."""

    return file_sha256(item.path)


def fingerprint_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
