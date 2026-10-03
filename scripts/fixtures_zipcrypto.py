"""Minimal ZipCrypto (traditional PKWARE) zip writer for synthetic test fixtures.

Real Alipay/WeChat bill exports are ZipCrypto-encrypted, which stdlib zipfile
can read but never write.  The algorithm is the documented, deliberately weak
legacy scheme; it is used here only for fully synthetic fixtures with fake
committed passwords, never for real data.
"""

from __future__ import annotations

import os
import re
import struct
import zipfile
import zlib
from pathlib import Path

# Allow only plain file names: single segment, no separators, no traversal.
_SAFE_ENTRY_RE = re.compile(r"^[A-Za-z0-9_\u4e00-\u9fff][A-Za-z0-9_.\-\u4e00-\u9fff]{0,120}$")
_SAFE_OUTPUT_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _safe_entry_name(name: str) -> str:
    if not _SAFE_ENTRY_RE.match(name):
        raise ValueError(f"unsafe zip entry name: {name!r}")
    return name


def _safe_output_path(output: str | Path) -> Path:
    resolved = Path(output).resolve()
    if not _SAFE_OUTPUT_RE.match(resolved.name):
        raise ValueError(f"unsafe zip output file name: {resolved.name!r}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


_CRC_TABLE = None


def _crc_table() -> list[int]:
    global _CRC_TABLE
    if _CRC_TABLE is None:
        table = []
        for i in range(256):
            crc = i
            for _ in range(8):
                crc = (crc >> 1) ^ (0xEDB88320 if crc & 1 else 0)
            table.append(crc)
        _CRC_TABLE = table
    return _CRC_TABLE


class _ZipCryptoStream:
    """The documented traditional PKWARE encryption keystream."""

    def __init__(self, password: bytes):
        # Standard initial keys from the PKWARE APPNOTE.
        self.k0 = 0x12345678
        self.k1 = 0x23456789
        self.k2 = 0x34567890
        for byte in password:
            self._update(byte)

    def _update(self, byte: int) -> None:
        # Classic key updates from the PKWARE APPNOTE:
        #   k0 = crc32(k0, byte); k1 += k0 & 0xFF; k1 = k1*134775813+1;
        #   k2 = crc32(k2, k1 >> 24)
        table = _crc_table()
        self.k0 = (self.k0 >> 8) ^ table[(self.k0 ^ byte) & 0xFF]
        self.k1 = ((self.k1 + (self.k0 & 0xFF)) * 134775813 + 1) & 0xFFFFFFFF
        self.k2 = (self.k2 >> 8) ^ table[((self.k2 ^ (self.k1 >> 24)) & 0xFF)]

    def keystream_byte(self) -> int:
        temp = (self.k2 | 2) & 0xFFFF
        return ((temp * (temp ^ 1)) >> 8) & 0xFF

    def encrypt(self, data: bytes) -> bytes:
        out = bytearray()
        for byte in data:
            cipher_byte = byte ^ self.keystream_byte()
            self._update(byte)
            out.append(cipher_byte)
        return bytes(out)


def write_zipcrypto_zip(output: str | Path, entries: list[tuple[bytes, str]], password: str) -> None:
    """Write a ZipCrypto-encrypted zip with deflate-compressed entries."""

    entries = [(data, _safe_entry_name(name)) for data, name in entries]
    destination = _safe_output_path(output)
    password_bytes = password.encode("utf-8")
    with destination.open("wb") as out:
        central: list[bytes] = []
        offset = 0
        for data, name in entries:
            encoded_name = name.encode("utf-8")
            flags = 0x0800 | 0x0001  # UTF-8 name | encrypted
            crc = zlib.crc32(data) & 0xFFFFFFFF
            compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
            body = compressor.compress(data) + compressor.flush()

            cipher = _ZipCryptoStream(password_bytes)
            # The last header byte must equal the CRC's highest byte
            # (bit 3 of the general-purpose flag is not set here).
            check_byte = (crc >> 24) & 0xFF
            header = cipher.encrypt(bytes(bytearray(os.urandom(11)) + bytearray([check_byte])))
            encrypted = header + cipher.encrypt(body)

            local = struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,
                flags,
                8,  # deflate
                0,
                0,
                crc,
                len(encrypted),
                len(data),
                len(encoded_name),
                0,
            ) + encoded_name
            out.write(local + encrypted)

            central.append(
                struct.pack(
                    "<IHHHHHHIIIHHHHHII",
                    0x02014B50,
                    20,
                    20,
                    flags,
                    8,
                    0,
                    0,
                    crc,
                    len(encrypted),
                    len(data),
                    len(encoded_name),
                    0,
                    0,
                    0,
                    0,
                    0,
                    offset,
                )
                + encoded_name
            )
            offset += len(local) + len(encrypted)

        central_dir = b"".join(central)
        out.write(central_dir)
        out.write(
            struct.pack(
                "<IHHHHIIH",
                0x06054B50,
                0,
                0,
                len(entries),
                len(entries),
                len(central_dir),
                offset,
                0,
            )
        )

    # Self-check: stdlib zipfile must read it back with the password.
    with zipfile.ZipFile(destination) as archive:
        for info in archive.infolist():
            data = archive.read(info, password_bytes)
            assert data == next(d for d, n in entries if n == info.filename), info.filename
