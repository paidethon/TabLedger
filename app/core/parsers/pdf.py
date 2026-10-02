"""Bank PDF statement helpers (watermark filtering, single-table extraction)."""

from __future__ import annotations

import io
import re

import pdfplumber

MAX_PDF_PAGES = 200


def open_statement(path, password: str | None = None):
    """Open a bank statement; encrypted PDFs are decrypted in memory only."""

    if not password:
        return pdfplumber.open(path)
    import pikepdf

    with pikepdf.open(path, password=password) as pdf:
        buffer = io.BytesIO()
        pdf.save(buffer)
    buffer.seek(0)
    return pdfplumber.open(buffer)


def detect_watermark_fonts(pdf: pdfplumber.PDF) -> set[str]:
    """The anti-forgery watermark uses a randomly-named subset font per export;
    identify it by the verification text it carries instead of by name."""

    fonts: dict[str, list[str]] = {}
    for page in pdf.pages:
        for char in page.chars:
            fonts.setdefault(char["fontname"], []).append(char["text"])
    return {font for font, texts in fonts.items() if "二维码" in "".join(texts)}


def extract_single_table(page: pdfplumber.page.Page, *, position: str) -> list[list]:
    tables = page.extract_tables()
    if len(tables) != 1:
        raise ValueError(f"Expected one table at {position}, found {len(tables)}")
    table = tables[0]
    if not table:
        raise ValueError(f"Empty table at {position}")
    return table


def clean_header_cell(value) -> str:
    return re.sub(r"\s+", "", str(value or ""))
