#!/usr/bin/env python3
"""Custom PII scan over tracked files (runs in CI alongside gitleaks).

Patterns cover the leak classes this project cares about: CN mobile
numbers, ID card numbers, bank card numbers, emails, API key prefixes,
private key blocks, and the legacy pipeline's personal identifiers.
Synthetic fixture data (张测试, TEST-*, test-*) is allow-listed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

PATTERNS: dict[str, re.Pattern[str]] = {
    "cn_mobile": re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    "cn_id": re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)"),
    # Long digit runs, excluding version-ish strings like pnpm@9.15.4 / hex GUIDs
    # and CFB class-id hex blobs.
    "bank_card": re.compile(r"(?<![\d.@a-fA-F])\d{16,19}(?![\d.\-a-fA-F])"),
    # email: `@` must be followed by a letter (a digit means a version spec
    # like pkg@8.18.0), and must not start at a scoped-package position.
    "email": re.compile(r"(?<![@\w.'\"])[A-Za-z0-9._%+-]+@[A-Za-z][A-Za-z0-9.-]+"),
    "api_key_prefix": re.compile(r"\b(sk|rk|pk)-[A-Za-z0-9]{16,}"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
}

ALLOW_PATTERNS = [
    re.compile(r"tests/fixtures/synthetic/"),
    re.compile(r"scripts/make_fixtures\.py"),
    re.compile(r"^#"),
    re.compile(r"example\.(com|org|net)"),
    re.compile(r"@users\.noreply\.github\.com"),
    re.compile(r"tabledger@example"),
    # npm registry tarball URLs, lockfile metadata and scoped package keys.
    re.compile(r"registry\.npmjs\.org|npmmirror\.com"),
    re.compile(r"ghcr\.io/"),
    re.compile(r"^\s*'@"),  # scoped npm package keys like "'@scope/name@ver'"
    # Lockfile package keys and dependency specifiers: `pkg@1.2.3(...)`.
    re.compile(r"^\s*[\w@.-]+@\d"),
    re.compile(r":\s*[\w@.-]+@\d"),
    # This scanner's own regex source lines.
    re.compile(r"PATTERNS|ALLOW_PATTERNS|re\.compile"),
    # SQLAlchemy-style URLs: driver://user:pass@host/db
    re.compile(r"://[\w.:-]+@"),
]

# Legacy personal markers that must never appear in tracked content.
# (Assembled from fragments so the scanner source does not contain the
#  marker strings themselves; the deploy domain is checked in docs only.)
LEGACY_MARKERS = [
    "\u5f20\u57f9\u8fdb",  # legacy statement owner name (real person)
    "71" + "48",  # legacy bank card tail
    "27" + "90",  # legacy bank card tail
    "oou" + "o",  # legacy personal domain fragment
]
GH_SLUG_ALLOW = re.compile(r"ghcr\.io/paidethon/tabledger|github\.com/paidethon/tabledger")


def tracked_files() -> list[str]:
    out = subprocess.run(["/usr/bin/git", "ls-files"], capture_output=True, text=True, check=True).stdout
    return [line for line in out.splitlines() if line.strip()]


def allowlisted(path: str, line: str) -> bool:
    if any(pattern.search(path) for pattern in ALLOW_PATTERNS[:2]):
        return True
    return any(pattern.search(line) for pattern in ALLOW_PATTERNS[2:])
def main() -> int:
    failures: list[str] = []
    binary_suffixes = {".png", ".jpg", ".pdf", ".xls", ".xlsx", ".zip", ".ico", ".woff2"}
    for path in tracked_files():
        if any(path.endswith(suffix) for suffix in binary_suffixes):
            continue
        try:
            content = Path(path).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(content.splitlines(), start=1):
            if allowlisted(path, line):
                continue
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    failures.append(f"{path}:{lineno}: {name}")
            for marker in LEGACY_MARKERS:
                if marker in line:
                    failures.append(f"{path}:{lineno}: legacy marker [{marker}]")
    if failures:
        print("PII scan FAILED:")
        for failure in failures[:50]:
            print(" ", failure)
        return 1
    print(f"PII scan PASS ({len(tracked_files())} tracked files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
