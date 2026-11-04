"""Live 2026-11-04 (icm-probe, 20 books): "pass budget 5.0 s spent after 8|16|19 of 20 books". Measured with
scripts/bench_account_pass.py: a 15m close's pass was mostly feature builds no book without agents reads, the
resampler's per-row Timestamp check and the history-gap scan of the same weekend holes at every close. The fast paths
must decide exactly what the code they replace decided: every test here compares with the reference behaviour."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data import resample
from goldbot.data.calendar import DEFAULT_SESSIONS
from goldbot.data.resample import ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine import Engine, EngineConfig, books, exploration, frames, history
from goldbot.engine.books import BookSpec, using
from goldbot.execution.broker import AccountInfo, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.instruments import load_instruments
from goldbot.ops.accounts import load_accounts
from goldbot.ops.services.engine import engine_pass
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES
from goldbot.risk import RiskLimits
from goldbot.specialists.trend import TrendSpecialist

START = pd.Timestamp("2025-03-05 10:13:30", tz="UTC")     # a Wednesday; two 15m closes follow
KEYS = ("XAGUSD", "US500")


class _Terminal(PaperBroker):
    """Paper terminal, demo trade mode, every symbol quoted by set_quote (the engine's tick hook does nothing)."""

    def account(self) -> AccountInfo:
        return super().account().model_copy(update={"trade_mode": "demo"})

    def on_tick(self, tick: Tick) -> None:
        return None


@pytest.fixture(scope="module")
def history_1m() -> dict[int, pd.DataFrame]:
    """Each book's 1m history (a weekend hole and a midweek one inside) and its live quotes, one a second."""
    out = {}
    for k in range(3):
        h = ticks_to_1m(synthetic_ticks("2025-02-26 00:00+00:00", START.floor("min").isoformat(), ticks_per_minute=1, seed=40 + k))
        hole = (h["ts_utc"] >= pd.Timestamp("2025-03-04 13:00", tz="UTC")) & \
               (h["ts_utc"] < pd.Timestamp("2025-03-04 15:00", tz="UTC"))
        out[k] = h[~hole].reset_index(drop=True)
        out[10 + k] = synthetic_ticks(START.floor("min").isoformat(), (START + pd.Timedelta(minutes=40)).isoformat(),
                                      ticks_per_minute=60, seed=60 + k)
    return out


def _engine(tmp_path: Path, bars: dict[int, pd.DataFrame]) -> tuple[Engine, _Terminal]:
    """icm-probe's own exploration arms on gold and two of its instruments; gold also has an agent (its frames'
    features are read), the other books have none."""
    acc = load_accounts(logins=False)["icm-probe"]
    cfg = exploration.config_for(acc)
    assert cfg is not None
    reg = load_instruments()
    specs = tuple(BookSpec(key=k, symbol=k, store_symbol=reg.get(k).store_symbol, magic_block=reg.get(k).magic_block,
                           digits=2, point=0.01, contract_size=reg.get(k).contract_size,
                           exploration_config=exploration.book_config(cfg, k), instrument=reg.get(k)) for k in KEYS)
    pb = _Terminal(equity=10_000, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-probe", broker_name="icm", mode="demo", magic_base=260500,
                              approval_mode="auto", declared_class="raw", state_dir=str(tmp_path), probe_only=True,
                              exploration_config=exploration.book_config(cfg, "XAUUSD"), instruments=specs),
                 pb, [TrendSpecialist()], {},
                 limits=RiskLimits(probe_only=True, exploration_arms={a.arm_id: 1_000.0 for a in cfg.arms},
                                   max_positions=50, daily_cap=0.5, max_risk_per_trade=0.2))
    eng.clock = lambda: START
    for k, sym in enumerate(eng.books):
        with using(eng, eng.books[sym]):
            eng.bars_1m = bars[k].copy()
            eng._closed_through = START.floor("min")
    return eng, pb


def _session(tmp_path: Path, bars: dict[int, pd.DataFrame], minutes: int = 33, every_s: int = 2) -> dict[str, Any]:
    """One account pass every `every_s` seconds for `minutes`; what the engine decided, sent and holds at the end."""
    eng, pb = _engine(tmp_path, bars)
    feed = books.QuoteFeed(pb)
    live = {sym: bars[10 + k] for k, sym in enumerate(eng.books)}
    clock = [START]
    eng.clock = lambda: clock[0]
    for s in range(0, minutes * 60, every_s):
        now = clock[0] = START + pd.Timedelta(seconds=s)
        for sym, q in live.items():
            i = max(int(pd.DatetimeIndex(q["ts_utc"]).searchsorted(now, side="right")) - 1, 0)
            pb.set_quote(sym, Tick(ts_utc=now, bid=float(q["bid"].iloc[i]), ask=float(q["ask"].iloc[i])))
        books.account_pass(eng, pb, engine_pass, feed, budget_s=1e9)
    out: dict[str, Any] = {"positions": sorted((p.symbol, p.side, p.lots, p.open_price, p.sl, p.tp, p.magic)
                                               for p in pb.account_positions()),
                           "deals": pb.deals_since(START - pd.Timedelta(days=1)).reset_index(drop=True)}
    for sym, b in eng.books.items():
        d = b.__dict__
        out[sym] = {"decisions": [{k: v for k, v in r.items() if not (k == "ts" and isinstance(v, float))}
                                  for r in d["decisions"]],
                    "open": sorted((pid, t.agent_id, t.side, t.lots, t.entry) for pid, t in d["open"].items()),
                    "gaps": dict(d["history"].gaps),
                    "queue": list(d["exploration"].queue) if d.get("exploration") is not None else None}
    return out


# ------------------------------------------------------------------------------------------------ references
def _eager_frame(eng: Engine, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> frames.Frame | None:
    """The frame as it was built before 2026-11-04: features at once, read or not."""
    fr = _reference_frame(eng, complete, tf, close_ts)
    return None if fr is None else frames.Frame(dec=fr[0], m=fr[1], atr=fr[3], features=lambda: fr[2])


def _reference_frame(eng: Engine, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> tuple | None:
    from goldbot.data.resample import mid
    from goldbot.features.technical import atr
    dec = eng._resample(complete, tf)
    dec = dec[dec["visible_at"] <= close_ts].tail(eng.cfg.feature_window).reset_index(drop=True)
    if len(dec) < 120:
        return None
    m = mid(dec)
    X = build_features(m, DEFAULT_FEATURE_NAMES, ctx=books.feature_ctx(eng))
    version = X.attrs["feature_version"]
    for ctf in context_tfs(tf):
        hit = eng._context(complete, ctf, close_ts)
        if hit is not None:
            X = merge_higher_tf(X, hit[1], hit[0], TF_LABEL[ctf])
    X.attrs["feature_version"] = version
    return dec, m, X, atr(m, 14)


def _reference_gaps(eng: Engine, complete: pd.DataFrame) -> list[history.HistoryGap]:
    return history.open_gaps(pd.DatetimeIndex(complete["ts_utc"]), eng.sessions,
                             max_open_min=eng.cfg.history_gap_max_min, covered=eng.history.covered)


def _reference_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resample, "_comparable", lambda col: col.to_numpy())
    monkeypatch.setattr(frames, "frame", _eager_frame)
    monkeypatch.setattr(history, "window_gaps", _reference_gaps)


# ------------------------------------------------------------------------------------------------ tests
def test_a_multi_book_session_decides_and_trades_exactly_as_the_reference_paths(tmp_path, history_1m, monkeypatch):
    """Two 15m closes and 33 minute closes on gold and two instruments, icm-probe's arms, entries through the RiskGate
    and the paper terminal: every journal row, open trade, position, gap and queued signal is the same."""
    fast = _session(tmp_path / "fast", history_1m)
    with monkeypatch.context() as m:
        _reference_paths(m)
        ref = _session(tmp_path / "ref", history_1m)
    deals = fast.pop("deals")
    pd.testing.assert_frame_equal(deals, ref.pop("deals"), check_exact=True)   # NaN where NaN
    assert fast == ref
    sent = [d for s in ("XAUUSD", *KEYS) for d in fast[s]["decisions"] if d.get("action") == "exploration:executed:auto"]
    assert len(sent) >= 3 and len(deals) >= 2 * len(sent)        # it traded: entries and their exits


def test_a_book_without_agents_builds_no_features_at_its_bar_close(tmp_path, history_1m, monkeypatch):
    eng, pb = _engine(tmp_path, history_1m)
    built: list[str] = []
    real = build_features

    def counted(*a: Any, **k: Any) -> pd.DataFrame:
        built.append(eng.cfg.symbol)
        return real(*a, **k)
    monkeypatch.setattr(frames, "build_features", counted)
    close = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    for sym in eng.books:
        with using(eng, eng.books[sym]):
            pb.set_quote(sym, Tick(ts_utc=close, bid=100.0, ask=100.1))
            eng.on_bar_close(close, Tick(ts_utc=close, bid=100.0, ask=100.1))
    assert set(built) == {"XAUUSD"}                     # gold's agent reads them; XAGUSD and US500 have no agent


def test_a_frame_read_builds_exactly_the_features_the_eager_build_returned(tmp_path, history_1m):
    eng, _ = _engine(tmp_path, history_1m)
    close = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    for sym in ("XAUUSD", "XAGUSD"):
        with using(eng, eng.books[sym]):
            complete = eng.bars_1m[eng.bars_1m["visible_at"] <= close]
            fr = eng._frame(complete, "15m", close)
            eng._ctx_cache.clear()
            ref = _reference_frame(eng, complete, "15m", close)
            assert fr is not None and ref is not None
            pd.testing.assert_frame_equal(fr.dec, ref[0], check_exact=True)
            pd.testing.assert_frame_equal(fr.X, ref[2], check_exact=True)
            assert fr.X is fr.X and fr.X.attrs == ref[2].attrs          # built once, stamped as research stamps it
            pd.testing.assert_series_equal(fr.atr, ref[3], check_exact=True)


def test_history_gaps_at_every_close_equal_a_fresh_scan(history_1m):
    """The holes already judged are reused; a new hole, another session table or a new covered span is judged again."""
    bars = history_1m[0]
    ts = pd.DatetimeIndex(bars["ts_utc"])
    memo = history.GapMemo()
    for end in (len(ts) - 500, len(ts) - 200, len(ts)):
        got = history.open_gaps(ts[:end], DEFAULT_SESSIONS, max_open_min=60, memo=memo)
        assert got == history.open_gaps(ts[:end], DEFAULT_SESSIONS, max_open_min=60)
    assert any(g.start.day == 4 for g in got)                    # the midweek hole is one
    covered = [(pd.Timestamp("2025-03-04 12:00", tz="UTC"), pd.Timestamp("2025-03-04 16:00", tz="UTC"))]
    got = history.open_gaps(ts, DEFAULT_SESSIONS, max_open_min=60, covered=covered, memo=memo)
    assert got == history.open_gaps(ts, DEFAULT_SESSIONS, max_open_min=60, covered=covered)
    assert not any(g.start.day == 4 for g in got)                # the covered span explains it
    always = DEFAULT_SESSIONS.model_copy(update={"always_open": True})
    got = history.open_gaps(ts, always, max_open_min=60, memo=memo)
    assert got == history.open_gaps(ts, always, max_open_min=60)


def test_history_gaps_keep_only_the_holes_still_in_the_window(history_1m):
    ts = pd.DatetimeIndex(history_1m[0]["ts_utc"])
    memo = history.GapMemo()
    history.open_gaps(ts, DEFAULT_SESSIONS, max_open_min=60, memo=memo)
    held = len(memo.holes)
    history.open_gaps(ts[-3000:], DEFAULT_SESSIONS, max_open_min=60, memo=memo)
    assert 0 < held and len(memo.holes) < held


def test_window_gaps_reuse_the_books_memo(tmp_path, history_1m, monkeypatch):
    eng, _ = _engine(tmp_path, history_1m)
    calls: list[int] = []
    real = DEFAULT_SESSIONS.__class__.expected_open

    def counted(self: Any, minutes: Any) -> np.ndarray:
        calls.append(len(minutes))
        return real(self, minutes)
    monkeypatch.setattr(DEFAULT_SESSIONS.__class__, "expected_open", counted)
    complete = eng.bars_1m
    first = history.window_gaps(eng, complete)
    n = len(calls)
    assert n > 0 and history.window_gaps(eng, complete) == first and len(calls) == n
