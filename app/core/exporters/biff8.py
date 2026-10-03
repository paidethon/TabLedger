"""Pure-Python BIFF8 (.xls, Excel 97-2003) writer with strict read-back validation.

Ported verbatim from the legacy pipeline's proven implementation: writes the
nine-column Yimu import sheet as a minimal valid CFB v3 container, then
re-reads and validates the produced bytes (headers, row count, amounts,
dates, formats).  Strings are stored in the SST as UTF-16 text cells, so a
cell can never be interpreted as a formula by Excel.
"""

from __future__ import annotations

import math
import struct
from collections import OrderedDict
from collections.abc import Iterator, Sequence
from pathlib import Path

EXPECTED_HEADERS = [
    "日期",
    "收支类型",
    "金额",
    "类别",
    "子类",
    "所属账本",
    "收支账户",
    "备注",
    "标签",
]

SHEET_NAME = "Sheet1"

BIFF_MAX_RECORD_DATA = 8224


def biff_record(record_id: int, payload: bytes = b"") -> bytes:
    """Return one BIFF record, enforcing the BIFF8 record-size limit."""

    if len(payload) > BIFF_MAX_RECORD_DATA:
        raise ValueError(f"BIFF payload 0x{record_id:04X} is too large: {len(payload)} bytes")
    return struct.pack("<HH", record_id, len(payload)) + payload


def bof_record(substream_type: int) -> bytes:
    """BIFF8 BOF: version 0x0600 with a conventional Excel 97 build marker."""

    payload = struct.pack(
        "<HHHHII",
        0x0600,  # BIFF8
        substream_type,
        0x0DBB,  # build identifier used by Excel 97-era writers
        0x07CC,  # build year (1996)
        0x00000041,
        0x00000006,
    )
    return biff_record(0x0809, payload)


def short_unicode_string(value: str) -> bytes:
    """BIFF8 ShortXLUnicodeString, always stored as UTF-16LE."""

    encoded = value.encode("utf-16le")
    code_units = len(encoded) // 2
    if code_units > 255:
        raise ValueError("Short BIFF Unicode string exceeds 255 UTF-16 code units")
    return struct.pack("<BB", code_units, 0x01) + encoded


def sst_unicode_string(value: str) -> bytes:
    """BIFF8 XLUnicodeRichExtendedString without rich/ext runs."""

    encoded = value.encode("utf-16le")
    code_units = len(encoded) // 2
    if code_units > 0x7FFF:
        raise ValueError("BIFF cell text exceeds 32767 UTF-16 code units")
    return struct.pack("<HB", code_units, 0x01) + encoded


def font_record(name: str, *, bold: bool) -> bytes:
    """Create an 11-point Simplified-Chinese BIFF8 FONT record."""

    # dyHeight, grbit, icv, bls, sss, uls, bFamily, bCharSet, reserved
    prefix = struct.pack(
        "<HHHHHBBBB",
        220,  # 11 points in twentieths of a point
        0,
        0x7FFF,  # automatic colour
        700 if bold else 400,
        0,
        0,
        2,  # Roman-family hint; Excel uses the explicit font name
        134,  # GB2312 charset
        0,
    )
    return biff_record(0x0031, prefix + short_unicode_string(name))


def xf_record(
    font_index: int,
    number_format_index: int,
    *,
    style_xf: bool = False,
    used_attributes: int = 0,
) -> bytes:
    """Create a compact BIFF8 XF record.

    XF 0 is the Normal style XF.  Cell XFs inherit from it; the amount XF marks
    the number-format attribute as used, while the header XF marks its font.
    """

    type_and_parent = 0xFFF5 if style_xf else 0x0001
    payload = struct.pack(
        "<HHHBBBBIIH",
        font_index,
        number_format_index,
        type_and_parent,
        0,  # horizontal/vertical alignment
        0,  # rotation
        0,  # indentation/shrink/reading order
        used_attributes,
        0,  # border lines and colours, part 1
        0,  # border lines and colours, part 2
        0,  # fill pattern and colours
    )
    assert len(payload) == 20
    return biff_record(0x00E0, payload)


def make_sst_records(strings: Sequence[str], total_string_count: int) -> bytes:
    """Build SST + CONTINUE records, splitting only between complete strings.

    Splitting at string boundaries avoids the special continuation compression
    flag needed when a Unicode character array itself crosses a record boundary.
    The longest source string is checked separately and is far below 8224 bytes.
    """

    entries = [sst_unicode_string(value) for value in strings]
    if entries and max(map(len, entries)) > BIFF_MAX_RECORD_DATA:
        raise ValueError("An SST string is too large for boundary-only splitting")

    chunks: list[bytes] = []
    current = bytearray(struct.pack("<II", total_string_count, len(strings)))
    for entry in entries:
        if len(current) + len(entry) > BIFF_MAX_RECORD_DATA:
            chunks.append(bytes(current))
            current = bytearray()
        current.extend(entry)
    chunks.append(bytes(current))

    result = bytearray()
    for index, chunk in enumerate(chunks):
        result.extend(biff_record(0x00FC if index == 0 else 0x003C, chunk))
    return bytes(result)


def boundsheet_record(sheet_offset: int, sheet_name: str = SHEET_NAME) -> bytes:
    payload = struct.pack("<IBB", sheet_offset, 0, 0) + short_unicode_string(sheet_name)
    return biff_record(0x0085, payload)


def workbook_globals(unique_strings: Sequence[str], total_strings: int, sheet_offset: int) -> bytes:
    """Return the BIFF8 workbook-global substream."""

    records = bytearray()
    records.extend(bof_record(0x0005))
    records.extend(biff_record(0x00E1, struct.pack("<H", 0x04B0)))  # INTERFACEHDR
    records.extend(biff_record(0x00C1, struct.pack("<H", 0)))  # MMS
    records.extend(biff_record(0x00E2))  # INTERFACEEND
    records.extend(biff_record(0x0042, struct.pack("<H", 1200)))  # CODEPAGE Unicode
    records.extend(biff_record(0x0161, struct.pack("<H", 0)))  # DSF
    records.extend(biff_record(0x01C0))  # EXCEL9FILE compatibility marker
    records.extend(biff_record(0x013D, struct.pack("<H", 1)))  # TABID
    records.extend(biff_record(0x009C, struct.pack("<H", 14)))  # FNGROUPCOUNT
    records.extend(biff_record(0x0019, struct.pack("<H", 0)))  # WINDOWPROTECT
    records.extend(biff_record(0x0012, struct.pack("<H", 0)))  # PROTECT
    records.extend(biff_record(0x0013, struct.pack("<H", 0)))  # PASSWORD
    records.extend(biff_record(0x01AF, struct.pack("<H", 0)))  # PROT4REV
    records.extend(biff_record(0x01BC, struct.pack("<H", 0)))  # PROT4REVPASS
    # x/y, width/height, flags, active tab, first tab, selected tabs, tab ratio
    records.extend(
        biff_record(0x003D, struct.pack("<HHHHHHHHH", 0, 0, 0x3FCF, 0x2A4E, 0x0038, 0, 0, 1, 600))
    )
    records.extend(biff_record(0x0040, struct.pack("<H", 0)))  # BACKUP
    records.extend(biff_record(0x008D, struct.pack("<H", 0)))  # HIDEOBJ
    records.extend(biff_record(0x0022, struct.pack("<H", 0)))  # DATEMODE (1900)
    records.extend(biff_record(0x000E, struct.pack("<H", 1)))  # PRECISION
    records.extend(biff_record(0x01B7, struct.pack("<H", 0)))  # REFRESHALL
    records.extend(biff_record(0x00DA, struct.pack("<H", 0)))  # BOOKBOOL

    # FONT indexes 0 and 1: body and bold header, both 宋体 11pt.
    records.extend(font_record("宋体", bold=False))
    records.extend(font_record("宋体", bold=True))

    # XF 0 Normal style, XF 1 body text, XF 2 bold header, XF 3 amount 0.00.
    records.extend(xf_record(0, 0, style_xf=True))
    records.extend(xf_record(0, 0))
    records.extend(xf_record(1, 0, used_attributes=0x02))
    records.extend(xf_record(0, 2, used_attributes=0x01))
    records.extend(biff_record(0x0293, struct.pack("<HBB", 0x8000, 0, 0)))

    records.extend(boundsheet_record(sheet_offset))
    records.extend(biff_record(0x008C, struct.pack("<HH", 86, 86)))  # COUNTRY CN
    records.extend(make_sst_records(unique_strings, total_strings))
    records.extend(biff_record(0x000A))
    return bytes(records)


def worksheet_substream(table: Sequence[Sequence[object]], sst_index: dict[str, int]) -> bytes:
    """Return the single BIFF8 worksheet substream."""

    row_count = len(table)
    col_count = len(EXPECTED_HEADERS)
    records = bytearray()
    records.extend(bof_record(0x0010))
    records.extend(biff_record(0x000D, struct.pack("<H", 1)))  # CALCMODE automatic
    records.extend(biff_record(0x000C, struct.pack("<H", 100)))  # CALCCOUNT
    records.extend(biff_record(0x000F, struct.pack("<H", 1)))  # REFMODE A1
    records.extend(biff_record(0x0011, struct.pack("<H", 0)))  # ITERATION off
    records.extend(biff_record(0x0010, struct.pack("<d", 0.001)))  # DELTA
    records.extend(biff_record(0x005F, struct.pack("<H", 1)))  # SAVERECALC
    records.extend(biff_record(0x0081, struct.pack("<H", 0)))  # WSBOOL
    records.extend(biff_record(0x0225, struct.pack("<HH", 0, 255)))  # DEFAULTROWHEIGHT
    records.extend(biff_record(0x0055, struct.pack("<H", 8)))  # DEFCOLWIDTH

    # Widths are in 1/256 character units and mirror the nine-column template.
    widths = [12, 10, 12, 16, 16, 14, 24, 60, 50]
    for column, width in enumerate(widths):
        records.extend(
            biff_record(0x007D, struct.pack("<HHHHHH", column, column, width * 256, 1, 0, 0))
        )

    records.extend(biff_record(0x0200, struct.pack("<IIHHH", 0, row_count, 0, col_count, 0)))

    for row_index, row in enumerate(table):
        if len(row) != col_count:
            raise ValueError(f"Row {row_index} has {len(row)} columns, expected 9")
        # A ROW record makes the worksheet cell table friendly to strict BIFF
        # readers as well as to Excel itself.  The 255-twip height is the BIFF
        # default; all option bits and the row-level XF are intentionally zero.
        records.extend(
            biff_record(0x0208, struct.pack("<HHHHHHI", row_index, 0, col_count, 255, 0, 0, 0))
        )
        for column_index, value in enumerate(row):
            if row_index > 0 and column_index == 2:
                amount = float(value)
                if not math.isfinite(amount) or amount <= 0:
                    raise ValueError(f"Invalid amount at row {row_index + 1}: {value!r}")
                payload = struct.pack("<HHHd", row_index, column_index, 3, amount)
                records.extend(biff_record(0x0203, payload))  # NUMBER
            else:
                text = str(value)
                try:
                    index = sst_index[text]
                except KeyError as exc:
                    raise AssertionError(f"Missing SST value: {text!r}") from exc
                xf = 2 if row_index == 0 else 1
                payload = struct.pack("<HHHI", row_index, column_index, xf, index)
                records.extend(biff_record(0x00FD, payload))  # LABELSST

    # Selected/active worksheet, standard zoom.  Kept after the cell table as
    # used by established BIFF writers.
    records.extend(biff_record(0x023E, struct.pack("<HHHIHHI", 0x06B6, 0, 0, 0x40, 0, 100, 0)))
    records.extend(biff_record(0x001D, struct.pack("<BHHHHHHBB", 3, 0, 0, 0, 1, 0, 0, 0, 0)))
    records.extend(biff_record(0x000A))
    return bytes(records)


def build_biff_workbook(import_rows: Sequence[dict[str, object]]) -> bytes:
    """Build the complete BIFF8 Workbook stream."""

    table: list[list[object]] = [EXPECTED_HEADERS.copy()]
    for row_number, source in enumerate(import_rows, start=2):
        missing = [header for header in EXPECTED_HEADERS if header not in source]
        if missing:
            raise ValueError(f"Import row {row_number} lacks columns: {missing}")
        table.append([source[header] for header in EXPECTED_HEADERS])

    # Preserve first appearance so LABELSST indexes are deterministic.
    index_by_string: OrderedDict[str, int] = OrderedDict()
    total_strings = 0
    for row_index, row in enumerate(table):
        for column_index, value in enumerate(row):
            if row_index > 0 and column_index == 2:
                continue
            total_strings += 1
            text = str(value)
            if text not in index_by_string:
                index_by_string[text] = len(index_by_string)

    unique_strings = list(index_by_string)
    sheet = worksheet_substream(table, dict(index_by_string))

    # BOUNDSHEET has fixed length, so a single placeholder pass gives the exact
    # worksheet BOF offset for the final pass.
    globals_placeholder = workbook_globals(unique_strings, total_strings, 0)
    globals_final = workbook_globals(unique_strings, total_strings, len(globals_placeholder))
    if len(globals_final) != len(globals_placeholder):
        raise AssertionError("BOUNDSHEET offset update unexpectedly changed record sizes")
    return globals_final + sheet


CFB_SIGNATURE = bytes.fromhex("D0CF11E0A1B11AE1")

FREESECT = 0xFFFFFFFF

ENDOFCHAIN = 0xFFFFFFFE

FATSECT = 0xFFFFFFFD

NOSTREAM = 0xFFFFFFFF

SECTOR_SIZE = 512

FAT_ENTRIES_PER_SECTOR = SECTOR_SIZE // 4


def directory_entry(
    name: str,
    object_type: int,
    *,
    left: int = NOSTREAM,
    right: int = NOSTREAM,
    child: int = NOSTREAM,
    start_sector: int = ENDOFCHAIN,
    stream_size: int = 0,
    clsid: bytes = b"\x00" * 16,
) -> bytes:
    encoded_name = (name + "\x00").encode("utf-16le")
    if len(encoded_name) > 64:
        raise ValueError(f"CFB directory name is too long: {name!r}")
    if len(clsid) != 16:
        raise ValueError("CFB CLSID must contain exactly 16 bytes")
    result = bytearray(128)
    result[0:64] = encoded_name.ljust(64, b"\x00")
    struct.pack_into("<H", result, 64, len(encoded_name))
    result[66] = object_type
    result[67] = 1  # black node
    struct.pack_into("<III", result, 68, left, right, child)
    result[80:96] = clsid
    struct.pack_into("<I", result, 96, 0)  # state bits
    # Creation and modification FILETIMEs remain zero.
    struct.pack_into("<I", result, 116, start_sector)
    struct.pack_into("<Q", result, 120, stream_size)
    return bytes(result)


def build_cfb(workbook_stream: bytes) -> bytes:
    """Wrap a BIFF Workbook stream in a minimal valid CFB v3 container."""

    # CFB stores streams below 4096 bytes in the mini-stream, which this
    # writer does not implement.  Small workbooks are zero-padded up to the
    # cutoff; readers stop at the BIFF EOF record, so trailing zeros are
    # ignored by Excel and by the strict read-back validator.
    if len(workbook_stream) < 4096:
        workbook_stream = workbook_stream.ljust(4096, b"\x00")

    workbook_sector_count = (len(workbook_stream) + SECTOR_SIZE - 1) // SECTOR_SIZE
    directory_sector_count = 1
    fat_sector_count = 1
    while True:
        total = workbook_sector_count + directory_sector_count + fat_sector_count
        required = (total + FAT_ENTRIES_PER_SECTOR - 1) // FAT_ENTRIES_PER_SECTOR
        if required == fat_sector_count:
            break
        fat_sector_count = required
    if fat_sector_count > 109:
        raise ValueError("Workbook would require DIFAT sectors; this writer caps at 109 FATs")

    first_directory_sector = workbook_sector_count
    first_fat_sector = workbook_sector_count + directory_sector_count
    total_sector_count = workbook_sector_count + directory_sector_count + fat_sector_count

    # FAT: regular Workbook stream chain, one directory sector, then FAT sectors.
    fat = [FREESECT] * (fat_sector_count * FAT_ENTRIES_PER_SECTOR)
    for sector in range(workbook_sector_count - 1):
        fat[sector] = sector + 1
    fat[workbook_sector_count - 1] = ENDOFCHAIN
    fat[first_directory_sector] = ENDOFCHAIN
    for sector in range(first_fat_sector, total_sector_count):
        fat[sector] = FATSECT

    # Excel 97-2003 worksheet class GUID, in little-endian on-disk byte order.
    excel_sheet8_clsid = bytes.fromhex("2008020000000000C000000000000046")

    header = bytearray(SECTOR_SIZE)
    header[0:8] = CFB_SIGNATURE
    header[8:24] = excel_sheet8_clsid
    struct.pack_into("<H", header, 24, 0x003E)  # minor version
    struct.pack_into("<H", header, 26, 0x0003)  # major version, 512-byte sectors
    struct.pack_into("<H", header, 28, 0xFFFE)  # little-endian byte order
    struct.pack_into("<H", header, 30, 9)  # sector shift
    struct.pack_into("<H", header, 32, 6)  # mini-sector shift
    struct.pack_into("<I", header, 40, 0)  # directory sectors (must be zero in v3)
    struct.pack_into("<I", header, 44, fat_sector_count)
    struct.pack_into("<I", header, 48, first_directory_sector)
    struct.pack_into("<I", header, 52, 0)  # transaction signature
    struct.pack_into("<I", header, 56, 4096)  # mini-stream cutoff
    struct.pack_into("<I", header, 60, ENDOFCHAIN)  # first MiniFAT sector
    struct.pack_into("<I", header, 64, 0)  # MiniFAT sector count
    struct.pack_into("<I", header, 68, ENDOFCHAIN)  # first DIFAT sector
    struct.pack_into("<I", header, 72, 0)  # DIFAT sector count
    difat = [first_fat_sector + i for i in range(fat_sector_count)]
    difat.extend([FREESECT] * (109 - len(difat)))
    struct.pack_into("<109I", header, 76, *difat)

    workbook_sectors = workbook_stream.ljust(workbook_sector_count * SECTOR_SIZE, b"\x00")

    # Root has one child (directory entry 1), which is the Workbook stream.
    directory = bytearray(SECTOR_SIZE)
    directory[0:128] = directory_entry("Root Entry", 5, child=1, clsid=excel_sheet8_clsid)
    directory[128:256] = directory_entry(
        "Workbook",
        2,
        start_sector=0,
        stream_size=len(workbook_stream),
    )

    fat_bytes = struct.pack(f"<{len(fat)}I", *fat)
    expected_size = SECTOR_SIZE * (1 + total_sector_count)
    result = bytes(header) + workbook_sectors + bytes(directory) + fat_bytes
    if len(result) != expected_size:
        raise AssertionError(f"CFB size mismatch: built {len(result)}, expected {expected_size}")
    return result


def iter_biff_records(data: bytes, start: int = 0) -> Iterator[tuple[int, bytes, int]]:
    """Yield (record_id, payload, record_offset) until EOF or invalid padding."""

    offset = start
    while offset + 4 <= len(data):
        record_id, size = struct.unpack_from("<HH", data, offset)
        end = offset + 4 + size
        if end > len(data):
            raise ValueError(f"Truncated BIFF record 0x{record_id:04X} at {offset}")
        payload = data[offset + 4 : end]
        yield record_id, payload, offset
        offset = end
        if record_id == 0x000A:
            break


def cfb_sector(file_bytes: bytes, sector_id: int, sector_size: int) -> bytes:
    start = sector_size * (sector_id + 1)
    end = start + sector_size
    if sector_id < 0 or end > len(file_bytes):
        raise ValueError(f"CFB sector {sector_id} is outside the file")
    return file_bytes[start:end]


def read_chain(
    file_bytes: bytes,
    fat: Sequence[int],
    start_sector: int,
    sector_size: int,
    *,
    max_sectors: int | None = None,
) -> bytes:
    result = bytearray()
    seen: set[int] = set()
    sector_id = start_sector
    while sector_id != ENDOFCHAIN:
        if sector_id in seen:
            raise ValueError("CFB FAT chain contains a cycle")
        if sector_id < 0 or sector_id >= len(fat):
            raise ValueError(f"CFB FAT chain references invalid sector {sector_id}")
        seen.add(sector_id)
        result.extend(cfb_sector(file_bytes, sector_id, sector_size))
        if max_sectors is not None and len(seen) > max_sectors:
            raise ValueError("CFB FAT chain exceeds its safety limit")
        sector_id = fat[sector_id]
    return bytes(result)


def parse_directory_entries(directory_bytes: bytes) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for offset in range(0, len(directory_bytes), 128):
        raw = directory_bytes[offset : offset + 128]
        if len(raw) < 128:
            break
        object_type = raw[66]
        name_size = struct.unpack_from("<H", raw, 64)[0]
        if object_type == 0:
            continue
        if name_size < 2 or name_size > 64 or name_size % 2:
            raise ValueError("Invalid CFB directory name length")
        name = raw[: name_size - 2].decode("utf-16le")
        entries.append(
            {
                "name": name,
                "object_type": object_type,
                "start_sector": struct.unpack_from("<I", raw, 116)[0],
                "stream_size": struct.unpack_from("<Q", raw, 120)[0],
            }
        )
    return entries


def read_workbook_stream(path: Path) -> tuple[bytes, dict[str, object]]:
    file_bytes = path.read_bytes()
    if file_bytes[:8] != CFB_SIGNATURE:
        raise ValueError("File does not begin with the CFB signature")
    major_version = struct.unpack_from("<H", file_bytes, 26)[0]
    byte_order = struct.unpack_from("<H", file_bytes, 28)[0]
    sector_shift = struct.unpack_from("<H", file_bytes, 30)[0]
    if major_version != 3 or byte_order != 0xFFFE or sector_shift != 9:
        raise ValueError("Expected a little-endian CFB v3 file with 512-byte sectors")
    sector_size = 1 << sector_shift
    fat_sector_count = struct.unpack_from("<I", file_bytes, 44)[0]
    first_directory_sector = struct.unpack_from("<I", file_bytes, 48)[0]
    first_mini_fat = struct.unpack_from("<I", file_bytes, 60)[0]
    mini_fat_count = struct.unpack_from("<I", file_bytes, 64)[0]
    first_difat = struct.unpack_from("<I", file_bytes, 68)[0]
    difat_count = struct.unpack_from("<I", file_bytes, 72)[0]
    if first_mini_fat != ENDOFCHAIN or mini_fat_count != 0:
        raise ValueError("Generated file unexpectedly uses a MiniFAT")
    if first_difat != ENDOFCHAIN or difat_count != 0:
        raise ValueError("Generated file unexpectedly uses DIFAT sectors")
    difat = list(struct.unpack_from("<109I", file_bytes, 76))
    fat_sector_ids = [value for value in difat if value != FREESECT]
    if len(fat_sector_ids) != fat_sector_count:
        raise ValueError("CFB header FAT count does not match its DIFAT entries")
    fat: list[int] = []
    for sector_id in fat_sector_ids:
        fat.extend(struct.unpack("<128I", cfb_sector(file_bytes, sector_id, sector_size)))

    directory_bytes = read_chain(
        file_bytes,
        fat,
        first_directory_sector,
        sector_size,
        max_sectors=64,
    )
    entries = parse_directory_entries(directory_bytes)
    workbook_entries = [
        entry for entry in entries if entry["object_type"] == 2 and entry["name"] in {"Workbook", "Book"}
    ]
    if len(workbook_entries) != 1:
        raise ValueError(f"Expected one Workbook stream, found {len(workbook_entries)}")
    workbook_entry = workbook_entries[0]
    stream_size = int(workbook_entry["stream_size"])
    if stream_size < 4096:
        raise ValueError("Workbook stream is too small for the regular-sector chain")
    workbook_bytes = read_chain(
        file_bytes,
        fat,
        int(workbook_entry["start_sector"]),
        sector_size,
        max_sectors=(stream_size + sector_size - 1) // sector_size + 1,
    )[:stream_size]
    metadata = {
        "file_size": len(file_bytes),
        "cfb_signature": file_bytes[:8].hex().upper(),
        "cfb_major_version": major_version,
        "sector_size": sector_size,
        "fat_sector_count": fat_sector_count,
        "directory_entries": [entry["name"] for entry in entries],
        "workbook_stream_name": workbook_entry["name"],
        "workbook_stream_size": stream_size,
    }
    return workbook_bytes, metadata


def decode_sst(records: Sequence[tuple[int, bytes, int]], sst_pos: int) -> tuple[list[str], int]:
    """Decode the boundary-split SST generated by this writer."""

    record_id, first_payload, _ = records[sst_pos]
    if record_id != 0x00FC or len(first_payload) < 8:
        raise ValueError("Invalid SST record")
    total_count, unique_count = struct.unpack_from("<II", first_payload, 0)
    combined = bytearray(first_payload[8:])
    pos = sst_pos + 1
    while pos < len(records) and records[pos][0] == 0x003C:
        combined.extend(records[pos][1])
        pos += 1

    values: list[str] = []
    offset = 0
    for _ in range(unique_count):
        if offset + 3 > len(combined):
            raise ValueError("Truncated SST string header")
        code_units, flags = struct.unpack_from("<HB", combined, offset)
        offset += 3
        if flags & 0x0C:
            raise ValueError("Validator expects SST strings without rich/ext data")
        if not (flags & 0x01):
            raise ValueError("Validator expects UTF-16 SST strings")
        byte_count = code_units * 2
        if offset + byte_count > len(combined):
            raise ValueError("Truncated SST Unicode character data")
        values.append(combined[offset : offset + byte_count].decode("utf-16le"))
        offset += byte_count
    if len(values) != unique_count:
        raise AssertionError("SST unique-string count mismatch")
    return values, total_count


def validate_xls(path: Path, expected_data_rows: int) -> dict[str, object]:
    """Strictly read back and validate the generated CFB/BIFF8 workbook."""

    workbook_bytes, report = read_workbook_stream(path)
    global_records = list(iter_biff_records(workbook_bytes, 0))
    if not global_records:
        raise ValueError("Workbook stream contains no BIFF records")
    bof_id, bof_payload, _ = global_records[0]
    if bof_id != 0x0809 or len(bof_payload) < 4:
        raise ValueError("Workbook stream does not begin with BOF")
    workbook_version, workbook_type = struct.unpack_from("<HH", bof_payload, 0)
    if workbook_version != 0x0600 or workbook_type != 0x0005:
        raise ValueError("Workbook BOF is not a BIFF8 workbook-global BOF")

    boundsheets = [payload for rid, payload, _ in global_records if rid == 0x0085]
    if len(boundsheets) != 1:
        raise ValueError(f"Expected one BOUNDSHEET record, found {len(boundsheets)}")
    boundsheet = boundsheets[0]
    sheet_offset = struct.unpack_from("<I", boundsheet, 0)[0]
    name_units, name_flags = struct.unpack_from("<BB", boundsheet, 6)
    name_bytes = name_units * (2 if name_flags & 0x01 else 1)
    name_encoding = "utf-16le" if name_flags & 0x01 else "latin1"
    sheet_name = boundsheet[8 : 8 + name_bytes].decode(name_encoding)
    if sheet_name != SHEET_NAME:
        raise ValueError(f"Expected sheet {SHEET_NAME!r}, found {sheet_name!r}")

    sst_positions = [i for i, (rid, _, _) in enumerate(global_records) if rid == 0x00FC]
    if len(sst_positions) != 1:
        raise ValueError(f"Expected one SST, found {len(sst_positions)}")
    sst, total_sst_count = decode_sst(global_records, sst_positions[0])

    xf_number_formats: list[int] = []
    for record_id, payload, _ in global_records:
        if record_id == 0x00E0:
            if len(payload) != 20:
                raise ValueError("Unexpected XF record length")
            xf_number_formats.append(struct.unpack_from("<H", payload, 2)[0])
    if len(xf_number_formats) < 4 or xf_number_formats[3] != 2:
        raise ValueError("Amount XF does not use the built-in 0.00 number format")

    if sheet_offset >= len(workbook_bytes):
        raise ValueError("BOUNDSHEET offset is outside the Workbook stream")
    sheet_records = list(iter_biff_records(workbook_bytes, sheet_offset))
    if not sheet_records:
        raise ValueError("Worksheet substream contains no BIFF records")
    sheet_bof_id, sheet_bof_payload, _ = sheet_records[0]
    if sheet_bof_id != 0x0809 or len(sheet_bof_payload) < 4:
        raise ValueError("Worksheet does not begin with BOF")
    sheet_version, sheet_type = struct.unpack_from("<HH", sheet_bof_payload, 0)
    if sheet_version != 0x0600 or sheet_type != 0x0010:
        raise ValueError("Worksheet BOF is not a BIFF8 worksheet BOF")

    dimensions = [payload for rid, payload, _ in sheet_records if rid == 0x0200]
    if len(dimensions) != 1 or len(dimensions[0]) != 14:
        raise ValueError("Worksheet must have exactly one 14-byte DIMENSIONS record")
    first_row, last_row_exclusive, first_col, last_col_exclusive, _ = struct.unpack(
        "<IIHHH", dimensions[0]
    )

    row_records = [payload for rid, payload, _ in sheet_records if rid == 0x0208]
    if len(row_records) != expected_data_rows + 1:
        raise ValueError(f"Expected {expected_data_rows + 1} ROW records, found {len(row_records)}")
    for expected_row, payload in enumerate(row_records):
        if len(payload) != 16:
            raise ValueError("Unexpected ROW record length")
        row_index, first_cell_col, last_cell_col = struct.unpack_from("<HHH", payload, 0)
        if (row_index, first_cell_col, last_cell_col) != (expected_row, 0, 9):
            raise ValueError("ROW record does not describe the strict nine-column row")

    cells: dict[tuple[int, int], object] = {}
    cell_xfs: dict[tuple[int, int], int] = {}
    for record_id, payload, _ in sheet_records:
        if record_id == 0x00FD:
            if len(payload) != 10:
                raise ValueError("Unexpected LABELSST record length")
            row, column, xf, sst_index = struct.unpack("<HHHI", payload)
            if sst_index >= len(sst):
                raise ValueError("LABELSST references an invalid SST index")
            cells[(row, column)] = sst[sst_index]
            cell_xfs[(row, column)] = xf
        elif record_id == 0x0203:
            if len(payload) != 14:
                raise ValueError("Unexpected NUMBER record length")
            row, column, xf, number = struct.unpack("<HHHd", payload)
            cells[(row, column)] = number
            cell_xfs[(row, column)] = xf

    expected_rows = expected_data_rows + 1
    expected_cell_count = expected_rows * len(EXPECTED_HEADERS)
    if len(cells) != expected_cell_count:
        raise ValueError(f"Expected {expected_cell_count} cells, read back {len(cells)}")
    if (first_row, last_row_exclusive, first_col, last_col_exclusive) != (
        0,
        expected_rows,
        0,
        len(EXPECTED_HEADERS),
    ):
        raise ValueError("DIMENSIONS does not match the expected used range")

    headers = [cells.get((0, column)) for column in range(len(EXPECTED_HEADERS))]
    if headers != EXPECTED_HEADERS:
        raise ValueError(f"Header mismatch: {headers!r}")
    if any(cell_xfs[(0, column)] != 2 for column in range(9)):
        raise ValueError("One or more header cells do not use the bold-header XF")

    amounts = [cells.get((row, 2)) for row in range(1, expected_rows)]
    if len(amounts) != expected_data_rows or not all(
        isinstance(value, float) and math.isfinite(value) and value > 0 for value in amounts
    ):
        raise ValueError("Amounts are not all positive numeric BIFF NUMBER cells")
    if any(cell_xfs[(row, 2)] != 3 for row in range(1, expected_rows)):
        raise ValueError("One or more amounts do not use the 0.00 amount XF")

    dates = [cells.get((row, 0)) for row in range(1, expected_rows)]
    if not all(
        isinstance(value, str) and len(value) == 10 and value[4] == "-" and value[7] == "-"
        for value in dates
    ):
        raise ValueError("Dates were not preserved as YYYY-MM-DD strings")

    report.update(
        {
            "container": "CFB v3 / OLE Compound File",
            "workbook_format": "BIFF8 (Excel 97-2003)",
            "workbook_bof_version": f"0x{workbook_version:04X}",
            "worksheet_bof_version": f"0x{sheet_version:04X}",
            "sheet_count": 1,
            "sheet_name": sheet_name,
            "row_count_including_header": expected_rows,
            "data_row_count": expected_data_rows,
            "column_count": len(EXPECTED_HEADERS),
            "headers": headers,
            "cell_count": len(cells),
            "row_record_count": len(row_records),
            "sst_total_string_references": total_sst_count,
            "sst_unique_strings": len(sst),
            "amount_cell_type": "BIFF NUMBER (IEEE-754 numeric)",
            "amount_number_format": "0.00 (built-in format id 2)",
            "amount_count": len(amounts),
            "all_amounts_positive": True,
            "minimum_amount": min(amounts),
            "maximum_amount": max(amounts),
            "dates_are_text_yyyy_mm_dd": True,
            "font": "宋体 11pt; header bold",
            "column_widths_set": True,
            "validation_status": "PASS",
        }
    )
    return report
