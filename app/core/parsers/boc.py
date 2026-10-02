"""BOC (中国银行) PDF statement parser."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.context import EngineConfig
from app.core.money import HONG_KONG_TZ
from app.core.parsers.common import (
    file_sha256,
    make_record,
    normalize_account,
    normalize_text,
    parse_money,
    validate_schema,
)
from app.core.parsers.pdf import MAX_PDF_PAGES, extract_single_table, open_statement


def extract_boc(
    path, password: str | None, config: EngineConfig
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    source = "中国银行"
    source_order_records: list[dict[str, Any]] = []
    page_row_counts: list[int] = []

    with open_statement(path, password) as pdf:
        if len(pdf.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF has too many pages: {len(pdf.pages)}")
        page_count = len(pdf.pages)
        for page_number, page in enumerate(pdf.pages, start=1):
            table = extract_single_table(page, position=f"{source} page {page_number}")
            if len(table[0]) != 12:
                raise ValueError(
                    f"Expected 12 BOC columns on page {page_number}, found {len(table[0])}"
                )
            data_rows = table[1:]
            page_row_counts.append(len(data_rows))

            for table_row, cells in enumerate(data_rows, start=1):
                if len(cells) != 12:
                    raise ValueError(
                        f"Expected 12 BOC cells at page {page_number} row {table_row}, found {len(cells)}"
                    )
                source_row = len(source_order_records) + 1
                dt_text = f"{normalize_text(cells[0])} {normalize_text(cells[1])}"
                try:
                    date_time = datetime.strptime(dt_text, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=HONG_KONG_TZ
                    )
                except ValueError as exc:
                    raise ValueError(
                        f"Invalid BOC datetime at page {page_number} row {table_row}: {dt_text!r}"
                    ) from exc
                signed_amount = parse_money(
                    cells[3], position=f"{source} page {page_number} row {table_row} amount"
                )
                balance = parse_money(
                    cells[4], position=f"{source} page {page_number} row {table_row} balance"
                )
                raw_type = normalize_text(cells[5])
                payment_method = normalize_text(cells[6])
                branch_name = normalize_text(cells[7])
                note = normalize_text(cells[8])
                raw_counterparty = normalize_text(cells[9])
                raw_account = normalize_account(cells[10])
                counterparty_bank = normalize_text(cells[11])
                location_parts = []
                if branch_name:
                    location_parts.append(f"网点:{branch_name}")
                if counterparty_bank:
                    location_parts.append(f"对方开户行:{counterparty_bank}")
                raw_location = "｜".join(location_parts)
                flags: list[str] = []
                if not note:
                    flags.append("note_missing_item_fallback")
                if not raw_counterparty:
                    flags.append("counterparty_missing_merchant_fallback")
                if not raw_account:
                    flags.append("counterparty_account_missing")

                source_order_records.append(
                    make_record(
                        config,
                        source=source,
                        source_row=source_row,
                        date_time=date_time,
                        signed_amount=signed_amount,
                        balance=balance,
                        merchant=raw_counterparty or note or raw_type,
                        item=note or raw_type,
                        payment_method=payment_method,
                        raw_type=raw_type,
                        raw_counterparty=raw_counterparty,
                        raw_account=raw_account,
                        raw_location=raw_location,
                        parse_flags=flags,
                    )
                )

    # The statement is newest-first.  Reversing (rather than merely sorting)
    # preserves the correct balance-chain order for transactions sharing an
    # identical timestamp.
    records = list(reversed(source_order_records))
    metadata = {
        "input_file": path.name,
        "input_sha256": file_sha256(path),
        "pdf_pages": page_count,
        "page_row_counts": page_row_counts,
        "original_order": "descending",
        "output_order": "ascending (exact reverse of PDF row order)",
    }
    errors = validate_schema(records)
    if errors:
        raise ValueError(f"{source} schema validation failed: {errors[:5]}")
    return records, metadata
