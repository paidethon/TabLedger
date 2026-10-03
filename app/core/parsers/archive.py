"""Encrypted-bill archive handling and bill source detection.

Security posture for untrusted uploads:
- exactly one data entry per bill archive (legacy behaviour, also blocks slip);
- entry count / expanded size / compression-ratio caps against zip bombs;
- nested archives are never extracted recursively;
- names are used only as weak detection signals, never as storage paths.
"""

from __future__ import annotations

import contextlib
import io
import re
import zipfile

MAX_ARCHIVE_ENTRIES = 8
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
SUPPORTED_SUFFIXES = {".zip", ".csv", ".xlsx", ".pdf"}


class ArchiveError(ValueError):
    """Raised when an uploaded archive violates safety constraints."""


def fix_zip_name(info: zipfile.ZipInfo) -> str:
    """微信/支付宝 zip 的文件名按 GBK 存放，zipfile 默认按 cp437 解码会乱码。"""

    name = info.filename
    if not info.flag_bits & 0x800:
        try:
            name = name.encode("cp437").decode("gbk")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return name


def _check_zip_safety(archive: zipfile.ZipFile, display_name: str) -> list[zipfile.ZipInfo]:
    infos = [i for i in archive.infolist() if not i.is_dir()]
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ArchiveError(f"{display_name} 包含过多条目（{len(infos)}）")
    total = 0
    for info in infos:
        name = info.filename
        if name.startswith("/") or ".." in name.split("/") or ":" in name:
            raise ArchiveError(f"{display_name} 内含不安全路径: {name!r}")
        if name.endswith((".zip", ".rar", ".7z")):
            raise ArchiveError(f"{display_name} 内含嵌套压缩包: {name!r}")
        total += info.file_size
        if total > MAX_EXPANDED_BYTES:
            raise ArchiveError(f"{display_name} 解压后总大小超过上限")
        if info.compress_size > 0 and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ArchiveError(f"{display_name} 压缩比异常，疑似压缩炸弹")
    return infos


def unzip_bill(zip_path_or_bytes, passwords: list[str], display_name: str = "账单.zip"):
    """Decrypt a bill zip (auto-trying passwords), return (data, inner filename).

    Passwords live only in this call frame; they are never logged.  Each
    trial re-opens the archive: a failed trial invalidates the ZipFile.
    """

    def _open():
        if isinstance(zip_path_or_bytes, (bytes, bytearray)):
            return zipfile.ZipFile(io.BytesIO(bytes(zip_path_or_bytes)))
        return zipfile.ZipFile(zip_path_or_bytes)

    last_error: Exception = ArchiveError("未提供密码")
    for password in passwords or [""]:
        try:
            with _open() as archive:
                archive.setpassword(password.encode("utf-8"))
                infos = _check_zip_safety(archive, display_name)
                if len(infos) != 1:
                    raise ArchiveError(f"{display_name} 内应有且只有一个数据文件，实际 {len(infos)} 个")
                return archive.read(infos[0]), fix_zip_name(infos[0])
        except Exception as exc:
            last_error = exc
    if isinstance(last_error, ArchiveError):
        raise last_error
    raise ArchiveError(f"{display_name} 解压失败（密码不匹配？）：{type(last_error).__name__}")


def sniff_payapp_csv(data: bytes) -> bool:
    try:
        head = data[:2000].decode("gb18030", errors="ignore")
    except Exception:
        return False
    return "交易时间" in head and "商品说明" in head


_ENTITY_RE = re.compile(r"&#(\d+);")


def _xml_text_haystack(raw: bytes) -> str:
    """Decode numeric character references so content matching works on
    inline-string workbooks (openpyxl writes Chinese as &#NNNNN;)."""

    text = raw.decode("utf-8", errors="ignore")

    def replace(match: re.Match) -> str:
        try:
            return chr(int(match.group(1)))
        except ValueError:
            return match.group(0)

    return _ENTITY_RE.sub(replace, text)


def sniff_wx_xlsx(data: bytes) -> bool:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())
            candidates = []
            if "xl/sharedStrings.xml" in names:
                candidates.append("xl/sharedStrings.xml")
            candidates.extend(sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")))
            for name in candidates:
                info = archive.getinfo(name)
                if info.file_size > 32 * 1024 * 1024:
                    continue
                content = _xml_text_haystack(archive.read(name))
                if "微信支付账单明细" in content:
                    return True
    except Exception:
        return False
    return False


def sniff_bank_pdf(path_or_bytes, passwords: list[str]):
    """Open a bank PDF (auto-trying passwords) and classify by header.

    Returns (source, plumber pdf, matched password).  Caller must close the pdf.
    """

    from app.core.parsers.pdf import clean_header_cell, open_statement

    winner = None
    last_error: Exception = ArchiveError("未提供密码")
    leaked: list = []
    try:
        for password in passwords or [None]:
            pdf = None
            try:
                pdf = open_statement(path_or_bytes, password or None)
                page = pdf.pages[0]
                tables = page.extract_tables()
                if not tables:
                    raise ValueError("第一页没有表格")
                header = [clean_header_cell(c) for c in tables[0][0]]
                if "交易日期" in header and len(header) == 13:
                    winner = ("工商银行", pdf, password)
                    return winner
                if "记账日期" in header and len(header) == 12:
                    winner = ("中国银行", pdf, password)
                    return winner
                raise ValueError(f"表头无法识别：{header[:6]}")
            except Exception as exc:
                last_error = exc
            finally:
                if pdf is not None and winner is None:
                    leaked.append(pdf)
    finally:
        for pdf in leaked:
            with contextlib.suppress(Exception):
                pdf.close()
    if isinstance(last_error, ArchiveError):
        raise last_error
    raise ArchiveError(f"PDF 打开失败（密码不匹配？）：{type(last_error).__name__}")
