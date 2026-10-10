"""Exit policies (design: Specialists table, Exit column; M3 trend trail, M6 breakout scale-out and trail, M7
session-open hard flat; R21 blackout early close). The same mechanics run in the labels, the shadow book and the
live engine, so research numbers describe live trading: each policy is checked label vs shadow on random paths and
label vs the live engine on the paper broker along a scripted path. A family without a policy keeps its labels
byte for byte."""
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.engine.runner import _Frame
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.broker import OrderResult, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.labels.exit_policy import ExitPolicy
from goldbot.labels.triple_barrier import BarrierSpec, one_at_a_time, triple_barrier
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.breakout import BreakoutSpecialist
from goldbot.specialists.session_open import SessionOpenSpecialist
from goldbot.specialists.trend import TrendSpecialist
from goldbot.telegram.approvals import Proposal

TREND = TrendSpecialist()
BREAKOUT = BreakoutSpecialist()
SESSION = SessionOpenSpecialist()


def _random_bars(n: int = 400, seed: int = 0, vol: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    mid = 2400 + np.cumsum(rng.normal(0, 1.2 * vol, n))
    hi = mid + rng.uniform(0.2, 2.5, n) * vol
    lo = mid - rng.uniform(0.2, 2.5, n) * vol
    close = mid + rng.normal(0, 0.5, n)
    spread = 0.2
    ts = pd.date_range("2025-03-03 00:00", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "bid_high": hi, "bid_low": lo, "bid_close": close,
                         "ask_high": hi + spread, "ask_low": lo + spread, "ask_close": close + spread})


def _digest(df: pd.DataFrame) -> str:
    num = df.select_dtypes("number").columns
    out = df.copy()
    out[num] = out[num].round(8)
    return hashlib.sha256(out.to_csv(index=False).encode()).hexdigest()


# ---------------------------------------------------------------------------------------------- no policy: unchanged
def test_labels_without_a_policy_are_identical_to_the_plain_barriers():
    """Families whose policy did not change must not see their research shift: no policy (or an empty one) takes the
    original code path. The digest was computed with triple_barrier as it was before exit policies existed."""
    bars = _random_bars(seed=3)
    spec = BarrierSpec(target_atr=1.5, stop_atr=1.0, max_bars=16)
    atr = pd.Series(np.full(len(bars), 2.0))
    sig = pd.DataFrame({"idx": np.arange(10, 380, 5), "side": np.where(np.arange(10, 380, 5) % 3 == 0, -1, 1)})
    plain = triple_barrier(bars, sig, spec, atr)
    assert _digest(plain) == PLAIN_DIGEST
    pd.testing.assert_frame_equal(triple_barrier(bars, sig, spec, atr, policy=None), plain)
    pd.testing.assert_frame_equal(triple_barrier(bars, sig, spec, atr, policy=ExitPolicy()), plain)
    assert "scale_exit" not in plain.columns


PLAIN_DIGEST = "10ed81c6f2f2731b22861707b6466cdfcb57d6223b8f4395dc76678de497ebf8"


def test_families_without_a_declared_policy_keep_the_plain_barriers():
    for name in ("time_series_momentum", "mean_reversion", "intraday_momentum"):
        cls = next(c for c in SPECIALISTS.values() if c.__module__.endswith(name))
        assert cls().exit_spec is None, name
    assert TREND.exit_spec == ExitPolicy(trail_atr=1.5, trail_after_atr=1.25)                                   # M3
    assert BREAKOUT.exit_spec == ExitPolicy(scale_atr=1.0, scale_fraction=0.5, trail_atr=1.0, trail_after_atr=1.0)  # M6
    assert SESSION.exit_spec.flat_before_min == 60                                                              # M7
    assert TREND.exit_policy()["trail_atr"] == 1.5 and BREAKOUT.exit_policy()["scale_atr"] == 1.0


# ---------------------------------------------------------------------------------------------- label vs shadow
@pytest.mark.parametrize("policy,expect", [
    (TREND.exit_spec, {"trail"}),
    (BREAKOUT.exit_spec, {"trail"}),
    (SESSION.exit_spec, {"flat"}),
])
@pytest.mark.parametrize("seed,vol", [(0, 1.0), (1, 1.0), (2, 0.15), (3, 0.15), (4, 0.5)])
def test_shadow_trades_reproduce_policy_labels(tmp_path, policy, expect, seed, vol):
    bars = _random_bars(seed=seed, vol=vol)
    atr = pd.Series(np.full(len(bars), 2.0))
    spec = BarrierSpec(target_atr=2.5, stop_atr=1.25, max_bars=16)
    idx = np.arange(20, 360, 7)
    signals = pd.DataFrame({"idx": idx, "side": np.where(idx % 2 == 0, 1, -1)})
    labels = one_at_a_time(triple_barrier(bars, signals, spec, atr, policy=policy)).set_index("idx")

    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    sig = dict(zip(signals["idx"], signals["side"]))
    opened = {}
    for i in range(len(bars)):
        bar = bars.iloc[i]
        book.on_bar(bar)
        if i in sig:
            opened[i] = book.open_trade(version="v1", agent_id="a", side=int(sig[i]), bar_ts=bar["ts_utc"],
                                        entry=float(bar["ask_close"] if sig[i] > 0 else bar["bid_close"]), atr_usd=2.0,
                                        target_atr=2.5, stop_atr=1.25, max_bars=16, p=0.6, policy=policy)
        if i == 200:                       # a restart mid-way: policy state survives the book file
            book.save(bar["ts_utc"])
            book = ShadowBook(tmp_path)
    taken = {i for i, t in opened.items() if t is not None}
    assert taken == set(labels.index)
    closed = {t.entry_ts: t for t in book.books["v1"].closed}
    barriers = set()
    for i in taken:
        lab = labels.loc[i]
        t = closed.get(bars["ts_utc"].iloc[i])
        if t is None:                     # still open at the end of the path: the label was cut at the last bar
            assert lab["t_exit"] == len(bars) - 1
            continue
        assert t.barrier == lab["barrier_hit"], i
        assert t.exit == pytest.approx(lab["exit"]) and t.ret == pytest.approx(lab["ret"]) and t.exit_ts == lab["ts_exit"]
        assert t.state is not None and (t.state.scale_exit is None) == bool(np.isnan(lab["scale_exit"]))
        barriers.add(t.barrier)
    if vol >= 0.5 or expect == {"flat"}:
        assert barriers & expect, barriers         # the policy's own exit is exercised


def test_breakout_scale_out_is_taken_and_weighted_in_the_return():
    bars = _random_bars(seed=4, vol=0.5)
    atr = pd.Series(np.full(len(bars), 2.0))
    spec = BarrierSpec(target_atr=2.0, stop_atr=1.0, max_bars=24)
    idx = np.arange(20, 360, 7)
    lab = triple_barrier(bars, pd.DataFrame({"idx": idx, "side": 1}), spec, atr, policy=BREAKOUT.exit_spec)
    scaled = lab[lab["scale_exit"].notna()]
    assert len(scaled) and (scaled["barrier_hit"] != "stop").all()     # once scaled, the rest is trailed at >= entry
    r = 0.5 * (scaled["scale_exit"] - scaled["entry"]) / scaled["entry"] + 0.5 * (scaled["exit"] - scaled["entry"]) / scaled["entry"]
    np.testing.assert_allclose(scaled["ret"], r)
    assert (scaled["ret"] > 0).all()


# ---------------------------------------------------------------------------------------------- mechanics
def test_session_open_hard_flat_is_one_hour_before_the_next_session_open():
    p = SESSION.exit_spec
    # London trade, winter: New York opens 13:30 UTC -> flat 12:30 UTC
    assert p.flat_deadline(pd.Timestamp("2025-03-03 08:30", tz="UTC")) == pd.Timestamp("2025-03-03 12:30", tz="UTC")
    # US on summer time, UK not yet (10 March 2025): New York opens 12:30 UTC -> flat 11:30 UTC
    assert p.flat_deadline(pd.Timestamp("2025-03-10 08:30", tz="UTC")) == pd.Timestamp("2025-03-10 11:30", tz="UTC")
    # New York trade: the next open is Asia (08:00 Tokyo = 23:00 UTC) -> flat 22:00 UTC
    assert p.flat_deadline(pd.Timestamp("2025-03-03 14:00", tz="UTC")) == pd.Timestamp("2025-03-03 22:00", tz="UTC")
    # Friday New York trade: the next open is Monday's Asia open (Sunday 23:00 UTC)
    assert p.flat_deadline(pd.Timestamp("2025-03-07 14:00", tz="UTC")) == pd.Timestamp("2025-03-09 22:00", tz="UTC")
    assert ExitPolicy(trail_atr=1.0).flat_deadline(pd.Timestamp("2025-03-03", tz="UTC")) is None


def _scripted(rows: list[tuple[float, float, float, float]], start: str, freq: str) -> pd.DataFrame:
    """Zero-spread bars from (open, high, low, close); visible_at is the bar close."""
    ts = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC")
    o, h, lo, c = (np.array(x, dtype=float) for x in zip(*rows))
    return pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(freq), "bid_open": o, "bid_high": h,
                         "bid_low": lo, "bid_close": c, "ask_open": o, "ask_high": h, "ask_low": lo, "ask_close": c})


# signal bar first (entry at its close, 2000.00), ATR 1.0 throughout
TREND_PATH = [(2000, 2000.2, 1999.8, 2000), (2000, 2000.6, 1999.6, 2000.4), (2000.4, 2001.4, 2000.2, 2001.2),
              (2001.2, 2001.8, 2001.0, 2001.5), (2001.5, 2001.6, 2000.0, 2000.2), (2000.2, 2000.5, 1999.0, 1999.5)]
BREAKOUT_PATH = [(2000, 2000.2, 1999.8, 2000), (2000, 2000.5, 1999.5, 2000.3), (2000.3, 2001.3, 2000.1, 2001.1),
                 (2001.1, 2001.2, 2000.0, 2000.1), (2000.1, 2000.3, 1998.0, 1998.5)]
SESSION_PATH = [(2000, 2000.2, 1999.8, 2000)] + [(2000 + 0.1 * k, 2000.3 + 0.1 * k, 1999.8 + 0.1 * k, 2000.1 + 0.1 * k)
                                                for k in range(10)]
LIVE_CASES = {
    "trend": (TREND, TREND_PATH, "2025-03-03 08:00", "1h", "trail", 0.3 / 2000),
    "breakout": (BREAKOUT, BREAKOUT_PATH, "2025-03-03 08:00", "1h", "trail", 0.5 * 1.0 / 2000 + 0.5 * 0.3 / 2000),
    # signal bar 10:45-11:00 UTC (London session); flat at the close of the 12:15 bar (New York opens 13:30 UTC)
    "session_open": (SESSION, SESSION_PATH, "2025-03-03 10:45", "15min", "flat", None),
}


def _ticks(bar: pd.Series, close_ts: pd.Timestamp) -> list[Tick]:
    """The bar's path in 0.01 steps: open -> low -> high -> close on an up bar, open -> high -> low -> close on a down
    bar, so every level in the range is printed (no gaps through a stop or a level)."""
    o, h, lo, c = bar["bid_open"], bar["bid_high"], bar["bid_low"], bar["bid_close"]
    legs = (o, lo, h, c) if c >= o else (o, h, lo, c)
    px: list[float] = []
    for a, b in zip(legs[:-1], legs[1:]):
        n = int(round(abs(b - a) / 0.01))
        px += [round(a + (b - a) * k / max(n, 1), 2) for k in range(n)]
    px.append(round(c, 2))
    start = close_ts - pd.Timedelta(minutes=14)
    step = pd.Timedelta(minutes=13) / len(px)
    return [Tick(ts_utc=start + k * step, bid=p, ask=p) for k, p in enumerate(px)]


def _forbid_approvals(eng: Engine) -> None:
    def boom(*_: Any, **__: Any) -> None:
        raise AssertionError("an exit consulted the approval queue")
    for name in ("propose", "decide", "poll_bus", "sweep_expired"):
        setattr(eng.center, name, boom)


def _engine(tmp_path: Path, agent: Any, model: Any = None, **cfg: Any) -> tuple[Engine, PaperBroker]:
    pb = PaperBroker(equity=10_000, commission_per_lot_side=0.0, adverse_slip_points=0.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=111, **cfg),
                 pb, [agent], {agent.family: model} if model is not None else {})
    return eng, pb


def _enter(eng: Engine, pb: PaperBroker, agent: Any, at: pd.Timestamp, price: float, lots: float = 0.1,
           side: int = 1) -> int:
    """Open one trade of `agent` at `price` (ATR 1.0) through the engine's order path; returns its position id."""
    pb.on_tick(Tick(ts_utc=at, bid=price, ask=price))
    ls = agent.label_spec
    stop, target = price - side * ls.stop_atr, price + side * ls.target_atr
    prop = Proposal(proposal_id=f"icm-demo-{int(at.timestamp())}-x", account_id="icm-demo", agent_id=agent.agent_id,
                    side=side, lots=lots, entry=price, stop=stop, target=target, p=0.6,
                    ev_r=0.1, spread_points=0.0, top_features=[])
    before = set(eng.open)
    eng._execute(prop, agent, lots, stop, target, requested=price, atr_usd=1.0)
    (pid,) = set(eng.open) - before
    return pid


def _mirror(rows: list[tuple[float, float, float, float]]) -> list[tuple[float, float, float, float]]:
    """The same path reflected about 2000 (a rally becomes a sell-off): the short-side twin of a long case."""
    return [(4000 - o, 4000 - lo, 4000 - h, 4000 - c) for o, h, lo, c in rows]


def _run_live(tmp_path: Path, agent: Any, bars: pd.DataFrame, side: int) -> tuple[Engine, PaperBroker, int, int]:
    """Enter at the first bar's close, then drive the engine as `on_tick` does (broker fills, per-tick stop check,
    scale-out) and as `on_bar_close` does (policies at the agent's bar close) until the paper position is gone."""
    eng, pb = _engine(tmp_path, agent)
    _forbid_approvals(eng)
    pid = _enter(eng, pb, agent, pd.Timestamp(bars["visible_at"].iloc[0]), 2000.0, side=side)
    tf = agent.timeframe
    for j in range(1, len(bars)):
        close_ts = pd.Timestamp(bars["visible_at"].iloc[j])
        for t in _ticks(bars.iloc[j], close_ts):
            pb.on_tick(t)
            eng._stop_check(t)
            eng._scale_out(t)
        pb.on_tick(Tick(ts_utc=close_ts, bid=float(bars["bid_close"].iloc[j]), ask=float(bars["ask_close"].iloc[j])))
        eng._manage_policies(tf, bars.iloc[: j + 1], close_ts)
        if not pb.positions():
            break
    return eng, pb, pid, j


@pytest.mark.parametrize("side", [1, -1])
@pytest.mark.parametrize("name", list(LIVE_CASES))
def test_live_engine_exits_as_the_label_on_a_scripted_path(tmp_path, name, side):
    """The live engine (paper broker, ticks through every price) and the label agree on the exit and the return, long
    and short (the short path is the long one reflected, so the expected return is the same)."""
    agent, rows, start, freq, barrier, ret = LIVE_CASES[name]
    bars = _scripted(rows if side > 0 else _mirror(rows), start, freq)
    lab = triple_barrier(bars, pd.DataFrame({"idx": [0], "side": [side]}), agent.label_spec,
                         pd.Series(np.ones(len(bars))), policy=agent.exit_spec).iloc[0]
    assert lab["barrier_hit"] == barrier
    if ret is not None:
        assert lab["ret"] == pytest.approx(ret)

    eng, pb, pid, j = _run_live(tmp_path, agent, bars, side)
    assert not pb.positions() and j == lab["t_exit"]
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    pnl = float(deals["pnl"].fillna(0).sum())
    assert pnl / (2000.0 * 0.1 * 100) == pytest.approx(lab["ret"], abs=1e-9)
    actions = [d["action"] for d in eng.decisions]
    if name == "breakout":
        assert actions.count("scale_out") == 1 and (deals["type"] == "partial").sum() == 1
    if barrier == "trail":
        assert "trail" in actions and deals["type"].iloc[-1] == "stop"
    else:
        assert "hard_flat" in actions and pid not in eng.open


# trend path whose last bar opens below the 2000.30 trail set at the previous close (a gap through the level)
TREND_GAP_PATH = TREND_PATH[:4] + [(2000.1, 2000.2, 1999.5, 1999.8)]


@pytest.mark.parametrize("side", [1, -1])
def test_a_gap_through_the_trail_exits_on_the_label_bar_but_fills_at_the_market(tmp_path, side):
    """Labels and the shadow book fill a stop at the stop even when the bar opens through it (an optimistic
    assumption, documented in labels.exit_policy); the broker fills a gap at the market. The live engine exits on the
    same bar as the label, at the gap price, so the live return is lower by exactly the gap."""
    bars = _scripted(TREND_GAP_PATH if side > 0 else _mirror(TREND_GAP_PATH), "2025-03-03 08:00", "1h")
    lab = triple_barrier(bars, pd.DataFrame({"idx": [0], "side": [side]}), TREND.label_spec,
                         pd.Series(np.ones(len(bars))), policy=TREND.exit_spec).iloc[0]
    assert lab["barrier_hit"] == "trail" and lab["ret"] == pytest.approx(0.3 / 2000)    # filled at 2000.30
    eng, pb, _, j = _run_live(tmp_path, TREND, bars, side)
    assert not pb.positions() and j == lab["t_exit"]
    deals = pb.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    assert deals["type"].iloc[-1] == "stop"
    ret = float(deals["pnl"].fillna(0).sum()) / (2000.0 * 0.1 * 100)
    assert ret == pytest.approx(0.1 / 2000, abs=1e-9)                                    # filled at the 2000.10 open
    assert ret < lab["ret"]


def test_trail_only_tightens_and_closes_when_price_is_already_through(tmp_path):
    eng, pb = _engine(tmp_path, TREND)
    _forbid_approvals(eng)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, TREND, t0, 2000.0)
    up = _scripted([(2000, 2001.5, 2000, 2001.4)], "2025-03-03 08:00", "1h")
    pb.on_tick(Tick(ts_utc=t0 + pd.Timedelta(hours=1), bid=2001.4, ask=2001.4))
    eng._manage_policies("1h", up, t0 + pd.Timedelta(hours=1))
    assert pb.positions()[0].sl == eng.open[pid].sl == 2000.0          # 2001.5 - 1.5 ATR
    lower = _scripted([(2000, 2001.3, 2000, 2001.0)], "2025-03-03 08:00", "1h")
    eng._manage_policies("1h", lower, t0 + pd.Timedelta(hours=2))     # a frame without the high: never loosens
    assert pb.positions()[0].sl == 2000.0
    higher = pd.concat([up, _scripted([(2001.4, 2003.0, 2001.4, 2001.6)], "2025-03-03 09:00", "1h")], ignore_index=True)
    pb.on_tick(Tick(ts_utc=t0 + pd.Timedelta(hours=2), bid=2001.4, ask=2001.4))   # price under the new 2001.50 stop
    eng._manage_policies("1h", higher, t0 + pd.Timedelta(hours=2))
    assert not pb.positions() and pid not in eng.open
    assert [d["action"] for d in eng.decisions][-1] == "trail_close"


def test_scale_out_never_splits_below_the_minimum_volume_and_runs_once(tmp_path):
    eng, pb = _engine(tmp_path, BREAKOUT)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, BREAKOUT, t0, 2000.0, lots=0.01)
    hit = Tick(ts_utc=t0 + pd.Timedelta(minutes=5), bid=2001.0, ask=2001.0)
    pb.on_tick(hit)
    eng._scale_out(hit)
    eng._scale_out(hit)
    assert pb.positions()[0].lots == 0.01 and eng.open[pid].scaled
    assert [d["action"] for d in eng.decisions if "scale" in d["action"]] == ["scale_out_skipped"]


def test_policy_state_of_an_open_trade_survives_a_restart(tmp_path):
    eng, pb = _engine(tmp_path, SESSION)
    t0 = pd.Timestamp("2025-03-03 11:00", tz="UTC")
    pid = _enter(eng, pb, SESSION, t0, 2000.0)
    again = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [SESSION], {})
    tr = again.open[pid]
    assert tr.policy == SESSION.exit_spec and tr.timeframe == "15m" and tr.entry == 2000.0
    assert tr.flat_at == pd.Timestamp("2025-03-03 12:30", tz="UTC")


def test_policy_trades_on_the_base_clock_keep_the_time_exit(tmp_path):
    eng, pb = _engine(tmp_path, SESSION)
    pid = _enter(eng, pb, SESSION, pd.Timestamp("2025-03-03 14:00", tz="UTC"), 2000.0)
    for _ in range(SESSION.label_spec.max_bars + 1):
        eng._manage_open(pd.DataFrame())
    assert pid not in eng.open and [d["action"] for d in eng.decisions][-1] == "time_exit"


# ---------------------------------------------------------------------------------------------- R21
def _frame(n: int = 5) -> _Frame:
    ts = pd.date_range("2025-03-03 12:00", periods=n, freq="15min", tz="UTC")
    X = pd.DataFrame({"ts_utc": ts, "f1": np.arange(n, dtype=float)})
    return _Frame(dec=pd.DataFrame({"ts_utc": ts}), m=pd.DataFrame(), X=X, atr=pd.Series(np.ones(n)))


@pytest.mark.parametrize("p,event,closed", [
    (0.4, {"title": "US CPI", "ts_utc": "2025-03-03 13:30"}, True),
    (0.6, {"title": "US CPI", "ts_utc": "2025-03-03 13:30"}, False),
    (0.4, {"title": "shock", "received_utc": "2025-03-03 13:30", "kind": "news_shock"}, False),
    (0.4, None, False),
])
def test_blackout_closes_open_trades_whose_rescored_p_is_below_one_half(tmp_path, p, event, closed):
    eng, pb = _engine(tmp_path, SESSION, ConstantModel(p=p), news_blackout=True)
    _forbid_approvals(eng)
    pid = _enter(eng, pb, SESSION, pd.Timestamp("2025-03-03 13:00", tz="UTC"), 2000.0)
    eng._blackout_event = event
    eng._blackout_close(pd.DataFrame(), pd.Timestamp("2025-03-03 13:15", tz="UTC"), {"15m": _frame()})
    assert (pid not in eng.open and not pb.positions()) is closed
    if closed:
        d = eng.decisions[-1]
        assert d["action"] == "blackout_close" and d["p"] == 0.4 and d["event"] == "US CPI"


class _RaisesOnFirstCall:
    """A model whose first prediction raises (a corrupt input, a library error); later ones return p = 0.4."""
    feature_names: list[str] = []
    feature_version = ""

    def __init__(self) -> None:
        self.calls = 0

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        self.calls += 1
        if self.calls == 1:
            raise ValueError("model blew up")
        return np.full(len(X), 0.4)


def test_a_raising_blackout_rescore_keeps_the_trade_and_still_manages_the_others(tmp_path):
    """ops/run.py catches only AssertionError: one bad re-score must not kill the engine for the whole blackout."""
    eng, pb = _engine(tmp_path, SESSION, _RaisesOnFirstCall(), news_blackout=True)
    first = _enter(eng, pb, SESSION, pd.Timestamp("2025-03-03 13:00", tz="UTC"), 2000.0)
    second = _enter(eng, pb, SESSION, pd.Timestamp("2025-03-03 13:01", tz="UTC"), 2000.0)
    eng._blackout_event = {"title": "US CPI", "ts_utc": "2025-03-03 13:30"}
    eng._blackout_close(pd.DataFrame(), pd.Timestamp("2025-03-03 13:15", tz="UTC"), {"15m": _frame()})
    assert first in eng.open and second not in eng.open
    assert [p.position_id for p in pb.positions()] == [first]
    failed = [d for d in eng.decisions if d["action"] == "blackout_rescore_failed"]
    assert len(failed) == 1 and failed[0]["position"] == first and "model blew up" in failed[0]["error"]


# ---------------------------------------------------------------------------------------------- safety review fixes
def _hit(t0: pd.Timestamp, k: int = 0, px: float = 2001.0) -> Tick:
    return Tick(ts_utc=t0 + pd.Timedelta(minutes=5, seconds=k), bid=px, ask=px)


def test_a_failing_symbol_info_does_not_stop_the_engine_and_the_scale_out_is_retried(tmp_path, monkeypatch):
    """MT5's symbol_info can fail (the adapter raises on a None reply). The tick is survived, the trade is kept and
    `scaled` stays false, so the scale-out is taken on a later tick once the broker answers."""
    eng, pb = _engine(tmp_path, BREAKOUT)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, BREAKOUT, t0, 2000.0)
    orig = pb.symbol_info

    def broken(symbol: str) -> Any:
        raise AttributeError("'NoneType' object has no attribute 'filling_mode'")
    monkeypatch.setattr(pb, "symbol_info", broken)
    eng.on_tick(_hit(t0))                                 # the engine's tick entry point: must not raise
    tr = eng.open[pid]
    assert not tr.scaled and tr.scale_tries == 1 and pb.positions()[0].lots == 0.1
    assert [d["action"] for d in eng.decisions if "scale" in d["action"]] == ["scale_out_failed"]
    monkeypatch.setattr(pb, "symbol_info", orig)
    eng.on_tick(_hit(t0, 1))
    assert tr.scaled and pb.positions()[0].lots == pytest.approx(0.05)


def test_scale_out_retries_are_bounded(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, BREAKOUT)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, BREAKOUT, t0, 2000.0)

    def refused(position_id: int, lots: float | None = None) -> OrderResult:
        return OrderResult(ok=False, retcode=10018, order_id=None, position_id=position_id, filled_lots=0.0, price=None)
    monkeypatch.setattr(pb, "close", refused)
    for k in range(eng.cfg.scale_out_retries + 2):
        hit = _hit(t0, k)
        pb.on_tick(hit)
        eng._scale_out(hit)
    tr = eng.open[pid]
    fails = [d for d in eng.decisions if d["action"] == "scale_out_failed"]
    assert tr.scaled and tr.scale_tries == eng.cfg.scale_out_retries == len(fails)
    assert fails[-1]["gave_up"] and not any(d["gave_up"] for d in fails[:-1])


def test_a_scale_out_failure_on_one_position_leaves_the_others_managed(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, BREAKOUT)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    bad = _enter(eng, pb, BREAKOUT, t0, 2000.0)
    good = _enter(eng, pb, BREAKOUT, t0 + pd.Timedelta(seconds=1), 2000.0)
    orig = pb.close

    def close(position_id: int, lots: float | None = None) -> OrderResult:
        if position_id == bad:
            raise RuntimeError("terminal disconnected")
        return orig(position_id, lots)
    monkeypatch.setattr(pb, "close", close)
    hit = _hit(t0)
    pb.on_tick(hit)
    eng._scale_out(hit)
    lots = {p.position_id: p.lots for p in pb.positions()}
    assert lots[bad] == 0.1 and not eng.open[bad].scaled
    assert lots[good] == pytest.approx(0.05) and eng.open[good].scaled


def _reject(calls: list[float | None]) -> Any:
    def modify(position_id: int, sl: float | None, tp: float | None) -> OrderResult:
        calls.append(sl)
        return OrderResult(ok=False, retcode=10016, order_id=None, position_id=position_id, filled_lots=0.0, price=None,
                           message="invalid stops")
    return modify


def test_price_through_the_engine_stop_closes_at_market_when_the_broker_stop_did_not_move(tmp_path, monkeypatch):
    """A rejected trail modify leaves the broker's stop looser than the engine's. Inside the bar, the first tick at or
    through the engine's stop closes the trade at market (on_tick), as the labels exit at that stop."""
    eng, pb = _engine(tmp_path, TREND)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, TREND, t0, 2000.0)
    broker_sl = pb.positions()[0].sl
    monkeypatch.setattr(pb, "modify", _reject([]))
    up = _scripted([(2000, 2001.5, 2000, 2001.4)], "2025-03-03 08:00", "1h")
    pb.on_tick(Tick(ts_utc=t0 + pd.Timedelta(hours=1), bid=2001.4, ask=2001.4))
    eng._manage_policies("1h", up, t0 + pd.Timedelta(hours=1))
    assert broker_sl is not None and broker_sl < 2000.0
    assert eng.open[pid].sl == 2000.0 and pb.positions()[0].sl == broker_sl
    for k, px in enumerate((2000.6, 2000.2, 2000.0, 1999.8)):
        eng.on_tick(Tick(ts_utc=t0 + pd.Timedelta(hours=1, minutes=1 + k), bid=px, ask=px))
        if k < 2:
            assert pb.positions()
    assert not pb.positions() and pid not in eng.open
    deals = pb.deals_since(t0)
    assert deals["type"].iloc[-1] == "close" and deals["price"].iloc[-1] == 2000.0
    d = next(d for d in eng.decisions if d["action"] == "engine_stop_close")
    assert d["sl"] == 2000.0 and d["broker_sl"] == broker_sl


def test_the_per_tick_stop_check_leaves_a_trade_to_the_broker_when_its_stop_is_in_place(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, TREND, t0, 2000.0)
    closes: list[int] = []
    monkeypatch.setattr(pb, "close", lambda position_id, lots=None: closes.append(position_id))
    sl = pb.positions()[0].sl
    assert sl is not None
    eng._stop_check(Tick(ts_utc=t0 + pd.Timedelta(minutes=1), bid=sl, ask=sl))   # the server stop executes it
    assert closes == [] and pid in eng.open


def test_reconcile_backs_off_on_repeated_modify_rejects_and_resets_on_success(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path, TREND)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, TREND, t0, 2000.0)
    eng.open[pid].sl = 1999.5                              # tighter than the broker's: reconcile re-sends it
    orig = pb.modify
    calls: list[float | None] = []
    monkeypatch.setattr(pb, "modify", _reject(calls))
    for k in range(20):                                    # a pass every 30 s for 10 minutes
        eng._reconcile(t0 + pd.Timedelta(seconds=30 * k))
    assert len(calls) == 5                                 # at 0, 30, 90, 210, 450 s: 30 s doubling, not 20 rejects
    assert sum(d["action"] == "reinstate_stops" for d in eng.decisions) == 5
    monkeypatch.setattr(pb, "modify", orig)
    eng._reconcile(t0 + pd.Timedelta(seconds=600))         # still backing off (next try at 930 s)
    assert pb.positions()[0].sl != 1999.5
    eng._reconcile(t0 + pd.Timedelta(seconds=930))
    assert pb.positions()[0].sl == 1999.5 and pid not in eng._modify_backoff


@pytest.mark.parametrize("engine_sl,expect", [(2001.0, 2001.0), (2000.5, 2000.8)])
def test_the_weekend_rule_never_loosens_the_engine_stop(tmp_path, engine_sl, expect):
    """Friday cut: a winner's stop goes to half the open profit (2000.80 here) or stays at the engine's tighter stop
    (kept after a rejected modify), whichever is more favourable; the broker is sent the same level."""
    eng, pb = _engine(tmp_path, TREND)
    t0 = pd.Timestamp("2025-03-07 19:00", tz="UTC")
    pid = _enter(eng, pb, TREND, t0, 2000.0)
    eng.open[pid].sl = engine_sl
    tick = Tick(ts_utc=t0 + pd.Timedelta(minutes=30), bid=2001.6, ask=2001.6)
    pb.on_tick(tick)
    eng._weekend_rule(tick)
    assert eng.open[pid].sl == expect and pb.positions()[0].sl == expect


def _orders(tmp_path: Path) -> Path:
    return tmp_path / "orders_icm-demo.json"


@pytest.mark.parametrize("recorded_atr", [True, False])
def test_a_trade_recovered_from_an_unconfirmed_send_keeps_its_agents_exit_policy(tmp_path, recorded_atr):
    """A crash between order_send and its result: the restart finds the fill by client id and restores the agent's
    policy, with the ATR recorded before sending (or, for an older row, the one implied by the initial stop)."""
    eng, pb = _engine(tmp_path, BREAKOUT)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, BREAKOUT, t0, 2000.0)
    d = json.loads(_orders(tmp_path).read_text())
    for row in d["sent"].values():
        row.update(status="sending", position_id=None)
        if not recorded_atr:
            row.pop("atr_usd")
    d["open"] = {}
    _orders(tmp_path).write_text(json.dumps(d))
    again = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), pb, [BREAKOUT], {})
    tr = again.open[pid]
    assert tr.agent_id == BREAKOUT.agent_id and tr.policy == BREAKOUT.exit_spec and tr.timeframe == "1h"
    assert tr.entry == 2000.0 and tr.atr_usd == pytest.approx(1.0) and tr.opened_utc == t0 and not tr.scaled


def test_an_adopted_orphan_of_a_known_agent_gets_its_policy_and_an_unknown_one_none(tmp_path):
    eng, pb = _engine(tmp_path, BREAKOUT)
    t0 = pd.Timestamp("2025-03-03 08:00", tz="UTC")
    pid = _enter(eng, pb, BREAKOUT, t0, 2000.0)
    known = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path / "a")), pb,
                   [BREAKOUT], {})                         # a fresh state dir: no orders file, the position is an orphan
    known._reconcile(t0)
    tr = known.open[pid]
    assert tr.agent_id == BREAKOUT.agent_id and tr.policy == BREAKOUT.exit_spec and tr.atr_usd == pytest.approx(1.0)
    assert tr.entry == 2000.0 and tr.timeframe == "1h"
    unknown = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path / "b")), pb, [], {})
    unknown._reconcile(t0)
    assert unknown.open[pid].agent_id == "orphan" and unknown.open[pid].policy is None
