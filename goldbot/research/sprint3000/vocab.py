"""The sprint-3000 vocabulary: the catalogue schema and every entry primitive and filter the screen evaluates.

"Both lanes use exactly this" (sprint-3000 brief, vocabulary section): a catalogue spec is
`{id, rank, prior_score, family, tf, entry:{type,params}, filters:[{type,params}], side, stop:{atr_n, atr_mult},
exit:{type,params}, max_hold_bars}`. Each type's params are a frozen pydantic model (extra keys refused), so a misspelt
parameter fails the catalogue load, never a silent default. Types the registry does not know fail the load too, every
one listed at once (`UnknownVocabulary`), except the four ADDENDUM A level types (PENDING_*): until the KEYLEVELS module
registers them, their specs load and are reported "not_evaluable", never dropped.

Pluggable: an entry or filter type is a function registered with `register_entry` / `register_filter` in any module
named `goldbot/research/sprint3000/vocab_*.py`; those modules are imported on first use, so a new type (the KEYLEVELS
lane's level_break, level_reject, near_level, room_to_level) is added in one module without touching the engine.
Exit types are the engine's walk and stay closed here.

Semantics, all at the close of entry-tf bar i and causal (a test cuts the tape and asserts no earlier signal changes):
  donchian_break(n)          close crosses above the prior n bars' high (below their low): first bar only
  ma_cross(fast, slow, kind) the fast EMA/SMA crosses the slow
  ema_pullback(n, depth_atr) EMA(n) rising, previous close above it, the low reaches EMA - depth_atr x ATR(14) and the
                             close is back above (mirror for shorts)
  keltner_break(n, k)        close crosses EMA(n) +- k x ATR(n)
  bb_break(n, k)             close crosses SMA(n) +- k x SD(n)
  nr_breakout(n)             bar i-1 is the narrowest of the last n; close beyond its high/low
  inside_bar_break           bar i-1 inside bar i-2; close beyond i-1's high/low
  session_range_break(s)     first close beyond the high/low of session s's last completed instance, before its next
                             instance opens (once per instance)
  opening_range_break(s, m)  first close beyond the first m minutes of session s's current instance, inside it
  prior_level_break(level)   close crosses the previous trading day's / week's / month's high or low
  momentum_sign(L)           sign(close - close L bars ago) turns positive (negative)
  consecutive_bars(n, mode)  the n-th consecutive higher (lower) close; follow = with it, fade = against it
  atr_expansion(n, mult)     true range >= mult x the previous ATR(n); side = the bar's body
  adx_onset(n, level)        ADX(n) crosses above level; side = the larger DI
  supertrend_flip(n, mult)   the supertrend direction flips
  macd_turn(f, s, sig)       the MACD line crosses its signal line
  rsi_threshold(n, lvl, mode) RSI(n) crosses above max(lvl, 100 - lvl) (follow: long) or below its mirror (short)
  zscore(n, z, mode)         (close - SMA(n)) / SD(n) crosses above z (follow: long) or below -z (short)
Filters, as of the same close (a higher timeframe reads its last bar closed at or before it):
  trend(tf, ema_n)           long only above, short only below the tf EMA
  vol_regime(atr_n, lo, hi)  the 1d ATR(atr_n) percentile among the trailing 250 days in [lo, hi]
  session(s)                 the decision time is inside session s
  weekday(days)              the trading day's weekday (0 = Monday) is listed
  adx_min(n, level)          ADX(n) >= level
  compression(n, pct_max)    the n-bar range before the bar is at or below its pct_max percentile of the last 250
ATR(n) is the simple mean of the true range; the stop is atr_mult x ATR(atr_n) on the entry timeframe."""
from __future__ import annotations

import hashlib
import importlib
import pkgutil
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml
from pydantic import Field, field_validator, model_validator

from goldbot.base import FrozenRecord
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.research.sprint3000.market import MIN, Bars, Market

TIMEFRAMES: tuple[str, ...] = ("15m", "1h", "4h", "1d")
SESSIONS = {"asia": "asia", "london": "london", "ny": "newyork", "newyork": "newyork"}
SessionName = Literal["asia", "london", "ny", "newyork"]
Mode = Literal["follow", "fade"]
Sides = tuple[np.ndarray, np.ndarray]
EntryFn = Callable[[Any, Bars, "Market | None"], Sides]
FilterFn = Callable[[Any, Bars, Market, np.ndarray], Sides]
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class UnknownVocabulary(ValueError):
    """The catalogue names types the registry does not hold; the message lists each with the specs that use it."""


class Registered(FrozenRecord):
    params: type[FrozenRecord]
    fn: Callable[..., Sides] | None = None


ENTRY: dict[str, Registered] = {}
FILTER: dict[str, Registered] = {}
EXIT: dict[str, Registered] = {}
# ADDENDUM A: the KEYLEVELS lane registers these after this engine merges. Until a module registers one, a spec using it
# loads (its params unchecked) and is reported "not_evaluable", never dropped and never run
PENDING_ENTRY = frozenset({"level_break", "level_reject"})
PENDING_FILTER = frozenset({"near_level", "room_to_level"})


def register_entry(name: str, params: type[FrozenRecord]) -> Callable[[EntryFn], EntryFn]:
    def deco(fn: EntryFn) -> EntryFn:
        ENTRY[name] = Registered(params=params, fn=fn)
        return fn
    return deco


def register_filter(name: str, params: type[FrozenRecord]) -> Callable[[FilterFn], FilterFn]:
    def deco(fn: FilterFn) -> FilterFn:
        FILTER[name] = Registered(params=params, fn=fn)
        return fn
    return deco


_PLUGINS_LOADED = False


def load_plugins() -> None:
    """Import every `vocab_*` module of this package once (each registers its own types)."""
    global _PLUGINS_LOADED
    if _PLUGINS_LOADED:
        return
    _PLUGINS_LOADED = True
    pkg = importlib.import_module("goldbot.research.sprint3000")
    for info in pkgutil.iter_modules(pkg.__path__):
        if info.name.startswith("vocab_"):
            importlib.import_module(f"goldbot.research.sprint3000.{info.name}")


# ---------------------------------------------------------------- the schema
class Rule(FrozenRecord):
    type: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)


class StopRule(FrozenRecord):
    atr_n: int = Field(ge=1)
    atr_mult: float = Field(gt=0)


def _parse(reg: dict[str, Registered], kind: str, rule: Rule) -> FrozenRecord:
    load_plugins()
    if rule.type not in reg:
        raise UnknownVocabulary(f"unknown {kind} type {rule.type!r}")
    return reg[rule.type].params.model_validate(rule.params)


class Spec(FrozenRecord):
    """One catalogue strategy. Its params are validated against the registry on construction."""
    id: str = Field(pattern=r"^S3K-\d{4,}$")
    rank: int = Field(ge=1)
    prior_score: float
    family: str = Field(min_length=1)
    tf: Literal["15m", "1h", "4h", "1d"]
    entry: Rule
    filters: tuple[Rule, ...] = ()
    side: Literal["signal", "long_only", "short_only"]
    stop: StopRule
    exit: Rule
    max_hold_bars: int = Field(ge=1)

    @model_validator(mode="after")
    def _known(self) -> "Spec":
        if self.entry.type not in PENDING_ENTRY or self.entry.type in ENTRY:
            _parse(ENTRY, "entry", self.entry)
        for f in self.filters:
            if f.type not in PENDING_FILTER or f.type in FILTER:
                _parse(FILTER, "filter", f)
        _parse(EXIT, "exit", self.exit)
        return self

    @property
    def pending(self) -> tuple[str, ...]:
        """The pending types this spec uses that no module has registered yet (empty = evaluable)."""
        load_plugins()
        names = [self.entry.type] if self.entry.type not in ENTRY else []
        return tuple(names + [f.type for f in self.filters if f.type not in FILTER])

    def exit_params(self) -> FrozenRecord:
        return _parse(EXIT, "exit", self.exit)


class Catalogue(FrozenRecord):
    version: str
    specs: tuple[Spec, ...]
    sha256: str
    path: str

    def by_id(self) -> dict[str, Spec]:
        return {s.id: s for s in self.specs}


def _unknown_types(raw: list[dict[str, Any]]) -> dict[str, list[str]]:
    load_plugins()
    out: dict[str, list[str]] = {}
    for s in raw:
        sid = str(s.get("id", "?"))
        named = [("entry", (s.get("entry") or {}).get("type"), ENTRY), ("exit", (s.get("exit") or {}).get("type"), EXIT)]
        named += [("filter", (f or {}).get("type"), FILTER) for f in s.get("filters") or []]
        for kind, name, reg in named:
            if name is not None and name not in reg and name not in PENDING_ENTRY | PENDING_FILTER:
                out.setdefault(f"{kind} {name}", []).append(sid)
    return out


def load_catalogue(path: Path) -> Catalogue:
    """The catalogue YAML (a `specs:` list, or a bare list): every spec validated, ids and ranks unique, specs in rank
    order. Unknown types fail first, all listed."""
    text = Path(path).read_bytes()
    doc = yaml.safe_load(text)
    raw = doc.get("specs") if isinstance(doc, dict) else doc
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{path}: no specs list")
    unknown = _unknown_types(raw)
    if unknown:
        lines = [f"  {k}: {len(v)} specs ({', '.join(v[:8])}{', ...' if len(v) > 8 else ''})" for k, v in sorted(unknown.items())]
        raise UnknownVocabulary(f"{path}: {len(unknown)} types the sprint-3000 engine does not know:\n" + "\n".join(lines))
    specs = [Spec.model_validate(s) for s in raw]
    for attr in ("id", "rank"):
        vals = [getattr(s, attr) for s in specs]
        dup = sorted({str(v) for v in vals if vals.count(v) > 1})
        if dup:
            raise ValueError(f"{path}: duplicate {attr}: {dup[:10]}")
    specs.sort(key=lambda s: s.rank)
    version = str(doc.get("version", "")) if isinstance(doc, dict) else ""
    return Catalogue(version=version, specs=tuple(specs), sha256=hashlib.sha256(text).hexdigest(), path=str(path))


# ---------------------------------------------------------------- indicators
def _memo(mk: Market | None, b: Bars, key: tuple[Any, ...], fn: Callable[[], Any]) -> Any:
    if mk is None or mk.bars.get(b.tf) is not b:
        return fn()
    k = (b.tf, *key)
    if k not in mk.memo:
        mk.memo[k] = fn()
    return mk.memo[k]


def true_range(b: Bars) -> np.ndarray:
    pc = np.r_[np.nan, b.c[:-1]]
    return np.fmax(b.h - b.l, np.fmax(np.abs(b.h - pc), np.abs(b.l - pc)))


def atr(b: Bars, n: int, mk: Market | None = None) -> np.ndarray:
    return np.asarray(_memo(mk, b, ("atr", n), lambda: pd.Series(true_range(b)).rolling(n).mean().to_numpy()))


def ema(x: np.ndarray, n: int) -> np.ndarray:
    return np.array(pd.Series(x).ewm(span=n, adjust=False).mean().to_numpy())


def sma(x: np.ndarray, n: int) -> np.ndarray:
    return np.array(pd.Series(x).rolling(n).mean().to_numpy())


def sd(x: np.ndarray, n: int) -> np.ndarray:
    return np.array(pd.Series(x).rolling(n).std(ddof=0).to_numpy())


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    return np.array(pd.Series(x).ewm(alpha=1.0 / n, adjust=False).mean().to_numpy())


def rsi(c: np.ndarray, n: int) -> np.ndarray:
    d = np.r_[np.nan, np.diff(c)]
    up = wilder(np.nan_to_num(np.clip(d, 0, None)), n)
    dn = wilder(np.nan_to_num(np.clip(-d, 0, None)), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100 - 100 / (1 + up / dn)
    out[:n] = np.nan
    return np.where(dn == 0, 100.0, out) if n else out


def adx(b: Bars, n: int, mk: Market | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    def calc() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        up = np.r_[np.nan, np.diff(b.h)]
        dn = np.r_[np.nan, -np.diff(b.l)]
        pdm = np.where((up > dn) & (up > 0), up, 0.0)
        mdm = np.where((dn > up) & (dn > 0), dn, 0.0)
        tr = wilder(np.nan_to_num(true_range(b)), n)
        with np.errstate(divide="ignore", invalid="ignore"):
            pdi = 100 * wilder(pdm, n) / tr
            mdi = 100 * wilder(mdm, n) / tr
            dx = 100 * np.abs(pdi - mdi) / (pdi + mdi)
        a = wilder(np.nan_to_num(dx), n)
        a[: 2 * n] = np.nan
        return a, pdi, mdi
    return _memo(mk, b, ("adx", n), calc)

def supertrend_dir(b: Bars, n: int, mult: float) -> np.ndarray:
    a = wilder(np.nan_to_num(true_range(b)), n)
    hl2 = (b.h + b.l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    N = len(b.c)
    fu, fl = ub.copy(), lb.copy()
    d = np.ones(N, dtype=np.int64)
    c = b.c
    for i in range(1, N):
        fu[i] = ub[i] if (ub[i] < fu[i - 1] or c[i - 1] > fu[i - 1]) else fu[i - 1]
        fl[i] = lb[i] if (lb[i] > fl[i - 1] or c[i - 1] < fl[i - 1]) else fl[i - 1]
        d[i] = (-1 if c[i] < fl[i] else 1) if d[i - 1] == 1 else (1 if c[i] > fu[i] else -1)
    d[: n + 1] = 0
    return d


def asof(dec_hi: np.ndarray, val: np.ndarray, at: np.ndarray) -> np.ndarray:
    """The higher-timeframe value of its last bar closed at or before each `at`."""
    k = np.searchsorted(dec_hi, at, side="right") - 1
    v = np.asarray(val, dtype=float)
    return np.where(k >= 0, v[np.clip(k, 0, max(len(v) - 1, 0))] if len(v) else np.nan, np.nan)


def cross_up(x: np.ndarray, y: np.ndarray | float) -> np.ndarray:
    y = np.broadcast_to(np.asarray(y, dtype=float), x.shape)
    px, py = np.r_[np.nan, x[:-1]], np.r_[np.nan, y[:-1]]
    return np.asarray((x > y) & (px <= py))


def cross_down(x: np.ndarray, y: np.ndarray | float) -> np.ndarray:
    y = np.broadcast_to(np.asarray(y, dtype=float), x.shape)
    px, py = np.r_[np.nan, x[:-1]], np.r_[np.nan, y[:-1]]
    return np.asarray((x < y) & (px >= py))


def _prev(x: np.ndarray, k: int = 1) -> np.ndarray:
    return np.r_[np.full(k, np.nan), x[:-k]] if len(x) > k else np.full(len(x), np.nan)


def _mode(L: np.ndarray, S: np.ndarray, mode: str) -> Sides:
    return (L, S) if mode == "follow" else (S, L)


def _need(mk: Market | None, name: str) -> Market:
    if mk is None:
        raise ValueError(f"{name} reads the minute tape: pass the Market")
    return mk


# ---------------------------------------------------------------- entry primitives
class DonchianBreak(FrozenRecord):
    n: int = Field(ge=2)


@register_entry("donchian_break", DonchianBreak)
def donchian_break(p: DonchianBreak, b: Bars, mk: Market | None) -> Sides:
    hi = pd.Series(b.h).rolling(p.n).max().shift(1).to_numpy()
    lo = pd.Series(b.l).rolling(p.n).min().shift(1).to_numpy()
    return cross_up(b.c, hi), cross_down(b.c, lo)


class MaCross(FrozenRecord):
    fast: int = Field(ge=1)
    slow: int = Field(ge=2)
    kind: Literal["ema", "sma"] = "ema"

    @model_validator(mode="after")
    def _order(self) -> "MaCross":
        if self.fast >= self.slow:
            raise ValueError("fast must be shorter than slow")
        return self


@register_entry("ma_cross", MaCross)
def ma_cross(p: MaCross, b: Bars, mk: Market | None) -> Sides:
    f = ema if p.kind == "ema" else sma
    a, z = f(b.c, p.fast), f(b.c, p.slow)
    return cross_up(a, z), cross_down(a, z)


class EmaPullback(FrozenRecord):
    n: int = Field(ge=2)
    depth_atr: float = Field(0.0, ge=0)


@register_entry("ema_pullback", EmaPullback)
def ema_pullback(p: EmaPullback, b: Bars, mk: Market | None) -> Sides:
    e, a = ema(b.c, p.n), atr(b, 14, mk)
    pe, pc = _prev(e), _prev(b.c)
    L = (e > pe) & (pc > pe) & (b.l <= e - p.depth_atr * a) & (b.c > e)
    S = (e < pe) & (pc < pe) & (b.h >= e + p.depth_atr * a) & (b.c < e)
    return L, S


class KeltnerBreak(FrozenRecord):
    n: int = Field(ge=2)
    k: float = Field(gt=0)


@register_entry("keltner_break", KeltnerBreak)
def keltner_break(p: KeltnerBreak, b: Bars, mk: Market | None) -> Sides:
    m, a = ema(b.c, p.n), atr(b, p.n, mk)
    return cross_up(b.c, m + p.k * a), cross_down(b.c, m - p.k * a)


class BbBreak(FrozenRecord):
    n: int = Field(ge=2)
    k: float = Field(gt=0)


@register_entry("bb_break", BbBreak)
def bb_break(p: BbBreak, b: Bars, mk: Market | None) -> Sides:
    m, s = sma(b.c, p.n), sd(b.c, p.n)
    return cross_up(b.c, m + p.k * s), cross_down(b.c, m - p.k * s)


class NrBreakout(FrozenRecord):
    n: int = Field(ge=2)


@register_entry("nr_breakout", NrBreakout)
def nr_breakout(p: NrBreakout, b: Bars, mk: Market | None) -> Sides:
    rng = b.h - b.l
    nr = _prev(rng) <= _prev(pd.Series(rng).rolling(p.n).min().to_numpy())
    return nr & (b.c > _prev(b.h)), nr & (b.c < _prev(b.l))


class NoParams(FrozenRecord):
    pass


@register_entry("inside_bar_break", NoParams)
def inside_bar_break(p: NoParams, b: Bars, mk: Market | None) -> Sides:
    ph, pl = _prev(b.h), _prev(b.l)
    ib = (ph <= _prev(b.h, 2)) & (pl >= _prev(b.l, 2))
    return ib & (b.c > ph), ib & (b.c < pl)


class SessionRangeBreak(FrozenRecord):
    session: SessionName


class Instances(FrozenRecord):
    """Completed and running instances of one session: UTC start, end (last minute + 1 min), high, low."""
    start: np.ndarray
    end: np.ndarray
    hi: np.ndarray
    lo: np.ndarray


def session_instances(mk: Market, session: str) -> Instances:
    name = SESSIONS[session]

    def calc() -> Instances:
        k = list(DEFAULT_SESSIONS.sessions_local).index(name)
        idx = np.flatnonzero(mk.code == k)
        if not len(idx):
            z = np.zeros(0)
            return Instances(start=np.zeros(0, np.int64), end=np.zeros(0, np.int64), hi=z, lo=z)
        s = mk.sstart[idx]
        first = np.r_[0, np.flatnonzero(np.diff(s)) + 1]
        start = s[first]
        # the SCHEDULED close, not the last minute seen: a range is complete only once its session has closed, so a
        # data gap inside an instance never ends it early (point-in-time; the truncation test pins it)
        end = np.zeros(len(start), np.int64)
        win = DEFAULT_SESSIONS.sessions_local[name]
        for off in (-1, 0, 1):
            o, c = win.bounds_ns(start // (24 * 60 * MIN) + off)
            end = np.where(o == start, c, end)
        return Instances(start=start, end=end, hi=np.maximum.reduceat(mk.tape.h[idx], first),
                         lo=np.minimum.reduceat(mk.tape.l[idx], first))
    key = ("sessions", name)
    if key not in mk.memo:
        mk.memo[key] = calc()
    return mk.memo[key]

def _first_per(keys: np.ndarray, hit: np.ndarray) -> np.ndarray:
    """`hit` kept on the first True bar of each key only."""
    i = np.flatnonzero(hit)
    _, first = np.unique(keys[i], return_index=True)
    out = np.zeros(len(hit), dtype=bool)
    out[i[first]] = True
    return out


@register_entry("session_range_break", SessionRangeBreak)
def session_range_break(p: SessionRangeBreak, b: Bars, mk: Market | None) -> Sides:
    inst = session_instances(_need(mk, "session_range_break"), p.session)
    j = np.searchsorted(inst.end, b.dec, side="right") - 1
    jj = np.clip(j, 0, None)
    nxt = np.r_[inst.start[1:], np.iinfo(np.int64).max][jj] if len(inst.start) else np.zeros(len(j), np.int64)
    ok = (j >= 0) & (b.dec < nxt)
    hi = np.where(ok, inst.hi[jj] if len(inst.hi) else np.nan, np.nan)
    lo = np.where(ok, inst.lo[jj] if len(inst.lo) else np.nan, np.nan)
    L, S = b.c > hi, b.c < lo
    first = _first_per(j, L | S)
    return L & first, S & first


class OpeningRangeBreak(FrozenRecord):
    session: SessionName
    minutes: int = Field(ge=1, le=480)


def opening_ranges(mk: Market, session: str, minutes: int) -> tuple[np.ndarray, np.ndarray]:
    """High and low of the first `minutes` minutes of each instance of `session` (aligned to `session_instances`)."""
    name = SESSIONS[session]

    def calc() -> tuple[np.ndarray, np.ndarray]:
        inst = session_instances(mk, name)
        k = list(DEFAULT_SESSIONS.sessions_local).index(name)
        idx = np.flatnonzero((mk.code == k) & (mk.tape.t < mk.sstart + minutes * MIN))
        hi, lo = np.full(len(inst.start), np.nan), np.full(len(inst.start), np.nan)
        if len(idx):
            s = mk.sstart[idx]
            first = np.r_[0, np.flatnonzero(np.diff(s)) + 1]
            pos = np.searchsorted(inst.start, s[first])
            hi[pos] = np.maximum.reduceat(mk.tape.h[idx], first)
            lo[pos] = np.minimum.reduceat(mk.tape.l[idx], first)
        return hi, lo
    key = ("or", name, minutes)
    if key not in mk.memo:
        mk.memo[key] = calc()
    return mk.memo[key]

@register_entry("opening_range_break", OpeningRangeBreak)
def opening_range_break(p: OpeningRangeBreak, b: Bars, mk: Market | None) -> Sides:
    m = _need(mk, "opening_range_break")
    inst = session_instances(m, p.session)
    orh, orl = opening_ranges(m, p.session, p.minutes)
    j = np.searchsorted(inst.start, b.dec, side="right") - 1
    jj = np.clip(j, 0, None)
    if not len(inst.start):
        z = np.zeros(len(b.c), dtype=bool)
        return z, z
    ok = (j >= 0) & (b.dec >= inst.start[jj] + p.minutes * MIN) & (b.dec <= inst.end[jj])
    L, S = ok & (b.c > orh[jj]), ok & (b.c < orl[jj])
    first = _first_per(j, L | S)
    return L & first, S & first


class PriorLevelBreak(FrozenRecord):
    level: Literal["day", "week", "month"]


def _period(tday: np.ndarray, level: str) -> np.ndarray:
    if level == "day":
        return tday
    if level == "week":
        return (tday + 3) // 7                            # Monday-based weeks (epoch day 0 was a Thursday)
    d = tday.astype("datetime64[D]")
    return (d.astype("datetime64[M]").astype(np.int64)).astype(np.int64)


def prior_levels(mk: Market, b: Bars, level: str) -> tuple[np.ndarray, np.ndarray]:
    """The previous trading day's / week's / month's high and low as of each bar of `b`."""
    def calc() -> tuple[np.ndarray, np.ndarray]:
        d = mk.bars["1d"]
        pk = _period(d.tday, level)
        if not len(pk):
            return np.full(len(b.c), np.nan), np.full(len(b.c), np.nan)
        first = np.r_[0, np.flatnonzero(np.diff(pk)) + 1]
        keys, ph, pl = pk[first], np.maximum.reduceat(d.h, first), np.minimum.reduceat(d.l, first)
        j = np.searchsorted(keys, _period(b.tday, level), side="left") - 1
        jj = np.clip(j, 0, None)
        return np.where(j >= 0, ph[jj], np.nan), np.where(j >= 0, pl[jj], np.nan)
    return _memo(mk, b, ("prior", level), calc)

@register_entry("prior_level_break", PriorLevelBreak)
def prior_level_break(p: PriorLevelBreak, b: Bars, mk: Market | None) -> Sides:
    hi, lo = prior_levels(_need(mk, "prior_level_break"), b, p.level)
    pc = _prev(b.c)
    return (b.c > hi) & (pc <= hi), (b.c < lo) & (pc >= lo)


class MomentumSign(FrozenRecord):
    lookback_bars: int = Field(ge=1)


@register_entry("momentum_sign", MomentumSign)
def momentum_sign(p: MomentumSign, b: Bars, mk: Market | None) -> Sides:
    m = np.sign(b.c - _prev(b.c, p.lookback_bars))
    pm = _prev(m)
    return (m > 0) & (pm <= 0), (m < 0) & (pm >= 0)


class ConsecutiveBars(FrozenRecord):
    n: int = Field(ge=1)
    mode: Mode = "follow"


def _run_length(x: np.ndarray) -> np.ndarray:
    out = np.zeros(len(x), dtype=np.int64)
    run = 0
    for i, v in enumerate(x):
        run = run + 1 if v else 0
        out[i] = run
    return out


@register_entry("consecutive_bars", ConsecutiveBars)
def consecutive_bars(p: ConsecutiveBars, b: Bars, mk: Market | None) -> Sides:
    pc = _prev(b.c)
    up, dn = _run_length(b.c > pc), _run_length(b.c < pc)
    return _mode(up == p.n, dn == p.n, p.mode)


class AtrExpansion(FrozenRecord):
    n: int = Field(ge=1)
    mult: float = Field(gt=0)


@register_entry("atr_expansion", AtrExpansion)
def atr_expansion(p: AtrExpansion, b: Bars, mk: Market | None) -> Sides:
    x = true_range(b) >= p.mult * _prev(atr(b, p.n, mk))
    return x & (b.c > b.o), x & (b.c < b.o)


class AdxOnset(FrozenRecord):
    n: int = Field(ge=2)
    level: float = Field(gt=0, lt=100)


@register_entry("adx_onset", AdxOnset)
def adx_onset(p: AdxOnset, b: Bars, mk: Market | None) -> Sides:
    a, pdi, mdi = adx(b, p.n, mk)
    on = cross_up(a, p.level)
    return on & (pdi > mdi), on & (mdi > pdi)


class SupertrendFlip(FrozenRecord):
    n: int = Field(ge=2)
    mult: float = Field(gt=0)


@register_entry("supertrend_flip", SupertrendFlip)
def supertrend_flip(p: SupertrendFlip, b: Bars, mk: Market | None) -> Sides:
    d = np.asarray(_memo(mk, b, ("supertrend", p.n, p.mult), lambda: supertrend_dir(b, p.n, p.mult)))
    pd_ = np.r_[0, d[:-1]]
    flip = (d != pd_) & (pd_ != 0) & (d != 0)
    return flip & (d == 1), flip & (d == -1)


class MacdTurn(FrozenRecord):
    fast: int = Field(ge=1)
    slow: int = Field(ge=2)
    signal: int = Field(ge=1)


@register_entry("macd_turn", MacdTurn)
def macd_turn(p: MacdTurn, b: Bars, mk: Market | None) -> Sides:
    m = ema(b.c, p.fast) - ema(b.c, p.slow)
    s = ema(m, p.signal)
    m[: p.slow], s[: p.slow] = np.nan, np.nan
    return cross_up(m, s), cross_down(m, s)


class RsiThreshold(FrozenRecord):
    n: int = Field(ge=2)
    level: float = Field(gt=0, lt=100)
    mode: Mode = "follow"


@register_entry("rsi_threshold", RsiThreshold)
def rsi_threshold(p: RsiThreshold, b: Bars, mk: Market | None) -> Sides:
    r = np.asarray(_memo(mk, b, ("rsi", p.n), lambda: rsi(b.c, p.n)))
    hi = max(p.level, 100 - p.level)
    return _mode(cross_up(r, hi), cross_down(r, 100 - hi), p.mode)


class Zscore(FrozenRecord):
    n: int = Field(ge=2)
    z: float = Field(gt=0)
    mode: Mode = "follow"


@register_entry("zscore", Zscore)
def zscore(p: Zscore, b: Bars, mk: Market | None) -> Sides:
    s = sd(b.c, p.n)
    with np.errstate(divide="ignore", invalid="ignore"):
        zs = np.where(s > 0, (b.c - sma(b.c, p.n)) / s, np.nan)
    return _mode(cross_up(zs, p.z), cross_down(zs, -p.z), p.mode)


def entry_signals(rule: Rule, b: Bars, mk: Market | None) -> Sides:
    """(long, short) boolean arrays over `b` for an entry rule; a bar signalling both sides is neither."""
    p = _parse(ENTRY, "entry", rule)
    fn = ENTRY[rule.type].fn
    assert fn is not None
    L, S = fn(p, b, mk)
    L, S = np.asarray(L, dtype=bool), np.asarray(S, dtype=bool)
    both = L & S
    return L & ~both, S & ~both


# ---------------------------------------------------------------- filters
class TrendFilter(FrozenRecord):
    tf: Literal["4h", "1d"]
    ema_n: int = Field(ge=2)


@register_filter("trend", TrendFilter)
def trend(p: TrendFilter, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    def calc() -> np.ndarray:
        h = mk.bars[p.tf]
        return np.sign(h.c - ema(h.c, p.ema_n))
    d = np.asarray(_memo(mk, mk.bars[p.tf], ("trend_dir", p.ema_n), calc))
    v = asof(mk.bars[p.tf].dec, d, b.dec)
    return v > 0, v < 0


class VolRegime(FrozenRecord):
    atr_n: int = Field(ge=1)
    pct_lo: float = Field(ge=0, le=100)
    pct_hi: float = Field(ge=0, le=100)

    @model_validator(mode="after")
    def _order(self) -> "VolRegime":
        if self.pct_lo > self.pct_hi:
            raise ValueError("pct_lo above pct_hi")
        return self


@register_filter("vol_regime", VolRegime)
def vol_regime(p: VolRegime, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    d = mk.bars["1d"]

    def calc() -> np.ndarray:
        return np.asarray(pd.Series(atr(d, p.atr_n, mk)).rolling(250).rank(pct=True).to_numpy() * 100)
    pct = asof(d.dec, np.asarray(_memo(mk, d, ("atr_pct", p.atr_n), calc)), b.dec)
    ok = (pct >= p.pct_lo) & (pct <= p.pct_hi)
    return ok, ok


class SessionFilter(FrozenRecord):
    session: SessionName


@register_filter("session", SessionFilter)
def session(p: SessionFilter, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    code = np.asarray(_memo(mk, b, ("dec_session",), lambda: DEFAULT_SESSIONS.session_windows(
        pd.DatetimeIndex(pd.to_datetime(b.dec, utc=True)))[0]))
    ok = code == list(DEFAULT_SESSIONS.sessions_local).index(SESSIONS[p.session])
    return ok, ok


class WeekdayFilter(FrozenRecord):
    days: tuple[int, ...] = Field(min_length=1)

    @field_validator("days", mode="before")
    @classmethod
    def _names(cls, v: Any) -> Any:
        return tuple(WEEKDAYS[str(x).lower()[:3]] if isinstance(x, str) else x for x in v)

    @field_validator("days")
    @classmethod
    def _range(cls, v: tuple[int, ...]) -> tuple[int, ...]:
        if any(not 0 <= x <= 6 for x in v):
            raise ValueError("weekdays are 0 (Monday) .. 6")
        return v


@register_filter("weekday", WeekdayFilter)
def weekday(p: WeekdayFilter, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    ok = np.isin((b.tday + 3) % 7, p.days)
    return ok, ok


class AdxMin(FrozenRecord):
    n: int = Field(ge=2)
    level: float = Field(gt=0, lt=100)


@register_filter("adx_min", AdxMin)
def adx_min(p: AdxMin, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    ok = adx(b, p.n, mk)[0] >= p.level
    return ok, ok


class Compression(FrozenRecord):
    n: int = Field(ge=2)
    pct_max: float = Field(gt=0, le=100)


@register_filter("compression", Compression)
def compression(p: Compression, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    def calc() -> np.ndarray:
        w = pd.Series(b.h).rolling(p.n).max() - pd.Series(b.l).rolling(p.n).min()
        return np.asarray(w.shift(1).rolling(250).rank(pct=True).to_numpy() * 100)
    ok = np.asarray(_memo(mk, b, ("compression", p.n), calc)) <= p.pct_max
    return ok, ok


def filter_ok(rule: Rule, b: Bars, mk: Market, risk: np.ndarray) -> Sides:
    """(long allowed, short allowed) per bar; `risk` is each bar's stop distance (USD/oz) for filters that need it."""
    p = _parse(FILTER, "filter", rule)
    fn = FILTER[rule.type].fn
    assert fn is not None
    L, S = fn(p, b, mk, risk)
    return np.asarray(L, dtype=bool), np.asarray(S, dtype=bool)


# ---------------------------------------------------------------- exits (the engine walks them)
class TargetR(FrozenRecord):
    r: float = Field(gt=0)


class Chandelier(FrozenRecord):
    atr_n: int = Field(ge=1)
    mult: float = Field(gt=0)


class TimeExit(FrozenRecord):
    bars: int = Field(ge=1)


EXIT.update({"target_r": Registered(params=TargetR), "chandelier": Registered(params=Chandelier),
             "time": Registered(params=TimeExit), "opposite_signal": Registered(params=NoParams)})


# ---------------------------------------------------------------- a spec's signals
class Signals(FrozenRecord):
    """A spec's entries on its timeframe after filters and side: bar index, decision time, side, stop distance."""
    bar: np.ndarray
    dec: np.ndarray
    side: np.ndarray
    risk: np.ndarray


def spec_signals(spec: Spec, mk: Market) -> Signals:
    b = mk.bars[spec.tf]
    L, S = entry_signals(spec.entry, b, mk)
    risk = spec.stop.atr_mult * atr(b, spec.stop.atr_n, mk)
    for f in spec.filters:
        lo, so = filter_ok(f, b, mk, risk)
        L, S = L & lo, S & so
    if spec.side == "long_only":
        S = np.zeros_like(S)
    elif spec.side == "short_only":
        L = np.zeros_like(L)
    ok = np.isfinite(risk) & (risk > 0)
    i = np.flatnonzero((L | S) & ok)
    return Signals(bar=i, dec=b.dec[i], side=np.where(L[i], 1, -1).astype(np.int64), risk=risk[i])
