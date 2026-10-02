"""Yimu (一木记账) export helpers."""

from __future__ import annotations

from pathlib import Path

from app.core.classification.rules import validate_pair
from app.core.exporters.biff8 import build_biff_workbook, build_cfb, validate_xls


def finalize_rows(rows: list[dict], categories: dict) -> list[dict]:
    """Reject any row whose category pair is not in the taxonomy."""

    invalid = [
        (r["_依据"], r["类别"], r["子类"])
        for r in rows
        if not validate_pair(r["收支类型"], r["类别"], r["子类"], categories)
    ]
    if invalid:
        raise ValueError(f"分类不合法（对照分类体系）：{invalid[:10]}")
    return rows


def write_yimu_xls(rows: list[dict], output: Path) -> dict:
    """Write the 9-column BIFF8 .xls and strictly validate by reading it back."""

    stream = build_biff_workbook(rows)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(build_cfb(stream))
    report = validate_xls(output, len(rows))
    if report.get("validation_status") != "PASS":
        raise ValueError(f"回读校验未通过：{report}")
    return report
