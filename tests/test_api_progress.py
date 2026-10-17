"""Progress tab API (ROADMAP W2-3, docs/ux/progress-screens.md "API gaps"): GET /api/gates, /api/attribution and
/api/trades/summary. Read-only for every signed-in role; nothing is written; accounts by config name with any run of
5+ digits cut to its last two; no operator-typed text, no raw exception text."""
import json
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from goldbot.api import progress
from goldbot.api.app import create_app
from goldbot.config import GateSettings, load_settings
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from goldbot.research.attribution import build_report, save_report
from tests.test_api_views import _login
from tests.test_attribution import FREE, _book, _trade
from tests.test_attribution import NOW as ATTR_NOW
from tests.test_attribution import S as ATTR_SETTINGS
from tests.test_phase_gates import (
    NOW,
    full_state,
    trade,
    trades_over,
    write_costs,
    write_phase,
    write_trades,
)

SETTINGS = load_settings()
LOGIN = "51234567"                      # stands for an MT5 login embedded in a config name
ACC = f"icm-demo-{LOGIN}"
CUT = "icm-demo-…67"


def snapshot(d: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(d)): (p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(d.rglob("*"))}


def view(state: Path, trials: Path | None = None, *, settings=SETTINGS, engines=(), now=NOW) -> progress.GatesView:
    return progress.gates_view(state, settings, now, list(engines), trials_path=trials or state / "none.jsonl")


def gate(v: progress.GatesView, name: str) -> progress.GateView:
    return next(g for g in v.gates if g.gate == name)


def item(v: progress.GatesView, g: str, name: str) -> progress.GateItemView:
    return next(i for i in gate(v, g).items if i.name == name)


# ------------------------------------------------------------------------------------------------ GET /api/gates
def test_gates_are_evaluated_from_the_evidence_and_nothing_is_written(tmp_path):
    state, trials = full_state(tmp_path)
    before = snapshot(tmp_path)
    v = view(state, trials)
    assert snapshot(tmp_path) == before                          # no gate_report.json, no evidence, no phase edit
    assert not (state / gates_phase.REPORT_FILE).exists()
    assert [g.met for g in v.gates] == [True, True, True, True] and v.phase == 3
    assert v.stop_rule.status == "ok" and v.cant_check is None


def test_a_recorded_gate_carries_the_time_from_the_gate_log(tmp_path):
    state, trials = full_state(tmp_path)
    v = view(state, trials)
    g = gate(v, "backtest_to_paper")
    assert g.recorded and g.recorded_utc == (NOW - pd.Timedelta(days=800)).to_pydatetime()
    assert gate(v, "tiny_live_to_full_size").recorded_utc is None


def test_paper_items_carry_progress_the_earliest_calendar_date_and_the_paper_interval(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    start = NOW - pd.Timedelta(days=7)
    write_phase(state, {"foundation_to_backtest": start - pd.Timedelta(days=5), "backtest_to_paper": start})
    write_trades(state, trades_over(12, [0.001, -0.0005, 0.0002] * 4, 6, 1))
    v = view(state)
    trades_ = item(v, "paper_to_tiny_live", "paper_trades")
    assert trades_.progress is not None and trades_.progress.model_dump() == {"value": 12, "target": 150, "unit": "trades"}
    days = item(v, "paper_to_tiny_live", "paper_days")
    assert days.progress is not None and days.progress.unit == "days" and days.progress.value == pytest.approx(7)
    assert days.progress.target == SETTINGS.gates.paper_min_days
    assert days.earliest_utc == (start + pd.Timedelta(days=SETTINGS.gates.paper_min_days)).to_pydatetime()
    exp = item(v, "paper_to_tiny_live", "paper_expectancy")
    assert exp.ci95 is not None and exp.ci95[0] < 0.000233 < exp.ci95[1]
    assert item(v, "paper_to_tiny_live", "chaos_drill").progress is None
    # live days not started: no earliest date (a calendar fact only once its clock has started)
    assert item(v, "tiny_live_to_full_size", "live_days").earliest_utc is None


def test_gate_evidence_never_carries_the_owners_typed_note_but_the_cli_still_prints_it(tmp_path):
    state, trials = full_state(tmp_path)
    gates_phase.record_evidence(state, "chaos_drill", passed=True, detail="killed terminal on host 10.1.2.3", now=NOW)
    v = view(state, trials)
    assert "10.1.2.3" not in v.model_dump_json() and "killed terminal" not in v.model_dump_json()
    assert item(v, "paper_to_tiny_live", "chaos_drill").evidence == f"passed on {NOW:%Y-%m-%d}"
    rep = gates_phase.evaluate_gates(state, SETTINGS, NOW, trials_path=trials)
    assert "killed terminal on host 10.1.2.3" in gates_phase.render(rep)


def test_proposed_thresholds_are_counted_from_the_settings_and_drop_out_when_signed_off(tmp_path):
    state, trials = full_state(tmp_path)
    v = view(state, trials)
    assert v.proposed_count == len(GateSettings.PROPOSED) == len(v.proposed)
    assert {p.name for p in v.proposed} == set(GateSettings.PROPOSED)
    assert item(v, "backtest_to_paper", "dsr").proposed and item(v, "paper_to_tiny_live", "paper_days").proposed
    assert not item(v, "paper_to_tiny_live", "paper_trades").proposed
    signed = SETTINGS.model_copy(update={"gates": SETTINGS.gates.model_copy(
        update={"signed_off": ["paper_min_days", "backtest_min_dsr"]})})
    w = view(state, trials, settings=signed)
    assert w.proposed_count == len(GateSettings.PROPOSED) - 2
    assert not item(w, "backtest_to_paper", "dsr").proposed and not item(w, "paper_to_tiny_live", "paper_days").proposed
    assert item(w, "tiny_live_to_full_size", "live_days").proposed


def test_signed_off_only_names_a_proposed_threshold_and_ships_empty():
    assert load_settings().gates.signed_off == []
    assert GateSettings(signed_off=["paper_min_days"]).signed_off == ["paper_min_days"]
    with pytest.raises(ValidationError):
        GateSettings(signed_off=["paper_min_trades"])            # a design number, not a proposal
    with pytest.raises(ValidationError):
        GateSettings(signed_off=["paper_min_dayz"])


def test_account_names_in_gate_evidence_have_their_digits_cut(tmp_path):
    state, trials = full_state(tmp_path)
    write_costs(state, account=ACC)
    body = view(state, trials).model_dump_json()
    assert LOGIN not in body and CUT in body


def _incident(i: int, frac: float) -> ClosedTrade:
    return trade(-frac, pnl=-frac * 10_000.0, account=ACC, days_ago=10 + i)


def test_stop_rule_is_structured_with_incidents_worst_first_capped_at_five_and_digits_cut(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    cap = SETTINGS.risk.weekly_cap
    fracs = [cap + 0.001 * (k + 1) for k in range(7)]
    write_trades(state, [_incident(i, f) for i, f in enumerate(fracs)])
    s = view(state).stop_rule
    assert s.status == "breached" and s.breach == ["single_loss"] and s.incident_count == 7
    assert [round(x.loss_frac, 6) for x in s.incidents] == [round(f, 6) for f in sorted(fracs, reverse=True)[:5]]
    assert {x.account for x in s.incidents} == {CUT} and s.incidents[0].mode == "demo"
    assert s.weekly_cap == cap and s.after_months == SETTINGS.gates.stop_after_months
    assert s.min_pooled_trades == SETTINGS.gates.stop_min_pooled_trades and s.confidence == SETTINGS.gates.stop_confidence
    assert s.min_trades_for_estimate == gates_phase.MIN_TRADES_FOR_BOUND == 2
    assert s.clock_source == "first_trade" and s.clock_start_utc == min(t.exit_utc for t in
                                                                         [_incident(i, f) for i, f in enumerate(fracs)])
    assert LOGIN not in view(state).model_dump_json()


def test_stop_rule_has_not_started_without_a_trade_or_the_paper_gate(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    s = view(state).stop_rule
    assert s.status == "not_started" and s.pooled_trades == 0 and s.clock_start_utc is None and s.clock_source is None
    write_phase(state, {"foundation_to_backtest": NOW - pd.Timedelta(days=9), "backtest_to_paper": NOW - pd.Timedelta(days=3)})
    s = view(state).stop_rule
    assert s.status == "ok" and s.clock_source == "paper_gate"
    assert s.clock_start_utc == (NOW - pd.Timedelta(days=3)).to_pydatetime()


def test_a_long_run_breach_is_named_in_the_breach_list(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    write_trades(state, trades_over(501, [-0.001, 0.0005] * 250 + [-0.001], 700, 1))
    write_phase(state, {"foundation_to_backtest": NOW - pd.Timedelta(days=760),
                        "backtest_to_paper": NOW - pd.Timedelta(days=750)})
    s = view(state).stop_rule
    assert s.status == "breached" and s.breach == ["long_run"] and s.lower_bound is not None and s.lower_bound < 0


def test_a_closed_trade_write_reported_lost_makes_the_stop_rule_cant_check(tmp_path):
    state, trials = full_state(tmp_path)
    s = view(state, trials, engines=[{"account": ACC, "closed_records_lost": [987654321]}]).stop_rule
    assert s.status == "cant_check" and s.cant_check is not None and "loss may be missing" in s.cant_check
    assert "987654321" not in s.model_dump_json() and LOGIN not in s.model_dump_json()


def test_unreadable_records_map_to_owner_wording_never_the_raw_error(tmp_path):
    state, trials = full_state(tmp_path)
    with open(gates_phase.closed_trade_file(state, "icmarkets-demo"), "a", encoding="utf-8") as fh:
        fh.write("{torn line from /home/someone/state\n")
    (state / gates_phase.PHASE_FILE).write_text("{not json")
    v = view(state, trials)
    assert v.cant_check is not None and "phase_state.json) is unreadable" in v.cant_check
    assert "closed-trade record is partly unreadable" in v.cant_check
    assert v.stop_rule.status == "cant_check" and v.stop_rule.cant_check is not None
    body = v.model_dump_json()
    assert "/home/someone" not in body and "line 1" not in body and "Expecting" not in body


def test_imputed_costs_of_gross_rows_are_structured(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    write_trades(state, [trade(0.001, gross_held_days=2.0), trade(0.0005, days_ago=11)])
    s = view(state).stop_rule
    assert s.imputed is not None
    assert s.imputed.n == 1 and s.imputed.charged_usd > 0 and s.imputed.unpriced == 0


def test_gates_cache_serves_one_evaluation_for_60_seconds():
    clock = [1000.0]
    cache = progress.GatesCache(ttl_s=60.0, clock=lambda: clock[0])
    calls: list[int] = []

    def build() -> int:
        calls.append(1)
        return len(calls)
    assert cache.get(build) == 1
    clock[0] += 59.9
    assert cache.get(build) == 1
    clock[0] += 0.2
    assert cache.get(build) == 2


# ------------------------------------------------------------------------------------------------ GET /api/attribution
def _report_with_exits(state: Path) -> None:
    trades = (_book(40, 20, hour=8) + [_trade(100 + i, r=0.3, barrier="time", day=i % 30) for i in range(31)]
              + _book(3, 30, side=-1, hour=14, agent="breakout-g1-2222222222", tf="15m"))
    save_report(build_report(trades, now=ATTR_NOW, costs=FREE, settings=ATTR_SETTINGS), state)


def test_attribution_view_drops_trades_and_calibration_and_keeps_each_rows_own_bar(tmp_path):
    _report_with_exits(tmp_path)
    raw = json.loads((tmp_path / "attribution.json").read_text())
    v = progress.attribution_view(tmp_path, SETTINGS, ATTR_NOW + pd.Timedelta(hours=1))
    assert v.report is not None and v.cant_check is None and not v.stale
    body = v.model_dump()
    assert "trades" not in body["report"] and "calibration" not in body["report"]
    assert v.report.t_bar == raw["t_bar"] and v.report.m == raw["m"] and v.report.m > 0
    # quant F1: each row keeps the bar it was judged on (fewer days need more), not the report's nominal bar
    for b, rows in raw["breakdowns"].items():
        for k, cell in rows.items():
            assert v.report.breakdowns[b][k].t_bar == cell["t_bar"]
    assert any(c.t_bar != v.report.t_bar for rows in v.report.breakdowns.values() for c in rows.values())
    one = v.report.breakdowns["side"]["long"]
    assert one.se is not None and one.ci_adj is not None and one.t_bar is not None
    assert one.ci_adj[1] - one.ci_adj[0] == pytest.approx(2 * one.t_bar * one.se, rel=1e-6)


def test_exit_target_and_stop_rows_read_by_construction(tmp_path):
    _report_with_exits(tmp_path)
    v = progress.attribution_view(tmp_path, SETTINGS, ATTR_NOW)
    assert v.report is not None
    for rows in (v.report.exits, v.report.breakdowns["exit"]):
        for k in ("target", "stop"):
            assert rows[k].verdict_adj == "by construction" and rows[k].verdict == "by construction"
            assert rows[k].ci_adj is None
        assert rows["time"].verdict_adj != "by construction"


def test_attribution_is_stale_only_after_its_slot_plus_the_lateness_allowance(tmp_path):
    _report_with_exits(tmp_path)                         # as_of = Fri 9 Oct 23:50, the job's slot
    sched = SETTINGS.scheduler.attribution
    nxt = ATTR_NOW + pd.Timedelta(days=1)
    fresh = progress.attribution_view(tmp_path, SETTINGS, nxt + pd.Timedelta(hours=sched.max_late_hours - 0.1))
    stale = progress.attribution_view(tmp_path, SETTINGS, nxt + pd.Timedelta(hours=sched.max_late_hours + 0.1))
    assert not fresh.stale and stale.stale
    assert fresh.next_run_utc == (nxt + pd.Timedelta(days=1)).to_pydatetime()


def test_attribution_missing_is_empty_and_unreadable_is_cant_check(tmp_path):
    v = progress.attribution_view(tmp_path, SETTINGS, ATTR_NOW)
    assert v.report is None and v.cant_check is None and not v.stale and v.next_run_utc is not None
    (tmp_path / "attribution.json").write_text("{torn /home/someone")
    v = progress.attribution_view(tmp_path, SETTINGS, ATTR_NOW)
    assert v.report is None and v.cant_check is not None and "/home/someone" not in v.model_dump_json()


def test_live_fills_and_cost_source_name_the_account_with_its_digits_cut(tmp_path):
    fills = pd.DataFrame({"ts_utc": pd.date_range(ATTR_NOW - pd.Timedelta(days=40), periods=40, freq="D"),
                          "side": [1] * 40, "requested": [2400.0] * 40, "filled": [2400.2] * 40})
    rep = build_report(_book(31, 0), now=ATTR_NOW, costs=FREE.model_copy(update={"source": f"cost table {ACC} built"}),
                       settings=ATTR_SETTINGS, fills={ACC: fills}, orders={ACC: {}})
    save_report(rep, tmp_path)
    v = progress.attribution_view(tmp_path, SETTINGS, ATTR_NOW)
    assert v.report is not None and list(v.report.live) == [CUT]
    assert LOGIN not in v.model_dump_json()


# ------------------------------------------------------------------------------------------------ GET /api/trades/summary
def _gbp(ret_pnl: float, exit_utc: pd.Timestamp, **kw) -> ClosedTrade:
    base = dict(account_id=ACC, broker="icmarkets", mode="demo", exit_utc=exit_utc, ret=0.001, pnl=ret_pnl,
                equity_before=1_000.0, commission=1.0, swap=0.0, pnl_basis="net", pnl_currency="GBP")
    return ClosedTrade(**{**base, **kw})


def test_trades_summary_is_the_last_30_opportunity_days_per_account(tmp_path):
    now = pd.Timestamp("2026-10-16 12:00", tz="UTC")                   # opportunity day Fri 16 Oct
    rows = [
        _gbp(40.0, pd.Timestamp("2026-10-13 10:00", tz="UTC")),
        _gbp(15.0, pd.Timestamp("2026-10-13 21:59", tz="UTC")),         # still Tue 13 Oct: 55 that day
        _gbp(-12.8, pd.Timestamp("2026-10-08 22:30", tz="UTC")),        # after 22:00 UTC: Fri 9 Oct
        _gbp(7.0, pd.Timestamp("2026-10-15 09:00", tz="UTC"), pnl_basis="gross", commission=None),   # flagged, not summed
        _gbp(99.0, pd.Timestamp("2026-09-16 10:00", tz="UTC")),         # Wed 16 Sep: 31st day back, out of the window
    ]
    write_trades(tmp_path, rows)
    (tmp_path / f"engine_{ACC}.json").write_text(json.dumps({"account": ACC, "mode": "demo", "currency": "GBP"}))
    (s,) = progress.trades_summary(tmp_path, now, [json.loads((tmp_path / f"engine_{ACC}.json").read_text())])
    assert s.account == CUT and s.mode == "demo" and s.currency == "GBP" and s.days == 30
    assert s.first_day == "2026-09-17" and s.last_day == "2026-10-16"
    assert s.net == pytest.approx(42.2) and s.n == 4 and s.net_n == 3 and s.gross_rows == 1
    assert s.days_traded == 2 and s.trading_days == 22
    assert s.days_at_target == 1 and s.target == 50.0
    assert s.best_day is not None and (s.best_day.day, s.best_day.net, s.best_day.n) == ("2026-10-13", 55.0, 2)
    assert s.worst_day is not None and (s.worst_day.day, s.worst_day.net) == ("2026-10-09", -12.8)
    assert [d.day for d in s.by_day] == ["2026-10-09", "2026-10-13"]


def test_an_engine_account_without_trades_is_listed_empty(tmp_path):
    (s,) = progress.trades_summary(tmp_path, NOW, [{"account": ACC, "mode": "demo", "currency": "USD"}])
    assert s.n == 0 and s.net == 0 and s.best_day is None and s.by_day == [] and s.account == CUT
    assert s.days_at_target is None                     # counted on a GBP account only (ADR 0004's £50)


# ------------------------------------------------------------------------------------------------ the routes
@pytest.fixture
def client(tmp_path):
    state, _ = full_state(tmp_path)
    write_costs(state, account=ACC)
    _report_with_exits(state)
    app = create_app(state, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    return c, _login(c, app), state


@pytest.mark.parametrize("path", ["/api/gates", "/api/attribution", "/api/trades/summary"])
def test_progress_routes_need_a_sign_in_and_serve_a_viewer_without_identifiers(client, path):
    c, h, _ = client
    assert c.get(path).status_code == 401
    r = c.get(path, headers=h["viewer"])
    assert r.status_code == 200
    assert LOGIN not in r.text and "o@x.io" not in r.text and "v@x.io" not in r.text


def test_gates_route_writes_nothing_and_is_cached(client):
    c, h, state = client
    gate_files = [state / gates_phase.PHASE_FILE, state / gates_phase.EVIDENCE_FILE]
    before = [(f.stat().st_mtime_ns, f.read_bytes()) for f in gate_files]
    first = c.get("/api/gates", headers=h["viewer"]).json()
    assert first["proposed_count"] == len(GateSettings.PROPOSED) and first["stop_rule"]["status"] == "ok"
    assert not (state / gates_phase.REPORT_FILE).exists()
    assert [(f.stat().st_mtime_ns, f.read_bytes()) for f in gate_files] == before
    write_trades(state, [])                               # within 60 s the cached evaluation is served
    assert c.get("/api/gates", headers=h["owner"]).json() == first
