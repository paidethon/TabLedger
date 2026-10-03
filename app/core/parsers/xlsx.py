"""Hardened OOXML reading for untrusted bill files.

The legacy pipeline parsed WeChat XLSX with plain ElementTree, which is
vulnerable to XML entity expansion ("billion laughs") on hostile input.
Here all XML is parsed with defusedxml and every decompressed stream is
size-capped so a decompression bomb cannot exhaust memory.
"""

from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from defusedxml import ElementTree as DefusedET

logger = logging.getLogger(__name__)

XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

MAX_XLSX_INNER_BYTES = 32 * 1024 * 1024
MAX_XLSX_ENTRIES = 512


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    info = archive.getinfo(name)
    if info.file_size > MAX_XLSX_INNER_BYTES:
        raise ValueError(f"xlsx member {name} too large: {info.file_size} bytes")
    return archive.read(name)


def excel_column_index(cell_reference: str) -> int:
    match = re.match(r"([A-Z]+)", cell_reference)
    if not match:
        raise ValueError(f"Invalid Excel cell reference: {cell_reference!r}")
    result = 0
    for char in match.group(1):
        result = result * 26 + ord(char) - ord("A") + 1
    return result - 1


def _xlsx_cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.get("t", "")
    value_node = cell.find(f"{XLSX_NS}v")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(f"{XLSX_NS}t"))
    if value_node is None or value_node.text is None:
        return ""
    if cell_type == "s":
        index = int(value_node.text)
        if index >= len(shared_strings):
            raise ValueError(f"shared string index out of range: {index}")
        return shared_strings[index]
    return value_node.text


def read_first_xlsx_sheet(path_or_bytes) -> tuple[dict[int, list[str]], list[str]]:
    """Read the first worksheet of an (untrusted) xlsx as a row map."""

    if isinstance(path_or_bytes, (bytes, bytearray)):
        archive_ctx = zipfile.ZipFile(io.BytesIO(bytes(path_or_bytes)))
    else:
        archive_ctx = zipfile.ZipFile(path_or_bytes)

    with archive_ctx as archive:
        names = set(archive.namelist())
        if len(names) > MAX_XLSX_ENTRIES:
            raise ValueError(f"xlsx contains too many entries: {len(names)}")
        sheet_name = _first_sheet_target(archive, names)
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            shared_root = DefusedET.fromstring(_read_member(archive, "xl/sharedStrings.xml"))
            for item in shared_root.findall(f"{XLSX_NS}si"):
                shared_strings.append(
                    "".join(node.text or "" for node in item.iter(f"{XLSX_NS}t"))
                )
        sheet_root = DefusedET.fromstring(_read_member(archive, sheet_name))
        rows: dict[int, list[str]] = {}
        for row in sheet_root.iter(f"{XLSX_NS}row"):
            row_number = int(row.get("r", "0"))
            values: dict[int, str] = {}
            for cell in row.findall(f"{XLSX_NS}c"):
                index = excel_column_index(cell.get("r", ""))
                values[index] = _xlsx_cell_value(cell, shared_strings)
            if values:
                last_index = max(values)
                rows[row_number] = [values.get(index, "") for index in range(last_index + 1)]
        return rows, shared_strings


def _first_sheet_target(archive: zipfile.ZipFile, names: set[str]) -> str:
    """Resolve the first sheet via workbook.xml; fall back to sheet1.xml."""

    if "xl/workbook.xml" in names and "xl/_rels/workbook.xml.rels" in names:
        try:
            workbook = DefusedET.fromstring(_read_member(archive, "xl/workbook.xml"))
            rels = DefusedET.fromstring(_read_member(archive, "xl/_rels/workbook.xml.rels"))
            first_sheet = workbook.find(f"{XLSX_NS}sheets")
            sheet = None
            if first_sheet is not None:
                sheets = first_sheet.findall(f"{XLSX_NS}sheet")
                if sheets:
                    sheet = sheets[0]
            if sheet is not None:
                rid = sheet.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                )
                rel_targets = {}
                for rel in rels:
                    rel_targets[rel.get("Id")] = rel.get("Target", "")
                if rid and rid in rel_targets:
                    target = rel_targets[rid].lstrip("/")
                    if not target.startswith("xl/"):
                        target = f"xl/{target}"
                    if target in names and ".." not in target and ":" not in target:
                        return target
        except Exception:
            logger.debug("workbook.xml sheet resolution failed; using fallback path")
    if "xl/worksheets/sheet1.xml" in names:
        return "xl/worksheets/sheet1.xml"
    raise ValueError("xlsx does not contain a readable first worksheet")


def parse_excel_datetime(value: str, *, location: str) -> str:
    try:
        serial = Decimal(str(value).strip())
    except InvalidOperation as exc:
        raise ValueError(f"Invalid Excel datetime at {location}: {value!r}") from exc
    # The 1899-12-30 epoch accounts for Excel's historical 1900 leap-year bug.
    total_seconds = int((serial * Decimal(86400)).to_integral_value(rounding=ROUND_HALF_UP))
    parsed = datetime(1899, 12, 30) + timedelta(seconds=total_seconds)
    return parsed.strftime("%Y-%m-%d %H:%M:%S")
