"""Pre-commit guard: refuse files that look like an MT5 trade-history report (the repo is public).

The owner's report holds the account number, name and broker (goldbot/research/owner_history.py reads it from a
private folder outside the repo). This hook rejects, whatever the file is called:
* report file names MT5 suggests (`ReportHistory-*`) and owner_history's own outputs (`owner_history_<stamp>.*`);
* an HTML table, an .xlsx sheet or delimited text with an English MT5 Positions / Orders / Deals header row (Symbol,
  Type, Volume, Price and one of Position / Deal / Order / Ticket as whole cells), or the header of owner_history's
  per-trade CSV: every format the analyser reads is caught whatever its length or stamp format;
* text containing the report title or an `Account:` label (or its Russian, German or Spanish word) followed by an
  account-like number, also in UTF-16 (the HTML report's usual encoding), with HTML tags allowed in between;
* a translated report, whose labels the two checks above may not know: an HTML table, an .xlsx sheet or delimited
  text (CSV, TSV, `;`) with at least MIN_STAMPED_ROWS rows holding an MT5 server stamp `YYYY.MM.DD HH:MM[:SS]` in
  one of their first STAMP_COLS visible cells AND either a one-cell section title such as Positions / Deals
  (English, Russian, German, Spanish) or at least MIN_STAMPED_ROWS of those rows as wide as an MT5
  Positions/Orders/Deals row (MIN_REPORT_COLS cells), so an unknown language is caught by shape. Hidden HTML cells
  (class "hidden", display:none) are dropped first, as a browser would;
* .xlsx (zip) files whose worksheet or shared strings contain any of the above.

Known gaps (stated, not hidden): a translated export with fewer than MIN_STAMPED_ROWS trades, and a translated
.xlsx that stores the stamps as Excel date numbers rather than text, pass the shape rule; only the title, account and
file-name checks see them. A hand-made CSV whose headers use other words (Size, Open Price) is not an MT5 export and
passes unless it has the report's shape. owner_history's .md and .json outputs are caught by name only. The translated section and label words are the terminal's usual terms, not checked against a real
translated export (none may enter the repo), which is why the shape rule does not depend on them.

Cost is linear in the input: HTML is read with html.parser in one pass (unclosed cells close at the next cell or
row), no regex backtracks over an unbounded span, and an .xlsx is scanned for at most MAX_ZIP_MEMBERS members and
MAX_TOTAL_BYTES uncompressed bytes.

Usage: python scripts/check_no_mt5_report.py FILE... (exit 1 and the offending names when any match).
"""
from __future__ import annotations

import csv
import io
import re
import sys
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

TITLE = re.compile(r"Trade\s+History\s+Report", re.IGNORECASE)
# `\w[^>]*` (not `\w+[^>]*`): the two parts cannot share characters, so an unclosed tag costs linear time
ACCOUNT = re.compile(r"(?:Account|Сч[её]т|Konto|Cuenta):\s*(?:</?\w[^>]*>\s*)*\d{5,}", re.IGNORECASE)
# MT5's suggested report name, and owner_history's own outputs (`owner_history_<YYYYmmdd-HHMMSS>.<ext>`)
NAME = re.compile(r"^(?:ReportHistory-|owner_history_\d{8}-\d{6}\.)", re.IGNORECASE)
# A trade-list header row, matched cell by cell (whole cell, case-folded), so the analyser's input is caught at any
# length and with any stamp format: (every one of these cells, plus at least one of these if non-empty)
TRADE_HEADERS: tuple[tuple[frozenset[str], frozenset[str]], ...] = (
    (frozenset({"symbol", "type", "volume", "price"}), frozenset({"position", "deal", "order", "ticket"})),  # MT5
    (frozenset({"seq", "side", "lots", "entry", "exit", "net", "r_multiple"}), frozenset()),  # *.trades.csv
)
STAMP = re.compile(r"\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}(?::\d{2})?")
SECTION_TITLES = frozenset({
    "positions", "orders", "deals",                                  # English
    "позиции", "ордера", "сделки",                                   # Russian
    "positionen", "aufträge", "geschäfte",                           # German
    "posiciones", "órdenes", "ordenes", "transacciones", "operaciones",  # Spanish
})
MIN_STAMPED_ROWS = 5           # rows holding an MT5 stamp before a table counts as a trade list
STAMP_COLS = 3                 # the stamp may sit in any of a row's first three visible cells
MIN_REPORT_COLS = 10           # MT5 Positions rows have 13 cells, Deals 14, Orders 11
MAX_SCAN = 20 * 2**20          # bytes read per file / zip member
MAX_ZIP_MEMBERS = 200          # xl/*.xml members scanned per .xlsx
MAX_TOTAL_BYTES = 50 * 2**20   # uncompressed bytes read per .xlsx, all members together
DELIMITERS = (",", "\t", ";")

_HTML = re.compile(r"<t[rdh]\b", re.IGNORECASE)
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


class _Rows(HTMLParser):
    """Visible, non-empty cell texts per table row, in one linear pass. A cell or row start closes any open cell, so
    unclosed <td>/<tr> (legal HTML) cost nothing extra; hidden cells are dropped, as a browser shows the table."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._hidden = False

    def _close_cell(self) -> None:
        if self._cell is not None and self._row is not None and not self._hidden:
            text = " ".join("".join(self._cell).split())
            if text:
                self._row.append(text)
        self._cell = None

    def _close_row(self) -> None:
        self._close_cell()
        if self._row is not None:
            self.rows.append(self._row)
        self._row = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._close_row()
            self._row = []
        elif tag in ("td", "th"):
            self._close_cell()
            if self._row is None:
                self._row = []
            a = {k.lower(): (v or "") for k, v in attrs}
            self._hidden = "hidden" in a.get("class", "").split() or \
                "display:none" in a.get("style", "").replace(" ", "").lower()
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th"):
            self._close_cell()
        elif tag in ("tr", "table"):
            self._close_row()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)

    def close(self) -> None:
        super().close()
        self._close_row()


def _html_rows(text: str) -> list[list[str]]:
    p = _Rows()
    p.feed(text)
    p.close()
    return p.rows


def _delimited_rows(text: str, delim: str) -> list[list[str]]:
    try:
        return [[c.strip() for c in r if c.strip()] for r in csv.reader(io.StringIO(text), delimiter=delim)]
    except csv.Error:                                   # not delimited text after all (NUL bytes, huge fields)
        return []


def _xlsx_rows(members: dict[str, bytes]) -> list[list[str]]:
    """Non-empty cell texts of every row of every scanned worksheet (shared, inline and plain values). A member cut
    short by the byte budget does not parse and is skipped."""
    def parse(raw: bytes) -> ElementTree.Element | None:
        try:
            return ElementTree.fromstring(raw)
        except ElementTree.ParseError:
            return None
    sst = parse(members["xl/sharedStrings.xml"]) if "xl/sharedStrings.xml" in members else None
    shared = [] if sst is None else ["".join(t.text or "" for t in si.iter(f"{_NS}t")) for si in sst.iter(f"{_NS}si")]
    rows: list[list[str]] = []
    for name in sorted(n for n in members if n.startswith("xl/worksheets/")):
        sheet = parse(members[name])
        if sheet is None:
            continue
        for row in sheet.iter(f"{_NS}row"):
            cells = []
            for c in row.iter(f"{_NS}c"):
                v = c.find(f"{_NS}v")
                text = v.text or "" if v is not None else ""
                if c.get("t") == "s":
                    cells.append(shared[int(text)] if text.isdigit() and int(text) < len(shared) else "")
                elif c.get("t") == "inlineStr":
                    cells.append("".join(t.text or "" for t in c.iter(f"{_NS}t")))
                else:
                    cells.append(text)
            rows.append([" ".join(x.split()) for x in cells if x.strip()])
    return rows


def _report_shaped(rows: list[list[str]]) -> bool:
    """A translated trade list: enough rows with an MT5 server stamp near their start, under a known section title or
    as wide as an MT5 report row."""
    stamped = [r for r in rows if any(STAMP.fullmatch(c) for c in r[:STAMP_COLS])]
    if len(stamped) < MIN_STAMPED_ROWS:
        return False
    titled = any(len(r) == 1 and r[0].casefold().rstrip(":") in SECTION_TITLES for r in rows)
    wide = sum(len(r) >= MIN_REPORT_COLS for r in stamped) >= MIN_STAMPED_ROWS
    return titled or wide


def _trade_header(rows: list[list[str]]) -> bool:
    """A row naming the columns of an MT5 Positions / Orders / Deals table (English) or of owner_history's per-trade
    output. Whole cells only: source code quoting the same words ("Symbol", ...) keeps its quotes and does not match."""
    for r in rows:
        cells = {c.casefold() for c in r}
        if any(need <= cells and (not one or cells & one) for need, one in TRADE_HEADERS):
            return True
    return False


def _zip_members(z: zipfile.ZipFile) -> dict[str, bytes]:
    """The first MAX_ZIP_MEMBERS xl/*.xml members, reading at most MAX_SCAN each and MAX_TOTAL_BYTES in all."""
    out: dict[str, bytes] = {}
    budget = MAX_TOTAL_BYTES
    for info in z.infolist():
        if len(out) >= MAX_ZIP_MEMBERS or budget <= 0:
            break
        if info.filename.startswith("xl/") and info.filename.endswith(".xml"):
            with z.open(info) as member:
                out[info.filename] = member.read(min(MAX_SCAN, budget))
            budget -= len(out[info.filename])
    return out


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
                members = _zip_members(z)
        except (zipfile.BadZipFile, KeyError, OSError):
            return False
        if any(_matches(_text(m)) for m in members.values()):
            return True
        rows = _xlsx_rows(members)
        return _report_shaped(rows) or _trade_header(rows)
    text = _text(raw)
    if _matches(text):
        return True
    low = text.casefold()
    if ("symbol" in low and "volume" in low) or "r_multiple" in low:   # cheap gate before the header parse
        rows = _html_rows(text) if _HTML.search(text) else [r for d in DELIMITERS for r in _delimited_rows(text, d)]
        if _trade_header(rows):
            return True
    if len(STAMP.findall(text)) < MIN_STAMPED_ROWS:      # cheap gate before any table parse
        return False
    if _HTML.search(text):
        return _report_shaped(_html_rows(text))
    return any(_report_shaped(_delimited_rows(text, d)) for d in DELIMITERS)


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
