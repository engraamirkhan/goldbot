"""The owner's own MT5 trade history, analysed privately to seed research hypotheses.

The owner trades XAUUSD by hand and reports £30-50 a day on a £50 account. The research director wants that record
turned into testable hypotheses (ADR 0004 north-star). This module reads the owner's MT5 export, describes how the
money was made (risk, R-multiples, daily P&L, sessions, holding time, leverage, ruin risk) and lists the recurring
patterns as *hypothesis seeds* in the `docs/research/hypotheses.md` format. Seeds are in-sample observations of one
discretionary record, never evidence: each one still needs a pre-registered trial (strategy-researcher) and the gates.

Privacy (public repo, docs/AGENT_STANDARDS.md S4): the report holds the owner's account number, name and broker.
The file is read from outside the repo (default `~/Personal project/trade-history/`): a report inside ANY git work
tree is refused before it is opened ("complete export, run locally, share only a summary", owner-method study). The
outputs go to that same private folder with mode 0600, and writing into ANY git work tree is refused (this repo under
any spelling of its path, a sibling worktree, another clone). The console prints only the shareable summary
(`render_share`, also written as `owner_history_*.share.md`): aggregates in R, %, counts, USD per ounce and K, never
an amount in the account's currency, a ticket or a header field. Rule for `render_share`: never add lots, total
ounces, a trade count per size, the USD rate or any amount to it. USD/oz x ounces is the P&L, and P&L / risk_pct is
the account size. Errors shown to the owner never print a path (the folder or file name may carry the owner's name or
account number): only the literal DEFAULT_DIR_SHOWN. The header's account number, name, company and server are never copied
into a record or an output; only the account currency is kept. Position, order and deal tickets are used only to
match a position to its opening order and are replaced by a sequence number. Seeds quote R, percentages and counts,
never money amounts: do not paste amounts into the repo. A pre-commit hook (`scripts/check_no_mt5_report.py`)
rejects any file that looks like an MT5 report. Inputs and xlsx members over 50 MB are refused. Tests use synthetic
fixtures only.

Stops: the report's Positions S/L is the FINAL stop (after any trailing or break-even move). 1R is the initial stop
from the opening order in the Orders section when present, else the final stop, and only when it sits on the loss
side of the entry; a stop moved to break-even or into profit is marked `sl_moved` and kept out of the R statistics.
A position is paired with its opening order by ticket, else by (symbol, side, open time) only when exactly one order
and one position share that key; an ambiguous key falls back to the final stop rather than mis-pair.

Statistics: a slice "clears" the bar only at Bonferroni 5%/K on Student's t with its own n - 1 degrees of freedom
(`slice_bar`). Sessions are goldbot's windows on local exchange clocks with each date's DST (goldbot.data.calendar).

Input formats (MT5 desktop: History tab -> right-click -> Report -> HTML or Open XML):
* HTML trade-history report (usually UTF-16 with a BOM): a header block of `Label:` / value rows (Name, Account
  `<number> (<currency>, <server>, <demo|real>, <Hedge|Netting>)`, Company, Date), then sections whose title is a
  one-cell row: Positions, Orders, Deals, Open Positions, Working Orders, Results/Summary. Positions columns:
  Time, Position, Symbol, Type, Volume, Price, S / L, T / P, Time, Price, Commission, Swap, Profit. Deals columns:
  Time, Deal, Symbol, Type, Direction, Volume, Price, Order, Commission, Fee, Swap, Profit, Balance, Comment.
  Stamps are broker *server* time `YYYY.MM.DD HH:MM[:SS]` (converted with `timeutil.server_to_utc` and the account's
  `server_tz`); numbers use a space as thousands separator. Hidden and colspan cells are tolerated.
* XLSX (the same table, one sheet), read with the standard library (no new dependency).
* CSV of deals or positions (`,` `;` or tab separated) with the same column names.
Translated (non-English) column headers fall back to the standard column order.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import statistics
import subprocess
import zipfile
from collections import Counter
from collections.abc import Iterable, Sequence
from datetime import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import numpy as np
import pandas as pd
from pydantic import Field
from scipy.stats import t as student_t

from goldbot.base import FrozenRecord, UtcTimestamp, write_private
from goldbot.data.calendar import DEFAULT_SESSIONS, LOCAL_SESSIONS, LocalSession
from goldbot.data.timeutil import epoch_ns, server_to_utc

DEFAULT_DIR_SHOWN = "~/Personal project/trade-history"     # in messages: never the expanded home path
DEFAULT_DIR = Path(DEFAULT_DIR_SHOWN).expanduser()
PRIVATE_TITLE = "# Owner trade history" + " (private)"     # split: the commit hook matches the joined title
CONTRACT_OZ = 100.0                 # XAUUSD: 100 troy ounces per lot (IC Markets / Vantage)
ROUND_STEP, ROUND_BAND = 10.0, 1.0  # "near a round number": within $1 of a $10 level (uniform base rate 20%)
OPEN_WINDOW_MIN = 30                # "near a session open": within 30 min after an Asia/London/NY open
# session opens on each date's local clock (DST per date): Tokyo 08:00 (23:00 UTC, the DEFAULT_SESSIONS Asia start),
# London 08:00 Europe/London, New York 08:30 America/New_York (goldbot.data.calendar.LOCAL_SESSIONS)
SESSION_OPENS: tuple[LocalSession, ...] = (
    LocalSession(name="asia", tz="Asia/Tokyo", open=time(8, 0), close=time(15, 0)),
    LOCAL_SESSIONS["london"], LOCAL_SESSIONS["newyork"])
MAX_INPUT_BYTES = 50 * 2**20        # a report this large is not an MT5 history export
MAX_ZIP_MEMBER_BYTES = 50 * 2**20   # uncompressed size of any xlsx member (zip bomb guard)
MIN_SLICE_N = 10                    # a slice with fewer trades is reported but never said to clear the bar
FAMILY_ALPHA = 0.05                 # family-wise error for the Bonferroni bar over the slices examined
NEWS_WINDOW_MIN = 30
OUTPUT_PREFIX = "owner_history"
SECTIONS = ("positions", "orders", "deals", "open positions", "working orders", "results", "summary")
CCY_SIGN = {"GBP": "£", "USD": "$", "EUR": "€"}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
HOLDING_QUANTILES = (10, 25, 50, 75, 90)
POSITION_COLS = ("open_time", "position", "symbol", "type", "volume", "open_price", "sl", "tp", "close_time",
                 "close_price", "commission", "swap", "profit")
DEAL_COLS = ("time", "deal", "symbol", "type", "direction", "volume", "price", "order", "commission", "fee", "swap",
             "profit", "balance", "comment")


# ------------------------------------------------------------------------------------------------ records

class Position(FrozenRecord):
    """One closed position as the report states it (times already in UTC, no ticket numbers)."""
    seq: int
    symbol: str
    side: Literal["buy", "sell"]
    lots: float = Field(gt=0)
    open_utc: UtcTimestamp
    close_utc: UtcTimestamp
    entry: float
    exit: float
    sl: float | None = None         # the report's S/L: the FINAL stop
    initial_sl: float | None = None  # the opening order's S/L (Orders section), when the report has it
    tp: float | None = None
    commission: float = 0.0
    swap: float = 0.0
    profit: float


class CashFlow(FrozenRecord):
    time_utc: UtcTimestamp
    amount: float                   # deposit > 0, withdrawal < 0, in account currency


class Report(FrozenRecord):
    """What is kept from a report: currency, positions, cash flows and the final balance. Nothing identifying."""
    currency: str = "USD"
    positions: list[Position]
    deposits: list[CashFlow] = Field(default_factory=list)
    final_balance: float | None = None
    source_format: str


# sl_initial / sl_final: 1R from that stop (counted in R stats); sl_moved: the only stop known is at or beyond the
# entry (break-even or trailing), so no 1R; proxy_avg_loss: no stop, 1R = average loss per lot (not in R stats)
RiskSource = Literal["sl_initial", "sl_final", "sl_moved", "proxy_avg_loss", "none"]
R_SOURCES = ("sl_initial", "sl_final")


class Trade(FrozenRecord):
    """A closed XAUUSD trade with the derived measures."""
    seq: int
    side: Literal["buy", "sell"]
    lots: float
    open_utc: UtcTimestamp
    close_utc: UtcTimestamp
    entry: float
    exit: float
    sl: float | None
    tp: float | None
    profit: float
    commission: float
    swap: float
    net: float
    holding_min: float
    risk: float | None              # account currency at risk if the stop were hit
    risk_source: RiskSource
    r_multiple: float | None
    session: str
    hour: int
    weekday: str
    equity_at_open: float | None
    leverage: float | None          # notional / equity at entry
    risk_pct: float | None = None   # 1R / equity at entry, only for a real stop (R_SOURCES)
    usd_per_oz: float = 0.0         # net result in USD per ounce traded (after costs; the transferable capture)
    move_per_oz: float = 0.0        # price move in the trade's direction, USD per ounce (before costs)
    near_round: bool
    near_session_open: bool
    near_news: bool | None


class GroupStat(FrozenRecord):
    key: str
    n: int
    net: float
    mean: float
    win_rate: float
    t_stat: float | None            # t of the mean net P&L per trade in this slice
    n_r: int = 0                    # trades in the slice with a real 1R (sl_initial / sl_final)
    mean_r: float | None = None
    mean_usd_per_oz: float | None = None    # mean net capture per ounce, USD (price terms, no account money)


class DailyStats(FrozenRecord):
    day: str                        # which clock defines a day: "UTC" (goldbot's risk-day) or "Europe/London"
    n_days: int
    mean: float
    median: float
    std: float
    t_stat: float | None            # t of the mean daily P&L (days as independent draws)
    ci95: tuple[float, float]       # normal-approximation 95% interval of the mean daily P&L
    best: float
    worst: float
    worst_day: str
    share_at_least: dict[str, float]     # target (account currency) -> share of trading days with net >= target
    days: dict[str, float]


class RuinEstimate(FrozenRecord):
    method: str
    horizon_trades: int
    n_paths: int
    ruin_level: float               # fraction of starting equity counted as ruin
    block_length: int               # circular block bootstrap block (goldbot.research.ruin.default_block_length)
    p_ruin: float
    p_halved: float
    median_end_log10: float         # log10 of the median end equity / start (finite: floored at -6, capped at +6)


class HypothesisSeed(FrozenRecord):
    id: str
    hypothesis: str
    status: str = "seed (in-sample, not pre-registered)"
    rationale: str
    data: str = "D"
    horizon: str
    edge_vs_cost: str
    trial_cost: str = "1"
    maps_to: str = ""


class Summary(FrozenRecord):
    currency: str
    source_format: str
    n_trades: int
    period_start: UtcTimestamp
    period_end: UtcTimestamp
    usd_to_ccy: float
    usd_to_ccy_source: str
    total_net: float
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    expectancy_t: float | None
    expectancy_r: float | None
    n_r: int
    profit_factor: float | None
    risk_sources: dict[str, int]
    median_risk: float | None
    median_holding_min: float
    holding_quantiles: dict[str, float]     # p10 / p25 / p50 / p75 / p90 of the holding time, minutes
    mean_usd_per_oz: float                  # mean net capture per ounce in USD, after costs (study section 3)
    mean_move_per_oz: float                 # mean price move in the trade's direction per ounce, before costs
    median_risk_pct: float | None           # 1R / equity at entry over trades with a real stop
    max_risk_pct: float | None
    daily: DailyStats
    daily_london: DailyStats
    mean_return_on_equity: float | None   # mean net / equity at entry, per trade
    return_on_equity_t: float | None
    by_session: list[GroupStat]
    by_hour: list[GroupStat]
    by_weekday: list[GroupStat]
    by_side: list[GroupStat]
    by_holding: list[GroupStat]
    news: list[GroupStat] | None
    news_note: str
    share_near_session_open: float
    session_open_base_rate: float   # share expected if entries were uniform over the owner's active hours
    share_near_round_number: float
    slices_examined: int            # K: every slice the report breaks the trades into (seeds pick from these)
    bonferroni_t: float             # |t| for family-wise 5% across K slices in the large-slice (normal) limit; a
    #                                 slice of n trades needs Student's t on n - 1 df (`slice_bar`), always higher
    start_equity: float | None
    start_equity_source: str
    median_leverage: float | None
    max_leverage: float | None
    lots_equity_corr: float | None
    longest_losing_streak: int
    longest_winning_streak: int
    max_drawdown: float
    max_drawdown_pct: float | None
    ruin: list[RuinEstimate]        # [compounding, fixed stakes, zero-edge compounding, zero-edge fixed stakes];
    #                                 empty when equity is unknown
    notes: list[str]


class Result(FrozenRecord):
    summary: Summary
    trades: list[Trade]
    seeds: list[HypothesisSeed]


# ------------------------------------------------------------------------------------------------ parsing

_SPACES = re.compile(r"[\s   ']")


def parse_number(text: object) -> float | None:
    """A report number in any locale: `1 234.56`, `1 234,56`, `1,234.56`, `1.234,56`, `−3.5`, `0.01 / 0.01`."""
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return None if isinstance(text, float) and math.isnan(text) else float(text)
    s = str(text).split("/")[0]
    s = _SPACES.sub("", s).replace("−", "-")
    if not s or not re.search(r"\d", s):
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"-?\d{1,3}(,\d{3}){2,}", s) else s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def parse_time(text: object) -> pd.Timestamp | None:
    """A naive server-time stamp: `2026.10.01 10:05:12`, `2026.10.01 10:05`, ISO, or an Excel serial number."""
    if text is None or text == "":
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        v = float(text)
        return pd.Timestamp("1899-12-30") + pd.to_timedelta(v, unit="D") if 20000 < v < 80000 else None
    s = str(text).strip()
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d"):
        try:
            return pd.Timestamp(pd.to_datetime(s, format=fmt))
        except (ValueError, TypeError):
            continue
    num = parse_number(s)
    if num is not None and re.fullmatch(r"\d+(\.\d+)?", s):
        return parse_time(num)
    return None


class _TableParser(HTMLParser):
    """Rows of every <tr> as lists of cell texts. Hidden cells are dropped; a colspan cell is padded with blanks so
    columns line up; rows keep both forms so a header/value colspan mismatch can fall back to the raw cells."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._span = 1
        self._hidden = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            try:
                self._span = max(1, int(a.get("colspan", "1") or 1))
            except ValueError:
                self._span = 1
            self._hidden = "hidden" in a.get("class", "").split() or "display:none" in a.get("style", "").replace(" ", "")
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            if not self._hidden:
                self._row.append(" ".join("".join(self._cell).split()))
                self._row.extend([""] * (self._span - 1))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8")
    head = raw[:400]
    if head.count(b"\x00") > len(head) // 4:
        return raw.decode("utf-16-le" if head[1:2] == b"\x00" else "utf-16-be")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def html_rows(raw: bytes) -> list[list[str]]:
    p = _TableParser()
    p.feed(_decode(raw))
    p.close()
    return p.rows


_XNS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "pr": "http://schemas.openxmlformats.org/package/2006/relationships"}


def _col_index(ref: str) -> int:
    n = 0
    for ch in re.match(r"[A-Z]+", ref.upper()).group(0):   # type: ignore[union-attr]
        n = n * 26 + ord(ch) - 64
    return n - 1


def xlsx_rows(raw: bytes) -> list[list[str]]:
    """Rows of the first worksheet of an .xlsx (shared, inline and numeric cells), standard library only."""
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        names = set(z.namelist())
        big = [i.file_size for i in z.infolist() if i.file_size > MAX_ZIP_MEMBER_BYTES]
        if big:
            raise ValueError(f"xlsx member too large ({max(big)} bytes uncompressed > {MAX_ZIP_MEMBER_BYTES}): "
                             "not an MT5 history export")
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            sst = ElementTree.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(f"{{{_XNS['m']}}}t")) for si in sst.findall("m:si", _XNS)]
        sheet = "xl/worksheets/sheet1.xml"
        try:
            wb = ElementTree.fromstring(z.read("xl/workbook.xml"))
            first = wb.find("m:sheets/m:sheet", _XNS)
            rid = first.get(f"{{{_XNS['r']}}}id") if first is not None else None
            rels = ElementTree.fromstring(z.read("xl/_rels/workbook.xml.rels"))
            for rel in rels.findall("pr:Relationship", _XNS):
                if rel.get("Id") == rid:
                    target = rel.get("Target", "").lstrip("/")
                    sheet = target if target.startswith("xl/") else f"xl/{target}"
        except KeyError:
            pass
        if sheet not in names:
            sheet = sorted(n for n in names if n.startswith("xl/worksheets/sheet"))[0]
        root = ElementTree.fromstring(z.read(sheet))
    rows: list[list[str]] = []
    for row in root.iter(f"{{{_XNS['m']}}}row"):
        cells: dict[int, str] = {}
        for i, c in enumerate(row.findall("m:c", _XNS)):
            idx = _col_index(c.get("r", "")) if c.get("r") else i
            t = c.get("t", "")
            v = c.find("m:v", _XNS)
            if t == "s" and v is not None and v.text is not None:
                val = shared[int(v.text)]
            elif t == "inlineStr":
                val = "".join(x.text or "" for x in c.iter(f"{{{_XNS['m']}}}t"))
            else:
                val = v.text or "" if v is not None else ""
            cells[idx] = val.strip()
        rows.append([cells.get(i, "") for i in range(max(cells) + 1)] if cells else [])
    return rows


def csv_rows(raw: bytes) -> list[list[str]]:
    text = _decode(raw)
    first = text.splitlines()[0] if text else ""
    delim = max(("\t", ";", ","), key=first.count)
    return [list(r) for r in csv.reader(io.StringIO(text), delimiter=delim)]


def _norm(h: str) -> str:
    return re.sub(r"[^a-z]", "", h.lower())


_ALIASES = {"time": "time", "opentime": "time", "position": "position", "ticket": "position", "symbol": "symbol",
            "type": "type", "volume": "volume", "size": "volume", "lots": "volume", "price": "price",
            "openprice": "price", "sl": "sl", "stoploss": "sl", "tp": "tp", "takeprofit": "tp",
            "closetime": "close_time", "closeprice": "close_price", "commission": "commission", "fee": "fee",
            "swap": "swap", "profit": "profit", "deal": "deal", "direction": "direction", "order": "order",
            "balance": "balance", "comment": "comment", "state": "state"}


def _header_map(header: Sequence[str], kind: str) -> dict[str, int]:
    """Column name -> index. The Positions table repeats Time and Price (open, then close)."""
    out: dict[str, int] = {}
    for i, h in enumerate(header):
        key = _ALIASES.get(_norm(h))
        if key is None:
            continue
        if kind == "positions" and key in ("time", "price"):
            first, second = ("open_time", "close_time") if key == "time" else ("open_price", "close_price")
            out[second if first in out else first] = i
        elif key not in out:
            out[key] = i
    if kind == "positions" and len(out) < 8 and len([h for h in header if h]) == len(POSITION_COLS):
        return {c: i for i, c in enumerate(POSITION_COLS)}          # translated headers: standard order
    if kind == "deals" and len(out) < 8 and len([h for h in header if h]) == len(DEAL_COLS):
        return {c: i for i, c in enumerate(DEAL_COLS)}
    return out


def _cell(row: Sequence[str], cols: dict[str, int], key: str) -> str:
    i = cols.get(key)
    return row[i] if i is not None and i < len(row) else ""


def _sections(rows: list[list[str]]) -> tuple[dict[str, str], dict[str, list[list[str]]]]:
    """(label -> value pairs from the header and Results block, section name -> rows incl. its header row)."""
    labels: dict[str, str] = {}
    sections: dict[str, list[list[str]]] = {}
    current: str | None = None
    for r in rows:
        cells = [c for c in r if c != ""]
        if not cells:
            continue
        if len(cells) == 1 and cells[0].strip().lower().rstrip(":") in SECTIONS:
            current = cells[0].strip().lower().rstrip(":")
            if current == "summary":
                current = "results"
            sections.setdefault(current, [])
            continue
        if cells[0].endswith(":") and (current is None or current == "results"):
            for k in range(0, len(cells) - 1, 2):
                if cells[k].endswith(":"):
                    labels.setdefault(cells[k].rstrip(":").strip().lower(), cells[k + 1])
            continue
        if current is not None:
            sections[current].append(r)
    return labels, sections


def _table(rows: list[list[str]], kind: str) -> tuple[dict[str, int], list[list[str]]]:
    """(column map from the section's header row, data rows). Hidden cells are already dropped and colspans padded
    by the parser, so header and value rows line up as they do on screen."""
    if not rows:
        return {}, []
    return _header_map(rows[0], kind), rows[1:]


def _side(text: str) -> Literal["buy", "sell"] | None:
    t = text.strip().lower()
    if t.startswith("buy"):
        return "buy"
    if t.startswith("sell"):
        return "sell"
    return None


def _initial_stops(cols: dict[str, int], body: list[list[str]]
                   ) -> tuple[dict[str, float], dict[tuple[str, str, pd.Timestamp], float]]:
    """The S/L each filled opening order was placed with: by order ticket (an MT5 position's ticket is its opening
    order's) and, as a fallback, by (symbol, side, open time). Orders without a stop are not matched. A fallback key
    shared by two or more filled orders (with or without a stop) is dropped: the position cannot be told apart, so
    it falls back to its final stop rather than borrow another order's."""
    by_ticket: dict[str, float] = {}
    by_open: dict[tuple[str, str, pd.Timestamp], float] = {}
    seen: Counter[tuple[str, str, pd.Timestamp]] = Counter()
    for r in body:
        state = _cell(r, cols, "state").strip().lower()
        if state and not state.startswith(("fill", "partial")):
            continue                                      # cancelled / expired / rejected orders opened nothing
        sl, side, t = parse_number(_cell(r, cols, "sl")), _side(_cell(r, cols, "type")), parse_time(
            _cell(r, cols, "time"))
        if side is None:
            continue
        key = (_cell(r, cols, "symbol").strip().upper(), side, t) if t is not None else None
        if key is not None:
            seen[key] += 1
        if not sl:
            continue
        ticket = _cell(r, cols, "order").strip()
        if ticket:
            by_ticket.setdefault(ticket, sl)
        if key is not None:
            by_open.setdefault(key, sl)
    return by_ticket, {k: v for k, v in by_open.items() if seen[k] == 1}


def _positions_from_table(cols: dict[str, int], body: list[list[str]], server_tz: str,
                          initial: tuple[dict[str, float], dict[tuple[str, str, pd.Timestamp], float]] | None = None
                          ) -> list[Position]:
    by_ticket, by_open = initial or ({}, {})
    # the (symbol, side, open time) fallback pairs only a position whose key no other position shares
    keys = Counter((_cell(r, cols, "symbol").strip().upper(), _side(_cell(r, cols, "type")),
                    parse_time(_cell(r, cols, "open_time"))) for r in body)
    raw = []
    for r in body:
        ot, ct = parse_time(_cell(r, cols, "open_time")), parse_time(_cell(r, cols, "close_time"))
        side = _side(_cell(r, cols, "type"))
        lots = parse_number(_cell(r, cols, "volume"))
        entry, exit_ = parse_number(_cell(r, cols, "open_price")), parse_number(_cell(r, cols, "close_price"))
        profit = parse_number(_cell(r, cols, "profit"))
        if ot is None or ct is None or side is None or not lots or entry is None or exit_ is None or profit is None:
            continue
        sl, tp = parse_number(_cell(r, cols, "sl")), parse_number(_cell(r, cols, "tp"))
        symbol = _cell(r, cols, "symbol").strip()
        ticket = _cell(r, cols, "position").strip()          # used for matching only, never stored
        init = by_ticket.get(ticket) if ticket else None
        if init is None and keys[(symbol.upper(), side, ot)] == 1:
            init = by_open.get((symbol.upper(), side, ot))
        raw.append(dict(symbol=symbol, side=side, lots=lots, ot=ot, ct=ct, entry=entry,
                        exit=exit_, sl=sl or None, initial_sl=init, tp=tp or None,
                        commission=parse_number(_cell(r, cols, "commission")) or 0.0,
                        swap=parse_number(_cell(r, cols, "swap")) or 0.0, profit=profit))
    return _to_positions(raw, server_tz)


def _to_positions(raw: list[dict], server_tz: str) -> list[Position]:
    if not raw:
        return []
    ots = server_to_utc(pd.DatetimeIndex([x["ot"] for x in raw]), server_tz)
    cts = server_to_utc(pd.DatetimeIndex([x["ct"] for x in raw]), server_tz)
    out = []
    for x, o, c in zip(raw, ots, cts):
        if pd.isna(o) or pd.isna(c):
            continue                                       # a stamp inside a DST overlap cannot be placed
        out.append(Position(seq=0, symbol=x["symbol"], side=x["side"], lots=x["lots"], open_utc=o, close_utc=c,
                            entry=x["entry"], exit=x["exit"], sl=x["sl"], initial_sl=x.get("initial_sl"),
                            tp=x["tp"], commission=x["commission"],
                            swap=x["swap"], profit=x["profit"]))
    out.sort(key=lambda p: (p.close_utc, p.open_utc))
    return [p.model_copy(update={"seq": i + 1}) for i, p in enumerate(out)]


def _deals(cols: dict[str, int], body: list[list[str]], server_tz: str
           ) -> tuple[list[Position], list[CashFlow]]:
    """Positions rebuilt from in/out deals (FIFO per symbol; a hedge account's simultaneous opposite positions are
    approximated), plus balance deposits/withdrawals."""
    flows_raw: list[tuple[pd.Timestamp, float]] = []
    open_q: dict[str, list[dict]] = {}
    raw: list[dict] = []
    for r in body:
        t = parse_time(_cell(r, cols, "time"))
        typ = _cell(r, cols, "type").strip().lower()
        if t is None:
            continue
        if typ in ("balance", "credit", "deposit", "withdrawal"):
            amt = parse_number(_cell(r, cols, "profit"))
            if amt:
                flows_raw.append((t, amt))
            continue
        side, sym = _side(typ), _cell(r, cols, "symbol").strip()
        direction = _norm(_cell(r, cols, "direction"))
        vol, price = parse_number(_cell(r, cols, "volume")), parse_number(_cell(r, cols, "price"))
        if side is None or not vol or price is None or not sym:
            continue
        com = (parse_number(_cell(r, cols, "commission")) or 0.0) + (parse_number(_cell(r, cols, "fee")) or 0.0)
        swap = parse_number(_cell(r, cols, "swap")) or 0.0
        profit = parse_number(_cell(r, cols, "profit")) or 0.0
        q = open_q.setdefault(sym, [])
        if direction == "in":
            q.append(dict(side=side, lots=vol, t=t, price=price, com_per_lot=com / vol))
            continue
        remaining = vol
        while remaining > 1e-9 and q:
            leg = q[0]
            take = min(leg["lots"], remaining)
            share = take / vol
            raw.append(dict(symbol=sym, side=leg["side"], lots=round(take, 8), ot=leg["t"], ct=t, entry=leg["price"],
                            exit=price, sl=None, tp=None, commission=leg["com_per_lot"] * take + com * share,
                            swap=swap * share, profit=profit * share))
            leg["lots"] -= take
            remaining -= take
            if leg["lots"] <= 1e-9:
                q.pop(0)
        if direction in ("inout", "in/out") and remaining > 1e-9:
            q.append(dict(side=side, lots=remaining, t=t, price=price, com_per_lot=0.0))
    flows = []
    if flows_raw:
        ts = server_to_utc(pd.DatetimeIndex([f[0] for f in flows_raw]), server_tz)
        flows = [CashFlow(time_utc=u, amount=a) for u, (_, a) in zip(ts, flows_raw) if not pd.isna(u)]
    return _to_positions(raw, server_tz), flows


def load_report(path: str | Path, server_tz: str = "Europe/Athens") -> Report:
    """Parse an MT5 report (HTML, XLSX or CSV). Only the currency, closed positions, cash flows and the final
    balance are kept; the header's account number, name, company and server are read past and discarded. A report
    inside a git work tree is refused before it is opened: one `git add` would publish it."""
    p = Path(path)
    refuse_in_git_tree(p)
    size = p.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise ValueError(f"report too large ({size} bytes > {MAX_INPUT_BYTES}): not an MT5 history export")
    raw = p.read_bytes()
    suffix = p.suffix.lower()
    if raw[:2] == b"PK" or suffix in (".xlsx", ".xlsm"):
        rows, fmt = xlsx_rows(raw), "xlsx"
    elif suffix in (".csv", ".tsv", ".txt"):
        rows, fmt = csv_rows(raw), "csv"
    else:
        rows, fmt = html_rows(raw), "html"
    labels, sections = _sections(rows)
    if fmt == "csv" and not sections and rows:
        kind = "deals" if any(_norm(h) in ("deal", "direction") for h in rows[0]) else "positions"
        sections = {kind: rows}
    currency = "USD"
    m = re.search(r"\(\s*([A-Z]{3})\s*,", labels.get("account", ""))
    if m:
        currency = m.group(1)
    elif labels.get("currency"):
        currency = labels["currency"].strip().upper()[:3]
    positions: list[Position] = []
    flows: list[CashFlow] = []
    initial = None
    if sections.get("orders"):
        cols, body = _table(sections["orders"], "orders")
        initial = _initial_stops(cols, body)
    if sections.get("positions"):
        cols, body = _table(sections["positions"], "positions")
        positions = _positions_from_table(cols, body, server_tz, initial)
    if sections.get("deals"):
        cols, body = _table(sections["deals"], "deals")
        from_deals, flows = _deals(cols, body, server_tz)
        if not positions:
            positions = from_deals
    final = parse_number(labels.get("balance", "")) if labels.get("balance") else None
    del labels                                             # nothing from the header outlives the parse
    return Report(currency=currency, positions=positions, deposits=flows, final_balance=final, source_format=fmt)


def is_xauusd(symbol: str) -> bool:
    s = symbol.upper()
    return s.startswith("XAUUSD") or s in ("GOLD", "GOLD.", "XAU/USD")


def xauusd_positions(report: Report) -> list[Position]:
    return [p for p in report.positions if is_xauusd(p.symbol)]


# ------------------------------------------------------------------------------------------------ analysis

def _t(x: np.ndarray) -> float | None:
    if len(x) < 2:
        return None
    sd = float(np.std(x, ddof=1))
    return None if sd == 0 else float(np.mean(x) / (sd / math.sqrt(len(x))))


def _group(keys: Iterable[str], nets: np.ndarray, order: Sequence[str] | None = None,
           rs: np.ndarray | None = None, oz: np.ndarray | None = None) -> list[GroupStat]:
    """Per-slice count, net, mean, win rate and t; with `rs` (NaN where a trade has no real 1R) also mean R, with
    `oz` the mean net capture per ounce in USD."""
    df = pd.DataFrame({"k": list(keys), "net": nets, "r": rs if rs is not None else np.nan,
                       "oz": oz if oz is not None else np.nan})
    out = []
    names = [k for k in (order or sorted(df["k"].unique())) if k in set(df["k"])]
    for k in names:
        sub = df.loc[df["k"] == k]
        x = sub["net"].to_numpy(dtype=float)
        r = sub["r"].dropna().to_numpy(dtype=float)
        out.append(GroupStat(key=str(k), n=len(x), net=float(x.sum()), mean=float(x.mean()),
                             win_rate=float((x > 0).mean()), t_stat=_t(x), n_r=len(r),
                             mean_r=float(r.mean()) if len(r) else None,
                             mean_usd_per_oz=float(sub["oz"].mean()) if oz is not None else None))
    return out


def streaks(nets: np.ndarray) -> tuple[int, int]:
    """(longest run of winning trades, longest run of losing trades) in close order; a flat trade breaks both."""
    win = loss = best_win = best_loss = 0
    for n in nets:
        win, loss = (win + 1 if n > 0 else 0), (loss + 1 if n < 0 else 0)
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return best_win, best_loss


def implied_usd_rate(positions: Sequence[Position], currency: str, contract: float = CONTRACT_OZ
                     ) -> tuple[float, str]:
    """USD -> account currency, read from the trades themselves: profit / (price move x lots x contract). The median
    over trades that moved at least 5 cents, inferred for every account (a "USD" account should give 1.0; anything
    else means the contract size or the currency is not what the report says, and the caller warns)."""
    rates = []
    for p in positions:
        move = (p.exit - p.entry) * (1 if p.side == "buy" else -1)
        if abs(move) >= 0.05 and p.profit:
            r = p.profit / (move * p.lots * contract)
            if 0.2 < r < 5:
                rates.append(r)
    if not rates:
        return 1.0, "assumed 1.0 (no trade moved enough to infer it)"
    return float(np.median(rates)), f"implied by {len(rates)} trades (median profit / USD move)"


LOG10_CAP = 6.0     # end multiples are reported as log10, floored/capped at +-6 (a millionfold), never inf


def bootstrap_ruin(returns: np.ndarray, horizon: int, n_paths: int = 2000, seed: int = 0,
                   ruin_level: float = 0.1, compounding: bool = True, demean: bool = False,
                   block_length: int | None = None) -> RuinEstimate:
    """Resample the owner's per-trade returns for `horizon` trades with goldbot.research.ruin's circular block
    bootstrap (blocks of consecutive trades keep short losing streaks; block `default_block_length(n)`).
    compounding: returns are on equity at entry, i.e. lots scale with the account; otherwise they are on the starting
    equity, i.e. the same money at stake whatever the balance. demean: remove the sample's edge (zero-edge stress).
    p_ruin: paths whose equity touches `ruin_level` x start; p_halved: paths that lose half at any point. An emptied
    account stops trading (a return of -100% or worse ends the path at zero)."""
    from goldbot.research.ruin import block_bootstrap, default_block_length
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0 or horizon <= 0:
        raise ValueError("no returns to resample")
    if demean:
        r = r - r.mean()
    block = block_length or default_block_length(len(r))
    draws = block_bootstrap(r, n_paths=n_paths, n_trades=horizon, block_length=block,
                            rng=np.random.default_rng(seed))
    if compounding:
        growth = np.clip(1.0 + draws, 0.0, None)
        wiped = (growth <= 0.0).cumsum(axis=1) > 0
        with np.errstate(divide="ignore"):
            path = np.where(wiped, -np.inf, np.cumsum(np.log10(np.where(growth > 0, growth, 1.0)), axis=1))
        low = path.min(axis=1)
        ruined, halved = low <= math.log10(ruin_level), low <= math.log10(0.5)
        end_log = path[:, -1]
    else:
        path = 1.0 + np.cumsum(draws, axis=1)
        low = path.min(axis=1)
        ruined, halved = low <= ruin_level, low <= 0.5
        with np.errstate(divide="ignore"):
            end_log = np.where(low <= 1e-9, -np.inf, np.log10(np.clip(path[:, -1], 1e-300, None)))
    how = "returns on equity at entry, compounding" if compounding else "fixed stakes in account currency"
    if demean:
        how += ", zero edge (sample mean removed)"
    med = float(np.clip(np.median(end_log), -LOG10_CAP, LOG10_CAP))
    return RuinEstimate(method=f"circular block bootstrap of per-trade P&L ({how})", horizon_trades=horizon,
                        n_paths=n_paths, ruin_level=ruin_level, block_length=int(block),
                        p_ruin=float(ruined.mean()), p_halved=float(halved.mean()), median_end_log10=med)


def _ruin(trades: list[Trade], start: float | None, n_paths: int, seed: int) -> tuple[list[RuinEstimate], str]:
    """Both readings of the owner's sizing (lots scaled with equity; fixed stakes), each also as a zero-edge
    (demeaned) stress run: the risk the sizing carries if the sample's edge is not real, whichever reading holds."""
    rets = np.array([t.net / t.equity_at_open for t in trades if t.equity_at_open and t.equity_at_open > 0])
    if len(rets) < 5 or not start:
        return [], "risk of ruin not estimated: equity at entry unknown or fewer than 5 trades"
    days = max(1, len({t.close_utc.date() for t in trades}))
    horizon = int(min(2500, max(len(rets), round(len(rets) / days * 250))))
    fixed = np.array([t.net for t in trades]) / start
    return [bootstrap_ruin(rets, horizon=horizon, n_paths=n_paths, seed=seed),
            bootstrap_ruin(fixed, horizon=horizon, n_paths=n_paths, seed=seed, compounding=False),
            bootstrap_ruin(rets, horizon=horizon, n_paths=n_paths, seed=seed, demean=True),
            bootstrap_ruin(fixed, horizon=horizon, n_paths=n_paths, seed=seed, compounding=False, demean=True)], ""


def session_open_flags(idx: pd.DatetimeIndex) -> np.ndarray:
    """Entries in the first OPEN_WINDOW_MIN minutes after an Asia (Tokyo 08:00), London (08:00 Europe/London) or New
    York (08:30 America/New_York) open, each on its own date's local clock, so DST moves the UTC window."""
    flags = np.zeros(len(idx), dtype=bool)
    for sess in SESSION_OPENS:
        m = sess.minutes_from_open(idx)
        flags |= (m >= 0) & (m < OPEN_WINDOW_MIN)
    return flags


def session_open_base_rate(idx: pd.DatetimeIndex) -> float:
    """The share of entries expected near an open if they were spread evenly over the owner's active hours: the UTC
    hours that hold at least one entry, on every date that has an entry (so a London-only trader is compared with
    London hours, not with a 24-hour day)."""
    hours = sorted(set(int(h) for h in idx.hour))
    dates = pd.DatetimeIndex(idx.normalize().unique())
    if not hours or not len(dates):
        return 0.0
    offs = pd.to_timedelta([h * 60 + m for h in hours for m in range(60)], unit="min")
    grid = pd.DatetimeIndex([d + o for d in dates for o in offs])
    return float(session_open_flags(grid).mean())


def _news_flags(idx: pd.DatetimeIndex, events: pd.DataFrame | None) -> tuple[np.ndarray | None, str]:
    if events is None:
        return None, "no economic calendar given: news proximity not measured"
    ev = events
    if "tier" in ev.columns:
        ev = ev[ev["tier"] == 1]
    if ev.empty or "ts_utc" not in ev.columns:
        return None, "the calendar has no tier-1 events: news proximity not measured"
    ts = np.sort(epoch_ns(pd.DatetimeIndex(pd.to_datetime(ev["ts_utc"], utc=True))))
    x = epoch_ns(idx)
    k = np.searchsorted(ts, x)
    w = NEWS_WINDOW_MIN * 60 * 10**9
    near = np.zeros(len(x), dtype=bool)
    for j in (k - 1, k):
        ok = (j >= 0) & (j < len(ts))
        near[ok] |= np.abs(x[ok] - ts[j[ok]]) <= w
    covered = (x >= ts[0] - 86400 * 10**9) & (x <= ts[-1] + 86400 * 10**9)
    note = "" if covered.all() else f"the calendar covers {int(covered.sum())} of {len(x)} entries"
    return near, note


def _daily(nets: np.ndarray, closes: pd.DatetimeIndex, tz: str, targets: Sequence[float]) -> DailyStats:
    """Net P&L per calendar day of the close on `tz`'s clock (days with at least one closed trade)."""
    local = closes.tz_convert(tz)
    days = pd.Series(nets, index=pd.DatetimeIndex(local.tz_localize(None)).normalize()).groupby(level=0).sum()
    vals = days.to_numpy(dtype=float)
    keys = pd.DatetimeIndex(days.index).strftime("%Y-%m-%d").tolist()
    worst = int(np.argmin(vals))
    sd = float(vals.std(ddof=1)) if len(vals) > 1 else 0.0
    half = 1.96 * sd / math.sqrt(len(vals)) if len(vals) > 1 else 0.0
    return DailyStats(
        day=tz, n_days=len(vals), mean=float(vals.mean()), median=float(np.median(vals)), std=sd, t_stat=_t(vals),
        ci95=(float(vals.mean() - half), float(vals.mean() + half)), best=float(vals.max()),
        worst=float(vals[worst]), worst_day=keys[worst],
        share_at_least={f"{t:g}": float((vals >= t).mean()) for t in targets},
        days={k: float(v) for k, v in zip(keys, vals)})


def _one_r(p: Position, contract: float, rate: float) -> tuple[float | None, RiskSource]:
    """1R in account currency from the initial stop (else the final one), only when it is on the loss side."""
    for stop, src in ((p.initial_sl, "sl_initial"), (p.sl, "sl_final")):
        if stop is None:
            continue
        loss_side = stop < p.entry if p.side == "buy" else stop > p.entry
        if loss_side:
            return abs(p.entry - stop) * p.lots * contract * rate, src     # type: ignore[return-value]
        return None, "sl_moved"
    return None, "none"


def analyse(report: Report, *, contract: float = CONTRACT_OZ, targets: Sequence[float] = (30.0, 50.0),
            start_balance: float | None = None, events: pd.DataFrame | None = None, n_paths: int = 2000,
            seed: int = 0) -> Result:
    """Every measure of the brief over the XAUUSD trades of `report`, plus hypothesis seeds."""
    pos = xauusd_positions(report)
    if not pos:
        raise ValueError("no XAUUSD trades found: is this the MT5 History report (Positions or Deals section)?")
    notes: list[str] = []
    rate, rate_src = implied_usd_rate(pos, report.currency, contract)
    if report.currency == "USD" and abs(rate - 1.0) > 0.02 and rate_src.startswith("implied"):
        notes.append(f"USD account, but the trades imply {rate:.3f} account units per USD: the contract size "
                     f"(--contract, now {contract:g} oz) or the report's currency is not what it seems; risk and "
                     "leverage use the implied rate.")
    nets = np.array([p.profit + p.commission + p.swap for p in pos])
    # 1R without a stop: the average loss per lot of the losing trades, scaled by lots (flagged as a proxy)
    losers = [(n, p.lots) for n, p in zip(nets, pos) if n < 0]
    loss_per_lot = float(np.mean([-n / lots for n, lots in losers])) if losers else None
    # starting equity: deposits in the report, else the owner's figure, else the final balance minus the P&L
    if report.deposits:
        start, start_src = 0.0, "deposits in the report"
    elif start_balance is not None:
        start, start_src = float(start_balance), "given on the command line"
    elif report.final_balance is not None:
        start, start_src = report.final_balance - float(nets.sum()), "final balance minus the trades' net P&L"
        notes.append("Starting equity inferred as final balance minus net P&L (assumes no deposits or withdrawals).")
    else:
        start, start_src = float("nan"), "unknown"
        notes.append("Starting equity unknown: leverage, drawdown % and ruin are not computed (pass --start-balance).")
    flows = sorted(report.deposits, key=lambda f: f.time_utc)
    close_ns = np.array([p.close_utc.value for p in pos])
    flow_ns = np.array([f.time_utc.value for f in flows], dtype=np.int64)
    flow_amt = np.array([f.amount for f in flows], dtype=float)

    open_idx = pd.DatetimeIndex([p.open_utc for p in pos])
    # goldbot's session windows on local exchange clocks, each resolved for the entry's own date (DST per date)
    sessions = DEFAULT_SESSIONS.session_label(open_idx)
    open_flags = session_open_flags(open_idx)
    news, news_note = _news_flags(open_idx, events)
    trades: list[Trade] = []
    for i, p in enumerate(pos):
        net = float(nets[i])
        risk, src = _one_r(p, contract, rate)
        if src == "none" and loss_per_lot:
            risk, src = loss_per_lot * p.lots, "proxy_avg_loss"
        eq: float | None = None
        if not math.isnan(start):
            o = p.open_utc.value
            eq = start + float(flow_amt[flow_ns <= o].sum()) + float(nets[close_ns <= o].sum())
            eq = eq if eq > 0 else None
        trades.append(Trade(
            seq=p.seq, side=p.side, lots=p.lots, open_utc=p.open_utc, close_utc=p.close_utc, entry=p.entry,
            exit=p.exit, sl=p.sl, tp=p.tp, profit=p.profit, commission=p.commission, swap=p.swap, net=net,
            holding_min=(p.close_utc - p.open_utc).total_seconds() / 60, risk=risk, risk_source=src,
            r_multiple=net / risk if risk else None, session=str(sessions[i]), hour=int(p.open_utc.hour),
            weekday=WEEKDAYS[p.open_utc.dayofweek], equity_at_open=eq,
            leverage=p.lots * contract * p.entry * rate / eq if eq else None,
            risk_pct=risk / eq if eq and risk and src in R_SOURCES else None,
            usd_per_oz=net / rate / (p.lots * contract),
            move_per_oz=(p.exit - p.entry) * (1 if p.side == "buy" else -1),
            near_round=abs(p.entry - round(p.entry / ROUND_STEP) * ROUND_STEP) <= ROUND_BAND,
            near_session_open=bool(open_flags[i]), near_news=None if news is None else bool(news[i])))

    wins, losses = nets[nets > 0], nets[nets < 0]
    r_all = np.array([t.r_multiple if t.risk_source in R_SOURCES and t.r_multiple is not None else np.nan
                      for t in trades])
    rs = r_all[np.isfinite(r_all)]
    sources = [t.risk_source for t in trades]
    if any(src not in R_SOURCES for src in sources):
        notes.append("R statistics use only trades whose 1R is a real stop on the loss side. Trades without a stop "
                     "carry a proxy 1R (average loss per lot) and trades whose only known stop sits at or beyond "
                     "the entry (`sl_moved`) carry none; both are kept out of the R statistics.")
    if "sl_final" in sources or "sl_moved" in sources:
        notes.append("The report's S/L column is the FINAL stop (after any trailing or break-even move); the Orders "
                     "section's initial stop was missing for some trades, so their 1R comes from the final stop.")
    closes = pd.DatetimeIndex([p.close_utc for p in pos]).tz_convert("UTC")
    daily = _daily(nets, closes, "UTC", targets)
    daily_london = _daily(nets, closes, "Europe/London", targets)

    win_streak, best_streak = streaks(nets)
    start_eq = None if math.isnan(start) else start + float(flow_amt[flow_ns <= pos[0].open_utc.value].sum())
    # drawdown from trading alone: deposits and withdrawals move the balance but are not losses
    curve = np.cumsum(nets)
    max_dd = float(np.max(np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:] - curve, initial=0.0))
    eq_open = np.array([t.equity_at_open if t.equity_at_open else np.nan for t in trades])
    dd_pct = None
    if np.isfinite(eq_open).all():
        index = np.cumprod(1.0 + nets / eq_open)              # flow-adjusted (time-weighted) equity index
        peak_idx = np.maximum.accumulate(np.concatenate([[1.0], index]))[1:]
        dd_pct = float(np.max(1.0 - index / peak_idx, initial=0.0))
    roe = nets / eq_open
    roe = roe[np.isfinite(roe)]
    levs = np.array([t.leverage for t in trades if t.leverage is not None])
    eqs = np.array([(t.lots, t.equity_at_open) for t in trades if t.equity_at_open is not None])
    corr = float(np.corrcoef(eqs[:, 0], eqs[:, 1])[0, 1]) if len(eqs) > 2 and eqs[:, 0].std() and eqs[:, 1].std() \
        else None
    ruin, ruin_note = _ruin(trades, start_eq, n_paths, seed)
    if ruin_note:
        notes.append(ruin_note)
    holding = np.array([t.holding_min for t in trades])
    per_oz = np.array([t.usd_per_oz for t in trades])
    risk_pct = np.array([t.risk_pct for t in trades if t.risk_pct is not None])
    hold_bucket = pd.cut(holding, [-1, 5, 15, 60, 240, 1440, np.inf],
                         labels=["<5m", "5-15m", "15-60m", "1-4h", "4-24h", ">1d"]).astype(str)
    news_groups = None
    if news is not None:
        news_groups = _group(np.where(news, f"within {NEWS_WINDOW_MIN} min of tier-1", "other"), nets,
                             [f"within {NEWS_WINDOW_MIN} min of tier-1", "other"], r_all, per_oz)
    by_session = _group(sessions, nets, ["asia", "london", "newyork"], r_all, per_oz)
    by_hour = _group([f"{t.hour:02d}" for t in trades], nets, None, r_all, per_oz)
    by_weekday = _group([t.weekday for t in trades], nets, WEEKDAYS, r_all, per_oz)
    by_side = _group([t.side for t in trades], nets, ["buy", "sell"], r_all, per_oz)
    by_holding = _group(hold_bucket, nets, ["<5m", "5-15m", "15-60m", "1-4h", "4-24h", ">1d"], r_all, per_oz)
    # K: every slice the report shows (the seeds are picked from among them) plus the two entry-context flags
    k = sum(len(g) for g in (by_session, by_hour, by_weekday, by_side, by_holding, news_groups or [])) + 2
    bonf = statistics.NormalDist().inv_cdf(1 - FAMILY_ALPHA / (2 * k))
    gp, gl = float(wins.sum()), float(-losses.sum())
    real_r = [t.risk for t in trades if t.risk_source in R_SOURCES and t.risk is not None]
    summary = Summary(
        currency=report.currency, source_format=report.source_format, n_trades=len(trades),
        period_start=min(p.open_utc for p in pos), period_end=max(p.close_utc for p in pos),
        usd_to_ccy=rate, usd_to_ccy_source=rate_src, total_net=float(nets.sum()),
        win_rate=float((nets > 0).mean()), avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0, expectancy=float(nets.mean()), expectancy_t=_t(nets),
        expectancy_r=float(rs.mean()) if len(rs) else None, n_r=len(rs),
        profit_factor=gp / gl if gl > 0 else None,
        risk_sources={k_: sources.count(k_) for k_ in ("sl_initial", "sl_final", "sl_moved", "proxy_avg_loss", "none")},
        median_risk=float(np.median(real_r)) if real_r else None,
        median_holding_min=float(np.median(holding)),
        holding_quantiles={f"p{q}": float(np.percentile(holding, q)) for q in HOLDING_QUANTILES},
        mean_usd_per_oz=float(per_oz.mean()), mean_move_per_oz=float(np.mean([t.move_per_oz for t in trades])),
        median_risk_pct=float(np.median(risk_pct)) if len(risk_pct) else None,
        max_risk_pct=float(risk_pct.max()) if len(risk_pct) else None,
        daily=daily, daily_london=daily_london,
        mean_return_on_equity=float(roe.mean()) if len(roe) else None, return_on_equity_t=_t(roe),
        by_session=by_session, by_hour=by_hour, by_weekday=by_weekday, by_side=by_side, by_holding=by_holding,
        news=news_groups, news_note=news_note,
        share_near_session_open=float(open_flags.mean()), session_open_base_rate=session_open_base_rate(open_idx),
        share_near_round_number=float(np.mean([t.near_round for t in trades])),
        slices_examined=k, bonferroni_t=float(bonf),
        start_equity=start_eq, start_equity_source=start_src,
        median_leverage=float(np.median(levs)) if len(levs) else None,
        max_leverage=float(levs.max()) if len(levs) else None, lots_equity_corr=corr,
        longest_losing_streak=best_streak, longest_winning_streak=win_streak, max_drawdown=max_dd, max_drawdown_pct=dd_pct, ruin=ruin, notes=notes)
    return Result(summary=summary, trades=trades, seeds=hypothesis_seeds(summary, trades))


# ------------------------------------------------------------------------------------------------ seeds

def _money(x: float, sign: str) -> str:
    return f"-{sign}{-x:.2f}" if x < 0 else f"{sign}{x:.2f}"


def _fmt_t(t: float | None) -> str:
    return "t n/a" if t is None else f"t {t:.2f}"


def slice_bar(n: int, k: int) -> float:
    """|t| a slice of `n` trades needs for family-wise FAMILY_ALPHA over `k` slices: Bonferroni (two-sided alpha / k)
    on Student's t with the slice's own n - 1 degrees of freedom. The normal quantile is too lenient for the small
    slices a discretionary record breaks into (12 trades need about 4.1 at K = 30, not 3.1)."""
    return float(student_t.ppf(1 - FAMILY_ALPHA / (2 * k), max(1, n - 1)))


def _slice(g: GroupStat, n: int, k: int) -> str:
    """A slice in R, percentages and counts only (never money): the seeds may be quoted in the public repo."""
    r = "no trades with a real 1R" if g.mean_r is None else f"mean {g.mean_r:+.2f} R over {g.n_r}"
    return f"{g.n}/{n} trades ({g.n / n:.0%}), win {g.win_rate:.0%}, {r}, {_verdict(g, k)}"


def _verdict(g: GroupStat, k: int) -> str:
    """The slice's t against the Bonferroni bar over K slices (`slice_bar`), or why it cannot be judged."""
    if g.t_stat is None:
        return "t n/a"
    if g.n < MIN_SLICE_N:
        return f"t {g.t_stat:.2f}, too few trades (< {MIN_SLICE_N}) to judge"
    need = slice_bar(g.n, k)
    return f"t {g.t_stat:.2f}, " + ("clears" if abs(g.t_stat) >= need else "below") + \
        f" the Bonferroni |t| {need:.2f} (Student's t, {g.n - 1} df)"


def _flag_slice(trades: list[Trade], flag: Sequence[bool], name: str, k: int) -> str:
    nets = np.array([t.net for t in trades])
    rs = np.array([t.r_multiple if t.risk_source in R_SOURCES and t.r_multiple is not None else np.nan
                   for t in trades])
    g = _group(np.where(np.asarray(flag, dtype=bool), name, "other"), nets, [name], rs)
    return _slice(g[0], len(trades), k) if g else "0 trades"


def hypothesis_seeds(s: Summary, trades: list[Trade]) -> list[HypothesisSeed]:
    """Recurring patterns written as hypotheses.md rows: in-sample descriptions of one record, never evidence.
    Each is phrased as a contrast to pre-register ("do entries in X differ from the owner's other entries"), quotes
    R, percentages and counts only, and says whether its t clears the Bonferroni bar over the K slices examined."""
    seeds: list[HypothesisSeed] = []
    n, n_slices = s.n_trades, s.slices_examined
    top = max(s.by_session, key=lambda g: (g.mean_r if g.mean_r is not None else -1e9, g.n))
    hour = max(s.by_hour, key=lambda g: (g.mean_r if g.mean_r is not None else -1e9, g.n))
    seeds.append(HypothesisSeed(
        id="OH-01", hypothesis=f"**Owner's session window**: test whether entries in the {top.key} session (around "
        f"{hour.key}:00 UTC) differ from the owner's other entries",
        rationale=f"{top.key}: {_slice(top, n, n_slices)}; hour {hour.key} UTC: {_slice(hour, n, n_slices)}",
        horizon="15m-1h", edge_vs_cost="unknown; a session filter on an existing 15m/1h family, judged net of "
        "the 0.2-0.4 R round trip", maps_to="H-04 / H-14"))
    med = s.median_holding_min
    best_hold = max(s.by_holding, key=lambda g: (g.mean_r if g.mean_r is not None else -1e9, g.n))
    seeds.append(HypothesisSeed(
        id="OH-02", hypothesis=f"**Owner's holding time**: test whether trades held {best_hold.key} differ from the "
        f"owner's other entries (median hold {med:.0f} min, {'a scalping' if med < 30 else 'an intraday'} style)",
        rationale=f"{best_hold.key}: {_slice(best_hold, n, n_slices)}",
        horizon="15m" if med < 60 else "1h",
        edge_vs_cost="short targets cost 0.3-0.4 R a round trip (hypotheses.md yardstick); needs a high hit rate",
        maps_to="H-14"))
    sides = {g.key: g for g in s.by_side}
    buy_share = (sides["buy"].n if "buy" in sides else 0) / n
    seeds.append(HypothesisSeed(
        id="OH-03", hypothesis=f"**Direction bias**: the owner is {buy_share:.0%} long; test whether the long entries "
        "differ from the short ones, and from a random-entry long over the same hours (side vs timing)",
        rationale="; ".join(f"{k}: {_slice(sides[k], n, n_slices)}" for k in ("buy", "sell") if k in sides),
        horizon="15m-1h", edge_vs_cost="long-only intraday pays no swap if flat by rollover", maps_to="H-04"))
    seeds.append(HypothesisSeed(
        id="OH-04", hypothesis=f"**Session-open entries**: test whether entries in the first {OPEN_WINDOW_MIN} min "
        f"after an Asia/London/NY open differ from the owner's other entries ({s.share_near_session_open:.0%} of "
        f"entries vs {s.session_open_base_rate:.0%} expected over the owner's active hours)",
        rationale=f"{_flag_slice(trades, [t.near_session_open for t in trades], 'open', n_slices)}; R-02 "
        "(session_open breakout) is retired, so only a different rule conditioned on the owner's filter is new",
        horizon="15m", edge_vs_cost="R-02 showed +0.042 R gross: needs a materially different mechanism",
        maps_to="R-02 / H-14"))
    seeds.append(HypothesisSeed(
        id="OH-05", hypothesis=f"**Round-number entries**: test whether entries within ${ROUND_BAND:g} of a "
        f"${ROUND_STEP:g} level differ from the owner's other entries ({s.share_near_round_number:.0%} of entries "
        "vs a 20% uniform base rate)",
        rationale=_flag_slice(trades, [t.near_round for t in trades], "round", n_slices),
        horizon="15m-1h", edge_vs_cost="Osler stop/target clustering; cascades move several ATR if real",
        maps_to="H-05 / H-14"))
    if s.news:
        g = s.news[0]
        seeds.append(HypothesisSeed(
            id="OH-06", hypothesis=f"**News-window trading**: test whether entries within {NEWS_WINDOW_MIN} min of "
            "tier-1 US releases differ from the owner's other entries",
            rationale=f"{_slice(g, n, n_slices)}. goldbot's RiskGate blacks these windows out, so any edge here is "
            "outside the current design",
            data="D, calendar", horizon="15m", edge_vs_cost="volatility burst several times the spread; slippage "
            "unmeasured", maps_to="H-10 / H-11"))
    return seeds


# ------------------------------------------------------------------------------------------------ output

def _row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _groups_md(title: str, groups: list[GroupStat], sign: str) -> list[str]:
    lines = [f"### {title}", "", _row(["", "trades", "net", "mean", "win", "mean R (n)", "t"]), _row(["---"] * 7)]
    for g in groups:
        lines.append(_row([g.key, g.n, f"{_money(g.net, sign)}", f"{_money(g.mean, sign)}", f"{g.win_rate:.0%}",
                           "n/a" if g.mean_r is None else f"{g.mean_r:+.2f} ({g.n_r})",
                           "n/a" if g.t_stat is None else f"{g.t_stat:.2f}"]))
    return lines + [""]


def render_markdown(r: Result) -> str:
    s = r.summary
    sign = CCY_SIGN.get(s.currency, s.currency + " ")
    d = s.daily

    def opt(x: float | None, fmt: str = "{:.2f}") -> str:
        return "n/a" if x is None else fmt.format(x)
    lines = [
        PRIVATE_TITLE, "",
        "> Private analysis of the owner's own MT5 history. Keep this file out of the repository. In-sample "
        "description of one discretionary record: nothing here is evidence of an edge.", "",
        f"Period {s.period_start:%Y-%m-%d} .. {s.period_end:%Y-%m-%d} UTC, {s.n_trades} XAUUSD trades, account "
        f"currency {s.currency} (USD rate {s.usd_to_ccy:.4f}, {s.usd_to_ccy_source}). Source: {s.source_format}.", "",
        "## Headline", "",
        _row(["measure", "value"]), _row(["---", "---"]),
        _row(["net P&L", f"{_money(s.total_net, sign)}"]),
        _row(["win rate", f"{s.win_rate:.1%} ({s.n_trades} trades)"]),
        _row(["average win / loss", f"{_money(s.avg_win, sign)} / {_money(s.avg_loss, sign)}"]),
        _row(["expectancy per trade", f"{_money(s.expectancy, sign)} ({_fmt_t(s.expectancy_t)})"]),
        _row(["expectancy in R", f"{opt(s.expectancy_r)} R over {s.n_r} trades with a real stop"]),
        _row(["return on equity per trade", "n/a" if s.mean_return_on_equity is None
              else f"{s.mean_return_on_equity:+.2%} ({_fmt_t(s.return_on_equity_t)})"]),
        _row(["profit factor", opt(s.profit_factor)]),
        _row(["risk source", ", ".join(f"{k} {v}" for k, v in s.risk_sources.items())]),
        _row(["median risk per trade (stop)", "n/a" if s.median_risk is None else f"{_money(s.median_risk, sign)}"]),
        _row(["median holding time", f"{s.median_holding_min:.0f} min"]),
        _row(["longest losing streak", f"{s.longest_losing_streak} trades"]),
        _row(["max drawdown (trading only; deposits and withdrawals excluded)", f"{_money(s.max_drawdown, sign)}"
              + ("" if s.max_drawdown_pct is None else f" ({s.max_drawdown_pct:.1%} of the flow-adjusted peak)")]),
        _row(["starting equity", "unknown" if s.start_equity is None
              else f"{_money(s.start_equity, sign)} ({s.start_equity_source})"]),
        _row(["leverage (notional / equity) median / max", f"{opt(s.median_leverage, '{:.1f}')}x / "
              f"{opt(s.max_leverage, '{:.1f}')}x"]),
        _row(["lots vs equity correlation", opt(s.lots_equity_corr)]), "",
        "## Daily P&L (days with at least one closed trade)", "",
        _row(["day", "days", "mean (t, 95% CI)", "median", "std", "best", "worst"]
             + [f"share >= {sign}{k}" for k in d.share_at_least]),
        _row(["---"] * (7 + len(d.share_at_least)))]
    for dd in (d, s.daily_london):
        lines.append(_row([dd.day, dd.n_days, f"{_money(dd.mean, sign)} ({_fmt_t(dd.t_stat)}, "
                           f"{_money(dd.ci95[0], sign)} .. {_money(dd.ci95[1], sign)})", _money(dd.median, sign),
                           _money(dd.std, sign), _money(dd.best, sign), f"{_money(dd.worst, sign)} ({dd.worst_day})"]
                          + [f"{v:.0%}" for v in dd.share_at_least.values()]))
    lines += ["", "## Breakdowns (entry time, UTC)", ""]
    lines += _groups_md("By session (local exchange clocks, DST per date)", s.by_session, sign)
    lines += _groups_md("By hour", s.by_hour, sign)
    lines += _groups_md("By weekday", s.by_weekday, sign)
    lines += _groups_md("By side", s.by_side, sign)
    lines += _groups_md("By holding time", s.by_holding, sign)
    if s.news:
        lines += _groups_md("Around tier-1 news", s.news, sign)
    if s.news_note:
        lines += [f"News: {s.news_note}.", ""]
    lines += [f"Entries in the first {OPEN_WINDOW_MIN} min after a session open (local clocks): "
              f"{s.share_near_session_open:.0%} (expected {s.session_open_base_rate:.0%} over the owner's active hours). "
              f"Entries within ${ROUND_BAND:g} of a ${ROUND_STEP:g} level: {s.share_near_round_number:.0%}.", ""]
    lines += ["## Risk of ruin of the owner's sizing", ""]
    if s.ruin:
        ru0 = s.ruin[0]
        lines += [f"{ru0.n_paths} circular-block-bootstrap paths (goldbot.research.ruin, block {ru0.block_length} "
                  f"trades) of {ru0.horizon_trades} trades (about a year at the observed pace), ruin = equity touching "
                  f"{ru0.ruin_level:.0%} of the start. Lots vs equity correlation {opt(s.lots_equity_corr)}: near 1 "
                  "means the compounding rows describe the owner, near 0 the fixed-stake rows. The zero-edge rows "
                  "remove the sample's mean: the risk each sizing carries if the edge is not real.", "",
                  _row(["sizing", "P(ruin)", "P(halved)", "median end, log10(x start)"]), _row(["---"] * 4)]
        for ru in s.ruin:
            lines.append(_row([ru.method, f"{ru.p_ruin:.1%}", f"{ru.p_halved:.1%}", f"{ru.median_end_log10:+.2f}"]))
        lines += ["", "The bootstrap assumes future trades look like these, independent of each other; it cannot see "
                  "regimes the record does not contain, so it understates ruin.", ""]
    else:
        lines += ["Not estimated (see notes).", ""]
    lines += ["## Hypothesis seeds", "",
              "**These are in-sample observations of the owner's own trades, not evidence.** They describe one "
              "discretionary record over one period, chosen after looking at it. Each seed needs the research "
              "director's pre-registration (rule fixed in advance, trial counted in the budget, data outside this "
              "period, holdout untouched) before it can mean anything. Format as docs/research/hypotheses.md.", "",
              f"K = {s.slices_examined} slices examined (every session, hour, weekday, side, holding and news slice "
              f"above, plus the session-open and round-number flags); the Bonferroni bar for 5% family-wise error is "
              f"Student's t at two-sided 5%/K on each slice's own n - 1 degrees of freedom (|t| >= "
              f"{s.bonferroni_t:.2f} for a very large slice, {slice_bar(30, s.slices_examined):.2f} for 30 trades, "
              f"higher still for fewer), and each rationale says whether its t clears it. A slice below the bar is "
              "indistinguishable from picking the best of K by chance.", "",
              "Rationales quote R, percentages and counts only. **Do not paste amounts into the repo**: money figures "
              "from this report stay in this private folder.", "",
              _row(["ID", "Hypothesis", "Status", "Rationale", "Data", "Horizon", "Expected gross edge vs cost",
                    "Trial cost"]), _row(["---"] * 8)]
    for sd in r.seeds:
        lines.append(_row([sd.id, sd.hypothesis + (f" (relates to {sd.maps_to})" if sd.maps_to else ""), sd.status,
                           sd.rationale, sd.data, sd.horizon, sd.edge_vs_cost, sd.trial_cost]))
    lines += ["", "## Notes", ""] + [f"- {x}" for x in s.notes or ["none"]] + [
        "- Daily P&L is shown by the UTC day of each close (goldbot's risk-day) and by the London day; hours and "
        "weekdays use the UTC entry time. Sessions are goldbot's windows on local exchange clocks, resolved for each "
        "entry's own date (Asia from Tokyo 08:00, London from 08:00 Europe/London, New York 08:30-17:00 "
        "America/New_York), so London and New York move an hour in UTC when their clocks change; session opens use "
        "the same local clocks.",
        "- Small samples: a group with a |t| under about 2 is noise."]
    return "\n".join(lines) + "\n"


def _share_groups(title: str, groups: list[GroupStat], n: int, k: int) -> list[str]:
    lines = [f"### {title}", "", _row(["", "trades", "share", "win", "mean R (n)", "mean USD/oz", "t vs bar"]),
             _row(["---"] * 7)]
    for g in groups:
        lines.append(_row([g.key, g.n, f"{g.n / n:.0%}", f"{g.win_rate:.0%}",
                           "n/a" if g.mean_r is None else f"{g.mean_r:+.2f} ({g.n_r})",
                           "n/a" if g.mean_usd_per_oz is None else f"{g.mean_usd_per_oz:+.2f}", _verdict(g, k)]))
    return lines + [""]


def render_share(r: Result) -> str:
    """The summary the owner may share (owner-method study: "share only a summary"): aggregates in R, %, counts,
    USD per ounce (price terms, the same on any account size) and K. No amount in the account's currency, no trade
    row, no ticket and nothing from the report header, so it names neither the account nor its size. Still keep it
    out of the repo; the research director quotes from it."""
    s = r.summary
    n, k = s.n_trades, s.slices_examined

    def pct(x: float | None, fmt: str = "{:.1%}") -> str:
        return "n/a (equity unknown)" if x is None else fmt.format(x)
    payoff = abs(s.avg_win / s.avg_loss) if s.avg_loss else None
    q = s.holding_quantiles
    lines = [
        "# Owner trade history: shareable summary", "",
        "Aggregates only: R, percentages, counts, USD per ounce and K. No account number, name, broker, ticket or "
        "amount in the account's currency. In-sample description of one discretionary record, not evidence of an "
        "edge. Share it with the research director; do not commit it.", "",
        f"{n} XAUUSD trades, {s.period_start:%Y-%m} .. {s.period_end:%Y-%m}, {s.daily.n_days} trading days "
        f"({n / s.daily.n_days:.1f} trades a day). Source: {s.source_format}. USD conversion: "
        f"{s.usd_to_ccy_source}.", "",
        _row(["measure", "value"]), _row(["---", "---"]),
        _row(["win rate", f"{s.win_rate:.1%}"]),
        _row(["mean R per trade", "n/a (no trade with a stop on the loss side)" if s.expectancy_r is None
              else f"{s.expectancy_r:+.2f} R over {s.n_r} trades with a real stop ({s.n_r / n:.0%} of trades)"]),
        _row(["mean net capture (after costs)", f"{s.mean_usd_per_oz:+.2f} USD/oz"]),
        _row(["mean price move in the trade's direction", f"{s.mean_move_per_oz:+.2f} USD/oz"]),
        _row(["payoff ratio (average win / average loss)", "n/a" if payoff is None else f"{payoff:.2f}"]),
        _row(["profit factor", "n/a" if s.profit_factor is None else f"{s.profit_factor:.2f}"]),
        _row(["holding time p10 / p25 / p50 / p75 / p90",
              " / ".join(f"{q[f'p{x}']:.0f}" for x in HOLDING_QUANTILES) + " min"]),
        _row(["longest winning streak", f"{s.longest_winning_streak} trades"]),
        _row(["longest losing streak", f"{s.longest_losing_streak} trades"]),
        _row(["max drawdown (flow-adjusted, % of peak)", pct(s.max_drawdown_pct)]),
        _row(["risk per trade (1R / equity at entry) median / max",
              f"{pct(s.median_risk_pct)} / {pct(s.max_risk_pct)}"]),
        _row(["leverage (notional / equity) median / max",
              f"{pct(s.median_leverage, '{:.1f}x')} / {pct(s.max_leverage, '{:.1f}x')}"]),
        _row(["return on equity per trade (mean)", pct(s.mean_return_on_equity, "{:+.2%}")]),
        _row(["P(ruin) in a year at the owner's sizing (compounding / fixed / zero edge compounding / zero edge "
              "fixed)", "n/a" if not s.ruin else " / ".join(f"{ru.p_ruin:.1%}" for ru in s.ruin)]), "",
        "## Histograms (entry time, UTC; sessions on local exchange clocks)", ""]
    lines += _share_groups("By session (asia = Tokyo, plus the thin hours after the New York close)", s.by_session,
                           n, k)
    lines += _share_groups("By hour (UTC)", s.by_hour, n, k)
    lines += _share_groups("By weekday", s.by_weekday, n, k)
    lines += _share_groups("By side (long = buy, short = sell)", s.by_side, n, k)
    lines += _share_groups("By holding time", s.by_holding, n, k)
    if s.news:
        lines += _share_groups("Around tier-1 news", s.news, n, k)
    lines += [f"Entries in the first {OPEN_WINDOW_MIN} min after a session open: {s.share_near_session_open:.0%} "
              f"(expected {s.session_open_base_rate:.0%}); within ${ROUND_BAND:g} of a ${ROUND_STEP:g} level: "
              f"{s.share_near_round_number:.0%}.", "",
              "## Overfitting control", "",
              f"K = {k} slices examined (every row of the histograms above plus the session-open and round-number "
              f"flags). A slice clears only at Bonferroni 5%/K on Student's t with its own n - 1 degrees of freedom "
              f"and n >= {MIN_SLICE_N} (|t| >= {s.bonferroni_t:.2f} for a very large slice, "
              f"{slice_bar(30, k):.2f} for 30 trades). Quote K with every pattern taken from this summary.", ""]
    return "\n".join(lines) + "\n"


def _rounded(x: object) -> object:
    """Floats to 6 decimals (readable, no float-representation noise); a non-finite float becomes null."""
    if isinstance(x, float):
        return round(x, 6) if math.isfinite(x) else None
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_rounded(v) for v in x]
    return x


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _fold(p: Path) -> str:
    return os.path.normcase(str(p)).casefold()


def git_tree_reason(out: Path) -> str | None:
    """Why `out` lies in a git work tree (and so may be committed to a public repo), or None. Checked: a `.git`
    directory (a clone) or file (a linked worktree or submodule) in `out` or any parent; this package's own checkout
    under any case spelling of its path (macOS file systems ignore case); and `git rev-parse --show-toplevel` from the
    nearest existing ancestor (catches GIT_DIR/GIT_WORK_TREE set in the environment)."""
    out = out.expanduser().resolve()
    for p in (out, *out.parents):
        if (p / ".git").exists():
            return f"{p} is a git work tree"
    repo = _fold(_repo_root())
    here = _fold(out)
    if here == repo or here.startswith(repo.rstrip(os.sep) + os.sep):
        return "it is inside this repository (case-insensitive match)"
    anc = next((p for p in (out, *out.parents) if p.is_dir()), None)
    if anc is not None:
        try:
            done = subprocess.run(["git", "-C", str(anc), "rev-parse", "--show-toplevel"], capture_output=True,
                                  timeout=10, check=False)
            if done.returncode == 0:
                return "git reports it is inside a work tree"
        except (OSError, subprocess.SubprocessError):
            pass                                          # no git binary: the two checks above still apply
    return None


def refuse_in_git_tree(report: Path) -> None:
    """Raise when the export sits in a git work tree. The message names neither the file nor the folder: the path
    may carry the owner's name or account number."""
    if git_tree_reason(report):
        raise ValueError(f"refusing to read a report inside a git work tree (it could be committed to a public repo); "
                         f"move it to a private folder such as {DEFAULT_DIR_SHOWN}")


def write_outputs(r: Result, out_dir: str | Path) -> list[Path]:
    """Markdown report, JSON summary and per-trade CSV, owner-readable only. Refuses any folder in a git work tree
    (this repo is public; a sibling worktree or another clone can be pushed too): the outputs describe the account."""
    out = Path(out_dir).expanduser().resolve()
    if git_tree_reason(out):
        raise ValueError("refusing to write the owner's history into a git work tree; "
                         f"use a private folder such as {DEFAULT_DIR_SHOWN}")
    stamp = pd.Timestamp.now("UTC").strftime("%Y%m%d-%H%M%S")
    md, js, tr, share = (out / f"{OUTPUT_PREFIX}_{stamp}.{ext}" for ext in ("md", "json", "trades.csv", "share.md"))
    write_private(md, render_markdown(r))
    write_private(share, render_share(r))
    write_private(js, json.dumps(_rounded(r.model_dump(mode="json", exclude={"trades"})), indent=2,
                                 allow_nan=False))
    write_private(tr, pd.DataFrame([t.model_dump(mode="json") for t in r.trades]).to_csv(index=False,
                                                                                        float_format="%.6g"))
    return [md, js, tr, share]


def _newest_report(folder: Path) -> Path:
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(OUTPUT_PREFIX)
             and p.suffix.lower() in (".html", ".htm", ".xlsx", ".csv")]
    if not files:
        raise SystemExit("no MT5 report (.html/.htm/.xlsx/.csv) in the given folder: see docs/OWNER_GUIDE.md")
    return max(files, key=lambda p: p.stat().st_mtime)


def _load_events(no_news: bool) -> pd.DataFrame | None:
    if no_news:
        return None
    try:
        from goldbot.config import load_settings
        from goldbot.data.store import Store
        ev = Store(load_settings().data_root).read("calendar_events")
        return ev if not ev.empty else None
    except Exception:                                      # no store on this machine: news proximity is skipped
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run owner-history",
                                 description="Analyse the owner's MT5 trade history privately (research seeds).")
    ap.add_argument("path", nargs="?", default=str(DEFAULT_DIR),
                    help=f"report file, or a folder (newest report in it); default {DEFAULT_DIR_SHOWN}")
    ap.add_argument("--server-tz", default="Europe/Athens", help="broker server time zone (IC Markets: Europe/Athens)")
    ap.add_argument("--contract", type=float, default=CONTRACT_OZ, help="ounces per lot")
    ap.add_argument("--targets", default="30,50", help="daily targets in account currency, comma separated")
    ap.add_argument("--start-balance", type=float, default=None, help="starting equity if the report has no deposit")
    ap.add_argument("--out", default=None, help="output folder (default: the report's folder; never the repo)")
    ap.add_argument("--no-news", action="store_true", help="skip the economic-calendar lookup")
    a = ap.parse_args(argv)
    src = Path(a.path).expanduser()
    try:
        refuse_in_git_tree(src)                            # before a folder in the repo is even listed
    except ValueError as e:
        raise SystemExit(str(e)) from None
    if src.is_dir():
        src = _newest_report(src)
    if not src.exists():
        # only the suffix: the path may name the owner or the account
        raise SystemExit(f"report not found (a {src.suffix or 'file without extension'}): export it from MT5 first "
                         "(docs/OWNER_GUIDE.md)")
    targets = tuple(float(x) for x in a.targets.split(",") if x.strip())
    try:
        report = load_report(src, a.server_tz)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    result = analyse(report, contract=a.contract, targets=targets,
                     start_balance=a.start_balance, events=_load_events(a.no_news))
    try:
        paths = write_outputs(result, a.out or src.parent)
    except ValueError as e:
        raise SystemExit(str(e)) from None
    print(render_share(result))                            # aggregates only: a session transcript may keep stdout
    print(f"wrote {len(paths)} files ({', '.join(p.name.split('.', 1)[1] for p in paths)}) next to the report; "
          "the .md holds the amounts and stays private, the .share.md is the summary to share")
    return 0


__all__ = ["Report", "Position", "Trade", "Summary", "Result", "HypothesisSeed", "load_report", "xauusd_positions",
           "analyse", "bootstrap_ruin", "render_markdown", "render_share", "streaks", "write_outputs",
           "git_tree_reason", "refuse_in_git_tree", "parse_number", "parse_time", "session_open_flags",
           "session_open_base_rate", "main"]
