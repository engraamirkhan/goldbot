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
from goldbot.data import crossfeed
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
from goldbot.research.director import ResearchPlan
from goldbot.research.model_registry import ModelEntry, ModelRegistry
from goldbot.research.population import Member, Population
from goldbot.research.promotion import PerfStats
from goldbot.risk import hwm
from goldbot.telegram import automode
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus
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


def case_bus_proposal(tmp: Path) -> None:
    bus = ApprovalBus(tmp)
    p = _proposal()
    bus.publish(p)
    _rewrite(bus.pending_dir / "p1.json")
    d = bus.submit("p1", approve=True, reason_code=None, by="dashboard:o")    # decisions stay strict (allowlist)
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


def case_hwm_confirmation(tmp: Path) -> None:
    """core.db high-water-mark confirmation row (BACKLOG 54, ADR 0017) with a field from a newer release."""
    conf = hwm.HwmConfirmation(account_id="icm-demo", mark=10_000.0, mark_utc=T0, pending_balance=10_500.0,
                               pending_since_utc=T0)
    assert hwm.save_confirmation(tmp, conf)
    with sqlite3.connect(tmp / "core.db") as conn:
        body = conn.execute("SELECT body FROM hwm_confirmation").fetchone()[0]
        conn.execute("UPDATE hwm_confirmation SET body = ?", (json.dumps(_newer(json.loads(body))),))
    assert hwm.load_confirmation(tmp, "icm-demo") == conf
    assert hwm.read_only_confirmation(tmp, "icm-demo") == conf


CASES = [case_closed_trade, case_bus_proposal, case_bus_finished_proposal, case_shadow_book,
         case_shadow_stats, case_model_registry_and_backtest, case_population, case_research_plan, case_costs,
         case_scheduler, case_health, case_digest, case_outbox, case_reconcile, case_ledger, case_hwm_confirmation]


@pytest.mark.parametrize("case", CASES, ids=[c.__name__.removeprefix("case_") for c in CASES])
def test_the_older_release_reads_a_file_the_newer_release_wrote(tmp_path, case):
    case(tmp_path)


def test_every_persisted_record_model_has_a_compat_case():
    """A new persisted model must add a case above (and a default that is safe when missing, AGENT_STANDARDS S12)."""
    covered = {ClosedTrade, Proposal, VersionBook, PerfStats, ModelEntry, Member, ResearchPlan,
               BrokerTerms, CostTable, PublishedCostTable, scheduler.JobState, health.WatchState, health.HealthReport,
               DigestState, OutboxState, crossfeed.ReconcileReport, ledger.LedgerReport,
               ledger.BalanceSnapshot, ledger.BalanceDeal, hwm.HwmConfirmation,
               # the engine's orders file: tests/test_engine_safety.py::
               # test_a_restart_after_a_rollback_loads_an_orders_file_a_newer_release_wrote
               SentOrder, OpenTrade, PendingClose,
               # engine_<account>.json `schema_skew`: tests/test_schema_skew_health.py::
               # test_health_reads_a_skew_block_a_newer_engine_wrote
               SkewSummary}
    loaded = {f.model for f in _tolerant_finds() if f.model not in GENERIC_MODEL_ARGS.values()}
    assert loaded - {m.__name__ for m in covered} == set()


# ------------------------------------------------------------------------------------------------- the AST guard
# Direct model_validate / model_validate_json calls allowed in goldbot/, keyed by (file, enclosing function, receiver).
# Everything else must use load_record / load_record_json. Keep each entry's reason; remove it when its call goes away.
STRICT_OR_NOT_PERSISTED: dict[tuple[str, str, str], str] = {
    ("goldbot/config.py", "load_settings", "Settings"): "strict: Settings (a misspelt key must fail)",
    ("goldbot/ops/health.py", "HealthContext.from_runtime", "Settings"): "strict: Settings",
    ("goldbot/ops/preflight.py", "run_preflight", "Settings"): "strict: Settings",
    ("goldbot/ops/setup_wizard.py", "merge_local_settings", "Settings"): "strict: Settings",
    ("goldbot/api/auth.py", "AuthStore.session_user", "User"):
        "strict: credentials (a newer lock or MFA flag must not be ignored)",
    ("goldbot/telegram/bus.py", "ApprovalBus.control", "Control"): "strict: the owner's halt; unreadable means halted",
    ("goldbot/telegram/bus.py", "ApprovalBus.decisions_for", "BusDecision"):
        "strict: an owner decision is never applied without a field this release does not know; skipped, it expires",
    ("goldbot/telegram/bus.py", "ApprovalBus.recent", "BusDecision"): "strict: as decisions_for (the decided cards)",
    ("goldbot/execution/bridge.py", "_decode", "cls"):
        "strict: order requests crossing the bridge (the server's arguments, and OrderIntent in either direction) "
        "are never guessed; responses from the MT5 box go through load_record (GENERIC_MODEL_ARGS)",
    ("goldbot/db/repo.py", "RecordRepo._load", "self.model"): "RecordRepo: strict unless built with tolerant=True",
    ("goldbot/risk/windows.py", "load_holidays", "HolidayList"):
        "strict: hand-edited config (a misspelt key fails: calendar_unavailable)",
    ("goldbot/research/min_lot.py", "min_lot_feasibility", "Feasibility"):
        "not persisted: built from a dict in the same function",
    ("goldbot/data/news.py", "score_batch", "HeadlineScore"): "not persisted: the LLM's answer, validated strictly",
    ("goldbot/api/app.py", "create_app.users", "UserRow"): "not persisted: API response from AuthStore.list_users()",
    ("goldbot/api/app.py", "create_app.recent_proposals", "DecidedProposal"):
        "not persisted: API response built from a loaded Proposal",
    ("goldbot/api/app.py", "create_app.calendar", "CalendarEvent"):
        "not persisted: API response from code-selected columns",
    ("goldbot/api/app.py", "create_app.news", "Headline"): "not persisted: API response from code-selected columns",
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
