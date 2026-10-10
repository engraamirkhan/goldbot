"""Shadow book: trades must reproduce the triple-barrier labels the models were trained on, survive a restart, and
feed the promotion gates and the CUSUM watch."""
import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowBook
from goldbot.labels.triple_barrier import BarrierSpec, one_at_a_time, triple_barrier
from goldbot.ops.jobs import JobContext, model_watch
from goldbot.research.model import MetaLabelModel
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.promotion import PerfStats, cusum_alarm
from goldbot.research.registry import TrialRegistry


def _bars(n: int = 400, seed: int = 0, vol: float = 1.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    mid = 2400 + np.cumsum(rng.normal(0, 1.2 * vol, n))
    hi = mid + rng.uniform(0.2, 2.5, n) * vol
    lo = mid - rng.uniform(0.2, 2.5, n) * vol
    close = mid + rng.normal(0, 0.5, n)
    spread = 0.2
    ts = pd.date_range("2025-03-03 00:00", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "bid_high": hi, "bid_low": lo, "bid_close": close,
                         "ask_high": hi + spread, "ask_low": lo + spread, "ask_close": close + spread})


@pytest.mark.parametrize("seed,vol", [(0, 1.0), (1, 1.0), (2, 0.15), (3, 0.15)])
def test_shadow_trades_reproduce_triple_barrier_labels(tmp_path, seed, vol):
    bars = _bars(seed=seed, vol=vol)
    atr = pd.Series(np.full(len(bars), 2.0))
    spec = BarrierSpec(target_atr=1.5, stop_atr=1.0, max_bars=16)
    signals = pd.DataFrame({"idx": np.arange(20, 360, 7), "side": np.where(np.arange(20, 360, 7) % 2 == 0, 1, -1)})
    # one position at a time on both sides: the shadow book refuses an entry while the agent's trade is open, the
    # research labels drop the candidates that fire before the previous one exited
    labels = one_at_a_time(triple_barrier(bars, signals, spec, atr)).set_index("idx")

    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    sig = dict(zip(signals["idx"], signals["side"]))
    opened = {}
    for i in range(len(bars)):
        bar = bars.iloc[i]
        book.on_bar(bar)                         # engine order: advance open trades, then open new ones at this close
        if i in sig:
            t = book.open_trade(version="v1", agent_id="a", side=int(sig[i]), bar_ts=bar["ts_utc"],
                                entry=float(bar["ask_close"] if sig[i] > 0 else bar["bid_close"]), atr_usd=2.0,
                                target_atr=1.5, stop_atr=1.0, max_bars=16, p=0.6)
            opened[i] = t
    taken = {i for i, t in opened.items() if t is not None}
    assert taken == set(labels.index)
    if vol < 1.0:
        assert len(taken) < len(signals)       # quiet: trades run to the time barrier, so later signals are skipped
    barriers = set()
    for idx in taken:
        t, lab = opened[idx], labels.loc[idx]
        assert t is not None and t.exit is not None, idx
        assert t.barrier == lab["barrier_hit"] and t.exit == pytest.approx(lab["exit"]) and t.ret == pytest.approx(lab["ret"])
        assert t.exit_ts == lab["ts_exit"]
        barriers.add(t.barrier)
    # every exit path is exercised: barriers in the volatile regime, time exits in the quiet one
    assert barriers >= ({"target", "stop"} if vol == 1.0 else {"time"})


def test_book_persists_and_reports_stats(tmp_path):
    bars = _bars()
    book = ShadowBook(tmp_path)
    t0 = bars["ts_utc"].iloc[0]
    book.track("v1", t0)
    book.open_trade(version="v1", agent_id="a", side=1, bar_ts=t0, entry=2400.0, atr_usd=2.0, target_atr=1.5,
                    stop_atr=1.0, max_bars=4, p=0.6)
    assert book.open_trade(version="v1", agent_id="a", side=1, bar_ts=t0, entry=2400.0, atr_usd=2.0, target_atr=1.5,
                           stop_atr=1.0, max_bars=4, p=0.6) is None        # one entry per signal bar
    book.save(t0)
    restarted = ShadowBook(tmp_path)                                         # engine restart keeps open trades
    assert len(restarted.books["v1"].open) == 1
    for i in range(1, 10):
        restarted.on_bar(bars.iloc[i])
    now = t0 + pd.Timedelta(weeks=2)
    restarted.save(now)
    st = PerfStats.model_validate_json((tmp_path / "shadow_v1.json").read_text())
    assert st.n_trades == 1 and st.weeks == pytest.approx(2.0) and st.trades_per_week == pytest.approx(0.5)
    assert restarted.returns_since("v1", t0) == [restarted.books["v1"].closed[0].ret]


def test_trades_count_bars_of_their_own_timeframe(tmp_path):
    bars = _bars(vol=0.01)                                                 # quiet: only the time barrier can close
    book = ShadowBook(tmp_path)
    t0 = bars["ts_utc"].iloc[0]
    book.track("v1", t0)
    book.open_trade(version="v1", agent_id="a", side=1, bar_ts=t0, entry=2400.0, atr_usd=50.0, target_atr=1.5,
                    stop_atr=1.0, max_bars=2, p=0.6, timeframe="1h")
    for i in range(1, 9):
        book.on_bar(bars.iloc[i], "15m")                                    # 15m bars do not age a 1h trade
    assert book.books["v1"].open and book.books["v1"].open[0].bars_held == 0
    hourly = bars.iloc[4::4].reset_index(drop=True)                          # stand-ins for later 1h bars
    for i in range(3):
        book.on_bar(hourly.iloc[i], "1h")
    t = book.books["v1"].closed[0]
    assert t.barrier == "time" and t.bars_held == 3 and t.timeframe == "1h"
    book.save(t0)
    assert ShadowBook(tmp_path).books["v1"].closed[0].timeframe == "1h"     # persisted


def test_cusum_flags_a_drop_but_not_noise():
    rng = np.random.default_rng(4)
    healthy = list(rng.normal(0.001, 0.004, 40))
    broken = list(rng.normal(-0.004, 0.004, 15))
    assert not cusum_alarm(healthy, 0.001, 0.004)
    assert cusum_alarm(broken, 0.001, 0.004)
    assert not cusum_alarm([], 0.001, 0.004) and not cusum_alarm(broken, 0.001, 0.0)


def _watch_ctx(tmp_path) -> JobContext:
    return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "t.jsonl"), accounts=[],
                      population=Population(tmp_path / "population.json"))


def _promote_pair(ctx: JobContext, t: pd.Timestamp) -> tuple[str, str]:
    bt = PerfStats(n_trades=300, sharpe_ann=1.2, hit_rate=0.45, max_dd=0.1, trades_per_week=4, mean_ret=0.001, std_ret=0.004)
    a = ctx.models.add_challenger(MetaLabelModel(feature_names=["x"]), family="session_open", agent_id="agent-x", backtest=bt.model_dump(), now=t)
    ctx.models.promote(a.version, now=t)
    b = ctx.models.add_challenger(MetaLabelModel(feature_names=["x"]), family="session_open", agent_id="agent-x",
                                  backtest=bt.model_dump(), now=t + pd.Timedelta(days=30))
    ctx.models.promote(b.version, now=t + pd.Timedelta(days=30))
    return a.version, b.version


def _shadow_rets(tmp_path, version: str, start: pd.Timestamp, rets: list[float]) -> None:
    book = ShadowBook(tmp_path)
    book.track(version, start)
    for i, r in enumerate(rets):
        ts = start + pd.Timedelta(hours=i + 1)
        t = book.open_trade(version=version, agent_id="b", side=1, bar_ts=ts, entry=2400.0, atr_usd=2.0, target_atr=1.5,
                            stop_atr=1.0, max_bars=16, p=0.6)
        assert t is not None
        ShadowBook._close(t, ts + pd.Timedelta(minutes=30), 2400.0 * (1 + r), "time")
        book.books[version].open.remove(t)
        book.books[version].closed.append(t)
    book.save(start + pd.Timedelta(days=5))


def test_model_watch_restores_the_previous_champion_on_a_cusum_alarm(tmp_path):
    ctx = _watch_ctx(tmp_path)
    t = pd.Timestamp("2026-09-01", tz="UTC")
    a, b = _promote_pair(ctx, t)
    promoted = t + pd.Timedelta(days=30)
    _shadow_rets(tmp_path, b, promoted, [-0.006] * 12)
    out = model_watch(ctx, promoted + pd.Timedelta(days=6))
    assert out["agent-x"]["action"] == "restored_previous"
    assert ctx.models.champion("agent-x").version == a               # type: ignore[union-attr]


def test_model_watch_uses_the_two_week_h_on_two_point_returns_at_the_backtest_hit_rate(tmp_path):
    from goldbot.research import cusum
    ctx = _watch_ctx(tmp_path)
    t = pd.Timestamp("2026-09-01", tz="UTC")
    a, b = _promote_pair(ctx, t)                                     # backtest: hit 0.45, 4 trades a week
    promoted = t + pd.Timedelta(days=30)
    k = ctx.settings.drift.cusum_k
    h_watch = cusum.calibrated_h(4.0, k, p=0.45, weeks=cusum.WATCH_WEEKS)
    h_quarter = cusum.calibrated_h(4.0, k, p=0.45)
    assert h_watch < min(h_quarter, cusum.FALLBACK_H)
    s = (h_watch + min(h_quarter, cusum.FALLBACK_H)) / 2              # one return that only the watch's h catches
    _shadow_rets(tmp_path, b, promoted, [0.001 - (s + k) * 0.004])
    out = model_watch(ctx, promoted + pd.Timedelta(days=3))
    assert out["agent-x"]["action"] == "restored_previous"
    assert ctx.models.champion("agent-x").version == a               # type: ignore[union-attr]


def test_model_watch_leaves_a_healthy_or_settled_champion_alone(tmp_path):
    ctx = _watch_ctx(tmp_path)
    t = pd.Timestamp("2026-09-01", tz="UTC")
    _, b = _promote_pair(ctx, t)
    promoted = t + pd.Timedelta(days=30)
    _shadow_rets(tmp_path, b, promoted, [0.002, -0.003, 0.004, 0.001, -0.002, 0.003])
    assert model_watch(ctx, promoted + pd.Timedelta(days=6))["agent-x"]["action"] == "ok"
    _shadow_rets(tmp_path, b, promoted, [-0.006] * 12)
    assert model_watch(ctx, promoted + pd.Timedelta(days=20)) == {}  # past the two-week window: not watched
    assert ctx.models.champion("agent-x").version == b               # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------- counterfactual (P9)
def test_every_candidate_is_recorded_with_its_decision_and_only_taken_ones_count_as_trades(tmp_path):
    bars = _bars(seed=1)
    atr = pd.Series(np.full(len(bars), 2.0))
    spec = BarrierSpec(target_atr=1.5, stop_atr=1.0, max_bars=16)
    idx = np.arange(20, 360, 7)
    signals = pd.DataFrame({"idx": idx, "side": np.where(idx % 2 == 0, 1, -1)})
    labels = one_at_a_time(triple_barrier(bars, signals, spec, atr)).set_index("idx")
    p_of = {int(i): (0.62 if k % 3 == 0 else 0.41) for k, i in enumerate(idx)}     # a third above the threshold
    book = ShadowBook(tmp_path)
    book.track("v1", bars["ts_utc"].iloc[0])
    sig = dict(zip(signals["idx"], signals["side"]))
    opened = {}
    for i in range(len(bars)):
        bar = bars.iloc[i]
        book.on_bar(bar)
        if i in sig:
            p = p_of[i]
            opened[i] = book.open_trade(version="v1", agent_id="a", side=int(sig[i]), bar_ts=bar["ts_utc"],
                                        entry=float(bar["ask_close"] if sig[i] > 0 else bar["bid_close"]), atr_usd=2.0,
                                        target_atr=1.5, stop_atr=1.0, max_bars=16, p=p, threshold=0.5, taken=p > 0.5,
                                        p_raw=p - 0.01)
    recorded = {i for i, t in opened.items() if t is not None}
    # the whole candidate stream is the research label set (one position at a time over every candidate, as
    # pipeline.prepare thins before the model filters), so the counterfactual outcomes are the labels themselves
    assert recorded == set(labels.index)
    for i in recorded:
        t = opened[i]
        assert t is not None and t.barrier == labels.loc[i, "barrier_hit"] and t.threshold == 0.5
        assert t.p_raw == pytest.approx(t.p - 0.01)
    taken = [t for t in book.books["v1"].closed if t.taken]
    skipped = [t for t in book.books["v1"].closed if not t.taken]
    assert taken and skipped
    # outcomes for recalibration: taken and not taken alike
    assert len(book.outcomes("v1")) == len(book.books["v1"].closed)
    # trading statistics, the CUSUM and the population see only the taken trades
    now = bars["ts_utc"].iloc[-1]
    assert book.stats("v1", now).n_trades == len(taken)
    assert book.returns_since("v1", bars["ts_utc"].iloc[0]) == [t.ret for t in taken]
    from goldbot.research.population import agent_trades
    assert len(agent_trades(book)["a"]) == len(taken)


def test_an_older_book_loads_as_taken_and_only_candidates_with_a_threshold_are_outcomes(tmp_path):
    import json
    t0 = pd.Timestamp("2026-09-01", tz="UTC")
    old_trade = {"version": "v1", "agent_id": "a", "side": 1, "entry_ts": t0.isoformat(), "entry": 2400.0, "stop": 2398.0,
                 "target": 2403.0, "max_bars": 4, "timeframe": "15m", "p": 0.6, "bars_held": 2,
                 "exit_ts": (t0 + pd.Timedelta(minutes=30)).isoformat(), "exit": 2403.0, "barrier": "target",
                 "ret": 3 / 2400}
    (tmp_path / "shadow_book.json").write_text(json.dumps(
        {"v1": {"version": "v1", "started_utc": t0.isoformat(), "open": [], "closed": [old_trade]}}))
    book = ShadowBook(tmp_path)
    t = book.books["v1"].closed[0]
    assert t.taken and t.threshold is None and t.p_raw is None
    assert book.outcomes("v1") == []             # selected trades only: a biased sample, never used for calibration
    assert book.stats("v1", t0 + pd.Timedelta(weeks=1)).n_trades == 1     # but still a shadow trade of its record
    # the engine now records every candidate with its threshold, taken or not
    book.track("v1", t0)
    t1 = t0 + pd.Timedelta(days=3)
    new = book.open_trade(version="v1", agent_id="a", side=1, bar_ts=t1, entry=2400.0, atr_usd=2.0, target_atr=1.5,
                          stop_atr=1.0, max_bars=4, p=0.4, threshold=0.55, taken=False)
    assert new is not None
    ShadowBook._close(new, t1 + pd.Timedelta(minutes=45), new.stop, "stop")
    book.books["v1"].open.remove(new)
    book.books["v1"].closed.append(new)
    book.save(t1 + pd.Timedelta(hours=1))
    again = ShadowBook(tmp_path)
    assert [x.threshold for x in again.outcomes("v1")] == [0.55]
    assert again.outcomes("v1", since=t1 + pd.Timedelta(hours=1)) == []
    assert again.stats("v1", t1 + pd.Timedelta(hours=1)).n_trades == 1
