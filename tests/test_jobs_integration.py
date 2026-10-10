"""The scheduler's jobs end to end against a synthetic store: nightly cost table + classifier from logged ticks and
fills, the Saturday retrain's challenger -> shadow -> promotion cycle, and one bounded research month."""
import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.quality import check_bars
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.store import Store
from goldbot.data.synthetic import synthetic_ticks
from goldbot.engine.shadow import ShadowBook
from goldbot.execution.costs import CostTable
from goldbot.ops.accounts import Account
from goldbot.ops.jobs import (
    JobContext,
    attribution,
    build_scheduler,
    cpcv_quarterly,
    gap_watch,
    label_grid,
    make_trial_runner,
    monthly_research,
    nightly_costs,
    research_families,
    saturday_retrain,
)
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population, founder_base
from goldbot.research.promotion import PerfStats
from goldbot.research.registry import TrialRegistry, quarter_of
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import AgentIdentity

pytestmark = pytest.mark.integration

ACC = Account(account_id="icm-demo", broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=260100, enabled=True)


@pytest.fixture(scope="module")
def bars_store(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("data")
    store = Store(root)
    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2022-10-01", "2025-10-01", ticks_per_minute=1, seed=11)))
    store.append("bars_1m", b1, source="synthetic")
    for tf in ("15m", "1h", "4h", "1d"):
        store.append(f"bars_{tf}", resample_bars(b1, tf), source="synthetic")
    return root


def _ctx(root: Path, tmp_path: Path, **research) -> JobContext:
    s = load_settings()
    if research:
        s = s.model_copy(update={"research": s.research.model_copy(update=research)})
    return JobContext(settings=s, store=Store(root), state_dir=tmp_path, models=ModelRegistry(tmp_path / "models"),
                      trials=TrialRegistry(tmp_path / "trials.jsonl"), accounts=[ACC],
                      population=Population(tmp_path / "population.json"))


def test_nightly_costs_from_logged_ticks_and_fills(tmp_path):
    store = Store(tmp_path / "data")
    ticks = synthetic_ticks("2025-09-22", "2025-09-27", ticks_per_minute=4, seed=3)
    store.append("ticks", ticks[["ts_utc", "bid", "ask"]], source="icm-demo", dedupe=False)
    fills = pd.DataFrame({"ts_utc": pd.date_range("2025-09-23 09:00", periods=60, freq="3min", tz="UTC"),
                          "client_order_id": [f"o{i}" for i in range(60)], "side": 1, "requested": 2400.0, "filled": 2400.04,
                          "order_type": "market", "commission": 0.0})
    store.append("fills", fills, source="icm-demo", dedupe=False)
    ctx = _ctx(tmp_path / "data", tmp_path)
    friday = pd.Timestamp("2025-09-26 23:10", tz="UTC")
    out = nightly_costs(ctx, friday)
    row = out["icm-demo"]
    assert row["ticks"] == len(ticks) and row["fills"] == 60
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and set(table.spread) >= {"london", "newyork"}
    assert not table.slippage["london:market"].from_prior and table.slippage["london:market"].mean == pytest.approx(0.04)
    assert table.commission_per_lot_side_usd == 3.5
    # Friday's run also classifies the account (persisted for the engine and the dashboard)
    assert row["account_class"] in {"raw", "standard", "unknown"}
    assert json.loads((tmp_path / "classifier_icm-demo.json").read_text())["class"] == row["account_class"]


def test_retrain_challenger_shadow_promotion_cycle(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    sat = pd.Timestamp("2025-09-27 06:00", tz="UTC")
    founder = AgentIdentity(family="session_open", config=dict(SPECIALISTS["session_open"].default_config)).agent_id
    first = saturday_retrain(ctx, sat)[founder]
    version = first["retrain"]["challenger"]
    assert ctx.models.get(version).status == "challenger"
    # a challenger in shadow blocks the next retrain from replacing it
    nxt = saturday_retrain(ctx, sat + pd.Timedelta(weeks=1))[founder]
    assert nxt["retrain"].startswith("skipped") and nxt["challengers"][0]["action"] == "waiting"
    # the shadow book reports a record that passes every gate -> automatic promotion; the engine can load it
    bt = PerfStats.model_validate(ctx.models.get(version).backtest)
    good = PerfStats(n_trades=max(bt.n_trades, 60), sharpe_ann=max(bt.sharpe_ann, 1.0), hit_rate=bt.hit_rate,
                     max_dd=bt.max_dd * 0.5, trades_per_week=bt.trades_per_week, weeks=6.0)
    (tmp_path / f"shadow_{version}.json").write_text(good.model_dump_json())
    third = saturday_retrain(ctx, sat + pd.Timedelta(weeks=6))[founder]
    assert third["challengers"][0] == {"version": version, "action": "promoted"}
    assert ModelRegistry(tmp_path / "models").champion_models()[founder].feature_names
    # the founder exists in the population and the dashboard's league table was written
    assert founder in ctx.population.members and (tmp_path / "agents.json").exists()
    assert "challenger" in third["retrain"]                 # a fresh challenger starts its own shadow period


@pytest.fixture(scope="module")
def long_bars_store(tmp_path_factory) -> Path:
    """Six years of 4h and 1d bars: the 4h window trains on 48 months before its first 6-month test fold."""
    root = tmp_path_factory.mktemp("data_long")
    store = Store(root)
    b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2019-09-01", "2025-10-01", ticks_per_minute=1, seed=11)))
    for tf in ("4h", "1d"):
        store.append(f"bars_{tf}", resample_bars(b1, tf), source="synthetic")
    return root


def test_a_4h_agent_retrains_into_a_challenger(long_bars_store, tmp_path):
    # settings.walkforward["4h"] (48/6/6, proposal P4): the Saturday retrain trains a 4h shadow agent into a challenger
    # on 4h bars with daily context, instead of skipping it as it did when only 15m and 1h had windows
    ctx = _ctx(long_bars_store, tmp_path)
    sat = pd.Timestamp("2025-09-27 06:00", tz="UTC")
    ctx.population.ensure_founders(sat - pd.Timedelta(days=30))
    for founder in ctx.population.members.values():
        founder.status = "retired"                    # this test trains only the 4h agent (15m/1h are covered above)
    m = ctx.population.spawn_founder("tsmom", founder_base("tsmom", "4h"), sat - pd.Timedelta(days=1),
                                     gap_id="uncovered_timeframe:tsmom:4h", origin="tsmom default on 4h")
    out = saturday_retrain(ctx, sat)
    assert set(out) - {"bars_synced"} == {m.agent_id}
    rt = out[m.agent_id]["retrain"]
    assert isinstance(rt, dict), rt
    entry = ctx.models.get(rt["challenger"])
    assert entry.status == "challenger" and entry.agent_id == m.agent_id and entry.family == "tsmom"
    folds = int(entry.notes[0].split()[1])            # "walk-forward N folds, M candidates"
    assert folds >= 1 and m.specialist().timeframe == "4h"
    # trained on 4h bars with daily context only (features.mtf.context_tfs("4h") == ["1d"]), as the engine serves it
    names = ctx.models.load(entry).feature_names
    assert any(f.startswith("d1_") for f in names) and not any(f.startswith(("h1_", "h4_")) for f in names)


def test_monthly_label_grid_is_paused_by_default(tmp_path):
    ctx = _ctx(tmp_path / "data", tmp_path)
    out = monthly_research(ctx, pd.Timestamp("2025-09-07 08:00", tz="UTC"))
    assert "paused" in out and ctx.trials.n_trials == 0


def test_monthly_research_is_bounded_and_counted(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path, trial_budget_per_month=1, label_grid_paused=False)
    out = monthly_research(ctx, pd.Timestamp("2025-09-07 08:00", tz="UTC"))
    # the budget is per specialist; every family's trials count towards one registry total. A family under its
    # pre-registered screen (asia_drift) gets no grid trial (quant review M1)
    fams = research_families()
    assert "asia_drift" in SPECIALISTS and "asia_drift" not in fams and "asia_drift" not in out
    assert all(out[f]["trials"] == 1 for f in fams) and ctx.trials.n_trials == len(fams)
    assert ctx.trials.budget_used(quarter_of()) == len(fams)          # and towards the quarter's trial budget
    report = Path(out["report"]).read_text()
    assert "Research loop 2025-09" in report and report.count("\n| ") >= 3


def test_monthly_research_stops_at_the_quarterly_budget(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path, trial_budget_per_month=1, label_grid_paused=False, trial_budget_quarter=2,
               reserved_trials_quarter=0)
    out = monthly_research(ctx, pd.Timestamp("2025-09-07 08:00", tz="UTC"))
    assert ctx.trials.n_trials == 2 and "trial budget exceeded" in out["budget"]


def test_label_grid_is_the_26_neighbours_of_the_base():
    grid = label_grid({"target_atr": 1.5, "stop_atr": 1.0, "max_bars": 16}, 0.25)
    assert len(grid) == 26 and {"target_atr": 1.5, "stop_atr": 1.0, "max_bars": 16} not in grid
    assert {g["max_bars"] for g in grid} == {12, 16, 20}


def test_build_scheduler_registers_every_job(tmp_path):
    ctx = _ctx(tmp_path / "data", tmp_path)
    sch = build_scheduler(ctx, clock=lambda: pd.Timestamp("2026-10-02 12:00", tz="UTC"))
    nxt = {k: v["next_slot"] for k, v in sch.status()["jobs"].items()}
    assert nxt == {"nightly_costs": "2026-10-02T23:10:00+00:00", "saturday_retrain": "2026-10-03T06:00:00+00:00",
                   "tournament": "2026-10-03T12:00:00+00:00", "research_director": "2026-10-03T12:30:00+00:00",
                   "model_watch": "2026-10-02T23:30:00+00:00",
                   "agents_daily": "2026-10-02T23:45:00+00:00", "agents_weekly": "2026-10-03T13:00:00+00:00",
                   "monthly_research": "2026-10-04T08:00:00+00:00", "calendar_archive": "2026-10-03T06:10:00+00:00",
                   "agents_presession": "2026-10-05T06:30:00+00:00", "recalibrate": "2026-10-03T11:30:00+00:00", "drift_watch": "2026-10-02T23:40:00+00:00",
                   "gap_watch": "2026-10-02T23:55:00+00:00", "feed_reconcile": "2026-10-02T23:20:00+00:00",
                   "backup": "2026-10-02T22:15:00+00:00", "restore_drill": "2026-10-04T10:00:00+00:00",
                   "attribution": "2026-10-02T23:50:00+00:00",
                   "cpcv_quarterly": "2026-10-04T14:00:00+00:00"}
    # quarterly: after October's run the next is the first Sunday of January
    later = build_scheduler(ctx, clock=lambda: pd.Timestamp("2026-10-05 12:00", tz="UTC"))
    assert later.status()["jobs"]["cpcv_quarterly"]["next_slot"] == "2027-01-03T14:00:00+00:00"


def test_cpcv_quarterly_attaches_evidence_to_gate_passing_trials_without_new_trials(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    cfg = {**SPECIALISTS["session_open"].default_config, "asia_range_max_atr_d": 1.2}
    peer = {**SPECIALISTS["session_open"].default_config, "asia_range_max_atr_d": 1.5}
    stale = ctx.trials.record(agent_id="a", family="session_open", config=cfg, feature_version="v", rationale="r",
                              results={"gates": {"passed": True}})
    ctx.trials.record(agent_id="b", family="session_open", config=peer, feature_version="v", rationale="r",
                      results={"gates": {"passed": False}})
    ctx.trials.record(agent_id="c", family="session_open", config=cfg, feature_version="v", rationale="r",
                      results={"gates": {"passed": True}}, status="holdout")      # a holdout scoring is never re-run
    slot = pd.Timestamp("2025-10-05 14:00", tz="UTC")
    # a trial recorded with other features than the store's bars build now is skipped, not re-run on new inputs
    first = cpcv_quarterly(ctx, slot)
    msg = first["trials"][stale["trial"]]
    assert msg.startswith("skipped: feature version") and msg.endswith("differs from the trial's v")
    assert not ctx.trials.evidence(kind="cpcv") and not first["errors"]
    version = msg.split("feature version ")[1].split(" differs")[0]
    passed = ctx.trials.record(agent_id="a", family="session_open", config=cfg, feature_version=version,
                               rationale="r", results={"gates": {"passed": True}})
    ctx.trials.record(agent_id="b", family="session_open", config=peer, feature_version=version, rationale="r",
                      results={"gates": {"passed": False}})
    n = ctx.trials.n_trials
    out = cpcv_quarterly(ctx, slot)
    assert ctx.trials.n_trials == n                                       # not a trial: no new row, no budget slot
    assert set(out["trials"]) == {stale["trial"], passed["trial"]} and not out["errors"]
    assert out["trials"][stale["trial"]].startswith("skipped")
    (ev,) = ctx.trials.evidence(passed["trial"], "cpcv")
    p = ev["payload"]
    assert ev["quarter"] == "2025Q4" and p["n_splits"] == 15 and p["n_paths"] == 5 and len(p["paths"]) == 5
    assert any(s["fitted"] for s in p["splits"])                         # real models, not empty paths
    assert pd.Timestamp(p["edges"][-1]) <= pd.Timestamp("2025-10-01", tz="UTC")   # never into the holdout
    # PBO across the comparable peers only (the stale ones are skipped), labelled a lower bound of the selection set
    assert p["pbo"] is not None and p["pbo"]["trials"] == [passed["trial"], passed["trial"] + 1]
    assert p["pbo"]["selection_set"] == 4 and p["pbo"]["lower_bound"] is True
    assert p["verdict"]["rule"] == "veto only (ADR 0003)" and isinstance(out["trials"][passed["trial"]]["fragile"], bool)
    text = Path(out["report"]).read_text()
    assert "Combinatorial purged CV 2025Q4" in text and "lower bound" in text
    # idempotent within the quarter
    again = cpcv_quarterly(ctx, slot + pd.Timedelta(hours=1))
    assert again["trials"][passed["trial"]] == "already evaluated this quarter"
    assert len(ctx.trials.evidence(kind="cpcv")) == 1


def test_research_analyst_trial_is_recorded_in_the_registry(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    runner = make_trial_runner(ctx, now=lambda: pd.Timestamp("2025-09-30", tz="UTC"))
    # a looser filter keeps enough candidates for a fold on three synthetic years (a tighter one would not)
    out = runner("session_open", {"asia_range_max_atr_d": 1.2}, "research analyst, hypothesis abc: looser filter")
    assert out["trial"] == 1 and out["n_folds"] >= 1 and "model_filtered" in out
    assert "gates" in out and out["gates"]["passed"] is False and out["rule_only"]["gross"]["n"] > 0
    row = ctx.trials._rows()[0]
    assert row["config"]["asia_range_max_atr_d"] == 1.2 and row["rationale"].startswith("research analyst")
    assert row["budget_quarter"] == quarter_of()                     # rows are charged to the quarter they are written in
    # the quarter's pre-registered budget is enforced for the analyst too
    capped = make_trial_runner(_ctx(bars_store, tmp_path, trial_budget_quarter=1), now=lambda: pd.Timestamp("2025-09-30", tz="UTC"))
    assert "trial budget exceeded" in capped("session_open", {}, "one too many")["error"]


def test_gap_watch_job_spawns_shadow_founders_and_saves_the_population(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    slot = pd.Timestamp("2025-10-01 23:55", tz="UTC")
    ctx.population.ensure_founders(slot - pd.Timedelta(days=30))
    out = gap_watch(ctx, slot)
    assert len(out["spawned"]) == ctx.settings.gaps.founders_per_month
    saved = Population(tmp_path / "population.json")
    new = [saved.members[a] for a in out["spawned"]]
    assert all(m.status == "shadow" and m.capital_weight == 0 and m.gap_id for m in new)
    report = json.loads((tmp_path / "gaps.json").read_text())
    assert report["notes"][0].startswith("regime: no champion")
    assert any("no walk-forward window for 1d" in r["reason"] for r in report["refused"])
    assert not any("no walk-forward window for 4h" in r["reason"] for r in report["refused"])


def test_no_spawn_during_system_halt(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    slot = pd.Timestamp("2025-10-01 23:55", tz="UTC")
    ctx.population.ensure_founders(slot - pd.Timedelta(days=30))
    halt = {"since": slot.isoformat(), "reasons": ["two agents halted"]}
    (tmp_path / "drift.json").write_text(json.dumps({"halted": {}, "system_halt": halt}))
    n = len(ctx.population.members)
    out = gap_watch(ctx, slot)
    assert out["spawned"] == [] and len(ctx.population.members) == n
    assert any("system halt" in r for r in out["refused"])
    report = json.loads((tmp_path / "gaps.json").read_text())
    assert any(g["kind"] == "system_halt" for g in report["gaps"])
    # no Anthropic key on this host: the on-demand risk officer is refused, never improvised
    assert any(r["action"] == "staff_run" and "anthropic-api-key" in r["reason"] for r in report["refused"])


def test_attribution_job_writes_the_report_from_the_shadow_book_and_changes_nothing_else(bars_store, tmp_path):
    ctx = _ctx(bars_store, tmp_path)
    slot = pd.Timestamp("2025-09-30 23:50", tz="UTC")
    book = ShadowBook(tmp_path)
    book.track("tsmom-g0-0123456789-v1", slot - pd.Timedelta(days=30))
    for i in range(12):
        t = book.open_trade(version="tsmom-g0-0123456789-v1", agent_id="tsmom-g0-0123456789", side=1,
                            bar_ts=slot - pd.Timedelta(days=20 - i, hours=14), entry=2400.0, atr_usd=10.0,
                            target_atr=2.0, stop_atr=1.0, max_bars=4, p=0.6, timeframe="1h", threshold=0.55)
        assert t is not None
        t.exit_ts, t.exit, t.barrier, t.ret = t.entry_ts + pd.Timedelta(hours=2), 2410.0, "time", 10.0 / 2400.0
        book.books[t.version].open.remove(t)
        book.books[t.version].closed.append(t)
    book.save(slot)
    before = {p.name for p in tmp_path.iterdir()}
    out = attribution(ctx, slot)
    assert out["taken"] == 12 and out["verdict"] == "noise" and out["costs"].startswith("settings priors")
    assert {p.name for p in tmp_path.iterdir()} - before == {"attribution.json", "attribution.md"}
    report = json.loads((tmp_path / "attribution.json").read_text())
    assert set(report["breakdowns"]["regime"]) <= {"low", "mid", "high"}       # 1h bars in the store give a regime
    assert report["live"]["icm-demo"]["fills"] == 0
