"""Cost of record v3 (ADR: cost of record v3, amending 0064): the fill-to-research-bar cost by broker x session x exit
type, day-clustered upper bound charged, target-exit slippage floored at 0, read beside the v2 table through
`arms.load_costs` and charged only to a screen that pins the v3 version."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml
from scipy import stats

from goldbot.db.connection import Database
from goldbot.engine import cost_probe
from goldbot.engine.exploration import LEDGER, ExplorationRecord
from goldbot.research import arms
from goldbot.research import cost_of_record as C
from goldbot.research import evidence_exclusions as EV

ROOT = Path(__file__).resolve().parents[1]
COSTS = ROOT / "config" / "arm_costs.yaml"
V3 = "cor-2026-11-15.4"
V2 = "cor-2026-10-31.2"
HOSTS = {"acct-a": "brk"}
DAY0 = pd.Timestamp("2026-10-27 09:00", tz="UTC")       # London
BASE = 2400.0


# ---------------------------------------------------------------- research bars and fills
def _bars(start: pd.Timestamp, minutes: int, path: dict[int, tuple[float, float, float, float]] | None = None,
          mid: float = BASE, half: float = 0.25) -> pd.DataFrame:
    """1m Dukascopy-shaped bars: flat at `mid` unless `path` gives (open, high, low, close) of a minute offset."""
    ts = pd.date_range(start, periods=minutes, freq="1min", tz="UTC")
    o = np.full(minutes, mid)
    h, lo, c = o.copy(), o.copy(), o.copy()
    for k, (a, b, d, e) in (path or {}).items():
        o[k], h[k], lo[k], c[k] = a, b, d, e
    return pd.DataFrame({"ts_utc": ts, "bid_open": o - half, "ask_open": o + half, "bid_high": h - half,
                         "ask_high": h + half, "bid_low": lo - half, "ask_low": lo + half, "bid_close": c - half,
                         "ask_close": c + half})


def _fill(cid: str, sent: pd.Timestamp, *, side: int = 1, fill_gap: float = 0.0, exit_px: float, reason: str,
          stop: float = 3.0, target: float = 3.0, hold_s: int = 600, comm_gbp: float = 0.06, swap_gbp: float = 0.0,
          exit_after_min: float = 2.0, arm: str = "S0", cls: str | None = "control",
          acct: str = "acct-a") -> ExplorationRecord:
    fill = BASE + side * fill_gap
    fx, oz = 0.75, 1.0
    pnl_usd_oz = side * (exit_px - fill) - comm_gbp / fx + swap_gbp / fx      # the broker's net: profit + swap - comm
    return ExplorationRecord(client_order_id=cid, account_id=acct, arm_id=arm, family="f", tf="1m", side=side,
                             lots=0.01, signal_utc=sent, sent_utc=sent, atr_usd=stop, stop_usd=stop,
                             target_usd=target, hold_s=hold_s, requested_price=fill, spread_usd=0.08, fill_price=fill,
                             exit_utc=sent + pd.Timedelta(minutes=exit_after_min), exit_price=exit_px,
                             exit_reason=reason, commission=-comm_gbp, swap=swap_gbp, fx_acct_per_usd=fx,
                             pnl=pnl_usd_oz * fx * oz, evidence_class=cls)


def test_the_replay_follows_the_research_mechanics() -> None:
    """Mid bars, entry at the send minute's mid open, stop first inside a minute, a minute opening through the stop
    fills at its open, a time exit at the last minute's mid close."""
    t0 = DAY0
    both = C.mid_bars(_bars(t0, 10, {2: (BASE, BASE + 5, BASE - 5, BASE)}))
    assert C.replay(both, 0, 1, 3.0, 3.0, t0 + pd.Timedelta(minutes=10)) == (BASE, BASE - 3.0, "stop")
    gap = C.mid_bars(_bars(t0, 10, {3: (BASE - 4, BASE - 3.5, BASE - 4.5, BASE - 4)}))
    assert C.replay(gap, 0, 1, 3.0, 3.0, t0 + pd.Timedelta(minutes=10)) == (BASE, BASE - 4.0, "stop")
    tgt = C.mid_bars(_bars(t0, 10, {4: (BASE, BASE + 3.5, BASE - 1, BASE + 3)}))
    assert C.replay(tgt, 0, 1, 3.0, 3.0, t0 + pd.Timedelta(minutes=10)) == (BASE, BASE + 3.0, "target")
    drift = C.mid_bars(_bars(t0, 10, {4: (BASE, BASE + 1, BASE, BASE + 1)}))
    assert C.replay(drift, 0, 1, 3.0, 3.0, t0 + pd.Timedelta(minutes=5)) == (BASE, BASE + 1, "time")


def _rows(fills: list[ExplorationRecord], bars: pd.DataFrame,
          probes: list[cost_probe.ProbeRecord] | None = None) -> pd.DataFrame:
    return C.fill_to_bar_rows(fills, probes or [], C.mid_bars(bars), HOSTS)


def test_a_fills_cost_is_the_replay_gross_less_the_broker_net_with_swap_left_to_the_screen() -> None:
    bars = _bars(DAY0, 30, {5: (BASE, BASE + 0.5, BASE - 3.4, BASE - 3)})
    # a long stopped out: filled $0.02 above the mid, exit $0.05 past the research stop; $0.08 commission
    f = _fill("s1", DAY0, fill_gap=0.02, exit_px=BASE - 3.05, reason="stop", swap_gbp=-0.03)
    r = _rows([f], bars).iloc[0]
    assert r["exit"] == "stop" and bool(r["matched"])
    assert r["cost_usd"] == pytest.approx(0.02 + 0.05 + 0.08)   # entry gap + exit gap + commission; swap excluded
    assert r["cost_with_swap_usd"] == pytest.approx(0.02 + 0.05 + 0.08 + 0.04)    # what the parity table measured
    assert r["commission_usd"] == pytest.approx(0.08)


def test_target_exit_slippage_is_floored_at_zero() -> None:
    """Demo's favourable target fills are not bankable for real money: a target exit costs the commission plus any
    adverse slippage against its own level, never less."""
    bars = _bars(DAY0, 30, {5: (BASE, BASE + 3.5, BASE - 0.5, BASE + 3)})
    lucky = _fill("t1", DAY0, fill_gap=0.10, exit_px=BASE + 0.10 + 3.0 + 0.20, reason="target")    # +0.20 better
    unlucky = _fill("t2", DAY0, fill_gap=0.10, exit_px=BASE + 0.10 + 3.0 - 0.05, reason="target")  # 0.05 worse
    rows = _rows([lucky, unlucky], bars).set_index("cid")
    assert rows.loc["t1", "raw_cost_usd"] == pytest.approx(0.10 - 0.30 + 0.08)
    assert rows.loc["t1", "cost_usd"] == pytest.approx(0.08)
    assert rows.loc["t2", "cost_usd"] == pytest.approx(0.08 + 0.05)
    # the level is the quote the bracket was priced from: a fill $0.10 worse than requested reaches requested + 3
    late = _fill("t3", DAY0, fill_gap=0.10, exit_px=BASE + 3.0, reason="target").model_copy(
        update={"requested_price": BASE})
    r = _rows([late], bars).iloc[0]
    assert r["cost_usd"] == pytest.approx(r["raw_cost_usd"]) == pytest.approx(0.10 + 0.08)


def test_a_fill_whose_barrier_disagrees_is_priced_in_its_research_cell() -> None:
    """Quant review of 89e69db3: pricing only agreeing fills selects on the outcome. A fill the research replay takes to
    target but the broker stops out costs the whole swing, and it is priced in the research (target) cell."""
    bars = _bars(DAY0, 30, {5: (BASE, BASE + 3.5, BASE - 0.5, BASE + 3)})      # research: target
    f = _fill("m1", DAY0, exit_px=BASE - 3.0, reason="stop")                    # broker: stop
    rows = _rows([f], bars)
    r = rows.iloc[0]
    assert r["exit"] == "target" and not bool(r["matched"])
    assert r["cost_usd"] == pytest.approx(3.0 - (-3.0 - 0.08))                  # replay +3 less the broker's -3.08
    pad = pd.concat([_frame(8, 10, 0.10, "stop"), _frame(8, 10, 0.08, "target"), _frame(8, 10, 0.2, "time")])
    one = pd.concat([pad, rows.assign(day="2026-10-01")])
    t = C.build_fill_to_bar(one, version="t", window_utc=("a", "b"), atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    cell = t["cells"]["brk"]["london"]["target"]
    assert cell["n_fills"] == 81 and cell["n_unmatched"] == 1
    assert cell["mean_usd"] == pytest.approx(float(one[one["exit"] == "target"]["cost_usd"].mean()))


def test_no_exit_charges_less_than_its_sessions_pooled_cost() -> None:
    """Pricing every fill in its research cell moves each disagreement's whole swing into one exit's cell (research
    stop / broker target lands in `stop` as a large negative cost). So an exit never charges below its session's
    all-exit cell: a stop-heavy arm is never charged less than the sample's total cost."""
    rows = pd.concat([_frame(8, 10, -0.40, "stop"), _frame(8, 10, 0.60, "target"), _frame(8, 10, 0.2, "time")])
    t = C.build_fill_to_bar(rows, version="t", window_utc=("a", "b"), atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    ldn = t["cells"]["brk"]["london"]
    assert ldn["stop"]["ci95"][1] < ldn["all"]["charged_usd"]
    for ex in ("stop", "target", "time"):
        assert ldn[ex]["charged_usd"] >= ldn["all"]["charged_usd"]
    assert ldn["target"]["charged_usd"] == pytest.approx(ldn["target"]["ci95"][1])


def test_the_parity_check_is_stated_under_both_definitions() -> None:
    pad = pd.concat([_frame(8, 10, 0.10, "stop"), _frame(8, 10, 0.08, "target"), _frame(8, 10, 0.2, "time")])
    pad = pad.reset_index(drop=True)
    pad.loc[pad.index[:5], "matched"] = False
    t = C.build_fill_to_bar(pad, version="t", window_utc=("a", "b"),
                            atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    assert t["checks"]["brk_controls_parity"]["n_fills"] == 240
    assert t["checks"]["brk_controls_parity_matched"]["n_fills"] == 235


def test_an_unbalanced_day_cluster_charges_the_wider_of_cr1_and_cr2() -> None:
    """Six days of 1, 2, 3, 4, 5 and 10 fills. Hand-computed: mean 0.14; CR1 upper 0.248740; CR2 (Bell-McCaffrey,
    intercept only: sum_g S_g^2 / (1 - n_g / n) / n^2) upper 0.261537, lower 0.018463, both with t(5)."""
    v = np.array([0.5, 0.1, 0.3, 0.0, 0.2, 0.4, 0.1, 0.1, 0.2, 0.0, 0.3, 0.2, 0.1, 0.25, 0.15,
                  0.05, 0.1, 0.0, 0.1, 0.05, 0.1, 0.0, 0.1, 0.05, 0.05])
    days = np.repeat(np.arange(6), [1, 2, 3, 4, 5, 10])
    m, lo, hi = C.day_clustered_ci(v, days)
    assert m == pytest.approx(0.14)
    assert hi == pytest.approx(0.26153664, abs=1e-7) and lo == pytest.approx(0.01846336, abs=1e-7)


def test_a_cell_whose_realised_mean_exceeds_its_charge_forces_a_refresh() -> None:
    rows = pd.concat([_frame(8, 10, 0.10, "stop"), _frame(8, 10, 0.08, "target"), _frame(8, 10, 0.2, "time")])
    t = C.build_fill_to_bar(rows, version="t", window_utc=("a", "b"), atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    assert C.refresh_due(t, rows) == []
    dear = rows.assign(cost_usd=np.where(rows["exit"] == "time", 0.9, rows["cost_usd"]))
    assert C.refresh_due(t, dear) == ["brk/london/time", "brk/london/all", "brk/all/time", "brk/all/all"]


def test_scheduled_probes_are_time_exits_at_the_nearest_minute_boundary() -> None:
    bars = _bars(DAY0, 30, {1: (BASE + 0.2, BASE + 0.4, BASE, BASE + 0.4)})
    p = cost_probe.ProbeRecord(client_order_id="p1", account_id="acct-a", agent_id="sched", side=1, lots=0.01,
                               sent_utc=DAY0 + pd.Timedelta(seconds=3), requested_price=BASE + 0.05,
                               fill_price=BASE + 0.05, exit_utc=DAY0 + pd.Timedelta(seconds=40),
                               exit_price=BASE + 0.35, commission=0.06, swap=0.0,
                               pnl=(0.30 - 0.08) * 0.75, fx_acct_per_usd=0.75, purpose="cost_measurement")
    r = _rows([], bars, [p]).iloc[0]
    # replay: mid open of minute 0 (2400.00) to minute 0's mid close (2400.00, exit at :40 rounds up to 09:01:00)
    assert r["exit"] == "time" and bool(r["probe"]) and bool(r["matched"])
    assert r["cost_usd"] == pytest.approx(0.0 - (0.30 - 0.08))


def test_the_interval_is_clustered_by_day_with_a_small_sample_t() -> None:
    rng = np.random.default_rng(3)
    days = np.repeat(np.arange(8), 25)
    v = rng.normal(0, 0.1, 200) + np.repeat(rng.normal(0, 0.2, 8), 25)
    m, lo, hi = C.day_clustered_ci(v, days)
    g = pd.Series(v).groupby(days).sum().to_numpy()
    n = pd.Series(v).groupby(days).size().to_numpy()
    se = np.sqrt((8 / 7) * ((g - n * v.mean()) ** 2).sum()) / len(v)
    assert m == pytest.approx(v.mean())
    assert hi == pytest.approx(v.mean() + stats.t.ppf(0.975, 7) * se)
    assert lo == pytest.approx(v.mean() - stats.t.ppf(0.975, 7) * se)
    iid = v.std(ddof=1) / np.sqrt(len(v)) * 1.96
    assert hi - m > 1.5 * iid                       # the day effect is not averaged away


def _frame(n_days: int, per_day: int, cost: float, exit_: str, session: str = "london", comm: float = 0.08,
           broker: str = "brk") -> pd.DataFrame:
    days = [f"2026-10-{d + 1:02d}" for d in range(n_days) for _ in range(per_day)]
    rng = np.random.default_rng(len(days))
    c = cost + rng.normal(0, 0.05, len(days))
    return pd.DataFrame({"broker": broker, "session": session, "day": days, "exit": exit_, "matched": True,
                         "probe": False, "control": True, "cost_usd": c, "raw_cost_usd": c, "cost_with_swap_usd": c,
                         "commission_usd": comm, "stop_exact": False, "cid": [f"{exit_}{i}" for i in range(len(c))]})


def test_a_sparse_cell_charges_at_least_the_brokers_pooled_exit_cell() -> None:
    rows = pd.concat([_frame(8, 10, 0.10, "stop", "london"), _frame(8, 10, 0.10, "stop", "newyork"),
                      _frame(2, 3, 0.01, "stop", "asia"), _frame(8, 10, 0.08, "target"), _frame(8, 10, 0.2, "time")])
    t = C.build_fill_to_bar(rows, version="t", window_utc=("a", "b"), atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    asia, pooled = t["cells"]["brk"]["asia"]["stop"], t["cells"]["brk"]["all"]["stop"]
    assert asia["sparse"] and asia["charged_usd"] >= pooled["charged_usd"]
    ldn = t["cells"]["brk"]["london"]["stop"]
    assert not ldn["sparse"] and ldn["charged_usd"] == pytest.approx(
        max(ldn["ci95"][1], ldn["commission_usd"], t["cells"]["brk"]["london"]["all"]["charged_usd"]))


def test_the_charge_is_never_below_the_commission() -> None:
    rows = pd.concat([_frame(8, 10, -0.30, "stop"), _frame(8, 10, 0.08, "target"), _frame(8, 10, 0.2, "time")])
    t = C.build_fill_to_bar(rows, version="t", window_utc=("a", "b"), atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    assert t["cells"]["brk"]["london"]["stop"]["charged_usd"] == pytest.approx(0.08)


def test_stops_filled_at_the_exact_stop_price_are_untrusted() -> None:
    rows = pd.concat([_frame(8, 10, 0.10, "stop").assign(stop_exact=True), _frame(8, 10, 0.08, "target"),
                      _frame(8, 10, 0.2, "time")])
    t = C.build_fill_to_bar(rows, version="t", window_utc=("a", "b"), atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    assert t["stops_trusted"]["brk"] is False


def _seed_db(tmp_path: Path) -> None:
    db = Database.open(tmp_path, "aux")
    with db.write() as conn:
        LEDGER.upsert(conn, _fill("k1", DAY0, exit_px=BASE - 3.05, reason="stop"))
        LEDGER.upsert(conn, _fill("void", DAY0, exit_px=BASE - 30, reason="stop"))
        LEDGER.upsert(conn, _fill("add", DAY0, exit_px=BASE - 3.05, reason="stop").model_copy(update={"group_id": "g"}))
    EV.record(db, [EV.Exclusion(client_order_id="void", kind="exploration", account_id="acct-a", scope=("cost_cells",),
                                reason="test", span_ref="dq_events:x", decided_utc=DAY0)])


def test_the_reader_drops_void_fills_and_pyramid_adds(tmp_path: Path) -> None:
    _seed_db(tmp_path)
    conn = sqlite3.connect(f"file:{tmp_path / 'aux.db'}?mode=ro", uri=True)
    try:
        fills, probes = C.read_fill_to_bar(conn, HOSTS, DAY0 - pd.Timedelta(hours=1), DAY0 + pd.Timedelta(days=1))
    finally:
        conn.close()
    assert [f.client_order_id for f in fills] == ["k1"] and probes == []


def test_the_rendered_table_loads_through_load_costs(tmp_path: Path) -> None:
    rows = pd.concat([_frame(8, 10, 0.10, "stop", s) for s in ("asia", "london", "newyork")]
                     + [_frame(8, 10, 0.08, "target", s) for s in ("asia", "london", "newyork")]
                     + [_frame(8, 10, 0.20, "time", s) for s in ("asia", "london", "newyork")])
    t = C.build_fill_to_bar(rows, version="cor-test.3", window_utc=("a", "b"),
                            atr14={"1m": 1.0, "5m": 2.0, "15m": 4.0})
    (tmp_path / "v3.yaml").write_text(C.render_fill_to_bar_yaml(t))
    raw = yaml.safe_load(COSTS.read_text())
    raw["cost_of_record"] = str(ROOT / "config" / "cost_of_record.yaml")
    raw["fill_to_bar_cost"] = "v3.yaml"
    (tmp_path / "arm_costs.yaml").write_text(yaml.safe_dump(raw))
    c = arms.load_costs(tmp_path / "arm_costs.yaml")
    assert c.fill_to_bar is not None and c.fill_to_bar.version == "cor-test.3"
    cell = c.fill_to_bar.cells["brk"]["london"]["stop"]
    assert cell.charged_usd == pytest.approx(t["cells"]["brk"]["london"]["stop"]["charged_usd"], abs=1e-3)


def test_a_v3_table_a_newer_release_wrote_still_reads(tmp_path: Path) -> None:
    """ADR 0010: read tolerantly. A field a newer release adds (top level or in a cell) is dropped, not refused."""
    t = yaml.safe_load((ROOT / "config" / "cost_of_record_v3.yaml").read_text())
    t["refreshed_by"] = "weekly job"
    t["cells"]["icm"]["asia"]["stop"]["latency_ms"] = 800.0
    (tmp_path / "v3.yaml").write_text(yaml.safe_dump(t))
    raw = yaml.safe_load(COSTS.read_text())
    raw["cost_of_record"] = str(ROOT / "config" / "cost_of_record.yaml")
    raw["fill_to_bar_cost"] = "v3.yaml"
    (tmp_path / "arm_costs.yaml").write_text(yaml.safe_dump(raw))
    c = arms.load_costs(tmp_path / "arm_costs.yaml")
    assert c.fill_to_bar is not None
    assert c.fill_to_bar.cells["icm"]["asia"]["stop"].n_fills == t["cells"]["icm"]["asia"]["stop"]["n_fills"]


def test_a_v3_cell_charged_below_its_upper_bound_is_refused(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """Refused, not read: logged, left unloaded, and any v3 pin then stops (quant review: never break v2 screens)."""
    t = yaml.safe_load((ROOT / "config" / "cost_of_record_v3.yaml").read_text())
    t["cells"]["icm"]["london"]["stop"]["charged_usd"] = 0.0
    (tmp_path / "v3.yaml").write_text(yaml.safe_dump(t))
    raw = yaml.safe_load(COSTS.read_text())
    raw["fill_to_bar_cost"] = "v3.yaml"
    raw.pop("cost_of_record", None)
    (tmp_path / "arm_costs.yaml").write_text(yaml.safe_dump(raw))
    with caplog.at_level("WARNING"):
        c = arms.load_costs(tmp_path / "arm_costs.yaml")
    assert c.fill_to_bar is None and "charged_usd" in caplog.text
    with pytest.raises(ValueError, match="not loaded"):
        c.check_pin(t["version"])


def test_the_cost_model_file_is_unchanged_so_running_lines_keep_their_hash() -> None:
    """arm_costs.yaml is in the sprint3000 engine content hash: v3 is found as its sibling, never by editing it."""
    assert "fill_to_bar_cost" not in yaml.safe_load(COSTS.read_text())
    assert (COSTS.parent / arms.FILL_TO_BAR_FILE).exists()


# ---------------------------------------------------------------- the repo table and the screen
def test_the_repo_carries_v3_beside_the_pinned_v2() -> None:
    c = arms.load_costs(COSTS)
    assert c.of_record is not None and c.of_record.version == V2            # pinned lines keep reading v2
    assert c.fill_to_bar is not None and c.fill_to_bar.version == V3
    assert c.fill_to_bar.basis == "fill_to_research_bar"
    for broker in ("icm", "vantage"):
        for session in ("asia", "london", "newyork", "all"):
            for exit_ in ("stop", "target", "time", "all"):
                cell = c.fill_to_bar.cells[broker][session][exit_]
                assert cell.ci95[0] <= cell.mean_usd + 1e-3 <= cell.ci95[1] + 2e-3
                assert cell.charged_usd >= max(cell.commission_usd, cell.ci95[1]) - 1e-3
    assert c.fill_to_bar.stops_trusted["icm"] is True


def test_the_repo_table_reproduces_the_icm_control_parity() -> None:
    """Acceptance, both definitions (swap included): matched barrier only, the parity table's $0.116 on 993 fills
    (why-strategies-fail-2026-11-15 section 1); every replayed control fill, what v3 prices (quant review: ~0.196)."""
    c = arms.load_costs(COSTS)
    assert c.fill_to_bar is not None
    matched = c.fill_to_bar.checks["icm_controls_parity_matched"]
    assert matched.n_fills == 993 and matched.mean_usd == pytest.approx(0.116, abs=0.01)
    every = c.fill_to_bar.checks["icm_controls_parity"]
    assert every.n_fills > matched.n_fills and every.mean_usd == pytest.approx(0.196, abs=0.02)


def test_the_stress_line_adds_070_once_to_a_v3_stop() -> None:
    """Quant review: Vantage's built-in stress plus the screen's stress line charged 1.47; the stress line is the cell
    without its built-in stress + 0.70."""
    c = arms.load_costs(COSTS)
    assert c.fill_to_bar is not None
    cell = c.fill_to_bar.cells["vantage"]["london"]["stop"].charged_usd
    for broker, base in (("vantage", cell), ("icm", c.fill_to_bar.cells["icm"]["london"]["stop"].charged_usd)):
        net = arms.screen_net_r(np.array([-3.0]), np.array([3.0]), np.array([True]), c, broker=broker,
                                sessions=np.array(["london"]), version=V3, exits=np.array(["stop"]),
                                overshoot_usd=arms.OVERSHOOT_STRESS_USD)
        assert net[0] == pytest.approx((-3.0 - (base + 0.70)) / 3.0)


def test_a_malformed_v3_table_never_breaks_a_v2_screen(tmp_path: Path) -> None:
    raw = yaml.safe_load(COSTS.read_text())
    raw["cost_of_record"] = str(ROOT / "config" / "cost_of_record.yaml")
    (tmp_path / "arm_costs.yaml").write_text(yaml.safe_dump(raw))
    (tmp_path / arms.FILL_TO_BAR_FILE).write_text("version: broken\ncells: 7\n")
    c = arms.load_costs(tmp_path / "arm_costs.yaml")
    assert c.of_record is not None and c.of_record.version == V2 and c.fill_to_bar is None
    c.check_pin(V2)
    with pytest.raises(ValueError, match="not loaded"):
        c.check_pin(V3)


def test_v3_charges_per_exit_type_when_the_screen_knows_it() -> None:
    c = arms.load_costs(COSTS)
    sessions = np.array(["asia", "london", "newyork", ""])
    exits = np.array(["stop", "target", "time", "stop"])
    rt, os_ = c.screen_charges("icm", sessions, V3, exits=exits)
    want = [c.fill_to_bar_charge_usd("icm", "asia", "stop"), c.fill_to_bar_charge_usd("icm", "london", "target"),
            c.fill_to_bar_charge_usd("icm", "newyork", "time"),
            max(c.fill_to_bar_charge_usd("icm", s, "stop") for s in ("asia", "london", "newyork"))]
    assert rt.tolist() == pytest.approx(want) and os_.tolist() == [0.0] * 4
    move, risk = np.array([-3.0, 3.0, 1.0, -3.0]), np.full(4, 3.0)
    net = arms.screen_net_r(move, risk, exits == "stop", c, broker="icm", sessions=sessions, version=V3, exits=exits)
    assert net.tolist() == pytest.approx(((move - np.array(want)) / risk).tolist())


def test_v3_without_exit_types_charges_the_dearer_non_stop_and_tops_up_stops() -> None:
    c = arms.load_costs(COSTS)
    rt, os_ = c.screen_charges("icm", np.array(["london"]), V3)
    non_stop = max(c.fill_to_bar_charge_usd("icm", "london", "target"), c.fill_to_bar_charge_usd("icm", "london", "time"))
    assert rt[0] == pytest.approx(non_stop)
    assert os_[0] == pytest.approx(max(0.0, c.fill_to_bar_charge_usd("icm", "london", "stop") - non_stop))


def test_v3_is_cheaper_than_v2_at_icm_and_vantage_stops_keep_the_stress() -> None:
    c = arms.load_costs(COSTS)
    for s in ("asia", "london", "newyork"):
        v2_stop = c.charged_round_trip_usd("icm", s) + c.overshoot_charge_usd("icm", s)
        assert c.fill_to_bar_charge_usd("icm", s, "stop") < v2_stop
    assert c.fill_to_bar is not None and c.fill_to_bar.stops_trusted["vantage"] is False
    cell = c.fill_to_bar.cells["vantage"]["london"]["stop"]
    assert c.fill_to_bar_charge_usd("vantage", "london", "stop") == pytest.approx(
        cell.charged_usd + arms.OVERSHOOT_STRESS_USD)


def test_v3_refuses_unknown_sessions_exits_and_other_brokers() -> None:
    c = arms.load_costs(COSTS)
    with pytest.raises(ValueError, match="session"):
        c.fill_to_bar_charge_usd("icm", "London", "stop")
    with pytest.raises(ValueError, match="exit"):
        c.fill_to_bar_charge_usd("icm", "london", "trail")
    with pytest.raises(KeyError, match="no cost of record"):
        c.fill_to_bar_charge_usd("pepperstone", "london", "stop")


def test_pinning_reads_the_right_table() -> None:
    c = arms.load_costs(COSTS)
    c.check_pin(V2)
    c.check_pin(V3)
    with pytest.raises(ValueError, match="version"):
        c.check_pin("cor-1999-01-01.1")
    with pytest.raises(ValueError, match="fill-to-bar"):
        c.require_version(V3)                       # the v2-shaped reader refuses a v3 pin, never mis-reads it
    rt2, _ = c.screen_charges("icm", np.array(["london"]), V2, exits=np.array(["target"]))
    assert rt2[0] == pytest.approx(c.charged_round_trip_usd("icm", "london"))   # v2 ignores exit types


def test_a_one_atr_stop_at_15m_or_less_is_flagged_when_cost_dominates() -> None:
    c = arms.load_costs(COSTS)
    assert c.fill_to_bar is not None
    atr5 = c.fill_to_bar.geometry.atr14_median_usd["5m"]
    assert c.cost_dominates("icm", "5m", atr5)                       # 1 ATR(5m): cost is a large share of R
    assert not c.cost_dominates("icm", "1h", 1.0)                    # only 15m and faster are flagged
    assert not c.cost_dominates("icm", "5m", 50.0)                   # a wide stop is not cost-dominated
    share = c.stop_cost_r("icm", 4.5)
    assert share == pytest.approx(max(c.fill_to_bar_charge_usd("icm", s, "stop")
                                      for s in ("asia", "london", "newyork", "all")) / 4.5)


def test_the_committed_v3_table_is_the_builders_output() -> None:
    text = (ROOT / "config" / "cost_of_record_v3.yaml").read_text()
    assert "GENERATED by `python -m goldbot.research.cost_of_record fill-to-bar`" in text
    assert "clustered by session day" in yaml.safe_load(text)["interval"]
