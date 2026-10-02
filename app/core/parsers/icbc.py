"""ICBC (工商银行) PDF statement parser."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.core.context import EngineConfig
from app.core.money import HONG_KONG_TZ
from app.core.parsers.common import (
    bank_datetime,
    file_sha256,
    make_record,
    normalize_account,
    normalize_text,
    parse_money,
    validate_schema,
)
from app.core.parsers.pdf import (
    MAX_PDF_PAGES,
    detect_watermark_fonts,
    extract_single_table,
    open_statement,
)


def extract_icbc(
    path, password: str | None, config: EngineConfig
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = "工商银行"
    records: list[dict[str, Any]] = []
    page_row_counts: list[int] = []
    watermark_counts: list[int] = []

    with open_statement(path, password) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has too many pages: {len(pdf.pages)}")
        page_count = len(pdf.pages)
        watermark_fonts = detect_watermark_fonts(pdf)
        for page_number, page in enumerate(pdf.pages, start=1):
            watermark_count = sum(char["fontname"] in watermark_fonts for char in page.chars)
            watermark_counts.append(watermark_count)
            clean_page = page.filter(lambda obj: obj.get("fontname") not in watermark_fonts)
            table = extract_single_table(clean_page, position=f"{source} page {page_number}")
            if len(table[0]) != 13:
                raise ValueError(
                    f"Expected 13 ICBC columns on page {page_number}, found {len(table[0])}"
                )
            data_rows = table[1:]
            page_row_counts.append(len(data_rows))

            for table_row, cells in enumerate(data_rows, start=1):
                if len(cells) != 13:
                    raise ValueError(
                        f"Expected 13 ICBC cells at page {page_number} row {table_row}, found {len(cells)}"
                    )
                source_row = len(records) + 1
                dt_text = normalize_text(cells[0]).replace(" ", "")
                match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(\d{2}:\d{2}:\d{2})", dt_text)
                if not match:
                    raise ValueError(
                        f"Invalid ICBC datetime at page {page_number} row {table_row}: {cells[0]!r}"
                    )
                date_time = datetime.strptime(
                    f"{match.group(1)} {match.group(2)}", "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=HONG_KONG_TZ)
                signed_amount = parse_money(
                    cells[8], position=f"{source} page {page_number} row {table_row} amount"
                )
                balance = parse_money(
                    cells[9], position=f"{source} page {page_number} row {table_row} balance"
                )
                raw_type = normalize_text(cells[6])
                raw_counterparty = normalize_text(cells[10])
                raw_account = normalize_account(cells[11])
                raw_location = normalize_text(cells[7])
                payment_method = normalize_text(cells[12])
                flags: list[str] = []
                if not raw_counterparty:
                    flags.append("counterparty_missing_merchant_fallback")
                if not raw_account:
                    flags.append("counterparty_account_missing")

                records.append(
                    make_record(
                        config,
                        source=source,
                        source_row=source_row,
                        date_time=date_time,
                        signed_amount=signed_amount,
                        balance=balance,
                        merchant=raw_counterparty,
                        item=raw_type,
                        payment_method=payment_method,
                        raw_type=raw_type,
                        raw_counterparty=raw_counterparty,
                        raw_account=raw_account,
                        raw_location=raw_location,
                        parse_flags=flags,
                    )
                )

    metadata = {
        "input_file": path.name,
        "input_sha256": file_sha256(path),
        "pdf_pages": page_count,
        "page_row_counts": page_row_counts,
        "original_order": "ascending",
        "watermark_filter": {
            "fonts": sorted(watermark_fonts),
            "characters_removed_by_page": watermark_counts,
            "characters_removed_total": sum(watermark_counts),
        },
    }
    errors = validate_schema(records)
    if errors:
        raise ValueError(f"{source} schema validation failed: {errors[:5]}")
    return records, metadata
