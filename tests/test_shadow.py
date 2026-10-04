"""Shadow book: trades must reproduce the triple-barrier labels the models were trained on, survive a restart, and
feed the promotion gates and the CUSUM watch."""
import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.store import Store
from goldbot.engine.shadow import ShadowBook
from goldbot.labels.triple_barrier import BarrierSpec, triple_barrier
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
    labels = triple_barrier(bars, signals, spec, atr).set_index("idx")

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
    barriers = set()
    for idx, t in opened.items():
        lab = labels.loc[idx]
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
