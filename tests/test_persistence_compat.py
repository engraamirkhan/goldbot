"""ADR 0010, "persisted state is read tolerantly, built strictly": after a rollback the older release reads files a
newer release wrote. Every persisted model is written by its real writer (or in its real format), given a field this
release does not know, and read back through its real load path: it must load as if the field were absent.

The AST guard below keeps it that way: every model_validate / model_validate_json in goldbot/ either goes through
goldbot.base.load_record / load_record_json or is on the explicit allowlist with its reason (strict: credentials,
the owner's halt, Settings, bridge order requests; or not persisted state)."""
from __future__ import annotations

import ast
import importlib
import json
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pandas as pd
import pytest
from pydantic import BaseModel, Field

from goldbot.base import Record, SkewSummary
from goldbot.config import load_settings
from goldbot.data import crossfeed, tv_pull
from goldbot.data.ticks import TickIndex
from goldbot.engine import cost_probe, exploration
from goldbot.engine.runner import OpenTrade, PendingClose, SentOrder
from goldbot.engine.shadow import ShadowBook, VersionBook
from goldbot.execution.costs import (
    BrokerTerms,
    CostTable,
    PublishedCostTable,
    SlippageStat,
    SpreadStat,
    load_research_cost_table,
)
from goldbot.ops import health, jobs, ledger, scheduler
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade, load_closed_trades
from goldbot.research import ruin
from goldbot.research.attribution import AttributionReport
from goldbot.research.director import ResearchPlan
from goldbot.research.metrics import DaySummedDsr, DsrInputs
from goldbot.research.model_registry import ModelEntry, ModelRegistry
from goldbot.research.population import Member, Population
from goldbot.research.promotion import PerfStats
from goldbot.risk import adaptive, hwm, limits_profile
from goldbot.risk.pilot import PilotState
from goldbot.telegram import automode
from goldbot.telegram.approvals import AskReason, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import STATE_FILE as DIGEST_FILE
from goldbot.telegram.digest import DigestSchedule, DigestState
from goldbot.telegram.outbox import Outbox, OutboxState
from tests.cards import submit_shown

T0 = pd.Timestamp("2026-10-05 08:00", tz="UTC")
NEW = {"future_field": {"from": "a newer release"}}
ROOT = Path(__file__).resolve().parents[1]


def _newer(obj: Any) -> Any:
    """`obj` (a JSON object, or a list of them) with a field this release does not know."""
    return [_newer(o) for o in obj] if isinstance(obj, list) else {**obj, **NEW}


def _rewrite(path: Path, fn: Callable[[Any], Any] = _newer) -> None:
    path.write_text(json.dumps(fn(json.loads(path.read_text()))))


def _proposal(pid: str = "p1", **kw: Any) -> Proposal:
    return Proposal(proposal_id=pid, account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2400.0, stop=2396.0,
                    target=2406.0, p=0.6, ev_r=0.3, spread_points=20.0, top_features=[("f", 0.1)], **kw)


# ------------------------------------------------------------------------------------------------- the cases
def case_closed_trade(tmp: Path) -> None:
    t = ClosedTrade(account_id="icm-demo", broker="icmarkets", mode="demo", exit_utc=T0, ret=0.001, pnl=10.0,
                    equity_before=10_000.0)
    append_closed_trade(tmp, t)
    f = next(tmp.glob("closed_trades_*.jsonl"))
    f.write_text(json.dumps(_newer(json.loads(f.read_text()))) + "\n")
    assert load_closed_trades(tmp) == ([t], None)


def case_closed_trade_from_before_ret_basis(tmp: Path) -> None:
    """A record written before `ret_basis` existed (or rewritten by an older release) reads it as None: unknown, never
    "pnl" (the cost imputation then charges it by its pnl_basis, as a legacy row)."""
    t = ClosedTrade(account_id="icm-demo", broker="icmarkets", mode="demo", exit_utc=T0, ret=0.001, pnl=10.0,
                    equity_before=10_000.0, ret_basis="price")
    row = json.loads(t.model_dump_json())
    del row["ret_basis"]
    (tmp / "closed_trades_icm-demo.jsonl").write_text(json.dumps(row) + "\n")
    (back,), err = load_closed_trades(tmp)
    assert err is None and back.ret_basis is None


def case_closed_trade_price_return_without_ret_basis(tmp: Path) -> None:
    """Review R1: the record the broker-deals path with no conversion wrote before `ret_basis` (and still writes under
    a rolled-back release): a price return labelled pnl_basis "net", r None, the initial stop known. Read back, it is
    charged commission and swap as a price return, never taken as net."""
    from goldbot.ops.cost_imputation import CostImputer
    row = {"account_id": "icm-demo", "broker": "icm", "mode": "demo", "exit_utc": "2026-10-06T12:00:00Z",
           "ret": -0.0026, "pnl": -8.9, "equity_before": 1_000.0, "position_id": 7, "side": 1, "lots": 0.01,
           "entry_utc": "2026-10-06T10:00:00Z", "entry_price": 4_150.0, "initial_sl": 4_139.1, "r": None,
           "commission": 0.05, "swap": 0.0, "money_source": "broker", "pnl_basis": "net"}
    (tmp / "closed_trades_icm-demo.jsonl").write_text(json.dumps(row) + "\n")
    (back,), err = load_closed_trades(tmp)
    assert err is None and back.ret_basis is None
    c = CostImputer.from_settings(load_settings()).charge(back)
    assert c.imputed and c.priced and c.ret > 0


def case_bus_proposal(tmp: Path) -> None:
    bus = ApprovalBus(tmp)
    p = _proposal()
    bus.publish(p)
    _rewrite(bus.pending_dir / "p1.json")
    d = submit_shown(bus, "p1", approve=True, reason_code=None, by="dashboard:o")    # decisions stay strict (allowlist)
    assert bus.pending() == [] and bus.get("p1") == p
    assert bus.decisions_for({"p1"}) == [d] and bus.recent() == [(p, d)]


def case_bus_finished_proposal(tmp: Path) -> None:
    bus = ApprovalBus(tmp)
    p = _proposal(outcome=Outcome.REJECTED, reason_code="news")
    bus.archive(p)
    _rewrite(bus.done_dir / "p1.json")
    assert bus.outcome("p1") == Outcome.REJECTED.value and bus.recent() == [(p, None)]
    assert automode._finished_proposals(bus) == [p]


def case_shadow_book(tmp: Path) -> None:
    book = ShadowBook(tmp)
    book.track("v1", T0)
    book.open_trade(version="v1", agent_id="a", side=1, bar_ts=T0, entry=2400.0, atr_usd=2.0, target_atr=1.5,
                    stop_atr=1.0, max_bars=4, p=0.6)
    book.save(T0)
    _rewrite(tmp / "shadow_book.json", lambda d: {k: _newer(v) for k, v in d.items()})
    assert ShadowBook(tmp).books == book.books
    assert ruin.r_from_shadow_book(tmp, version="v1").r.size == 0          # read path of the ruin study


def case_shadow_stats(tmp: Path) -> None:
    st = PerfStats(n_trades=3, sharpe_ann=1.0, hit_rate=0.5, max_dd=0.01, trades_per_week=1.0)
    (tmp / "shadow_v1.json").write_text(json.dumps(_newer(st.model_dump(mode="json"))))
    entry = SimpleNamespace(version="v1")
    assert jobs.shadow_stats(SimpleNamespace(state_dir=tmp), entry) == st         # type: ignore[arg-type]


def case_model_registry_and_backtest(tmp: Path) -> None:
    bt = PerfStats(n_trades=300, sharpe_ann=1.2, hit_rate=0.55, max_dd=0.05, trades_per_week=3.0)
    e = ModelEntry(version="v1", family="f", agent_id="a", status="champion", created_utc=T0, feature_version="1",
                   feature_names=["x"], artefact="v1.joblib", sha256="0" * 64, backtest=bt.model_dump(mode="json"))
    reg = ModelRegistry(tmp)
    reg.entries.append(e)
    reg._save()
    _rewrite(reg.path)
    again = ModelRegistry(tmp)
    assert again.entries == [e]
    assert jobs._backtest_stats(e.model_copy(update={"backtest": _newer(e.backtest)})) == bt


def case_population(tmp: Path) -> None:
    m = Member(agent_id="a", family="f", config={"k": 1}, created_utc=T0, status_since_utc=T0)
    pop = Population(tmp / "population.json")
    pop.members["a"] = m
    pop.save()
    _rewrite(pop.path)
    assert Population(tmp / "population.json").members == {"a": m}


def case_research_plan(tmp: Path) -> None:
    plan = ResearchPlan(created_utc=T0, quarter="2026Q4", quarter_budget=10, quarter_used=1, total_budget=5, floor=1,
                        cap=None, budget={"f": 5}, grid_budget={"f": 1}, unallocated=0, holdout_from="2025-01-01",
                        holdout_to="2026-01-01", holdout_trials_ignored=0, focus=[], evidence=[])
    plan.save(tmp / "plan.json")
    _rewrite(tmp / "plan.json")
    assert ResearchPlan.load(tmp / "plan.json") == plan


def case_costs(tmp: Path) -> None:
    terms = BrokerTerms(account_id="icm-demo", measured_utc=T0, swap_long_usd_per_lot=-5.0)
    terms.save(tmp / "terms.json")
    _rewrite(tmp / "terms.json")
    assert BrokerTerms.load(tmp / "terms.json") == terms
    table = CostTable(account_id="icm-demo", built_utc=T0, spread={"london": SpreadStat(median=0.2, p90=0.3, n=10)},
                      slippage={"london:market": SlippageStat(mean=0.1, n=5, from_prior=False)},
                      commission_per_lot_side_usd=3.5, slippage_prior_usd=0.1)
    table.save(tmp / "costs.json")
    _rewrite(tmp / "costs.json")
    assert CostTable.load(tmp / "costs.json") == table
    assert load_research_cost_table(tmp / "costs.json") == table
    pub = PublishedCostTable.from_table(table, "icmarkets")
    (tmp / "published.json").write_text(json.dumps(_newer(pub.model_dump(mode="json"))))
    assert load_research_cost_table(tmp / "published.json") == pub.to_cost_table()


def case_scheduler(tmp: Path) -> None:
    sched = scheduler.Scheduler(tmp / "scheduler.json", clock=lambda: T0)
    st = scheduler.JobState(last_slot=T0, runs=2)
    (tmp / "scheduler.json").write_text(json.dumps({"jobs": {"j": _newer(st.model_dump(mode="json"))}}))
    assert scheduler._file_jobs(tmp / "scheduler.json")[0] == {"j": st}
    with sched.db.write() as conn:
        conn.execute("INSERT INTO scheduler_jobs (name, body) VALUES (?, ?)",
                     ("j", json.dumps(_newer(st.model_dump(mode="json")))))
        assert scheduler.merge_scheduler_json(tmp / "scheduler.json", conn) == 0     # same slot: the DB row stays
    assert scheduler.Scheduler(tmp / "other.json", clock=lambda: T0).state == {"j": st}


def case_health(tmp: Path) -> None:
    rep = health.HealthReport(ts=T0, status="ok", checks=[health.Check(name="c", status="ok", reason="fine")])
    health.HealthWatch(tmp).record(rep)
    _rewrite(tmp / health.WATCH_FILE)
    assert health.HealthWatch(tmp).state == health.WatchState(ts=T0, statuses={"c": "ok"})
    assert health._last_run(tmp) == T0
    ctx = health.HealthContext(state_dir=tmp, now=T0, get_secret=lambda k: None)
    assert health.check_alert_loop(ctx).status == "ok"
    (tmp / "report.json").write_text(json.dumps(_newer(rep.model_dump(mode="json"))))
    assert health._load_report(str(tmp / "report.json")) == rep


def case_digest(tmp: Path) -> None:
    st = DigestState(last_slot="2026-10-05", sent_utc="2026-10-05T05:45:00+00:00")
    (tmp / DIGEST_FILE).write_text(json.dumps(_newer(st.model_dump())))
    assert DigestSchedule(tmp).state == st


def case_outbox(tmp: Path) -> None:
    (tmp / "telegram_outbox.json").write_text(json.dumps(_newer({"sent": {"p1": [[42, 7]]}, "reports_offset": None})))
    assert Outbox(tmp).sent == {"p1": [(42, 7)]}


def case_reconcile(tmp: Path) -> None:
    rep = crossfeed.ReconcileReport(account_id="icm-demo", ts_utc=T0, window_from=T0 - pd.Timedelta(days=1),
                                    window_to=T0, tolerance=0.05, broker_minutes=100, engine_minutes=100, matched=100,
                                    close_mismatch=0, missing_engine=0, missing_broker=0, max_close_diff=0.0,
                                    divergence=0.0, severity="ok")
    rep.save(tmp)
    _rewrite(crossfeed.reconcile_file(tmp, "icm-demo"))
    ctx = health.HealthContext(state_dir=tmp, now=T0, settings=load_settings(), get_secret=lambda k: None)
    assert "unreadable" not in crossfeed.check_reconciliation(ctx, "icm-demo").reason


def case_tick_index(tmp: Path) -> None:
    """state/ticks/<account>/index.json (BACKLOG 79) with a field from a newer release, top level and per day."""
    from goldbot.data import ticks
    day = T0.date()
    ticks.write_day(tmp, "icm-demo", day, pd.DataFrame({"ts_utc": [T0], "bid": [2400.0], "ask": [2400.2]}),
                    symbol="XAUUSD", server_tz="Europe/Athens", pulled_utc=T0)
    _rewrite(ticks.account_dir(tmp, "icm-demo") / ticks.INDEX_FILE,
             lambda o: _newer({**o, "days": {k: _newer(v) for k, v in o["days"].items()}}))
    assert ticks.read_index(tmp, "icm-demo")[day.isoformat()].rows == 1
    ctx = health.HealthContext(state_dir=tmp, now=T0, settings=load_settings(), get_secret=lambda k: None)
    assert "unreadable" not in ticks.check_tick_record(ctx, "icm-demo").reason


def case_ledger(tmp: Path) -> None:
    """aux.db broker ledger rows (snapshot, balance deal, report) with a field from a newer release."""
    from goldbot.db import Database
    db = Database.open(tmp, "aux")
    snap = ledger.BalanceSnapshot(account_id="icm-demo", ts_utc=T0, balance=1000.0, equity=990.0, margin=10.0,
                                  margin_free=980.0, currency="GBP")
    deal = ledger.BalanceDeal(account_id="icm-demo", ticket=5, ts_utc=T0, kind="deposit", deal_type=2, amount=50.0)
    for table, row in (("balance_snapshots", ledger.SNAPSHOTS.row(snap)), ("balance_deals", ledger.DEALS.row(deal))):
        row["body"] = json.dumps(_newer(json.loads(row["body"])))
        with db.write() as conn:
            conn.execute(f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)})", row)
    assert ledger.snapshots(db, "icm-demo") == [snap] and ledger.balance_deals(db, "icm-demo") == [deal]
    rep = ledger.LedgerReport(account_id="icm-demo", ts_utc=T0, window_from=T0 - pd.Timedelta(days=1), currency="GBP",
                              positions_checked=1, matched=0, pending=0, status="warn",
                              diffs=[ledger.TradeDiff(position_id=7, kind="missing", where="closed")])
    row = ledger.REPORTS.row(rep)
    row["body"] = json.dumps({**_newer(json.loads(row["body"])), "diffs": _newer(json.loads(row["body"])["diffs"])})
    with db.write() as conn:
        conn.execute("INSERT INTO ledger_reports (key, account_id, ts_ns, status, body) "
                     "VALUES (:key, :account_id, :ts_ns, :status, :body)", row)
    assert ledger.latest_report(tmp, "icm-demo") == rep


def case_cost_probe(tmp: Path) -> None:
    """aux.db cost_probes row (ADR 0037) with a field from a newer release: the costs reader still reads it."""
    from goldbot.db import Database
    db = Database.open(tmp, "aux")
    rec = cost_probe.ProbeRecord(client_order_id="icm-demo-1-a", account_id="icm-demo", agent_id="a", version="a-v2",
                                 side=1, lots=0.01, signal_price=2400.0, requested_price=2400.2, fill_price=2400.3,
                                 sent_utc=T0)
    row = cost_probe.LEDGER.row(rec)
    row["body"] = json.dumps(_newer(json.loads(row["body"])))
    with db.write() as conn:
        conn.execute(f"INSERT INTO cost_probes ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)})", row)
    assert cost_probe.probes(tmp, "icm-demo") == [rec]


def case_cost_probe_skip(tmp: Path) -> None:
    """aux.db cost_probe_skips row (a mirror probe skipped at its own stop) with a field from a newer release."""
    from goldbot.db import Database
    db = Database.open(tmp, "aux")
    rec = cost_probe.SkipRecord(client_order_id="cp-1-a", account_id="icm-demo", agent_id="a", version="a-v2",
                                timeframe="4h", side=1, taken=True, signal_utc=T0, atr_usd=30.0, native_stop_loss=40.0,
                                probe_max_loss=4.0)
    row = cost_probe.SKIPS.row(rec)
    row["body"] = json.dumps(_newer(json.loads(row["body"])))
    with db.write() as conn:
        conn.execute(f"INSERT INTO cost_probe_skips ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)})",
                     row)
    assert cost_probe.skips(tmp, "icm-demo") == [rec]


def case_exploration_trade(tmp: Path) -> None:
    """aux.db exploration_trades row (plan 2026-10-25, lane L2) with a field from a newer release."""
    rec = exploration.ExplorationRecord(client_order_id="exabcde-1741118400-12345678", account_id="icm-probe",
                                        arm_id="a", family="donchian", tf="15m", side=1, lots=0.01, signal_utc=T0,
                                        sent_utc=T0, atr_usd=10.0, stop_usd=10.0, target_usd=20.0, hold_s=3600)
    db = exploration.ledger_db(str(tmp))
    row = exploration.LEDGER.row(rec)
    row["body"] = json.dumps(_newer(json.loads(row["body"])))
    with db.write() as conn:
        conn.execute(f"INSERT INTO exploration_trades ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)})",
                     row)
    assert exploration.trades(str(tmp), "icm-probe") == [rec]


def case_exploration_twin(tmp: Path) -> None:
    """aux.db exploration_twins row (H-80 BXR's virtual twins, engine lane E3) with a field from a newer release."""
    rec = exploration.TwinRecord(twin_id="exabcde-1741118400-12345678:fixed", client_order_id="exabcde-1741118400-12345678",
                                 account_id="icm-probe", broker="icm", arm_id="BXF15", of_arm_id="BXR15",
                                 exit_mode="fixed", tf="15m", side=1, signal_utc=T0, entry_price=2900.0, risk_usd=9.0,
                                 target_r=2.0, hold_bars=96)
    db = exploration.ledger_db(str(tmp))
    row = exploration.TWINS.row(rec)
    row["body"] = json.dumps(_newer(json.loads(row["body"])))
    with db.write() as conn:
        conn.execute(f"INSERT INTO exploration_twins ({', '.join(row)}) VALUES ({', '.join(':' + c for c in row)})",
                     row)
    assert exploration.twins(str(tmp), "icm-probe") == [rec]


def case_hwm_confirmation(tmp: Path) -> None:
    """core.db high-water-mark confirmation row (BACKLOG 54, ADR 0018) with a field from a newer release."""
    conf = hwm.HwmConfirmation(account_id="icm-demo", mark=10_000.0, mark_utc=T0, pending_balance=10_500.0,
                               pending_since_utc=T0, candidate_utc=T0)
    assert hwm.HwmStore(tmp).save(conf)
    with sqlite3.connect(tmp / "core.db") as conn:
        body = conn.execute("SELECT body FROM hwm_confirmation").fetchone()[0]
        conn.execute("UPDATE hwm_confirmation SET body = ?", (json.dumps(_newer(json.loads(body))),))
    assert hwm.HwmStore(tmp).load("icm-demo") == conf
    assert hwm.read_only_confirmation(tmp, "icm-demo") == conf


def case_pilot_state(tmp: Path) -> None:
    """core.db live pilot record (ADR 0055) with a field from a newer release: the sticky stop still loads."""
    from goldbot.engine import pilot
    from goldbot.risk.pilot import PilotState
    rec = PilotState(account_id="icm-live", pilot_id="icm-live-adr55", realised_loss=25.0, entries=7, stopped_utc=T0,
                     stop_reason="stop_loss", read_utc=T0)
    assert pilot.PilotStore(tmp).save(rec)
    with sqlite3.connect(tmp / "core.db") as conn:
        body = conn.execute("SELECT body FROM pilot_state").fetchone()[0]
        conn.execute("UPDATE pilot_state SET body = ?", (json.dumps(_newer(json.loads(body))),))
    assert pilot.PilotStore(tmp).load("icm-live") == rec


def case_limits_profile(tmp: Path) -> None:
    """core.db demo-limits revert row and change log (ADR 0032) with a field from a newer release."""
    store = limits_profile.LimitsStore(tmp)
    state = limits_profile.LimitsState(account_id="icm-demo", reverted=True, since_utc=T0, reason="stage size_down")
    cur = limits_profile.RiskLimitsCurrent(profile="default", daily_cap=0.02, weekly_cap=0.05, reason="reverted")
    change = limits_profile.LimitsChange(account_id="icm-demo", ts_utc=T0, previous_profile="demo-2026-10-18",
                                         current=cur)
    assert store.save(state) and store.log_change(change)
    with sqlite3.connect(tmp / "core.db") as conn:
        for table in ("limits_state", "limits_changes"):
            body = conn.execute(f"SELECT body FROM {table}").fetchone()[0]
            conn.execute(f"UPDATE {table} SET body = ?", (json.dumps(_newer(json.loads(body))),))
    assert store.load("icm-demo") == state and store.changes("icm-demo") == [change]


def case_limits_state(tmp: Path) -> None:
    """core.db adaptive-limits row (ADR 0031) with a field from a newer release, at the top level and in the limits."""
    from datetime import date

    state = adaptive.AdaptiveState(account_id="icm-demo", day=date(2026, 11, 2), up_streak={"daily_cap": 2},
                                 limits=adaptive.Limits(daily_cap=0.025, weekly_cap=0.06, supervisor_daily_cap=0.015,
                                                        supervisor_weekly_cap=0.04, min_lot_risk_cap=0.006))
    store = adaptive.AdaptiveStore(tmp)
    store.save(state, [])
    with sqlite3.connect(tmp / "core.db") as conn:
        body = json.loads(conn.execute("SELECT body FROM adaptive_state").fetchone()[0])
        body = {**_newer(body), "limits": _newer(body["limits"])}
        conn.execute("UPDATE adaptive_state SET body = ?", (json.dumps(body),))
    assert adaptive.AdaptiveStore(tmp).load("icm-demo") == state


def case_risk_term(tmp: Path) -> None:
    """ADR 0039: a strategy's risk term read back from its dump (the limits change log / a journal) with a field from
    a newer release: it still loads, at the clamped risk."""
    term = adaptive.RiskTerm(r=0.004, n_days=3, k_vol=0.5, reason="evidence")
    got = limits_profile._terms({"v1": _newer(term.model_dump())}, {"v1": 0.003})
    assert got == {"v1": term.model_copy(update={"r": 0.003})}


def case_trial_dsr_inputs(tmp: Path) -> None:
    """A trial row's stored deflated-Sharpe inputs (results.model_filtered.dsr_inputs, metrics.DsrInputs; quant review
    2026-10-16) with a field from a newer release: gate 1 still recomputes the DSR from them."""
    from goldbot.ops.gates_phase import current_dsr
    inputs = DsrInputs(sr=0.12, n=600, skew=0.0, kurtosis=3.0, var_sr_trials=1 / 600)
    row = json.loads(json.dumps({"results": {"model_filtered": {"dsr_inputs": _newer(inputs.model_dump())}}}))
    assert current_dsr(row, 7) == (inputs.dsr(7), "")


def case_trial_day_summed_dsr(tmp: Path) -> None:
    """A trial row's stored day-summed DSR (results.model_filtered.day_summed, metrics.DaySummedDsr; E-6) with a field
    from a newer release at the top and in both nested inputs: the evaluator's reading still re-reads it on today's K."""
    from goldbot.research.gates import day_summed_check
    per_trade = DsrInputs(sr=0.15, n=900, skew=0.0, kurtosis=3.0, var_sr_trials=1 / 900)
    day = DsrInputs(sr=0.2, n=450, skew=0.0, kurtosis=3.0, var_sr_trials=1 / 450)
    stored = DaySummedDsr.of(per_trade, day, 5).model_dump()
    stored = _newer({**stored, "per_trade_r": _newer(stored["per_trade_r"]), "day_summed": _newer(stored["day_summed"])})
    row = json.loads(json.dumps({"status": "evaluated", "results": {"model_filtered": {"day_summed": stored}}}))
    check = day_summed_check(row, [row])
    assert check["n_trials"] == 1 and check["dsr_day_summed"] == day.dsr(1)


def case_attribution_report(tmp: Path) -> None:
    """state/attribution.json (research/attribution.py AttributionReport) with a field from a newer release at the top
    and in a cell: the Progress tab's results view still serves it (goldbot/api/progress.py)."""
    from goldbot.api.progress import attribution_view
    from goldbot.research.attribution import CostModel, build_report, save_report
    free = CostModel(spread_usd={}, slippage_usd={}, slippage_prior_usd=0.0, commission_usd_per_oz=0.0, swap=None,
                     source="test")
    save_report(build_report([], now=T0, costs=free, settings=load_settings().attribution), tmp)
    _rewrite(tmp / "attribution.json", lambda d: {**_newer(d), "overall": _newer(d["overall"])})
    v = attribution_view(tmp, load_settings(), T0)
    assert v.cant_check is None and v.report is not None and v.report.overall.n == 0


def case_daily_review(tmp: Path) -> None:
    """state/aux/daily_review/<day>.json and weekly-<sunday>.json (research/daily_review.py DailyReview,
    WeeklyRollup) with a field from a newer release at the top and in a nested row: the morning report's review
    lines and the research director's roll-up still read them."""
    from goldbot.research import daily_review as dr
    from goldbot.telegram.digest import review_section
    cost = dr.SessionCosts(broker="icm", host="all", session="all", fills=3, spread_usd=0.2, round_trip_usd=0.37)
    review = dr.DailyReview(day="2026-10-25", generated_utc=T0, costs=[cost], volume_total=3, plan_total=370)
    week = dr.WeeklyRollup(week_ending="2026-10-25", generated_utc=T0, trials_left=2, proposals=[
        dr.VariantProposal(title="Limit-entry variant", rationale="fill rate 80% over the week")])
    for path, rec in ((dr.review_path(tmp, "2026-10-25"), review), (dr.weekly_path(tmp, "2026-10-25"), week)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rec.model_dump_json())
    _rewrite(dr.review_path(tmp, "2026-10-25"), lambda d: {**_newer(d), "costs": [_newer(d["costs"][0])]})
    _rewrite(dr.weekly_path(tmp, "2026-10-25"), lambda d: {**_newer(d), "proposals": [_newer(d["proposals"][0])]})
    assert dr.load_review(tmp, "2026-10-25") == review and dr.load_weekly(tmp, "2026-10-25") == week
    assert review_section(tmp, "2026-10-25")[0].text.startswith("Review: costs ICM $0.37/oz")


CASES = [case_closed_trade, case_closed_trade_from_before_ret_basis, case_closed_trade_price_return_without_ret_basis,
         case_bus_proposal, case_bus_finished_proposal, case_shadow_book,
         case_shadow_stats, case_model_registry_and_backtest, case_population, case_research_plan, case_costs,
         case_scheduler, case_health, case_digest, case_outbox, case_reconcile, case_ledger, case_cost_probe, case_cost_probe_skip, case_hwm_confirmation, case_pilot_state, case_limits_profile, case_risk_term,
         case_limits_state,
         case_trial_dsr_inputs, case_trial_day_summed_dsr, case_attribution_report, case_tick_index,
         case_daily_review, case_exploration_trade, case_exploration_twin]


@pytest.mark.parametrize("case", CASES, ids=[c.__name__.removeprefix("case_") for c in CASES])
def test_the_older_release_reads_a_file_the_newer_release_wrote(tmp_path, case):
    case(tmp_path)


def test_an_older_release_loads_a_file_with_ambiguous_rows(tmp_path):
    """BACKLOG 72 rollback (ADR 0010, X7): the release before it knows neither the `ambiguous` status nor `notified` /
    `unknown_notified`. Its SentOrder (this one without the two fields) still loads the row: the status is a plain
    string, the new fields are dropped, and the id stays in its sent_ids, so it never re-sends; it only fails to
    resolve the row (its restart loop and its in-process look-up read `sending` rows only)."""
    from pydantic import create_model

    from goldbot.base import load_record
    from goldbot.engine.store import JsonEngineStore, OrdersSnapshot
    new_fields = {"notified", "unknown_notified"}
    older = create_model("SentOrder", __base__=Record, **{  # type: ignore[call-overload]
        k: (f.annotation, f) for k, f in SentOrder.model_fields.items() if k not in new_fields})
    row = SentOrder(client_order_id="icm-demo-72-a", ts_utc=T0, agent_id="a", side=1, magic=260150, lots=0.1,
                    max_bars=4, sl=2396.0, tp=2406.0, status="ambiguous", notified="opened", unknown_notified=True)
    JsonEngineStore(tmp_path, "icm-demo").save_orders(OrdersSnapshot(sent={row.client_order_id: row}, open={},
                                                                     closing=[], records_lost=[]))
    saved = json.loads((tmp_path / "orders_icm-demo.json").read_text())
    back = {k: load_record(older, v, source="orders_<account>.json") for k, v in saved["sent"].items()}
    assert set(back) == {"icm-demo-72-a"}                                    # older sent_ids = set(the table)
    old: Any = back["icm-demo-72-a"]
    assert old.status == "ambiguous" and old.status != "sending"             # never resolved, never re-sent
    assert not set(old.model_dump()) & new_fields


def test_every_persisted_record_model_has_a_compat_case():
    """A new persisted model must add a case above (and a default that is safe when missing, AGENT_STANDARDS S12)."""
    from goldbot.research import daily_review as dr
    covered = {ClosedTrade, Proposal, VersionBook, PerfStats, ModelEntry, Member, ResearchPlan,
               BrokerTerms, CostTable, PublishedCostTable, scheduler.JobState, health.WatchState, health.HealthReport,
               DigestState, OutboxState, crossfeed.ReconcileReport, ledger.LedgerReport,
               ledger.BalanceSnapshot, ledger.BalanceDeal, hwm.HwmConfirmation, PilotState, limits_profile.LimitsState,
               limits_profile.LimitsChange, adaptive.AdaptiveState, adaptive.RiskTerm, DsrInputs, DaySummedDsr,
               AttributionReport, TickIndex, cost_probe.ProbeRecord, cost_probe.SkipRecord, exploration.ExplorationRecord,
               exploration.TwinRecord,
               # the engine's orders file: tests/test_engine_safety.py::
               # test_a_restart_after_a_rollback_loads_an_orders_file_a_newer_release_wrote
               SentOrder, OpenTrade, PendingClose,
               # engine_<account>.json `schema_skew`: tests/test_schema_skew_health.py::
               # test_health_reads_a_skew_block_a_newer_engine_wrote
               SkewSummary,
               # state/tv.db pull log: tests/test_tv_pull.py::test_pull_log_rows_a_newer_release_wrote_still_read
               tv_pull.PassReport, tv_pull.StudyMeta, tv_pull.StudySpec,
               # engine_<account>.json and approval files `ask_reason` (backlog 103; every field defaults, a missing
               # reason is None): tests/test_api_ui_contract.py::
               # test_a_newer_engines_reason_with_an_extra_field_is_read_tolerantly
               AskReason,
               # state/aux/daily_review/: case_daily_review
               dr.DailyReview, dr.WeeklyRollup}
    loaded = {f.model for f in _tolerant_finds() if f.model not in GENERIC_MODEL_ARGS.values()}
    assert loaded - {m.__name__ for m in covered} == set()


# ------------------------------------------------------------------------------------------------- the AST guard
# Direct model_validate / model_validate_json calls allowed in goldbot/, keyed by (file, enclosing function, receiver).
# Everything else must use load_record / load_record_json. Keep each entry's reason; remove it when its call goes away.
STRICT_OR_NOT_PERSISTED: dict[tuple[str, str, str], str] = {
    ("goldbot/research/arm_rules_spec.py", "_parse", "StrategySpec"):
        "strict: a frozen spec_rule arm's canonical spec (config/spec_arms.yaml, hash-checked; ADR 0058)",
    ("goldbot/ops/autodisc_register.py", "journal", "Registration"):
        "strict: the hash-chained arm registration journal (ADR 0058); a row that does not read must refuse",
    ("goldbot/config.py", "load_settings", "Settings"): "strict: Settings (a misspelt key must fail)",
    ("goldbot/research/autodisc/ledger.py", "SearchLedger.get_spec", "StrategySpec"):
        "strict: hash-chained spec; a field dropped on read would change spec_hash and break the ledger chain (ADR 0058)",
    ("goldbot/research/autodisc/gen_gp.py", "_spec", "base"):
        "not persisted: a GP child built in memory from its parent spec; strict so an invalid tree is refused (ADR 0058)",
    ("goldbot/research/arms.py", "load_arms", "Arm"):
        "strict: frozen arm spec, checked-in config with a sha256 per arm (an edited or misspelt arm must fail)",
    ("goldbot/research/arms.py", "qualification_refusal", "Qualification"):
        "strict: an arm row's checked-in qualification (ADR 0061); a malformed or misspelt record must refuse the arm's "
        "demo entries (caught, never raised), not be read loosely as a pass",
    ("goldbot/engine/exploration.py", "_load_spec_arm_rows", "SpecArm"):
        "strict: the same frozen Arm rows from config/spec_arms.yaml (hash-checked), read one row at a time so a bad row "
        "is dropped and journalled, never a loosened read (ADR 0058 phase 4)",
    ("goldbot/features/smc_replica.py", "state_from_json", "ReplicaState"):
        "strict: research replica state snapshot, not persisted operational state (restart of a replica run only)",
    ("goldbot/ops/health.py", "HealthContext.from_runtime", "Settings"): "strict: Settings",
    ("goldbot/ops/preflight.py", "run_preflight", "Settings"): "strict: Settings",
    ("goldbot/ops/setup_wizard.py", "merge_local_settings", "Settings"): "strict: Settings",
    ("goldbot/api/auth.py", "AuthStore.session_user", "User"):
        "strict: credentials (a newer lock or MFA flag must not be ignored)",
    ("goldbot/research/cells.py", "_read_cell_rows", "CellRow"):
        "strict: untrusted matrix-cell artifacts; a bad row becomes a failed trial (security review 2026-10-18)",
    ("goldbot/webhook/app.py", "create_app.tv", "Alert"):
        "strict: an inbound request body, not persisted state (a malformed alert is refused, TradingView sees 422)",
    ("goldbot/telegram/bus.py", "read_control", "Control"): "strict: the owner's halt; unreadable means halted",
    ("goldbot/telegram/bus.py", "ApprovalBus.decisions_for", "BusDecision"):
        "strict: an owner decision is never applied without a field this release does not know; skipped, it expires",
    ("goldbot/telegram/bus.py", "ApprovalBus.recent", "BusDecision"): "strict: as decisions_for (the decided cards)",
    ("goldbot/execution/bridge.py", "_decode", "cls"):
        "strict: order requests crossing the bridge (the server's arguments, and OrderIntent in either direction) "
        "are never guessed; responses from the MT5 box go through load_record (GENERIC_MODEL_ARGS)",
    ("goldbot/db/repo.py", "RecordRepo._load", "self.model"): "RecordRepo: strict unless built with tolerant=True",
    ("goldbot/risk/windows.py", "load_holidays", "HolidayList"):
        "strict: hand-edited config (a misspelt key fails: calendar_unavailable)",
    ("goldbot/api/strategy_labels.py", "load_catalog", "Labels"):
        "strict: hand-edited config (a misspelt key fails: the names fall back to the keys in words)",
    ("goldbot/api/strategy_labels.py", "_reads_file", "ReadsFile"):
        "strict: hand-edited checked-in config, not persisted state (a misspelt read fails closed: no arm counts as "
        "read, every arm its own unit, ADR 0050)",
    ("goldbot/research/arm_stops.py", "_load", "StopRules"):
        "strict: hand-edited checked-in config, not persisted state (a misspelt stop rule fails closed: exploration "
        "entries refused as stop_rules_unreadable)",
    ("goldbot/research/min_lot.py", "min_lot_feasibility", "Feasibility"):
        "not persisted: built from a dict in the same function",
    ("goldbot/data/news.py", "score_batch", "HeadlineScore"): "not persisted: the LLM's answer, validated strictly",
    ("goldbot/api/app.py", "create_app.users", "UserRow"): "not persisted: API response from AuthStore.list_users()",
    ("goldbot/api/app.py", "create_app.recent_proposals", "DecidedProposal"):
        "not persisted: API response built from a loaded Proposal",
    ("goldbot/api/app.py", "create_app.calendar", "CalendarEvent"):
        "not persisted: API response from code-selected columns",
    ("goldbot/api/app.py", "create_app.news", "Headline"): "not persisted: API response from code-selected columns",
    ("goldbot/risk/adaptive.py", "AdaptiveController.current", "Evidence"):
        "not persisted, strict: the evidence for a risk decision (ADR 0031: corrupt evidence gives the floors)",
    ("goldbot/api/explain.py", "_plan", "PlanReservation"):
        "API schema model (pydantic default extra='ignore'): tolerant",
}
# Models that fail closed: never read tolerantly, by load_record or by RecordRepo(..., tolerant=True).
STRICT_MODELS = {"User", "Invite", "PasswordReset", "Control", "Settings", "BusDecision"}
# The only tolerant reads whose model is not a class named at the call: (file, function) -> the argument.
GENERIC_MODEL_ARGS = {("goldbot/db/repo.py", "RecordRepo._load"): "self.model",
                      # the wire record named by "__record__", from bridge._RECORDS minus _REQUEST_RECORDS
                      ("goldbot/execution/bridge.py", "_decode"): "cls"}
TOLERANT_FUNCS = ("load_record", "load_record_json")


@dataclass(frozen=True)
class Find:
    kind: str           # "direct" (model_validate[_json]) | "tolerant" (load_record[_json]) | "repo" (tolerant RecordRepo)
    rel: str
    where: str          # enclosing class/function qualname ("" at module level)
    model: str          # the receiver or model argument, as source text

    @property
    def plain_class(self) -> bool:
        return self.model.isidentifier() and self.model[:1].isupper()


class _Scanner(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel, self.stack, self.found = rel, [], []          # type: ignore[var-annotated]

    def _scope(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.stack.append(node.name)
        self.generic_visit(node)
        self.stack.pop()

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _scope

    def visit_Call(self, node: ast.Call) -> None:
        func = ast.unparse(node.func)
        name = func.rsplit(".", 1)[-1]
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        model: ast.expr | None = None
        kind = ""
        if name in ("model_validate", "model_validate_json") and isinstance(node.func, ast.Attribute):
            kind, model = "direct", node.func.value
        elif name in TOLERANT_FUNCS:
            kind, model = "tolerant", node.args[0] if node.args else kw.get("cls")
        elif name == "RecordRepo" and "tolerant" in kw:
            flag = kw["tolerant"]
            if not (isinstance(flag, ast.Constant) and flag.value is False):      # anything but a literal False
                kind, model = "repo", node.args[1] if len(node.args) > 1 else kw.get("model")
        if kind:
            self.found.append(Find(kind, self.rel, ".".join(self.stack), ast.unparse(model) if model else "?"))
        self.generic_visit(node)


def _scan(rel: str, source: str) -> list[Find]:
    sc = _Scanner(rel)
    sc.visit(ast.parse(source))
    return sc.found


def _finds() -> list[Find]:
    out: list[Find] = []
    for f in sorted((ROOT / "goldbot").rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        if rel != "goldbot/base.py":                       # the helper itself
            out += _scan(rel, f.read_text(encoding="utf-8"))
    return out


def _tolerant_finds() -> list[Find]:
    return [f for f in _finds() if f.kind in ("tolerant", "repo")]


def _violations(finds: list[Find]) -> list[str]:
    """Tolerant reads of a strict model, or of a model the guard cannot name (not a plain class at the call)."""
    out = []
    for f in finds:
        if f.kind == "direct":
            continue
        if f.model in STRICT_MODELS:
            out.append(f"{f.rel} {f.where}: {f.model} is strict and must not be read tolerantly")
        elif not f.plain_class and GENERIC_MODEL_ARGS.get((f.rel, f.where)) != f.model:
            out.append(f"{f.rel} {f.where}: name the model class at the call, not {f.model!r}")
    return out


def test_every_persisted_load_uses_load_record_or_is_on_the_strict_allowlist():
    found = {(f.rel, f.where, f.model) for f in _finds() if f.kind == "direct"}
    allowed = set(STRICT_OR_NOT_PERSISTED)
    assert found - allowed == set(), "read persisted state with goldbot.base.load_record(_json), or add the call to " \
                                     "the strict allowlist with its reason (ADR 0010)"
    assert allowed - found == set(), "stale allowlist entries: remove them"


def test_strict_models_are_never_read_tolerantly_and_every_tolerant_read_names_its_class():
    assert _violations(_finds()) == []


@pytest.mark.parametrize("snippet", [
    'R = RecordRepo("users", User, key="email", columns={}, tolerant=True)',
    'R = RecordRepo("invites", model=Invite, key="t", columns={}, tolerant=flag)',
    'x = goldbot.base.load_record(PasswordReset, d, source="s")',
    'x = base.load_record_json(Control, t, source="s")',
    'x = load_record(type(y), d, source="s")',
    'x = load_record(cls, d, source="s")',
    'R = RecordRepo("t", models[0], key="k", columns={}, tolerant=True)',
])
def test_the_guard_flags_a_tolerant_read_of_a_strict_or_unnamed_model(snippet):
    assert len(_violations(_scan("goldbot/x.py", snippet))) == 1


@pytest.mark.parametrize("snippet", [
    'R = RecordRepo("users", User, key="email", columns={})',
    'R = RecordRepo("users", User, key="email", columns={}, tolerant=False)',
    'x = goldbot.base.load_record_json(Proposal, t, source="s")',
])
def test_the_guard_passes_a_strict_repo_and_a_named_tolerant_read(snippet):
    assert _violations(_scan("goldbot/x.py", snippet)) == []


def _core_schema_problems(schema: Any, path: str) -> list[str]:
    out: list[str] = []
    if isinstance(schema, dict):
        if schema.get("type") == "tagged-union":
            out.append(f"{path}: discriminated union")
        if schema.get("type") == "union" and schema.get("mode") == "left_to_right":
            out.append(f"{path}: union_mode='left_to_right'")
        for k, v in schema.items():
            out += _core_schema_problems(v, path)
    elif isinstance(schema, list):
        for v in schema:
            out += _core_schema_problems(v, path)
    return out


def test_tolerant_models_have_no_left_to_right_or_discriminated_unions():
    """ADR 0010: with unknown keys ignored, a left-to-right union can take a newer variant as the first older one that
    validates (silently a different record), and a discriminated union cannot ignore a new tag. Smart-mode unions
    (the default) are fine: they pick the member that matches the most fields."""
    finds = [f for f in _tolerant_finds() if f.model not in GENERIC_MODEL_ARGS.values()]
    modules = {f.rel: importlib.import_module(f.rel.removesuffix(".py").replace("/", ".")) for f in finds}
    models: dict[str, set[type[BaseModel]]] = {}
    todo: list[type[BaseModel]] = [BaseModel]
    while todo:                                       # every model class loaded, by name (a model may be imported
        for sub in todo.pop().__subclasses__():       # inside a function, as ruin does with VersionBook)
            if sub.__module__.startswith("goldbot."):
                models.setdefault(sub.__name__, set()).add(sub)
            todo.append(sub)
    problems = []
    for f in finds:
        cls = getattr(modules[f.rel], f.model, None)          # the name as the module sees it ...
        if cls is None:                                         # ... else the one goldbot model of that name
            assert len(models.get(f.model, ())) == 1, f"{f.model}: not exactly one goldbot model of that name"
            cls = next(iter(models[f.model]))
        problems += _core_schema_problems(cls.__pydantic_core_schema__, f"{f.rel} {f.model}")
    assert problems == []


def test_bridge_responses_are_read_tolerantly_only_for_response_records_that_may_be():
    """The bridge's generic read (`_decode`, `cls`) covers bridge._RECORDS minus the request records: none of them is
    a strict model or has a left-to-right/discriminated union, and an order request (OrderIntent) is never tolerant."""
    from goldbot.execution import bridge
    assert "OrderIntent" in bridge._REQUEST_RECORDS and bridge._REQUEST_RECORDS <= set(bridge._RECORDS)
    responses = {n: c for n, c in bridge._RECORDS.items() if n not in bridge._REQUEST_RECORDS}
    assert set(responses) & STRICT_MODELS == set()
    assert [p for n, c in responses.items() for p in _core_schema_problems(c.__pydantic_core_schema__, n)] == []


def test_the_union_check_flags_left_to_right_and_discriminated_unions():
    class A(Record):
        k: Literal["a"] = "a"

    class B(Record):
        k: Literal["b"] = "b"

    class L2R(Record):
        v: A | B = Field(union_mode="left_to_right")

    class Tagged(Record):
        v: A | B = Field(discriminator="k")

    class Smart(Record):
        v: A | B

    assert len(_core_schema_problems(L2R.__pydantic_core_schema__, "L2R")) == 1
    assert len(_core_schema_problems(Tagged.__pydantic_core_schema__, "Tagged")) == 1
    assert _core_schema_problems(Smart.__pydantic_core_schema__, "Smart") == []
