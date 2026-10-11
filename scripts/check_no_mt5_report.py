"""Pre-commit guard: refuse files that look like an MT5 trade-history report (the repo is public).

The owner's report holds the account number, name and broker (goldbot/research/owner_history.py reads it from a
private folder outside the repo). This hook rejects, whatever the file is called:
* report file names MT5 suggests (`ReportHistory-*`);
* text containing the report title or an `Account:` label (or its Russian, German or Spanish word) followed by an
  account-like number, also in UTF-16 (the HTML report's usual encoding), with HTML tags allowed in between;
* a translated report, whose labels the two checks above may not know: an HTML table or .xlsx sheet with at least
  MIN_STAMPED_ROWS rows that start with an MT5 server stamp `YYYY.MM.DD HH:MM[:SS]` AND either a one-cell section
  title such as Positions / Deals (English, Russian, German, Spanish) or at least MIN_STAMPED_ROWS of those rows as
  wide as an MT5 Positions/Orders/Deals row (MIN_REPORT_COLS cells), so an unknown language is caught by shape;
* .xlsx (zip) files whose worksheet or shared strings contain any of the above.

The translated section and label words are the terminal's usual terms; they were not checked against a real
translated export (none may enter the repo), which is why the shape rule does not depend on them.

Usage: python scripts/check_no_mt5_report.py FILE... (exit 1 and the offending names when any match).
"""
from __future__ import annotations

import html
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree

TITLE = re.compile(r"Trade\s+History\s+Report", re.IGNORECASE)
ACCOUNT = re.compile(r"(?:Account|Сч[её]т|Konto|Cuenta):\s*(?:</?\w+[^>]*>\s*)*\d{5,}", re.IGNORECASE)
NAME = re.compile(r"^ReportHistory-", re.IGNORECASE)
STAMP = re.compile(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}(?::\d{2})?")
SECTION_TITLES = frozenset({
    "positions", "orders", "deals",                                  # English
    "позиции", "ордера", "сделки",                                   # Russian
    "positionen", "aufträge", "geschäfte",                           # German
    "posiciones", "órdenes", "ordenes", "transacciones", "operaciones",  # Spanish
})
MIN_STAMPED_ROWS = 5           # rows starting with an MT5 stamp before a table counts as a trade list
MIN_REPORT_COLS = 10           # MT5 Positions rows have 13 cells, Deals 14, Orders 11
MAX_SCAN = 20 * 2**20          # bytes read per file / zip member

_ROW = re.compile(r"<tr\b", re.IGNORECASE)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG = re.compile(r"<[^>]+>")
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _text(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16", errors="replace")
    head = raw[:400]
    if head and head.count(b"\x00") > len(head) // 4:
        return raw.decode("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _matches(text: str) -> bool:
    return bool(TITLE.search(text) or ACCOUNT.search(text))


def _html_rows(text: str) -> list[list[str]]:
    """Non-empty cell texts of every <tr> (tags stripped, entities decoded)."""
    rows = []
    for chunk in _ROW.split(text)[1:]:
        cells = [" ".join(html.unescape(_TAG.sub("", c)).split()) for c in _CELL.findall(chunk)]
        rows.append([c for c in cells if c])
    return rows


def _xlsx_rows(z: zipfile.ZipFile) -> list[list[str]]:
    """Non-empty cell texts of every row of every worksheet (shared, inline and plain values)."""
    names = set(z.namelist())
    shared: list[str] = []
    if "xl/sharedStrings.xml" in names:
        with z.open("xl/sharedStrings.xml") as fh:
            sst = ElementTree.fromstring(fh.read(MAX_SCAN))
        shared = ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in sst.iter(f"{_NS}si")]
    rows: list[list[str]] = []
    for name in sorted(n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")):
        with z.open(name) as fh:
            sheet = ElementTree.fromstring(fh.read(MAX_SCAN))
        for row in sheet.iter(f"{_NS}row"):
            cells = []
            for c in row.iter(f"{_NS}c"):
                v = c.find(f"{_NS}v")
                if c.get("t") == "s" and v is not None and (v.text or "").isdigit() and int(v.text or 0) < len(shared):
                    cells.append(shared[int(v.text or 0)])
                elif c.get("t") == "inlineStr":
                    cells.append("".join(t.text or "" for t in c.iter(f"{_NS}t")))
                elif v is not None:
                    cells.append(v.text or "")
            rows.append([" ".join(x.split()) for x in cells if x.strip()])
    return rows


def _report_shaped(rows: list[list[str]]) -> bool:
    """A translated trade list: enough rows led by an MT5 server stamp, under a known section title or as wide as
    an MT5 report row."""
    stamped = [r for r in rows if r and STAMP.fullmatch(r[0])]
    if len(stamped) < MIN_STAMPED_ROWS:
        return False
    titled = any(len(r) == 1 and r[0].casefold().rstrip(":") in SECTION_TITLES for r in rows)
    wide = sum(len(r) >= MIN_REPORT_COLS for r in stamped) >= MIN_STAMPED_ROWS
    return titled or wide


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
                return _report_shaped(_xlsx_rows(z))
        except (zipfile.BadZipFile, ElementTree.ParseError, KeyError, OSError):
            return False
    text = _text(raw)
    return _matches(text) or (bool(_ROW.search(text)) and _report_shaped(_html_rows(text)))


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
