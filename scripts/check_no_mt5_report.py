"""Pre-commit guard: refuse files that look like an MT5 trade-history report (the repo is public).

The owner's report holds the account number, name and broker (goldbot/research/owner_history.py reads it from a
private folder outside the repo). This hook rejects, whatever the file is called:
* report file names MT5 suggests (`ReportHistory-*`);
* text containing the report title or an `Account:` label followed by an account-like number, also in UTF-16 (the
  HTML report's usual encoding), with HTML tags allowed in between;
* .xlsx (zip) files whose worksheet or shared strings contain the same.

Usage: python scripts/check_no_mt5_report.py FILE... (exit 1 and the offending names when any match).
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

TITLE = re.compile(r"Trade\s+History\s+Report", re.IGNORECASE)
ACCOUNT = re.compile(r"Account:\s*(?:</?\w+[^>]*>\s*)*\d{5,}", re.IGNORECASE)
NAME = re.compile(r"^ReportHistory-", re.IGNORECASE)
MAX_SCAN = 20 * 2**20          # bytes read per file / zip member


def _text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    head = raw[:400]
    if head and head.count(b"\x00") > len(head) // 4:
        return raw.decode("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _matches(text: str) -> bool:
    return bool(TITLE.search(text) or ACCOUNT.search(text))


def looks_like_report(path: str) -> bool:
    p = Path(path)
    if NAME.match(p.name):
        return True
    try:
        with p.open("rb") as fh:
            raw = fh.read(MAX_SCAN)
    except OSError:
        return False
    if raw[:2] == b"PK":
        try:
            with zipfile.ZipFile(p) as z:
                for info in z.infolist():
                    if info.filename.startswith("xl/") and info.filename.endswith(".xml"):
                        with z.open(info) as member:
                            if _matches(_text(member.read(MAX_SCAN))):
                                return True
        except (zipfile.BadZipFile, OSError):
            return False
        return False
    return _matches(_text(raw))


def offending(paths: list[str]) -> list[str]:
    return [p for p in paths if looks_like_report(p)]


def main(argv: list[str]) -> int:
    bad = offending(argv)
    for p in bad:
        print(f"{p}: looks like an MT5 trade-history report (personal account data); keep it in "
              "~/Personal project/trade-history/, never in this public repo")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
