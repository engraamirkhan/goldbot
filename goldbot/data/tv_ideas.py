"""TradingView author ideas: the published chart ideas of the authors the owner follows (owner directive 2026-10-28),
read-only and rate-limited, into state/tv_ideas.db (brain) or raw/tv_ideas/ (the Mac's research copy).

    python -m goldbot.ops.run tv-ideas poll        # one pass over settings tv_ideas.authors
    python -m goldbot.ops.run tv-ideas follow      # a pass every poll_interval_s + jitter (12-15 min), backoff on failure
    python -m goldbot.ops.run tv-ideas backfill    # the authors' whole back catalogue (resumable)
    python -m goldbot.ops.run tv-ideas summary     # counts per author and symbol, date range, shares; no network
    python -m goldbot.ops.run tv-ideas export --out DIR    # ideas.parquet + updates.parquet for research; no network
    python -m goldbot.ops.run tv-ideas reparse     # recompute the parsed levels from the stored text; no network

The source (measured 2026-10-28, unofficial, can change without notice):

* `GET https://www.tradingview.com/api/v1/ideas/?by=<handle>&sort=recent_extended&per_page=100&page=N` is what the
  profile page's ideas tab calls. Public: no cookie is sent (the owner's session never touches this path). Newest
  activity first (a new update lifts an idea to the top). `count` is the author's whole catalogue, but a query serves
  at most 1000 items (page 11 at 100 answers 400 `bad_pagination_request`). The back catalogue is reached by slicing:
  `exchange`, `symbol`, `type`, `is_video`, `q` (a word) narrow a query, `sort=trending` orders it differently; slices are
  read until the union reaches `count` (`backfill_listing`). The listing's text is cut at 500 characters.
* `GET /api/v1/ideas/<uuid>/` has the full text, `direction` (1 long, 2 short, 0 neutral), `interval`, and `updates`:
  the author's later notes ("Trade active", "Target reached"...), each with TradingView's server time.

Point in time ("every join to bars via asof_join on available_utc"):

* Live: `first_seen_utc` is our first listing sighting, `available_utc` the moment we held the full text (normally the
  same pass). An update first seen live is available when we saw it.
* Back catalogue: anything published (or updated) before the author's first live poll, and everything the `backfill`
  command writes, is `backfilled=1` and `available_utc = min(TradingView's time + backfill_latency_s, first held)`:
  the author's claim, server-stamped, never earlier than a live poll could have held it. Research can drop or
  separate backfilled rows.
* First release is immutable: a changed title, text, direction or timeframe is a new `tv_idea_versions` row with the
  time we saw it; `edited` is set (bookkeeping only; the versions and updates carry the times). Nothing is overwritten.

Levels: `parse_levels` reads entry / stop / target prices that follow a keyword (entry, buy, sell, SL, stop, TP,
target...) in the text. Heuristic: the raw text is kept, so research can re-parse.

Third-party content (the idea text, the listing items, the snapshot URLs) lives only in tv_ideas.db and the
gitignored raw/; the repo holds handles (public) and synthetic fixtures only. Images are not downloaded.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import random
import re
import sqlite3
import sys
import time
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, Record, UtcTimestamp
from goldbot.data.tv_wire import SITE, ReadOnlyNetwork, TvNetwork, TvPullError
from goldbot.db.connection import connect, write
from goldbot.db.migrations import migrate

if TYPE_CHECKING:
    from goldbot.config import TvIdeasSettings
    from goldbot.ops.checks._base import Check

log = logging.getLogger("goldbot.tv_ideas")

DB_FILE = "tv_ideas.db"
SOURCE = "tradingview_ideas"
LIST_URL = f"{SITE}/api/v1/ideas/"
LISTING_CAP = 1000                       # items one listing query serves, whatever its count
PAGE = 100                               # per_page the listing accepts at most
DIRECTIONS = {1: "long", 2: "short", 0: "neutral"}
Direction = Literal["long", "short", "neutral", "unknown"]
_UUID = re.compile(r"^[A-Za-z0-9]{4,20}$")
# exchanges gold ideas are published on: probed once for the top slice, beside those seen in the listing
GOLD_EXCHANGES = ("FX", "OANDA", "TVC", "FX_IDC", "FOREXCOM", "CAPITALCOM", "PEPPERSTONE", "ICMARKETS", "SAXO",
                  "COMEX", "FXOPEN", "EIGHTCAP", "BLACKBULL", "VANTAGE", "SPREADEX", "CURRENCYCOM")
_STOP_WORDS = frozenset("the and for with this that from are was will have has but not you our your its into out "
                        "what can all more now just when been they their there here than then also only over "
                        "after before about above below".split())


def _ts(t: Any) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _epoch(x: Any) -> pd.Timestamp | None:
    if x is None or x == "":
        return None
    return pd.Timestamp(float(x), unit="s", tz="UTC")


def _iso(t: pd.Timestamp) -> str:
    """One fixed text form, so stored times compare as text."""
    return _ts(t).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def tf_of(interval: str) -> str | None:
    """TradingView's interval as our timeframe label: minutes as digits, 1D / 1W / 1M; None when absent."""
    s = (interval or "").strip().upper()
    if not s:
        return None
    if s.isdigit():
        return str(int(s))
    m = re.fullmatch(r"(\d*)([DWM])", s)
    if m:
        return f"{m.group(1) or '1'}{m.group(2)}"
    return s


# ------------------------------------------------------------------------------------------------- levels
class Levels(FrozenRecord):
    entry: list[float] = Field(default_factory=list)
    stop: list[float] = Field(default_factory=list)
    target: list[float] = Field(default_factory=list)
    support: list[float] = Field(default_factory=list)
    resistance: list[float] = Field(default_factory=list)

    @property
    def any(self) -> bool:
        """An entry, stop or target (support / resistance alone does not count)."""
        return bool(self.entry or self.stop or self.target)


_TAG = re.compile(r"\[/?[a-z]+(?:=[^\]]*)?\]", re.I)
_KEY = re.compile(
    r"\b(?P<stop>sl|stop[\s-]*loss|stop|invalidation)\b"
    r"|\b(?P<target>tp\d?|targets?|take[\s-]*profits?)\b"
    r"|\b(?P<entry>entry|entries|enter|(?:buy|sell)(?:\s+(?:limit|stop))?|(?:long|short)\s+(?:from|at))\b"
    r"|\b(?P<support>supports?)\b|\b(?P<resistance>resistances?)\b",
    re.I)
_N = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
# a price or a range (`2350 - 2355`, `1970-75`, `4,060 to 4,080`); not a percentage, pips, points or a figure glued to
# letters (4H, TP1, 100pips)
_NUM = re.compile(rf"(?<![\w.$])\$?(?P<a>{_N})(?:\s*(?:-|–|to)\s*\$?(?P<b>{_N}))?"
                  rf"(?![\w.]|\s*(?:%|pips?\b|points?\b|pts\b))", re.I)
_BREAK = re.compile(r"\n|[!?;]|\.(?=\s|$)")
# a move size, not a price: `100-140pips`, `2%`, `50 points`
_NOT_PRICE = re.compile(r"\$?\d[\d,.]*(?:\s*(?:-|–|to)\s*\d[\d,.]*)?\s*(?:%|pips?\b|points?\b|pts\b)", re.I)
_MAX_GAP = 30                          # characters between a keyword (or its last number) and the next number
# `1970-75 target`, `4843 Goldturn support`: a keyword right after a number, at most one word between, names it ...
_TRAIL = re.compile(r"\s{0,2}(?:[A-Za-z]{1,12}\s{1,2})?")
_GLUED = 3                             # ... unless the number sits right after its own keyword (`Entry: 4035`)


def _values(m: re.Match[str]) -> list[float]:
    a = m.group("a").replace(",", "")
    b = m.group("b")
    out = [float(a)]
    if b:
        b = b.replace(",", "")
        ai, bi = a.split(".")[0], b.split(".")[0]
        if "." not in b and len(bi) < len(ai) and float(b) < float(a):
            b = ai[: len(ai) - len(bi)] + bi          # 1970-75 -> 1975, 1698-5 -> 1695
        out.append(float(b))
    return [v for v in out if v > 0]


def parse_levels(text: str) -> Levels:
    """Prices named in `text`, by the keyword that names them on the same line or sentence: after it (at most
    `_MAX_GAP` characters on from the keyword or its previous number) or right before it (`the 1970-75 target`)."""
    clean = _NOT_PRICE.sub(" ", _TAG.sub("", text or ""))
    out: dict[str, list[float]] = {k: [] for k in ("entry", "stop", "target", "support", "resistance")}
    for seg in _BREAK.split(clean):
        keys = list(_KEY.finditer(seg))
        if not keys:
            continue
        last_kind: str | None = None
        pos = 0
        for n in _NUM.finditer(seg):
            before = [k for k in keys if k.end() <= n.start()]
            if before and before[-1].end() > pos:          # a new keyword since the last number
                last_kind, pos = before[-1].lastgroup, before[-1].end()
            gap = n.start() - pos if before else _MAX_GAP + 1
            trailing = next((k for k in keys if k.start() >= n.end()
                             and _TRAIL.fullmatch(seg[n.end():k.start()])), None)
            if gap <= _GLUED or (trailing is None and gap <= _MAX_GAP):
                kind = last_kind                           # `Entry: 4035`, `TP 2370 / 2385`
            elif trailing is not None:
                kind = trailing.lastgroup                  # `the 1970-75 target`, `1795 support`
            else:
                continue
            if kind is None:
                continue
            for v in _values(n):
                if v not in out[kind]:
                    out[kind].append(v)
            if kind == last_kind:
                pos = n.end()
    return Levels(**out)


# ------------------------------------------------------------------------------------------------- records
class Idea(FrozenRecord):
    idea_id: int
    uuid: str
    author: str
    symbol: str                            # TradingView's full name, e.g. FX:XAUUSD
    exchange: str
    interval: str
    tf: str | None
    direction: Direction
    title: str
    description: str
    is_education: bool
    is_video: bool
    published_utc: UtcTimestamp
    updated_utc: UtcTimestamp | None
    chart_url: str
    snapshot_url: str | None
    levels: Levels

    @property
    def content_hash(self) -> str:
        body = json.dumps([self.title, self.description, self.direction, self.interval, self.symbol],
                          ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()[:32]


class IdeaUpdate(FrozenRecord):
    update_id: int
    idea_id: int
    type: str
    value: str | None
    label: str | None
    description: str
    created_utc: UtcTimestamp
    levels: Levels


def _direction(code: Any) -> Direction:
    try:
        return DIRECTIONS.get(int(code), "unknown")  # type: ignore[return-value]
    except (TypeError, ValueError):
        return "unknown"


def idea_from_detail(det: Mapping[str, Any]) -> tuple[Idea, list[IdeaUpdate]]:
    """The detail endpoint's idea and its updates."""
    sym = det.get("symbol") or {}
    interval = str(det.get("interval") or sym.get("interval") or "")
    published = _epoch(det.get("created_timestamp")) or _ts(det["created_at"])
    chart = str(det.get("chart_url") or "")
    image = det.get("image") or {}
    desc = str(det.get("description") or "")
    idea = Idea(
        idea_id=int(det["id"]), uuid=str(det["uuid"]), author=str((det.get("user") or {}).get("username") or ""),
        symbol=str(sym.get("full_name") or sym.get("name") or ""), exchange=str(sym.get("exchange") or ""),
        interval=interval, tf=tf_of(interval), direction=_direction(det.get("direction", sym.get("direction"))),
        title=str(det.get("name") or ""), description=desc, is_education=bool(det.get("is_education")),
        is_video=bool(det.get("video")), published_utc=published,
        updated_utc=_epoch(det.get("last_updated_timestamp")),
        chart_url=chart if chart.startswith("http") else SITE + chart,
        snapshot_url=(image.get("big") or image.get("middle")) if isinstance(image, Mapping) else None,
        levels=parse_levels(f"{det.get('name') or ''}\n{desc}"))
    ups = [IdeaUpdate(update_id=int(u["id"]), idea_id=idea.idea_id, type=str(u.get("type") or ""),
                      value=None if u.get("value") is None else str(u["value"]),
                      label=None if u.get("label") is None else str(u["label"]),
                      description=str(u.get("description") or ""), created_utc=_ts(u["created_at"]),
                      levels=parse_levels(str(u.get("description") or "")))
           for u in det.get("updates") or []]
    return idea, ups


def _author_of(item: Mapping[str, Any]) -> str:
    return str((item.get("user") or {}).get("username") or "")


# ------------------------------------------------------------------------------------------------- the client
class BudgetExhausted(TvPullError):
    pass


class IdeasClient:
    """Every request: the read-only guard (tv_wire allow list; GET only; no cookie), the tv_pull Pacer, a budget."""

    def __init__(self, network: ReadOnlyNetwork, pacer: Any, budget: int):
        self.network, self.pacer, self.budget = network, pacer, budget

    @classmethod
    def build(cls, inner: TvNetwork, *, min_interval_s: float, budget: int,
              sleep: Callable[[float], None] = time.sleep,
              monotonic: Callable[[], float] = time.monotonic) -> IdeasClient:
        from goldbot.data.tv_pull import Pacer
        return cls(ReadOnlyNetwork(inner, cookie="", secrets=[]), Pacer(min_interval_s, monotonic, sleep), budget)

    @property
    def requests(self) -> int:
        return int(self.pacer.count)

    def get_json(self, url: str) -> Any:
        if self.pacer.count >= self.budget:
            raise BudgetExhausted(f"request budget reached ({self.budget})")
        self.pacer.wait()
        body = self.network.get(url, with_cookie=False, xhr=True)
        try:
            return json.loads(body)
        except ValueError:
            raise TvPullError(f"not JSON from {url.split('?')[0]}") from None

    def listing(self, params: Mapping[str, Any]) -> dict[str, Any]:
        data = self.get_json(LIST_URL + "?" + urlencode(dict(params)))
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            raise TvPullError("unexpected ideas listing shape")
        return data

    def detail(self, uuid: str) -> dict[str, Any]:
        if not _UUID.match(uuid):
            raise TvPullError(f"refused idea id {uuid!r}")
        data = self.get_json(f"{LIST_URL}{uuid}/")
        if not isinstance(data, dict) or "id" not in data:
            raise TvPullError("unexpected idea detail shape")
        return data


# ------------------------------------------------------------------------------------------------- the store
class Outcome(FrozenRecord):
    new_idea: bool = False
    new_updates: int = 0
    new_version: bool = False


class IdeasDb:
    """state/tv_ideas.db (goldbot/db/migrations.py TV_IDEAS); a fresh connection per unit of work."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        conn = connect(self.path, "tv_ideas")
        try:
            migrate(conn, "tv_ideas")
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = connect(self.path, "tv_ideas")
        conn.row_factory = sqlite3.Row
        return conn

    # ---- listing (first sighting)
    def add_listing(self, items: Iterable[Mapping[str, Any]], seen: pd.Timestamp) -> int:
        conn = self._conn()
        try:
            with write(conn):
                n = 0
                for it in items:
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO tv_idea_listing (idea_id, uuid, author, updated_ts, item, seen_utc) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (int(it["id"]), str(it.get("image_url") or ""), _author_of(it),
                         it.get("updated_date_timestamp"), json.dumps(it, ensure_ascii=False), _iso(seen)))
                    n += cur.rowcount
                return n
        finally:
            conn.close()

    def listing_ids(self, author: str) -> set[int]:
        conn = self._conn()
        try:
            return {int(r[0]) for r in conn.execute("SELECT idea_id FROM tv_idea_listing WHERE author = ? "
                                                    "COLLATE NOCASE", (author,))}
        finally:
            conn.close()

    def listing_items(self, author: str) -> list[dict[str, Any]]:
        conn = self._conn()
        try:
            return [json.loads(r[0]) for r in conn.execute("SELECT item FROM tv_idea_listing WHERE author = ? "
                                                           "COLLATE NOCASE", (author,))]
        finally:
            conn.close()

    def pending_details(self, authors: Iterable[str]) -> list[tuple[int, str]]:
        names = [a.lower() for a in authors]
        conn = self._conn()
        try:
            rows = conn.execute("SELECT l.idea_id, l.uuid, l.author FROM tv_idea_listing l LEFT JOIN tv_ideas i "
                                "ON i.idea_id = l.idea_id WHERE i.idea_id IS NULL ORDER BY l.idea_id").fetchall()
        finally:
            conn.close()
        return [(int(r[0]), str(r[1])) for r in rows if str(r[2]).lower() in names]

    def known(self, idea_id: int) -> sqlite3.Row | None:
        conn = self._conn()
        try:
            return conn.execute("SELECT title, direction, updated_utc FROM tv_ideas WHERE idea_id = ?",
                                (idea_id,)).fetchone()
        finally:
            conn.close()

    # ---- ideas, versions, updates
    def record(self, idea: Idea, updates: list[IdeaUpdate], seen: pd.Timestamp, *, live_since: pd.Timestamp | None,
               latency_s: float) -> Outcome:
        """Write what a detail read held at `seen`. `live_since`: the author's first live poll (None: a back catalogue
        read, everything is backfilled). Insert-only for content; never overwrites a first release."""
        lat = pd.Timedelta(seconds=latency_s)

        def back(t: pd.Timestamp) -> bool:
            return live_since is None or t < live_since

        conn = self._conn()
        try:
            with write(conn):
                row = conn.execute("SELECT content_hash, edited FROM tv_ideas WHERE idea_id = ?",
                                   (idea.idea_id,)).fetchone()
                lst = conn.execute("SELECT seen_utc FROM tv_idea_listing WHERE idea_id = ?", (idea.idea_id,)).fetchone()
                first_seen = min(_ts(lst[0]), seen) if lst else seen
                h = idea.content_hash
                levels = idea.levels.model_dump_json()
                new_idea = new_version = False
                if row is None:
                    backfilled = back(idea.published_utc)
                    available = min(idea.published_utc + lat, seen) if backfilled else seen
                    edited = int(idea.updated_utc is not None or bool(updates))
                    conn.execute(
                        "INSERT INTO tv_ideas (idea_id, uuid, author, symbol, exchange, interval, tf, direction, title,"
                        " description, description_complete, is_education, is_video, published_utc, updated_utc,"
                        " chart_url, snapshot_url, levels, has_entry, has_stop, has_target, content_hash,"
                        " first_seen_utc, available_utc, backfilled, edited, last_seen_utc, source) VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (idea.idea_id, idea.uuid, idea.author, idea.symbol, idea.exchange, idea.interval, idea.tf,
                         idea.direction, idea.title, idea.description, int(idea.is_education), int(idea.is_video),
                         _iso(idea.published_utc), _iso(idea.updated_utc) if idea.updated_utc is not None else None,
                         idea.chart_url, idea.snapshot_url, levels, int(bool(idea.levels.entry)),
                         int(bool(idea.levels.stop)), int(bool(idea.levels.target)), h, _iso(first_seen),
                         _iso(available), int(backfilled), edited, _iso(seen), SOURCE))
                    conn.execute("INSERT INTO tv_idea_versions (idea_id, content_hash, seen_utc, title, description,"
                                 " direction, interval, levels) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                 (idea.idea_id, h, _iso(available), idea.title, idea.description, idea.direction,
                                  idea.interval, levels))
                    new_idea = True
                else:
                    have = conn.execute("SELECT 1 FROM tv_idea_versions WHERE idea_id = ? AND content_hash = ?",
                                        (idea.idea_id, h)).fetchone()
                    if have is None:
                        conn.execute("INSERT INTO tv_idea_versions (idea_id, content_hash, seen_utc, title, "
                                     "description, direction, interval, levels) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                     (idea.idea_id, h, _iso(seen), idea.title, idea.description, idea.direction,
                                      idea.interval, levels))
                        new_version = True
                    edited = int(row[1]) or int(new_version or idea.updated_utc is not None or bool(updates))
                    conn.execute("UPDATE tv_ideas SET updated_utc = ?, edited = ?, last_seen_utc = ? WHERE idea_id = ?",
                                 (_iso(idea.updated_utc) if idea.updated_utc is not None else None, edited, _iso(seen),
                                  idea.idea_id))
                n_up = 0
                for u in updates:
                    ub = back(u.created_utc)
                    avail = min(u.created_utc + lat, seen) if ub else seen
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO tv_idea_updates (update_id, idea_id, type, value, label, description,"
                        " levels, created_utc, first_seen_utc, available_utc, backfilled) VALUES "
                        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (u.update_id, u.idea_id, u.type, u.value, u.label, u.description, u.levels.model_dump_json(),
                         _iso(u.created_utc), _iso(seen), _iso(avail), int(ub)))
                    n_up += cur.rowcount
                return Outcome(new_idea=new_idea, new_updates=n_up, new_version=new_version)
        finally:
            conn.close()

    # ---- derived levels
    def reparse(self) -> int:
        """Recompute the parsed levels from the stored text (they are derived: the text is the record and is never
        changed). For a parser change; returns the rows whose levels changed."""
        conn = self._conn()
        changed = 0
        try:
            with write(conn):
                for r in conn.execute("SELECT idea_id, title, description, levels FROM tv_ideas").fetchall():
                    lv = parse_levels(f"{r['title']}\n{r['description']}")
                    js = lv.model_dump_json()
                    if js != r["levels"]:
                        changed += 1
                        conn.execute("UPDATE tv_ideas SET levels = ?, has_entry = ?, has_stop = ?, has_target = ? "
                                     "WHERE idea_id = ?", (js, int(bool(lv.entry)), int(bool(lv.stop)),
                                                           int(bool(lv.target)), r["idea_id"]))
                for r in conn.execute("SELECT idea_id, content_hash, title, description FROM tv_idea_versions"
                                      ).fetchall():
                    conn.execute("UPDATE tv_idea_versions SET levels = ? WHERE idea_id = ? AND content_hash = ?",
                                 (parse_levels(f"{r['title']}\n{r['description']}").model_dump_json(), r["idea_id"],
                                  r["content_hash"]))
                for r in conn.execute("SELECT update_id, description FROM tv_idea_updates").fetchall():
                    conn.execute("UPDATE tv_idea_updates SET levels = ? WHERE update_id = ?",
                                 (parse_levels(r["description"]).model_dump_json(), r["update_id"]))
            return changed
        finally:
            conn.close()

    # ---- runs and state
    def get_state(self, key: str) -> str | None:
        conn = self._conn()
        try:
            r = conn.execute("SELECT value FROM tv_ideas_state WHERE key = ?", (key,)).fetchone()
            return None if r is None else str(r[0])
        finally:
            conn.close()

    def set_state(self, key: str, value: str, now: pd.Timestamp) -> None:
        conn = self._conn()
        try:
            with write(conn):
                conn.execute("INSERT INTO tv_ideas_state (key, value, updated_utc) VALUES (?, ?, ?) ON CONFLICT (key) "
                             "DO UPDATE SET value = excluded.value, updated_utc = excluded.updated_utc",
                             (key, value, _iso(now)))
        finally:
            conn.close()

    def live_since(self, author: str) -> pd.Timestamp | None:
        v = self.get_state(f"live_since:{author.lower()}")
        return None if v is None else _ts(v)

    def record_run(self, rep: RunReport, started: pd.Timestamp, finished: pd.Timestamp) -> None:
        conn = self._conn()
        try:
            with write(conn):
                conn.execute("INSERT INTO tv_ideas_runs (kind, started_utc, finished_utc, ok, requests, new_ideas, "
                             "new_updates, new_versions, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                             (rep.kind, _iso(started), _iso(finished), int(rep.ok), rep.requests, rep.new_ideas,
                              rep.new_updates, rep.new_versions, rep.reason[:500]))
        finally:
            conn.close()

    def requests_since(self, since: pd.Timestamp, kind: str = "poll") -> int:
        conn = self._conn()
        try:
            r = conn.execute("SELECT COALESCE(SUM(requests), 0) FROM tv_ideas_runs WHERE kind = ? AND started_utc >= ?",
                             (kind, _iso(since))).fetchone()
            return int(r[0])
        finally:
            conn.close()


# ------------------------------------------------------------------------------------------------- live poll
class RunReport(Record):
    kind: str
    ok: bool = True
    reason: str = ""
    requests: int = 0
    new_ideas: int = 0
    new_updates: int = 0
    new_versions: int = 0

    def text(self) -> str:
        state = "ok" if self.ok else f"FAILED ({self.reason})"
        return (f"tv-ideas {self.kind}: {state}; {self.requests} requests, {self.new_ideas} new ideas, "
                f"{self.new_updates} new updates, {self.new_versions} new versions")


def _changed(known: sqlite3.Row, item: Mapping[str, Any]) -> bool:
    upd = item.get("updated_date_timestamp")
    have = known["updated_utc"]
    have_s = None if have is None else int(_ts(have).timestamp())
    new_s = None if upd is None else int(float(upd))
    sym = item.get("symbol") or {}
    return (have_s != new_s or str(item.get("name") or "") != known["title"]
            or _direction(sym.get("direction")) != known["direction"])


def poll_once(cfg: TvIdeasSettings, db: IdeasDb, client: IdeasClient, *,
              now: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC")) -> RunReport:
    """One pass: each author's newest-activity page; the detail of every new or changed idea. Fails soft: a failed
    author or detail is reported and retried next pass (its first sighting is kept). A pass over the daily budget is
    skipped and not recorded."""
    started = now()
    used = db.requests_since(started - pd.Timedelta(days=1))
    if used >= cfg.max_requests_per_day:
        return RunReport(kind="poll", ok=False,
                         reason=f"daily request budget reached ({used} of {cfg.max_requests_per_day} in 24 h)")
    rep = RunReport(kind="poll")
    problems: list[str] = []
    for author in cfg.authors:
        try:
            data = client.listing({"by": author, "sort": "recent_extended", "per_page": cfg.live_page_size})
        except TvPullError as exc:
            problems.append(f"{author}: {exc}")
            continue
        seen = now()
        items = [it for it in data["results"] if _author_of(it).lower() == author.lower()]
        db.add_listing(items, seen)
        live_since = db.live_since(author) or started
        author_ok = True
        for it in items:
            k = db.known(int(it["id"]))
            if k is not None and not _changed(k, it):
                continue
            try:
                idea, ups = idea_from_detail(client.detail(str(it.get("image_url") or "")))
            except (TvPullError, KeyError, ValueError, TypeError) as exc:
                problems.append(f"{author} idea {it.get('id')}: {type(exc).__name__}: {exc}"[:200])
                author_ok = False
                continue
            out = db.record(idea, ups, now(), live_since=live_since, latency_s=cfg.backfill_latency_s)
            rep.new_ideas += int(out.new_idea)
            rep.new_updates += out.new_updates
            rep.new_versions += int(out.new_version)
        if author_ok and db.live_since(author) is None:
            db.set_state(f"live_since:{author.lower()}", _iso(started), now())
    rep.requests = client.requests
    rep.ok = not problems
    rep.reason = "; ".join(problems)[:500]
    db.record_run(rep, started, now())
    return rep


def follow(once: Callable[[], RunReport], cfg: TvIdeasSettings, *, sleep: Callable[[float], None] = time.sleep,
           max_passes: int | None = None, rand: Callable[[], float] = random.random) -> None:
    """A pass every poll_interval_s plus jitter; after a failure the wait doubles from backoff_base_s to backoff_max_s
    and is never shorter than the poll interval (a failure never speeds the polling up)."""
    errors = passes = 0
    while max_passes is None or passes < max_passes:
        passes += 1
        try:
            rep = once()
            print(rep.text(), flush=True)
            ok = rep.ok
        except Exception as exc:  # the loop outlives any one pass; logged by type only
            log.warning("tv ideas: pass raised %s", type(exc).__name__)
            ok = False
        if ok:
            errors = 0
            wait = cfg.poll_interval_s + rand() * cfg.poll_jitter_s
        else:
            errors += 1
            wait = min(max(cfg.backoff_base_s * 2 ** min(errors - 1, 30), cfg.poll_interval_s), cfg.backoff_max_s)
        sleep(wait)


# ------------------------------------------------------------------------------------------------- back catalogue
class SliceReport(Record):
    total: int = 0                      # the author's catalogue as the listing counts it
    collected: int = 0                  # distinct ideas read from the listing slices
    slices: int = 0
    requests: int = 0


class BackfillReport(Record):
    ok: bool = True
    reason: str = ""
    listing: dict[str, SliceReport] = Field(default_factory=dict)
    details_fetched: int = 0
    detail_failures: int = 0
    new_updates: int = 0
    requests: int = 0


def _vocabulary(items: Iterable[Mapping[str, Any]], tried: set[str], limit: int = 40) -> list[str]:
    """Words worth a `q` slice: in at least 1% and at most 80% of the items, most frequent first."""
    docs = [set(re.findall(r"[a-z][a-z0-9]{2,}", f"{it.get('name') or ''} {it.get('description') or ''}".lower()))
            for it in items]
    df = Counter(w for d in docs for w in d if w not in _STOP_WORDS)
    n = max(len(docs), 1)
    words = [w for w, c in df.most_common() if 0.01 * n <= c <= 0.8 * n and w not in tried]
    return words[:limit]


def backfill_listing(client: IdeasClient, db: IdeasDb, author: str, now: Callable[[], pd.Timestamp],
                     rep: SliceReport, *, max_requests: int = 600) -> None:
    """Read the author's whole listing although one query serves only 1000: every slice whose count fits is read to
    the end; a bigger one is read to its cap and split further (sort, exchange, type, video, then words), until the
    union reaches the author's count or nothing is left to try. Each item goes to tv_idea_listing at first sighting."""
    base = {"by": author, "sort": "recent_extended", "per_page": PAGE}
    collected = db.listing_ids(author)
    tried: set[tuple[tuple[str, str], ...]] = set()
    big: list[dict[str, str]] = []
    q_tried: set[str] = set()
    start_requests = client.requests

    def done() -> bool:
        return rep.total > 0 and len(collected) >= rep.total

    def read(f: dict[str, str], *, full: bool) -> tuple[int, list[dict[str, Any]]]:
        key = tuple(sorted(f.items()))
        tried.add(key)
        rep.slices += 1
        params = {**base, **f}
        first = client.listing({**params, "page": 1})
        count = int(first.get("count") or 0)
        pages = min(int(first.get("page_count") or 1), LISTING_CAP // PAGE)
        items = list(first["results"])
        if count <= LISTING_CAP or full:
            for p in range(2, pages + 1):
                if done() and not full:
                    break
                items += client.listing({**params, "page": p})["results"]
        mine = [it for it in items if _author_of(it).lower() == author.lower()]
        db.add_listing(mine, now())
        collected.update(int(it["id"]) for it in mine)
        rep.collected, rep.requests = len(collected), client.requests - start_requests
        return count, mine

    def refinements(f: dict[str, str], items: list[dict[str, Any]], top: bool) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        if f.get("sort", "recent_extended") == "recent_extended" and "sort" not in f:
            out.append({**f, "sort": "trending"})
        if "exchange" not in f and "q" not in f:
            seen_ex = [str((it.get("symbol") or {}).get("exchange") or "") for it in items]
            for ex in dict.fromkeys([e for e in seen_ex if e] + (list(GOLD_EXCHANGES) if top else [])):
                out.append({**f, "exchange": ex})
        if "type" not in f:
            out.append({**f, "type": "education"})
        if "is_video" not in f:
            out.append({**f, "is_video": "true"})
        return [g for g in out if tuple(sorted(g.items())) not in tried]

    total, items = read({}, full=False)
    rep.total = total
    queue: deque[dict[str, str]] = deque()
    if total > LISTING_CAP:
        big.append({})
        queue.extend(refinements({}, items, top=True))
    rounds = 0
    while not done():
        at_start = len(collected)
        while queue and not done() and rep.requests < max_requests:
            f = queue.popleft()
            if tuple(sorted(f.items())) in tried:
                continue
            count, got = read(f, full=f.get("sort") not in (None, "recent_extended"))
            if count > LISTING_CAP and f.get("sort") in (None, "recent_extended"):
                big.append(f)
                queue.extend(refinements(f, got, top=False))
        # a word round that adds less than 0.5% of the catalogue ends it: the listing's count can include ideas it
        # never serves (hidden or removed), and chasing the last few costs hundreds of requests (measured 2026-10-28:
        # Goldviewfx 2220 of 2222 after an hour)
        found = len(collected) - at_start
        if done() or rounds >= 3 or rep.requests >= max_requests or (rounds and found < max(3, 0.005 * rep.total)):
            break
        rounds += 1
        # words from what has been read so far: a `q` slice of a big slice usually fits under the cap
        vocab = _vocabulary(db.listing_items(author), q_tried)
        q_tried.update(vocab)
        for f in [g for g in big if "q" not in g and "sort" not in g]:
            queue.extend({**f, "q": w} for w in vocab)
        if not queue:
            break
    # the newest 1000 of every big slice not already read to its cap
    for f in big:
        if done() or rep.requests >= max_requests:
            break
        key = tuple(sorted({**f, "_full": "1"}.items()))
        if key in tried:
            continue
        tried.add(key)
        read(f, full=True)


def backfill(cfg: TvIdeasSettings, db: IdeasDb, client: IdeasClient, *, authors: list[str] | None = None,
             now: Callable[[], pd.Timestamp] = lambda: pd.Timestamp.now(tz="UTC"), listing: bool = True,
             max_consecutive_failures: int = 10) -> BackfillReport:
    """The back catalogue: the listing slices, then the detail of every idea not yet held (resumable: a second run
    reads only what is missing). Every row is backfilled. Stops at the client's request budget."""
    started = now()
    rep = BackfillReport()
    names = authors or list(cfg.authors)
    fails = 0
    try:
        if listing:
            for a in names:
                rep.listing[a] = SliceReport()
                backfill_listing(client, db, a, now, rep.listing[a])
        for idea_id, uuid in db.pending_details(names):
            try:
                idea, ups = idea_from_detail(client.detail(uuid))
            except BudgetExhausted:
                raise
            except (TvPullError, KeyError, ValueError, TypeError) as exc:
                rep.detail_failures += 1
                fails += 1
                log.warning("tv ideas backfill: idea %s: %s", idea_id, type(exc).__name__)
                if fails >= max_consecutive_failures:
                    raise TvPullError(f"{fails} detail reads failed in a row (last: {exc})"[:300]) from None
                continue
            fails = 0
            out = db.record(idea, ups, now(), live_since=None, latency_s=cfg.backfill_latency_s)
            rep.details_fetched += int(out.new_idea)
            rep.new_updates += out.new_updates
    except TvPullError as exc:
        rep.ok, rep.reason = False, str(exc)[:300]
    rep.requests = client.requests
    if rep.ok:
        incomplete = [f"{a} {r.collected}/{r.total}" for a, r in rep.listing.items() if r.collected < r.total]
        if incomplete:
            rep.reason = "listing incomplete: " + ", ".join(incomplete)
    db.record_run(RunReport(kind="backfill", ok=rep.ok, reason=rep.reason, requests=rep.requests,
                            new_ideas=rep.details_fetched, new_updates=rep.new_updates), started, now())
    return rep


# ------------------------------------------------------------------------------------------------- research reads
_TIME_COLS = ("published_utc", "updated_utc", "first_seen_utc", "available_utc", "last_seen_utc")


def _frame(path: str | Path, sql: str, times: Iterable[str]) -> pd.DataFrame:
    conn = connect(path, "tv_ideas")
    try:
        df = pd.read_sql_query(sql, conn)
    finally:
        conn.close()
    for c in times:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], utc=True, format="ISO8601").dt.as_unit("ns")
    return df


def ideas_frame(path: str | Path) -> pd.DataFrame:
    """Every idea as first released, one row each; join to bars on `available_utc` only (asof_join)."""
    df = _frame(path, "SELECT * FROM tv_ideas ORDER BY available_utc, idea_id", _TIME_COLS)
    for c in ("backfilled", "edited", "is_education", "is_video", "has_entry", "has_stop", "has_target",
              "description_complete"):
        df[c] = df[c].astype(bool)
    return df


def updates_frame(path: str | Path) -> pd.DataFrame:
    df = _frame(path, "SELECT * FROM tv_idea_updates ORDER BY available_utc, update_id",
                ("created_utc", "first_seen_utc", "available_utc"))
    df["backfilled"] = df["backfilled"].astype(bool)
    return df


def summary(path: str | Path) -> dict[str, Any]:
    """Counts per author and symbol, the published range and the shares the research brief asks for."""
    ideas = ideas_frame(path)
    ups = updates_frame(path)
    out: dict[str, Any] = {"authors": {}}
    for author, g in ideas.groupby("author", sort=True):
        n = len(g)
        tickers = g["symbol"].str.split(":").str[-1]
        allthree = g["has_entry"] & g["has_stop"] & g["has_target"]
        anylvl = g["has_entry"] | g["has_stop"] | g["has_target"]
        out["authors"][str(author)] = {
            "ideas": n,
            "symbols": {str(k): int(v) for k, v in tickers.value_counts().items()},
            "first_published": _iso(g["published_utc"].min()), "last_published": _iso(g["published_utc"].max()),
            "share_direction": float(g["direction"].isin(["long", "short"]).mean()),
            "directions": {str(k): int(v) for k, v in g["direction"].value_counts().items()},
            "share_any_level": float(anylvl.mean()), "share_entry_stop_target": float(allthree.mean()),
            "share_entry": float(g["has_entry"].mean()), "share_stop": float(g["has_stop"].mean()),
            "share_target": float(g["has_target"].mean()), "share_edited": float(g["edited"].mean()),
            "share_backfilled": float(g["backfilled"].mean()),
            "share_support_resistance": float(g["levels"].map(
                lambda js: bool(json.loads(js).get("support") or json.loads(js).get("resistance"))).mean()),
            "n_updates": int(ups["idea_id"].isin(g["idea_id"]).sum()) if len(ups) else 0,
            "timeframes": {str(k): int(v) for k, v in g["tf"].fillna("?").value_counts().items()},
        }
    return out


# ------------------------------------------------------------------------------------------------- health
def check_tv_ideas(ctx: Any) -> Check:
    """tv_ideas: WARN when no successful poll for `stale_hours` on a weekday (UTC), or the file is unreadable. OK
    before the first poll (not set up) and at weekends. Never FAIL: the ideas never gate trading or exits."""
    from goldbot.ops.checks._base import Check
    name = "tv_ideas"
    settings = getattr(ctx, "settings", None)
    stale_h = float(settings.tv_ideas.stale_hours) if settings is not None and hasattr(settings, "tv_ideas") else 2.0
    path = Path(ctx.state_dir) / DB_FILE
    if not path.exists():
        return Check(name=name, status="ok", reason="no TradingView ideas poll yet")
    try:
        conn = connect(path, "tv_ideas")
        try:
            has = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'tv_ideas_runs'").fetchone()
            polls = conn.execute("SELECT COUNT(*) FROM tv_ideas_runs WHERE kind = 'poll'").fetchone()[0] if has else 0
            last = conn.execute("SELECT MAX(finished_utc) FROM tv_ideas_runs WHERE kind = 'poll' AND ok = 1"
                                ).fetchone()[0] if has else None
        finally:
            conn.close()
    except (sqlite3.Error, OSError, RuntimeError) as exc:
        return Check(name=name, status="warn", reason=f"tv_ideas.db unreadable: {type(exc).__name__}")
    if not polls:
        return Check(name=name, status="ok", reason="no TradingView ideas poll yet (back catalogue only)")
    now = _ts(ctx.now)
    weekday = now.weekday() < 5
    if last is None:
        return Check(name=name, status="warn" if weekday else "ok", reason="no successful TradingView ideas poll")
    age_h = (now - _ts(last)).total_seconds() / 3600
    if age_h > stale_h and weekday:
        return Check(name=name, status="warn", reason=f"last successful TradingView ideas poll {age_h:.1f} h ago")
    return Check(name=name, status="ok", reason=f"last successful TradingView ideas poll {age_h:.1f} h ago")


# ------------------------------------------------------------------------------------------------- CLI
def _client_from(cfg: TvIdeasSettings, *, min_interval_s: float, budget: int) -> IdeasClient:
    from goldbot.data.tv_wire import LiveNetwork
    return IdeasClient.build(LiveNetwork(cfg.timeout_s), min_interval_s=min_interval_s, budget=budget)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py tv-ideas", description="TradingView author ideas (read-only, no "
                                 "cookie): the followed authors' published ideas into tv_ideas.db.")
    ap.add_argument("action", choices=("poll", "follow", "backfill", "summary", "export", "reparse"))
    ap.add_argument("--state", default="state", help="directory of tv_ideas.db (the Mac copy: raw/tv_ideas)")
    ap.add_argument("--author", action="append", default=[], help="backfill: only this handle (repeatable)")
    ap.add_argument("--no-listing", action="store_true", help="backfill: only the details still missing")
    ap.add_argument("--max-requests", type=int, default=None, help="backfill: request budget for this run")
    ap.add_argument("--out", default=None, help="export: directory for ideas.parquet and updates.parquet")
    ap.add_argument("--json", action="store_true", help="summary: JSON")
    a = ap.parse_args(argv)
    from goldbot.config import load_settings
    cfg = load_settings().tv_ideas
    path = Path(a.state) / DB_FILE
    if a.action == "reparse":
        print(f"tv-ideas reparse: levels changed on {IdeasDb(path).reparse()} ideas")
        return 0
    if a.action in ("summary", "export"):
        if not path.exists():
            print(f"tv-ideas: no {path}", file=sys.stderr)
            return 2
        if a.action == "summary":
            s = summary(path)
            print(json.dumps(s, indent=1) if a.json else _summary_text(s))
            return 0
        if not a.out:
            print("tv-ideas export: give --out DIR", file=sys.stderr)
            return 2
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        ideas_frame(path).to_parquet(out / "ideas.parquet", index=False)
        updates_frame(path).to_parquet(out / "updates.parquet", index=False)
        print(f"tv-ideas export: {out}/ideas.parquet, {out}/updates.parquet")
        return 0
    if not cfg.enabled:
        print("tv-ideas: tv_ideas.enabled is false", file=sys.stderr)
        return 2
    db = IdeasDb(path)
    if a.action == "backfill":
        client = _client_from(cfg, min_interval_s=cfg.backfill_min_interval_s,
                              budget=a.max_requests or cfg.backfill_max_requests)
        brep = backfill(cfg, db, client, authors=a.author or None, listing=not a.no_listing)
        print(brep.model_dump_json(indent=1))
        return 0 if brep.ok else 1

    def once() -> RunReport:
        return poll_once(cfg, db, _client_from(cfg, min_interval_s=cfg.min_interval_s, budget=10_000))

    if a.action == "poll":
        rep = once()
        print(rep.text())
        return 0 if rep.ok else 1
    try:
        follow(once, cfg)
    except KeyboardInterrupt:
        return 0
    return 0


def _summary_text(s: Mapping[str, Any]) -> str:
    lines = []
    for author, a in s["authors"].items():
        top = ", ".join(f"{k} {v}" for k, v in list(a["symbols"].items())[:5])
        lines.append(f"{author}: {a['ideas']} ideas {a['first_published'][:10]}..{a['last_published'][:10]}; {top}; "
                     f"direction {a['share_direction']:.0%}, any level {a['share_any_level']:.0%}, entry+stop+target "
                     f"{a['share_entry_stop_target']:.0%}, edited {a['share_edited']:.0%}, updates {a['n_updates']}")
    return "\n".join(lines) or "no ideas"


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["Idea", "IdeaUpdate", "IdeasClient", "IdeasDb", "Levels", "RunReport", "backfill", "check_tv_ideas",
           "follow", "idea_from_detail", "ideas_frame", "parse_levels", "poll_once", "summary", "updates_frame"]
