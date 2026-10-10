"""The owner's own MT5 trade history, analysed privately to seed research hypotheses.

The owner trades XAUUSD by hand and reports £30-50 a day on a £50 account. The research director wants that record
turned into testable hypotheses (ADR 0004 north-star). This module reads the owner's MT5 export, describes how the
money was made (risk, R-multiples, daily P&L, sessions, holding time, leverage, ruin risk) and lists the recurring
patterns as *hypothesis seeds* in the `docs/research/hypotheses.md` format. Seeds are in-sample observations of one
discretionary record, never evidence: each one still needs a pre-registered trial (strategy-researcher) and the gates.

Privacy (public repo, docs/AGENT_STANDARDS.md S4): the report holds the owner's account number, name and broker.
The file is read from outside the repo (default `~/Personal project/trade-history/`), the outputs go to that same
private folder (writing inside the repository is refused) with mode 0600, and the header's account number, name,
company and server are never copied into a record or an output; only the account currency is kept. Position, order
and deal tickets are replaced by a sequence number. Tests use synthetic fixtures only.

Input formats (MT5 desktop: History tab -> right-click -> Report -> HTML or Open XML):
* HTML "Trade History Report" (usually UTF-16 with a BOM): a header block of `Label:` / value rows (Name, Account
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
import re
import zipfile
from collections.abc import Iterable, Sequence
from html.parser import HTMLParser
from pathlib import Path
from typing import Literal
from xml.etree import ElementTree

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp, write_private
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.timeutil import epoch_ns, server_to_utc

DEFAULT_DIR = Path("~/Personal project/trade-history").expanduser()
CONTRACT_OZ = 100.0                 # XAUUSD: 100 troy ounces per lot (IC Markets / Vantage)
ROUND_STEP, ROUND_BAND = 10.0, 1.0  # "near a round number": within $1 of a $10 level (uniform base rate 20%)
OPEN_WINDOW_MIN = 30                # "near a session open": within 30 min after an Asia/London/NY open
NEWS_WINDOW_MIN = 30
OUTPUT_PREFIX = "owner_history"
SECTIONS = ("positions", "orders", "deals", "open positions", "working orders", "results", "summary")
CCY_SIGN = {"GBP": "£", "USD": "$", "EUR": "€"}
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
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
    sl: float | None = None
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
    risk_source: Literal["sl", "proxy_avg_loss", "none"]
    r_multiple: float | None
    session: str
    hour: int
    weekday: str
    equity_at_open: float | None
    leverage: float | None          # notional / equity at entry
    near_round: bool
    near_session_open: bool
    near_news: bool | None


class GroupStat(FrozenRecord):
    key: str
    n: int
    net: float
    mean: float
    win_rate: float
    t_stat: float | None


class DailyStats(FrozenRecord):
    n_days: int
    mean: float
    median: float
    std: float
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
    p_ruin: float
    p_halved: float
    median_end_multiple: float


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
    daily: DailyStats
    by_session: list[GroupStat]
    by_hour: list[GroupStat]
    by_weekday: list[GroupStat]
    by_side: list[GroupStat]
    by_holding: list[GroupStat]
    news: list[GroupStat] | None
    news_note: str
    share_near_session_open: float
    share_near_round_number: float
    start_equity: float | None
    start_equity_source: str
    median_leverage: float | None
    max_leverage: float | None
    lots_equity_corr: float | None
    longest_losing_streak: int
    max_drawdown: float
    max_drawdown_pct: float | None
    ruin: list[RuinEstimate]        # [compounding, fixed stakes]; empty when equity is unknown
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


def _positions_from_table(cols: dict[str, int], body: list[list[str]], server_tz: str) -> list[Position]:
    raw = []
    for r in body:
        ot, ct = parse_time(_cell(r, cols, "open_time")), parse_time(_cell(r, cols, "close_time"))
        side = _side(_cell(r, cols, "type"))
        lots = parse_number(_cell(r, cols, "volume"))
        entry, exit_ = parse_number(_cell(r, cols, "open_price")), parse_number(_cell(r, cols, "close_price"))
        profit = parse_number(_cell(r, cols, "profit"))
        if None in (ot, ct, side, lots, entry, exit_, profit) or not lots:
            continue
        sl, tp = parse_number(_cell(r, cols, "sl")), parse_number(_cell(r, cols, "tp"))
        raw.append(dict(symbol=_cell(r, cols, "symbol").strip(), side=side, lots=lots, ot=ot, ct=ct, entry=entry,
                        exit=exit_, sl=sl or None, tp=tp or None,
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
                            entry=x["entry"], exit=x["exit"], sl=x["sl"], tp=x["tp"], commission=x["commission"],
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
    balance are kept; the header's account number, name, company and server are read past and discarded."""
    p = Path(path)
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
    if sections.get("positions"):
        cols, body = _table(sections["positions"], "positions")
        positions = _positions_from_table(cols, body, server_tz)
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


def _group(keys: Iterable[str], nets: np.ndarray, order: Sequence[str] | None = None) -> list[GroupStat]:
    df = pd.DataFrame({"k": list(keys), "net": nets})
    out = []
    names = [k for k in (order or sorted(df["k"].unique())) if k in set(df["k"])]
    for k in names:
        x = df.loc[df["k"] == k, "net"].to_numpy(dtype=float)
        out.append(GroupStat(key=str(k), n=len(x), net=float(x.sum()), mean=float(x.mean()),
                             win_rate=float((x > 0).mean()), t_stat=_t(x)))
    return out


def implied_usd_rate(positions: Sequence[Position], currency: str, contract: float = CONTRACT_OZ
                     ) -> tuple[float, str]:
    """USD -> account currency, read from the trades themselves: profit / (price move x lots x contract). The median
    over trades that moved at least 5 cents; 1.0 for a USD account or when no trade moved."""
    if currency == "USD":
        return 1.0, "USD account"
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


def bootstrap_ruin(returns: np.ndarray, horizon: int, n_paths: int = 2000, seed: int = 0,
                   ruin_level: float = 0.1, compounding: bool = True) -> RuinEstimate:
    """Resample per-trade returns i.i.d. for `horizon` trades (goldbot has no `research.ruin` module, so a plain
    bootstrap). compounding: returns are on equity at entry, i.e. lots scale with the account; otherwise they are on
    the starting equity, i.e. the same money at stake whatever the balance.
    p_ruin: paths whose equity touches `ruin_level` x start; p_halved: paths that lose half at any point."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0 or horizon <= 0:
        raise ValueError("no returns to resample")
    rng = np.random.default_rng(seed)
    draws = rng.choice(r, size=(n_paths, horizon), replace=True)
    if compounding:
        with np.errstate(divide="ignore"):
            path = np.cumsum(np.log1p(np.clip(draws, -1.0, None)), axis=1)
        low, end = path.min(axis=1), np.exp(path[:, -1])
        ruined, halved = low <= math.log(ruin_level), low <= math.log(0.5)
    else:
        path = 1.0 + np.cumsum(draws, axis=1)
        low = path.min(axis=1)
        end = np.where(low <= 1e-9, 0.0, path[:, -1])     # an emptied account stops trading
        ruined, halved = low <= ruin_level, low <= 0.5
    how = "returns on equity at entry, compounding" if compounding else "fixed stakes in account currency"
    return RuinEstimate(method=f"bootstrap of per-trade P&L ({how}; i.i.d.)", horizon_trades=horizon,
                        n_paths=n_paths, ruin_level=ruin_level, p_ruin=float(ruined.mean()),
                        p_halved=float(halved.mean()), median_end_multiple=float(np.median(np.maximum(end, 0.0))))


def _ruin(trades: list[Trade], start: float | None, n_paths: int, seed: int) -> tuple[list[RuinEstimate], str]:
    """Both readings of the owner's sizing: lots scaled with equity (compounding) and fixed stakes."""
    rets = np.array([t.net / t.equity_at_open for t in trades if t.equity_at_open and t.equity_at_open > 0])
    if len(rets) < 5 or not start:
        return [], "risk of ruin not estimated: equity at entry unknown or fewer than 5 trades"
    days = max(1, len({t.close_utc.date() for t in trades}))
    horizon = int(min(2500, max(len(rets), round(len(rets) / days * 250))))
    fixed = np.array([t.net for t in trades]) / start
    return [bootstrap_ruin(rets, horizon=horizon, n_paths=n_paths, seed=seed),
            bootstrap_ruin(fixed, horizon=horizon, n_paths=n_paths, seed=seed, compounding=False)], ""


def _session_open_flags(idx: pd.DatetimeIndex) -> np.ndarray:
    mins = idx.hour * 60 + idx.minute
    flags = np.zeros(len(idx), dtype=bool)
    for a, _ in DEFAULT_SESSIONS.sessions_utc.values():
        start = a.hour * 60 + a.minute
        flags |= np.asarray(((mins - start) % 1440) < OPEN_WINDOW_MIN)
    return flags


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


def analyse(report: Report, *, contract: float = CONTRACT_OZ, targets: Sequence[float] = (30.0, 50.0),
            start_balance: float | None = None, events: pd.DataFrame | None = None, n_paths: int = 2000,
            seed: int = 0) -> Result:
    """Every measure of the brief over the XAUUSD trades of `report`, plus hypothesis seeds."""
    pos = xauusd_positions(report)
    if not pos:
        raise ValueError("no XAUUSD trades found: is this the MT5 History report (Positions or Deals section)?")
    notes: list[str] = []
    rate, rate_src = implied_usd_rate(pos, report.currency, contract)
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
    sessions = DEFAULT_SESSIONS.session_label(open_idx)
    open_flags = _session_open_flags(open_idx)
    news, news_note = _news_flags(open_idx, events)
    trades: list[Trade] = []
    for i, p in enumerate(pos):
        net = float(nets[i])
        if p.sl is not None:
            risk: float | None = abs(p.entry - p.sl) * p.lots * contract * rate
            src: Literal["sl", "proxy_avg_loss", "none"] = "sl"
        elif loss_per_lot:
            risk, src = loss_per_lot * p.lots, "proxy_avg_loss"
        else:
            risk, src = None, "none"
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
            near_round=abs(p.entry - round(p.entry / ROUND_STEP) * ROUND_STEP) <= ROUND_BAND,
            near_session_open=bool(open_flags[i]), near_news=None if news is None else bool(news[i])))

    wins, losses = nets[nets > 0], nets[nets < 0]
    rs = np.array([t.r_multiple for t in trades if t.r_multiple is not None and t.risk_source == "sl"])
    if (np.array([t.risk_source for t in trades]) != "sl").any():
        notes.append("Expectancy in R uses only trades with a stop loss; trades without one carry a proxy 1R "
                     "(average loss per lot) shown per trade but kept out of the R statistics.")
    days = pd.Series(nets, index=pd.DatetimeIndex([p.close_utc for p in pos]).tz_convert("UTC").normalize()
                     ).groupby(level=0).sum()
    day_vals = days.to_numpy(dtype=float)
    day_keys = pd.DatetimeIndex(days.index).strftime("%Y-%m-%d").tolist()
    worst_i = int(np.argmin(day_vals))
    daily = DailyStats(
        n_days=len(days), mean=float(day_vals.mean()), median=float(np.median(day_vals)),
        std=float(day_vals.std(ddof=1)) if len(days) > 1 else 0.0, best=float(day_vals.max()),
        worst=float(day_vals[worst_i]), worst_day=day_keys[worst_i],
        share_at_least={f"{t:g}": float((day_vals >= t).mean()) for t in targets},
        days={k: float(v) for k, v in zip(day_keys, day_vals)})

    streak = best_streak = 0
    for n in nets:
        streak = streak + 1 if n < 0 else 0
        best_streak = max(best_streak, streak)
    start_eq = None if math.isnan(start) else start + float(flow_amt[flow_ns <= pos[0].open_utc.value].sum())
    if math.isnan(start):
        curve = np.cumsum(nets)
        peak = np.maximum.accumulate(np.concatenate([[0.0], curve]))[1:]
        dd_pct = None
    else:
        curve = np.array([start + float(flow_amt[flow_ns <= c].sum()) for c in close_ns]) + np.cumsum(nets)
        peak = np.maximum.accumulate(np.concatenate([[start_eq], curve]))[1:]
        dd_pct = float(np.max((peak - curve) / np.where(peak > 0, peak, np.nan), initial=0.0))
    max_dd = float(np.max(peak - curve, initial=0.0))
    levs = np.array([t.leverage for t in trades if t.leverage is not None])
    eqs = np.array([(t.lots, t.equity_at_open) for t in trades if t.equity_at_open is not None])
    corr = float(np.corrcoef(eqs[:, 0], eqs[:, 1])[0, 1]) if len(eqs) > 2 and eqs[:, 0].std() and eqs[:, 1].std() \
        else None
    ruin, ruin_note = _ruin(trades, start_eq, n_paths, seed)
    if ruin_note:
        notes.append(ruin_note)
    holding = np.array([t.holding_min for t in trades])
    hold_bucket = pd.cut(holding, [-1, 5, 15, 60, 240, 1440, np.inf],
                         labels=["<5m", "5-15m", "15-60m", "1-4h", "4-24h", ">1d"]).astype(str)
    news_groups = None
    if news is not None:
        news_groups = _group(np.where(news, f"within {NEWS_WINDOW_MIN} min of tier-1", "other"), nets,
                             [f"within {NEWS_WINDOW_MIN} min of tier-1", "other"])
    gp, gl = float(wins.sum()), float(-losses.sum())
    summary = Summary(
        currency=report.currency, source_format=report.source_format, n_trades=len(trades),
        period_start=min(p.open_utc for p in pos), period_end=max(p.close_utc for p in pos),
        usd_to_ccy=rate, usd_to_ccy_source=rate_src, total_net=float(nets.sum()),
        win_rate=float((nets > 0).mean()), avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0, expectancy=float(nets.mean()), expectancy_t=_t(nets),
        expectancy_r=float(rs.mean()) if len(rs) else None, n_r=len(rs),
        profit_factor=gp / gl if gl > 0 else None,
        risk_sources={k: sum(t.risk_source == k for t in trades) for k in ("sl", "proxy_avg_loss", "none")},
        median_risk=float(np.median([t.risk for t in trades if t.risk_source == "sl" and t.risk is not None]))
        if any(t.risk_source == "sl" for t in trades) else None,
        median_holding_min=float(np.median(holding)), daily=daily,
        by_session=_group(sessions, nets, ["asia", "london", "newyork"]),
        by_hour=_group([f"{t.hour:02d}" for t in trades], nets),
        by_weekday=_group([t.weekday for t in trades], nets, WEEKDAYS),
        by_side=_group([t.side for t in trades], nets, ["buy", "sell"]),
        by_holding=_group(hold_bucket, nets, ["<5m", "5-15m", "15-60m", "1-4h", "4-24h", ">1d"]),
        news=news_groups, news_note=news_note,
        share_near_session_open=float(open_flags.mean()),
        share_near_round_number=float(np.mean([t.near_round for t in trades])),
        start_equity=start_eq,
        start_equity_source=start_src,
        median_leverage=float(np.median(levs)) if len(levs) else None,
        max_leverage=float(levs.max()) if len(levs) else None, lots_equity_corr=corr,
        longest_losing_streak=best_streak, max_drawdown=max_dd, max_drawdown_pct=dd_pct, ruin=ruin, notes=notes)
    return Result(summary=summary, trades=trades, seeds=hypothesis_seeds(summary, trades))


# ------------------------------------------------------------------------------------------------ seeds

def _money(x: float, sign: str) -> str:
    return f"-{sign}{-x:.2f}" if x < 0 else f"{sign}{x:.2f}"


def _fmt_t(t: float | None) -> str:
    return "t n/a" if t is None else f"t {t:.2f}"


def hypothesis_seeds(s: Summary, trades: list[Trade]) -> list[HypothesisSeed]:
    """Recurring patterns written as hypotheses.md rows. Descriptions of one record, never evidence."""
    sign = CCY_SIGN.get(s.currency, s.currency + " ")
    seeds: list[HypothesisSeed] = []
    n = s.n_trades
    top = max(s.by_session, key=lambda g: g.net)
    busiest = max(s.by_session, key=lambda g: g.n)
    hour = max(s.by_hour, key=lambda g: g.net)
    seeds.append(HypothesisSeed(
        id="OH-01", hypothesis=f"**Owner's session window**: entries in the {top.key} session (owner's best) "
        f"and around {hour.key}:00 UTC carry the owner's edge",
        rationale=f"{top.key}: {top.n}/{n} trades, net {_money(top.net, sign)}, win {top.win_rate:.0%}, "
        f"{_fmt_t(top.t_stat)}; busiest session {busiest.key} ({busiest.n}); best hour {hour.key} UTC "
        f"({hour.n} trades, net {_money(hour.net, sign)})",
        horizon="15m-1h", edge_vs_cost="unknown; a session filter on an existing 15m/1h family, judged net of "
        "the 0.2-0.4 R round trip", maps_to="H-04 / H-14"))
    med = s.median_holding_min
    best_hold = max(s.by_holding, key=lambda g: g.mean)
    seeds.append(HypothesisSeed(
        id="OH-02", hypothesis=f"**Owner's holding time**: a short hold (median {med:.0f} min) and "
        f"{'a scalping' if med < 30 else 'an intraday'} target",
        rationale=f"median hold {med:.0f} min over {n} trades; best bucket {best_hold.key} "
        f"(mean {_money(best_hold.mean, sign)}, {best_hold.n} trades, {_fmt_t(best_hold.t_stat)})",
        horizon="15m" if med < 60 else "1h",
        edge_vs_cost="short targets cost 0.3-0.4 R a round trip (hypotheses.md yardstick); needs a high hit rate",
        maps_to="H-14"))
    sides = {g.key: g for g in s.by_side}
    buy, sell = sides.get("buy"), sides.get("sell")
    buy_share = (buy.n if buy else 0) / n
    seeds.append(HypothesisSeed(
        id="OH-03", hypothesis=f"**Direction bias**: the owner is {buy_share:.0%} long; test whether the edge is "
        "the side (long drift) or the entry timing",
        rationale="; ".join(f"{g.key} {g.n} trades net {_money(g.net, sign)} win {g.win_rate:.0%} {_fmt_t(g.t_stat)}"
                            for g in (buy, sell) if g),
        horizon="15m-1h", edge_vs_cost="long-only intraday pays no swap if flat by rollover; compare with a "
        "random-entry long benchmark over the same hours", maps_to="H-04"))
    near_open = [t.net for t in trades if t.near_session_open]
    seeds.append(HypothesisSeed(
        id="OH-04", hypothesis=f"**Session-open entries**: {s.share_near_session_open:.0%} of entries fall in the "
        f"first {OPEN_WINDOW_MIN} min after an Asia/London/NY open (uniform-day base rate 6%)",
        rationale=f"{len(near_open)} trades, net {_money(sum(near_open), sign)}; R-02 (session_open breakout) is "
        "retired, so only a different rule (fade or continuation conditioned on the owner's filter) is new",
        horizon="15m", edge_vs_cost="R-02 showed +0.042 R gross: needs a materially different mechanism",
        maps_to="R-02 / H-14"))
    near_round = [t.net for t in trades if t.near_round]
    seeds.append(HypothesisSeed(
        id="OH-05", hypothesis=f"**Round-number entries**: {s.share_near_round_number:.0%} of entries are within "
        f"${ROUND_BAND:g} of a ${ROUND_STEP:g} level (uniform base rate 20%)",
        rationale=f"{len(near_round)} trades, net {_money(sum(near_round), sign)} vs "
        f"{_money(s.total_net - sum(near_round), sign)} elsewhere",
        horizon="15m-1h", edge_vs_cost="Osler stop/target clustering; cascades move several ATR if real",
        maps_to="H-05 / H-14"))
    if s.news:
        g = s.news[0]
        seeds.append(HypothesisSeed(
            id="OH-06", hypothesis="**News-window trading**: entries within 30 min of tier-1 US releases",
            rationale=f"{g.n} trades, net {_money(g.net, sign)}, win {g.win_rate:.0%}, {_fmt_t(g.t_stat)}. goldbot's "
            "RiskGate blacks these windows out, so any edge here is outside the current design",
            data="D, calendar", horizon="15m", edge_vs_cost="volatility burst several times the spread; slippage "
            "unmeasured", maps_to="H-10 / H-11"))
    return seeds


# ------------------------------------------------------------------------------------------------ output

def _row(cells: Iterable[object]) -> str:
    return "| " + " | ".join(str(c) for c in cells) + " |"


def _groups_md(title: str, groups: list[GroupStat], sign: str) -> list[str]:
    lines = [f"### {title}", "", _row(["", "trades", "net", "mean", "win", "t"]), _row(["---"] * 6)]
    for g in groups:
        lines.append(_row([g.key, g.n, f"{_money(g.net, sign)}", f"{_money(g.mean, sign)}", f"{g.win_rate:.0%}",
                           "n/a" if g.t_stat is None else f"{g.t_stat:.2f}"]))
    return lines + [""]


def render_markdown(r: Result) -> str:
    s = r.summary
    sign = CCY_SIGN.get(s.currency, s.currency + " ")
    d = s.daily

    def opt(x: float | None, fmt: str = "{:.2f}") -> str:
        return "n/a" if x is None else fmt.format(x)
    lines = [
        "# Owner trade history (private)", "",
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
        _row(["expectancy in R", f"{opt(s.expectancy_r)} R over {s.n_r} trades with a stop"]),
        _row(["profit factor", opt(s.profit_factor)]),
        _row(["risk source", ", ".join(f"{k} {v}" for k, v in s.risk_sources.items())]),
        _row(["median risk per trade (stop)", "n/a" if s.median_risk is None else f"{_money(s.median_risk, sign)}"]),
        _row(["median holding time", f"{s.median_holding_min:.0f} min"]),
        _row(["longest losing streak", f"{s.longest_losing_streak} trades"]),
        _row(["max drawdown", f"{_money(s.max_drawdown, sign)}" + ("" if s.max_drawdown_pct is None
                                                                else f" ({s.max_drawdown_pct:.1%} of peak)")]),
        _row(["starting equity", "unknown" if s.start_equity is None
              else f"{_money(s.start_equity, sign)} ({s.start_equity_source})"]),
        _row(["leverage (notional / equity) median / max", f"{opt(s.median_leverage, '{:.1f}')}x / "
              f"{opt(s.max_leverage, '{:.1f}')}x"]),
        _row(["lots vs equity correlation", opt(s.lots_equity_corr)]), "",
        "## Daily P&L (UTC days with at least one closed trade)", "",
        _row(["days", "mean", "median", "std", "best", "worst"] + [f"share >= {sign}{k}" for k in d.share_at_least]),
        _row(["---"] * (6 + len(d.share_at_least))),
        _row([d.n_days, f"{_money(d.mean, sign)}", f"{_money(d.median, sign)}", f"{_money(d.std, sign)}", f"{_money(d.best, sign)}",
              f"{_money(d.worst, sign)} ({d.worst_day})"] + [f"{v:.0%}" for v in d.share_at_least.values()]), "",
        "## Breakdowns (entry time, UTC)", ""]
    lines += _groups_md("By session (goldbot DEFAULT_SESSIONS)", s.by_session, sign)
    lines += _groups_md("By hour", s.by_hour, sign)
    lines += _groups_md("By weekday", s.by_weekday, sign)
    lines += _groups_md("By side", s.by_side, sign)
    lines += _groups_md("By holding time", s.by_holding, sign)
    if s.news:
        lines += _groups_md("Around tier-1 news", s.news, sign)
    if s.news_note:
        lines += [f"News: {s.news_note}.", ""]
    lines += [f"Entries in the first {OPEN_WINDOW_MIN} min after a session open: {s.share_near_session_open:.0%}. "
              f"Entries within ${ROUND_BAND:g} of a ${ROUND_STEP:g} level: {s.share_near_round_number:.0%}.", ""]
    lines += ["## Risk of ruin of the owner's sizing", ""]
    if s.ruin:
        ru0 = s.ruin[0]
        lines += [f"{ru0.n_paths} bootstrap paths of {ru0.horizon_trades} trades (about a year at the observed pace), "
                  f"ruin = equity touching {ru0.ruin_level:.0%} of the start. Lots vs equity correlation "
                  f"{opt(s.lots_equity_corr)}: near 1 means the compounding row describes the owner, near 0 the "
                  "fixed-stake row.", "",
                  _row(["sizing", "P(ruin)", "P(halved)", "median end (x start)"]), _row(["---"] * 4)]
        for ru in s.ruin:
            lines.append(_row([ru.method, f"{ru.p_ruin:.1%}", f"{ru.p_halved:.1%}", f"{ru.median_end_multiple:.2f}"]))
        lines += ["", "The bootstrap assumes future trades look like these, independent of each other; it cannot see "
                  "regimes the record does not contain, so it understates ruin.", ""]
    else:
        lines += ["Not estimated (see notes).", ""]
    lines += ["## Hypothesis seeds", "",
              "**These are in-sample observations of the owner's own trades, not evidence.** They describe one "
              "discretionary record over one period, chosen after looking at it. Each seed needs the research "
              "director's pre-registration (rule fixed in advance, trial counted in the budget, data outside this "
              "period, holdout untouched) before it can mean anything. Format as docs/research/hypotheses.md.", "",
              _row(["ID", "Hypothesis", "Status", "Rationale", "Data", "Horizon", "Expected gross edge vs cost",
                    "Trial cost"]), _row(["---"] * 8)]
    for sd in r.seeds:
        lines.append(_row([sd.id, sd.hypothesis + (f" (relates to {sd.maps_to})" if sd.maps_to else ""), sd.status,
                           sd.rationale, sd.data, sd.horizon, sd.edge_vs_cost, sd.trial_cost]))
    lines += ["", "## Notes", ""] + [f"- {x}" for x in s.notes or ["none"]] + [
        "- Daily P&L uses the UTC day of each close (goldbot's risk-day); sessions, hours and weekdays use the UTC "
        "entry time.",
        "- Small samples: a group with a |t| under about 2 is noise."]
    return "\n".join(lines) + "\n"


def console_summary(r: Result) -> str:
    s = r.summary
    sign = CCY_SIGN.get(s.currency, s.currency + " ")
    shares = ", ".join(f">= {sign}{k}: {v:.0%}" for k, v in s.daily.share_at_least.items())
    ruin = "n/a" if not s.ruin else " / ".join(f"{ru.p_ruin:.1%}" for ru in s.ruin) + " (compounding / fixed)"
    return (f"{s.n_trades} XAUUSD trades {s.period_start:%Y-%m-%d}..{s.period_end:%Y-%m-%d}: net {_money(s.total_net, sign)}, "
            f"win {s.win_rate:.0%}, PF {'n/a' if s.profit_factor is None else f'{s.profit_factor:.2f}'}, "
            f"expectancy {_money(s.expectancy, sign)}/trade\n"
            f"days {s.daily.n_days}: mean {_money(s.daily.mean, sign)}, worst {_money(s.daily.worst, sign)} "
            f"({s.daily.worst_day}); {shares}\n"
            f"max drawdown {_money(s.max_drawdown, sign)}, losing streak {s.longest_losing_streak}, ruin {ruin}\n"
            f"{len(r.seeds)} hypothesis seeds (in-sample, not evidence)")


def _rounded(x: object) -> object:
    """Floats to 6 decimals: readable, and no float-representation noise in a private file."""
    if isinstance(x, float):
        return round(x, 6)
    if isinstance(x, dict):
        return {k: _rounded(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_rounded(v) for v in x]
    return x


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def write_outputs(r: Result, out_dir: str | Path) -> list[Path]:
    """Markdown report, JSON summary and per-trade CSV, owner-readable only. Refuses any folder inside the
    repository (public): the outputs describe the owner's account."""
    out = Path(out_dir).expanduser().resolve()
    repo = _repo_root()
    if out == repo or repo in out.parents:
        raise ValueError(f"refusing to write the owner's history inside the repository ({out}); "
                         f"use a private folder such as {DEFAULT_DIR}")
    stamp = pd.Timestamp.now("UTC").strftime("%Y%m%d-%H%M%S")
    md, js, tr = (out / f"{OUTPUT_PREFIX}_{stamp}.{ext}" for ext in ("md", "json", "trades.csv"))
    write_private(md, render_markdown(r))
    write_private(js, json.dumps(_rounded(r.model_dump(mode="json", exclude={"trades"})), indent=2))
    write_private(tr, pd.DataFrame([t.model_dump(mode="json") for t in r.trades]).to_csv(index=False,
                                                                                        float_format="%.6g"))
    return [md, js, tr]


def _newest_report(folder: Path) -> Path:
    files = [p for p in folder.iterdir() if p.is_file() and not p.name.startswith(OUTPUT_PREFIX)
             and p.suffix.lower() in (".html", ".htm", ".xlsx", ".csv")]
    if not files:
        raise SystemExit(f"no MT5 report (.html/.htm/.xlsx/.csv) in {folder}: see docs/OWNER_GUIDE.md")
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
                    help="report file, or a folder (newest report in it); default %(default)s")
    ap.add_argument("--server-tz", default="Europe/Athens", help="broker server time zone (IC Markets: Europe/Athens)")
    ap.add_argument("--contract", type=float, default=CONTRACT_OZ, help="ounces per lot")
    ap.add_argument("--targets", default="30,50", help="daily targets in account currency, comma separated")
    ap.add_argument("--start-balance", type=float, default=None, help="starting equity if the report has no deposit")
    ap.add_argument("--out", default=None, help="output folder (default: the report's folder; never the repo)")
    ap.add_argument("--no-news", action="store_true", help="skip the economic-calendar lookup")
    a = ap.parse_args(argv)
    src = Path(a.path).expanduser()
    if src.is_dir():
        src = _newest_report(src)
    if not src.exists():
        raise SystemExit(f"{src} not found: export it from MT5 first (docs/OWNER_GUIDE.md)")
    targets = tuple(float(x) for x in a.targets.split(",") if x.strip())
    result = analyse(load_report(src, a.server_tz), contract=a.contract, targets=targets,
                     start_balance=a.start_balance, events=_load_events(a.no_news))
    paths = write_outputs(result, a.out or src.parent)
    print(console_summary(result))
    for p in paths:
        print(f"wrote {p}")
    return 0


__all__ = ["Report", "Position", "Trade", "Summary", "Result", "HypothesisSeed", "load_report", "xauusd_positions",
           "analyse", "bootstrap_ruin", "render_markdown", "write_outputs", "parse_number", "parse_time", "main"]
