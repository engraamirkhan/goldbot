"""ADR 0010, "persisted state is read tolerantly, built strictly": after a rollback the older release reads files a
newer release wrote. Every persisted model is written by its real writer (or in its real format), given a field this
release does not know, and read back through its real load path: it must load as if the field were absent.

The AST guard below keeps it that way: every model_validate / model_validate_json in goldbot/ either goes through
goldbot.base.load_record / load_record_json or is on the explicit allowlist with its reason (strict: credentials,
the owner's halt, Settings, bridge order requests; or not persisted state)."""
from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data import crossfeed
from goldbot.engine.shadow import ShadowBook, VersionBook
from goldbot.execution.costs import (
    BrokerTerms,
    CostTable,
    PublishedCostTable,
    SlippageStat,
    SpreadStat,
    load_research_cost_table,
)
from goldbot.ops import health, jobs, scheduler
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade, load_closed_trades
from goldbot.research import ruin
from goldbot.research.director import ResearchPlan
from goldbot.research.model_registry import ModelEntry, ModelRegistry
from goldbot.research.population import Member, Population
from goldbot.research.promotion import PerfStats
from goldbot.telegram import automode
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus, BusDecision
from goldbot.telegram.digest import STATE_FILE as DIGEST_FILE
from goldbot.telegram.digest import DigestSchedule, DigestState
from goldbot.telegram.outbox import Outbox, OutboxState

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


def case_bus_proposal_and_decision(tmp: Path) -> None:
    bus = ApprovalBus(tmp)
    p = _proposal()
    bus.publish(p)
    _rewrite(bus.pending_dir / "p1.json")
    d = bus.submit("p1", approve=True, reason_code=None, by="dashboard:o")
    _rewrite(bus.decisions_dir / "p1.json")
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


CASES = [case_closed_trade, case_bus_proposal_and_decision, case_bus_finished_proposal, case_shadow_book,
         case_shadow_stats, case_model_registry_and_backtest, case_population, case_research_plan, case_costs,
         case_scheduler, case_health, case_digest, case_outbox, case_reconcile]


@pytest.mark.parametrize("case", CASES, ids=[c.__name__.removeprefix("case_") for c in CASES])
def test_the_older_release_reads_a_file_the_newer_release_wrote(tmp_path, case):
    case(tmp_path)


def test_every_persisted_record_model_has_a_compat_case():
    """A new persisted model must add a case above (and a default that is safe when missing, AGENT_STANDARDS S12)."""
    covered = {ClosedTrade, Proposal, BusDecision, VersionBook, PerfStats, ModelEntry, Member, ResearchPlan,
               BrokerTerms, CostTable, PublishedCostTable, scheduler.JobState, health.WatchState, health.HealthReport,
               DigestState, OutboxState, crossfeed.ReconcileReport}
    loaded = {name for _, name in _tolerant_loads() if name.isidentifier() and name[0].isupper()}   # not `cls`
    assert loaded - {m.__name__ for m in covered} == set()


# ------------------------------------------------------------------------------------------------- the AST guard
# Direct model_validate / model_validate_json calls allowed in goldbot/, keyed by (file, receiver). Everything else
# must use load_record / load_record_json. Keep each entry's reason; remove an entry when its call goes away.
STRICT_OR_NOT_PERSISTED: dict[tuple[str, str], str] = {
    ("goldbot/config.py", "Settings"): "strict: Settings (a misspelt key must fail)",
    ("goldbot/ops/health.py", "Settings"): "strict: Settings",
    ("goldbot/ops/preflight.py", "Settings"): "strict: Settings",
    ("goldbot/ops/setup_wizard.py", "Settings"): "strict: Settings",
    ("goldbot/api/auth.py", "User"): "strict: credentials (a newer lock or MFA flag must not be ignored)",
    ("goldbot/telegram/bus.py", "Control"): "strict: the owner's halt; unreadable means halted",
    ("goldbot/execution/bridge.py", "cls"): "strict: bridge order requests (an order is never guessed)",
    ("goldbot/db/repo.py", "self.model"): "RecordRepo: strict unless built with tolerant=True",
    ("goldbot/research/min_lot.py", "Feasibility"): "not persisted: built from a dict in the same function",
    ("goldbot/data/news.py", "HeadlineScore"): "not persisted: the LLM's answer, validated strictly",
    ("goldbot/api/app.py", "UserRow"): "not persisted: API response from AuthStore.list_users()",
    ("goldbot/api/app.py", "DecidedProposal"): "not persisted: API response built from a loaded Proposal",
    ("goldbot/api/app.py", "CalendarEvent"): "not persisted: API response from code-selected columns",
    ("goldbot/api/app.py", "Headline"): "not persisted: API response from code-selected columns",
    ("goldbot/api/explain.py", "PlanReservation"): "API schema model (pydantic default extra='ignore'): tolerant",
}
# Temporary: other lanes are editing these files; the follow-up is listed in HANDOFF (ADR 0010).
TEMPORARY: dict[tuple[str, str], str] = {
    ("goldbot/engine/runner.py", "Proposal"): "ADR 0010 follow-up: approvals pending scan (~:394)",
    ("goldbot/engine/runner.py", "SentOrder"): "ADR 0010 follow-up: _load_orders",
    ("goldbot/engine/runner.py", "OpenTrade"): "ADR 0010 follow-up: _load_orders",
    ("goldbot/engine/runner.py", "PendingClose"): "ADR 0010 follow-up: _load_orders",
}


def _calls() -> list[tuple[str, str, ast.Call]]:
    out = []
    for f in sorted((ROOT / "goldbot").rglob("*.py")):
        rel = f.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                out.append((rel, ast.unparse(node.func), node))
    return out


def _direct_validations() -> set[tuple[str, str]]:
    return {(rel, func.rsplit(".", 1)[0]) for rel, func, _ in _calls()
            if func.endswith((".model_validate", ".model_validate_json")) and rel != "goldbot/base.py"}


def _tolerant_loads() -> set[tuple[str, str]]:
    return {(rel, ast.unparse(node.args[0])) for rel, func, node in _calls()
            if func in ("load_record", "load_record_json") and node.args}


def test_every_persisted_load_uses_load_record_or_is_on_the_strict_allowlist():
    found = _direct_validations()
    allowed = set(STRICT_OR_NOT_PERSISTED) | set(TEMPORARY)
    assert found - allowed == set(), "read persisted state with goldbot.base.load_record(_json), or add the call to " \
                                     "the strict allowlist with its reason (ADR 0010)"
    assert allowed - found == set(), "stale allowlist entries: remove them"


def test_strict_models_are_never_read_tolerantly():
    names = {name for _, name in _tolerant_loads()}
    assert names.isdisjoint({"User", "Invite", "PasswordReset", "Control", "Settings"})
