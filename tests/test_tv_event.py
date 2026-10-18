"""TradingView event specialists (plan docs/plans/tradingview-strategies.md, lane C): a catalogued event plot with a
known side as the primary signal, shadow only; live staleness fails closed; the TV grid is pinned, counted in K and
never dilutes the loop's GRID_V1."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data import tv_catalog as cat
from goldbot.data.store import Store
from goldbot.engine.tv_context import with_tv
from goldbot.features import tv_plots
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.research import generator, tv_grid
from goldbot.research.lot_fit import LotFitSpec
from goldbot.research.registry import TrialRegistry, config_hash, quarter_of, quarter_trials
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import Specialist
from goldbot.specialists.tv_event import MAX_LAG, TvEventSpecialist

H4 = pd.Timedelta(hours=4)
T0 = pd.Timestamp("2026-06-01T00:00:00Z")
N = 60
LAST_OPEN = T0 + (N - 1) * H4
LAST_CLOSE = LAST_OPEN + H4


def meta(tf: str = "240") -> cat.StudyMeta:
    return cat.StudyMeta(study="K-Indi", version="2.02", tf=tf, inputs={"in_0": 50},
                         plots=[cat.StudyPlot(id="plot_1", type="shapes", title="Buy large"),
                                cat.StudyPlot(id="plot_2", type="shapes", title="Signal")])


def buy(tf: str = "240") -> cat.PlotSpec:
    return cat.catalog(meta(tf))[0]


def no_side() -> cat.PlotSpec:
    return cat.catalog(meta())[1]


def bars() -> pd.DataFrame:
    """N 4h mid bars (ATR about $3, price about $2,600): the minimum lot fits a $1,000 account at 0.5 %."""
    opens = pd.DatetimeIndex([T0 + i * H4 for i in range(N)])
    close = 2600.0 + np.sin(np.arange(N)) * 1.0
    return pd.DataFrame({"ts_utc": opens, "visible_at": opens + H4, "open": close - 0.5, "high": close + 1.5,
                         "low": close - 1.5, "close": close, "spread": 0.2, "tick_count": 100})


def tv_rows(bar_open: pd.Timestamp, lag: pd.Timedelta, *, value: float = 1.0, plot: str = "plot_1",
            origin: str = "pull_live") -> pd.DataFrame:
    return pd.DataFrame([{"ts_utc": bar_open, "bar_time": bar_open, "available_utc": bar_open + H4 + lag,
                          "indicator": "K-Indi", "version": "2.02", "tf": "240", "plot": plot, "value": value,
                          "origin": origin, "meta_hash": meta().meta_hash, "intrabar": False,
                          "repaint_validated": False}])


def store_with(tmp_path: Path, rows: pd.DataFrame) -> Store:
    s = Store(tmp_path / "data")
    s.append("tv_indicators", rows, source="tv_pull")
    return s


def live_cands(spec: TvEventSpecialist, store: Any, now: pd.Timestamp) -> pd.DataFrame:
    m = bars()
    X = pd.DataFrame({"ts_utc": m["ts_utc"]})
    return spec.candidates(m, with_tv(spec, m, X, store, now, int(MAX_LAG.total_seconds())))


def entry_on_last_bar(cands: pd.DataFrame) -> bool:
    return not cands.empty and int(cands["idx"].iloc[-1]) == N - 1


# ---------------------------------------------------------------- GRID_V1 untouched


class _TvDummy(Specialist):
    """A constructible TradingView-dependent family: without the exclusion it would enter the loop grid."""
    family = "tv_dummy"
    timeframe = "4h"
    requires_tv = True
    default_config = {"target_atr": 2.0, "stop_atr": 1.0, "max_bars": 12}

    @property
    def label_spec(self) -> BarrierSpec:
        return BarrierSpec(target_atr=2.0, stop_atr=1.0, max_bars=12, name="tv_dummy")

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        return pd.DataFrame({"idx": pd.Series(dtype=int), "side": pd.Series(dtype=int)})


def test_grid_v1_family_set_and_hashes_unchanged(monkeypatch):
    monkeypatch.setitem(SPECIALISTS, "tv_event", TvEventSpecialist)
    monkeypatch.setitem(SPECIALISTS, "tv_dummy", _TvDummy)
    s = load_settings()
    assert generator.grid_members(s) == generator.GRID_V1
    assert {c.family for c in generator.grid(s)} == {f for f, _ in generator.GRID_V1}
    assert "tv_dummy" not in generator._eligible(s) and "tv_event" not in generator._eligible(s)


def test_the_loop_refuses_a_tradingview_family_whatever_its_hash(monkeypatch):
    monkeypatch.setitem(SPECIALISTS, "tv_dummy", _TvDummy)
    s = load_settings()
    cfg = _TvDummy().config
    monkeypatch.setattr(generator, "GRID_V1", generator.GRID_V1 | {("tv_dummy", config_hash(cfg))})
    why = generator.loop_refusal([], [("tv_dummy", cfg)], s)
    assert why is not None and "TV_GRID_V1" in why


# ---------------------------------------------------------------- live staleness fails closed


def test_stale_by_1s_gives_no_entry(tmp_path):
    spec = TvEventSpecialist.from_plot(buy())
    late = MAX_LAG + pd.Timedelta(seconds=1)
    store = store_with(tmp_path, tv_rows(LAST_OPEN, late))
    assert not entry_on_last_bar(live_cands(spec, store, LAST_CLOSE + late))
    # research reads the same rule: a row 61 s after the close never makes a candidate
    m = bars()
    research = spec.candidates_in_context(m, pd.DataFrame({"ts_utc": m["ts_utc"]}), None,
                                          {"tv_indicators": tv_rows(LAST_OPEN, late)})
    assert not entry_on_last_bar(research)


def test_lag_exactly_max_is_accepted(tmp_path):
    spec = TvEventSpecialist.from_plot(buy())
    store = store_with(tmp_path, tv_rows(LAST_OPEN, MAX_LAG))
    cands = live_cands(spec, store, LAST_CLOSE + MAX_LAG)
    assert entry_on_last_bar(cands) and int(cands["side"].iloc[-1]) == 1
    m = bars()
    research = spec.candidates_in_context(m, pd.DataFrame({"ts_utc": m["ts_utc"]}), None,
                                          {"tv_indicators": tv_rows(LAST_OPEN, MAX_LAG)})
    assert entry_on_last_bar(research)


def test_a_value_that_has_not_arrived_at_decision_time_is_not_used(tmp_path):
    spec = TvEventSpecialist.from_plot(buy())
    store = store_with(tmp_path, tv_rows(LAST_OPEN, pd.Timedelta(seconds=30)))
    assert not entry_on_last_bar(live_cands(spec, store, LAST_CLOSE + pd.Timedelta(seconds=10)))
    assert entry_on_last_bar(live_cands(spec, store, LAST_CLOSE + pd.Timedelta(seconds=30)))


def test_the_previous_bars_event_never_triggers_the_decision_bar(tmp_path):
    spec = TvEventSpecialist.from_plot(buy())
    store = store_with(tmp_path, tv_rows(LAST_OPEN - H4, pd.Timedelta(seconds=5)))
    assert not entry_on_last_bar(live_cands(spec, store, LAST_CLOSE + pd.Timedelta(seconds=5)))


def test_a_bar_without_the_mark_gives_no_entry(tmp_path):
    spec = TvEventSpecialist.from_plot(buy())
    store = store_with(tmp_path, tv_rows(LAST_OPEN, pd.Timedelta(seconds=5), value=np.nan))
    assert not entry_on_last_bar(live_cands(spec, store, LAST_CLOSE + pd.Timedelta(seconds=5)))


def test_tv_store_outage_reads_as_missing_never_raises():
    class Down:
        def read(self, *a: Any, **k: Any) -> pd.DataFrame:
            raise OSError("tv_indicators unreadable")

    spec = TvEventSpecialist.from_plot(buy())
    m = bars()
    X = pd.DataFrame({"ts_utc": m["ts_utc"]})
    out = with_tv(spec, m, X, Down(), LAST_CLOSE, 60)          # type: ignore[arg-type]
    assert out[spec.column].isna().all() and spec.candidates(m, out).empty
    assert with_tv(spec, m, X, None, LAST_CLOSE, 60)[spec.column].isna().all()


def test_with_tv_leaves_every_other_agents_frame_untouched(tmp_path):
    m = bars()
    X = pd.DataFrame({"ts_utc": m["ts_utc"]})
    assert with_tv(SPECIALISTS["tsmom"](), m, X, Store(tmp_path), LAST_CLOSE, 60) is X


def test_max_lag_setting_equals_the_feature_allowance():
    from goldbot.engine import EngineConfig
    assert load_settings().tv.max_lag_s == int(tv_plots.MAX_LAG.total_seconds()) == int(MAX_LAG.total_seconds())
    assert EngineConfig.model_fields["tv_max_lag_s"].default == load_settings().tv.max_lag_s


# ---------------------------------------------------------------- the specialist


def test_only_an_event_with_a_known_side_on_the_decision_timeframe_becomes_a_specialist():
    with pytest.raises(ValueError, match="known side"):
        TvEventSpecialist.from_plot(no_side())
    with pytest.raises(ValueError, match="timeframe"):
        TvEventSpecialist.from_plot(buy("60"))                   # a 1h plot on the 4h default
    assert TvEventSpecialist.from_plot(buy("60"), timeframe="1h").timeframe == "1h"
    with pytest.raises(ValueError):
        TvEventSpecialist()                                      # no plot: nothing to trade
    assert TvEventSpecialist.requires_tv and not Specialist.requires_tv


def test_an_unsizable_trade_gives_no_candidate(tmp_path):
    store = store_with(tmp_path, tv_rows(LAST_OPEN, pd.Timedelta(seconds=5)))
    fits = TvEventSpecialist.from_plot(buy())
    assert entry_on_last_bar(live_cands(fits, store, LAST_CLOSE + pd.Timedelta(seconds=5)))
    # $100 at 0.5 %: the 0.01 lot at a $3 stop risks $3, over 1.2 x $0.50
    tiny = TvEventSpecialist.from_plot(buy(), lot_fit=LotFitSpec(equity=100.0))
    assert tiny.agent_id == fits.agent_id                        # the account is not part of the configuration
    assert not entry_on_last_bar(live_cands(tiny, store, LAST_CLOSE + pd.Timedelta(seconds=5)))


def test_the_specialist_is_opt_in():
    assert "tv_event" not in SPECIALISTS


# ---------------------------------------------------------------- TV_GRID_V1


def test_computed_tv_grid_equals_the_frozen_tv_grid_v1():
    """Editing a default or a step of the TV grid changes the computed points; that must fail here and force a new
    dated grid version (TV_GRID_V2), never an edit of TV_GRID_V1."""
    assert tv_grid.grid_points() == tv_grid.TV_GRID_V1
    assert {tf for tf, _ in tv_grid.TV_GRID_V1} == set(tv_grid.SEARCH_TFS) == {"4h", "1d"}


def test_tv_grid_offers_only_trigger_plots_on_their_own_searchable_timeframe():
    s = load_settings()
    h4 = cat.catalog(meta("240"))
    h1 = cat.catalog(meta("60"))
    out = tv_grid.tv_grid(h4 + h1, s)
    assert out and {c.feature_id for c in out} == {h4[0].feature_id}
    assert {c.timeframe for c in out} == {"4h"}
    assert len(out) == len({c.config_hash for c in out}) == sum(tf == "4h" for tf, _ in tv_grid.TV_GRID_V1)
    assert all(tv_grid.tv_refusal([], [(c.family, c.overrides)], s) is None for c in out)


def test_tv_refusal_reads_the_effective_config():
    s = load_settings()
    effective = TvEventSpecialist.from_plot(buy("1D"), timeframe="1d").config
    assert tv_grid.tv_refusal([], [("tv_event", effective)], s) is None
    # the same job as bare overrides: the 1d timeframe defaults fill the rest, so it is the same grid point although
    # its own hash differs (a check on the overrides' hash would refuse it)
    raw = {k: effective[k] for k in tv_grid.PLOT_KEYS} | {"timeframe": "1d"}
    assert config_hash(raw) != config_hash(effective)
    assert tv_grid.tv_refusal([], [("tv_event", raw)], s) is None
    assert tv_grid.tv_refusal([], [("tv_event", {**raw, "stop_atr": effective["stop_atr"] * 1.25})], s) is None


def test_tv_refusal_refuses_off_grid_shadow_timeframes_and_double_dispatch():
    s = load_settings()
    cfg = TvEventSpecialist.from_plot(buy()).config
    off = tv_grid.tv_refusal([], [("tv_event", {**cfg, "stop_atr": 1.1})], s)
    assert off is not None and "not a point of TV_GRID_V1" in off
    shadow = TvEventSpecialist.from_plot(buy("15"), timeframe="15m").config
    why = tv_grid.tv_refusal([], [("tv_event", shadow)], s)
    assert why is not None and "shadow-forward only" in why
    tried = [{"family": "tv_event", "config_hash": config_hash(cfg), "status": "evaluated"}]
    again = tv_grid.tv_refusal(tried, [("tv_event", cfg)], s)
    assert again is not None and "already in the registry" in again
    other = tv_grid.tv_refusal([], [("tsmom", SPECIALISTS["tsmom"]().config)], s)
    assert other is not None and "not a TradingView family" in other


def test_tv_trials_counted_in_k(tmp_path):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record(agent_id="tsmom-x", family="tsmom", config={"a": 1}, feature_version="v", rationale="core",
               results={})
    before_k, before_q = reg.n_trials_effective, quarter_trials(reg._rows(), quarter_of())
    cands = tv_grid.tv_grid(cat.catalog(meta()), load_settings())[:3]
    for c in cands:
        spec = TvEventSpecialist(**c.overrides)
        reg.record(agent_id=spec.agent_id, family=c.family, config=spec.config, feature_version="v",
                   rationale=c.rationale, results={}, rule_ref=tv_grid.TV_RULE_REF)
    assert reg.n_trials_effective == before_k + len(cands) == 4
    # unlike a loop trial, a TV trial spends the quarter's pre-registered budget too
    assert quarter_trials(reg._rows(), quarter_of()) == before_q + len(cands)
    assert tv_grid.TV_RULE_REF != generator.LOOP_RULE_REF


# ---------------------------------------------------------------- the engine: shadow only, exits never gated


def _engine_run(tmp_path: Path, *, outage: bool, monkeypatch: pytest.MonkeyPatch):
    from goldbot.data.synthetic import synthetic_ticks
    from goldbot.engine import ConstantModel, Engine, EngineConfig
    from goldbot.execution.broker import Tick
    from goldbot.execution.paper import PaperBroker

    ticks = synthetic_ticks("2025-03-03", "2025-03-06", ticks_per_minute=1, seed=5)
    data = tmp_path / "data"
    m15 = pd.Timedelta(minutes=15)
    opens = pd.date_range("2025-03-03", "2025-03-06", freq="15min", tz="UTC")
    rows = pd.concat([tv_rows(o, pd.Timedelta(0)) for o in opens], ignore_index=True)
    rows = rows.assign(tf="15", available_utc=rows["bar_time"] + m15, meta_hash=meta("15").meta_hash)
    Store(data).append("tv_indicators", rows, source="tv_pull")
    if outage:
        real = Store.read

        def read(self: Store, table: str, **kw: Any) -> pd.DataFrame:
            if table == "tv_indicators":
                raise OSError("TradingView store unreadable")
            return real(self, table, **kw)
        monkeypatch.setattr(Store, "read", read)
    from goldbot.allocator.rules import RuleAllocator

    class WeightsTv(RuleAllocator):
        """The family gets full weight, so only the shadow-only rule can keep it from the broker."""
        def weights(self, r: Any) -> dict[str, float]:
            return {**super().weights(r), "tv_event": 1.0}

    tv = TvEventSpecialist.from_plot(buy("15"), timeframe="15m", lot_fit=LotFitSpec(equity=1_000_000.0))
    session = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="auto", state_dir=str(tmp_path),
                              data_root=str(data), shadow_host=True),
                 PaperBroker(equity=10_000), [tv, session],
                 {tv.agent_id: ConstantModel(p=0.9), "session_open": ConstantModel(p=0.65)},
                 allocator=WeightsTv(), shadow_models={"tv-v1": (tv.agent_id, ConstantModel(p=0.9))})
    decisions = []
    for ts, bid, ask in zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)):
        decisions += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
    return eng, tv, decisions


@pytest.mark.integration
def test_a_tradingview_agent_is_shadow_only_never_a_live_entry(tmp_path, monkeypatch):
    eng, tv, decisions = _engine_run(tmp_path, outage=False, monkeypatch=monkeypatch)
    assert eng.shadow is not None
    book = eng.shadow.books["tv-v1"]
    assert book.open + book.closed, "fresh TradingView events should reach the shadow book"
    assert not [d for d in decisions if d.get("agent") == tv.agent_id and str(d["action"]).startswith("executed")]
    assert all(o.get("agent") != tv.agent_id for o in eng.decisions if o.get("action") == "order")


@pytest.mark.integration
def test_tv_outage_never_blocks_an_exit(tmp_path, monkeypatch):
    eng, tv, decisions = _engine_run(tmp_path, outage=True, monkeypatch=monkeypatch)
    deals = eng.broker.deals_since(pd.Timestamp("2025-01-01", tz="UTC"))
    assert (deals["type"] == "entry").sum() >= 1
    assert (deals["type"] != "entry").sum() >= 1, "positions of other agents still exit during a TradingView outage"
    assert eng.shadow is not None and not (eng.shadow.books["tv-v1"].open + eng.shadow.books["tv-v1"].closed)
