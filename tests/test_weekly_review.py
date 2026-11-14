"""The weekly review (goldbot/research/weekly_review.py, architect plan 2026-11-13 item 2): per family net result
against its matched control (the strategy ranking's figures; a missing control reads "unpaired", never an edge), loss
attribution, cost drift against the cost of record, reads due at their registered checkpoint, MFE/MAE exit
diagnostics, and the Monday roll-up's variant proposals, ranked into one action list (aux.db `review_actions`). The
only automatic action is a stop already registered in config/arm_stops.yaml (tighten only); the research director
consumes the list once; backlog suggestions go to GitHub through the daily review's scrubbed issue path; the Sunday
digest says what changed."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.api.schema import RankFigures, RankingView, RankProgress, RankRow, RecentFigures
from goldbot.base import write_atomic
from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.research import daily_review as dr
from goldbot.research import review_actions as ra
from goldbot.research import weekly_review as wr
from goldbot.research.arm_stops import RULES_PATH

S = load_settings()
SLOT = pd.Timestamp("2026-11-14 13:00", tz="UTC")             # a Saturday, after research_director (12:30)
WEEK = "2026-11-14"


class Sink:
    def __init__(self) -> None:
        self.issues: list[tuple[str, str, str]] = []

    def comment(self, number: int, body: str) -> str:
        raise AssertionError("the weekly review never comments on a numbered issue")

    def open_or_comment(self, label: str, title: str, body: str) -> str:
        self.issues.append((label, title, body))
        return "https://github.invalid/issues/901"


def _fig(n: int = 60, edge: float | None = None, ci: tuple[float, float] | None = None,
         net: float | None = 0.05) -> RankFigures:
    none = RecentFigures(n=0, net_r_per_trade=None)
    return RankFigures(n=n, days=12, clusters=12, net_r_per_trade=net, ci95=None, lower_adjusted=None, hit_rate=0.5,
                       net_pnl=None, net_per_trade=None, currency=None, profit_factor=None, no_losses=False,
                       max_drawdown_r=None, cost_per_trade=None, cost_per_trade_r=None, edge_vs_control_r=edge,
                       edge_ci=ci, edge_ci_level=0.95 if edge is not None else None,
                       edge_source="interim" if edge is not None else None, last_7d=none, last_30d=none, trend=None)


def _row(family: str, fig: RankFigures, *, kind: Any = "exploration", progress: RankProgress | None = None,
         band: Any = "ranked") -> RankRow:
    return RankRow(key=family, family=family, family_label=family.replace("_", " ").capitalize(), version=1,
                   style="scalp", kind=kind, timeframes=["15m"], accounts=["Lab A"], band=band,
                   rank=1 if band == "ranked" else None, unranked_reason=None, status="early",
                   read_progress=progress, figures=fig, variants=[])


def _view(rows: list[RankRow], window: Any = "all") -> RankingView:
    return RankingView(as_of=datetime(2026, 11, 14, 13, tzinfo=timezone.utc), window=window, account=None,
                       style=None, heading="h", k_ranked=len(rows), t_adjusted=None, min_fills=30, min_days=10,
                       rows=rows, controls=[], retired=[], account_choices=[], unreadable=[])


def _daily(sd: Path, day: str, health: dict[str, dict[str, Any]]) -> None:
    r = dr.DailyReview(day=day, generated_utc=pd.Timestamp(f"{day} 06:15", tz="UTC"), arm_health=health)
    write_atomic(dr.review_path(sd, day), r.model_dump_json(indent=1))


def _proposal_file(sd: Path, sunday: str, titles: list[str]) -> None:
    w = dr.WeeklyRollup(week_ending=sunday, generated_utc=pd.Timestamp(sunday, tz="UTC") + pd.Timedelta(days=1),
                        proposals=[dr.VariantProposal(title=t, rationale=f"why {t}") for t in titles])
    write_atomic(dr.weekly_path(sd, sunday), w.model_dump_json(indent=1))


def _run(sd: Path, *, life: RankingView | None = None, sink: Sink | None = None,
         now: pd.Timestamp = SLOT) -> wr.WeeklyReview:
    return wr.run_weekly_review(settings=S, state_dir=sd, week_view=_view([], "7d"), life_view=life or _view([]),
                                now=now, sink=sink)


def _rows(sd: Path, sql: str, *args: Any) -> list[tuple[Any, ...]]:
    with Database.open(sd, "aux").connection() as conn:
        return [tuple(r) for r in conn.execute(sql, args)]


# ------------------------------------------------------------------------------------------------ automatic: tighten only
def test_actions_only_tighten(tmp_path):
    """The only automatic action is a registered stop the daily review found breached and that is not yet recorded:
    it is recorded in exploration_arm_status (refuses new entries only); an arm already retired is never touched,
    nothing is ever deleted or re-opened, and config/arm_stops.yaml is unchanged. Every other action is advisory."""
    rules_before = RULES_PATH.read_bytes()
    db = Database.open(tmp_path, "aux")
    dr.retire(db, broker="icm", arm_id="S2A", day="2026-11-02", now=pd.Timestamp("2026-11-02", tz="UTC"))
    _daily(tmp_path, "2026-11-12", {"icm": {"S1A": "breached", "S2A": "breached", "S3A": "ok"}})
    life = _view([_row("box_break", _fig(edge=-0.2, ci=(-0.3, -0.1)))])
    out = _run(tmp_path, life=life)
    auto = [a for a in out.actions if a.automatic]
    assert [(a.kind, a.subject, a.broker) for a in auto] == [("stop", "S1A", "icm")]
    assert all(a.kind in ra.AUTOMATIC_KINDS for a in auto)
    assert all(not a.automatic for a in out.actions if a.kind != "stop")
    status = _rows(tmp_path, "SELECT broker, arm_id, status, day FROM exploration_arm_status ORDER BY arm_id")
    assert status == [("icm", "S1A", "retired", WEEK), ("icm", "S2A", "retired", "2026-11-02")]   # S2A's day stands
    assert ("icm", "S3A") not in {(b, a) for b, a, *_ in status}       # an ok arm is never stopped
    assert RULES_PATH.read_bytes() == rules_before
    # the below-control family is advisory: queued, never stopped by the review
    assert any(a.kind == "below_control" and not a.automatic for a in out.actions)


def test_an_unreadable_rollup_is_not_marked_read(tmp_path):
    dr.review_root(tmp_path).mkdir(parents=True)
    dr.weekly_path(tmp_path, "2026-11-08").write_text("{broken")
    out = _run(tmp_path)
    assert "weekly-2026-11-08.json" in out.unreadable
    assert "weekly-2026-11-08.json" not in dr.weekly_consumed(tmp_path)


def test_a_dry_or_unreadable_daily_record_stops_nothing(tmp_path):
    (dr.review_root(tmp_path)).mkdir(parents=True)
    dr.review_path(tmp_path, "2026-11-12").write_text("{not json")
    out = _run(tmp_path)
    assert not [a for a in out.actions if a.automatic]
    assert _rows(tmp_path, "SELECT COUNT(*) FROM exploration_arm_status") == [(0,)]


# ------------------------------------------------------------------------------------------------ consumed once
def test_proposals_are_consumed_once(tmp_path):
    """The Monday roll-up's proposals (weekly-<sunday>.json, unread before) join the action list once, whichever
    weekly review reads the file first; the director consumes each unconsumed action once, and a rerun of the same
    director slot gets the same set back (restart-safe), never another slot's."""
    _proposal_file(tmp_path, "2026-11-08", ["Limit-entry variant", "Shorter hold variant to free open slots"])
    first = _run(tmp_path)
    props = [a for a in first.actions if a.kind == "variant_proposal"]
    assert sorted(a.title for a in props) == ["Limit-entry variant", "Shorter hold variant to free open slots"]
    assert dr.weekly_consumed(tmp_path)["weekly-2026-11-08.json"]["by"] == "weekly_review"   # loop check (R140)
    nxt = _run(tmp_path, now=SLOT + pd.Timedelta(days=7))           # the same file is still in the window
    assert not [a for a in nxt.actions if a.kind == "variant_proposal"]
    db = Database.open(tmp_path, "aux")
    got = ra.consume(db, "research_director:2026-11-21T12:30", now=SLOT + pd.Timedelta(days=7))
    assert {a.title for a in got} >= {a.title for a in props}
    again = ra.consume(db, "research_director:2026-11-21T12:30", now=SLOT + pd.Timedelta(days=7))
    assert [a.action_id for a in again] == [a.action_id for a in got]          # same slot rerun: same set
    assert ra.consume(db, "research_director:2026-11-28T12:30", now=SLOT + pd.Timedelta(days=14)) == []


def test_the_director_plan_carries_the_consumed_actions_and_its_budget_does_not_move(tmp_path):
    """Queue input only: the plan lists the review's actions; the trial budget and the focus order are the same
    as without them."""
    from goldbot.research.director import build_plan
    _proposal_file(tmp_path, "2026-11-08", ["Limit-entry variant"])
    _run(tmp_path)
    queue = ra.consume(Database.open(tmp_path, "aux"), "slot", now=SLOT)
    kw: dict[str, Any] = dict(quarter_budget=20, monthly_total=6, trial_budget_per_month=1, floor=1)
    plain = build_plan(SLOT, ["breakout", "trend"], [], [], [], **kw)
    fed = build_plan(SLOT, ["breakout", "trend"], [], [], [], review_queue=queue, **kw)
    assert [a.title for a in fed.review_queue] == ["Limit-entry variant"]
    assert fed.budget == plain.budget and [f.family for f in fed.focus] == [f.family for f in plain.focus]


# ------------------------------------------------------------------------------------------------ restart
def test_restart_mid_run_keeps_one_action_set(tmp_path, monkeypatch):
    """A run that dies after writing its actions (here in the GitHub post) and is rerun for the same week keeps the
    first action set: the same ids, written once, the stop recorded once, the backlog issue posted once."""
    _daily(tmp_path, "2026-11-12", {"icm": {"S1A": "breached"}})
    life = _view([_row("orb", _fig(n=80, edge=None))])                 # unpaired: a backlog suggestion
    calls = {"n": 0}
    real = wr._post

    def dying(*a: Any, **k: Any) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("killed mid-run")
        return real(*a, **k)

    monkeypatch.setattr(wr, "_post", dying)
    with pytest.raises(RuntimeError):
        _run(tmp_path, life=life, sink=Sink())
    first = _rows(tmp_path, "SELECT action_id, rank, kind FROM review_actions WHERE week = ? ORDER BY rank", WEEK)
    assert first
    _proposal_file(tmp_path, "2026-11-08", ["Limit-entry variant"])     # new input after the crash: not added
    sink = Sink()
    out = _run(tmp_path, life=life, sink=sink)
    again = _rows(tmp_path, "SELECT action_id, rank, kind FROM review_actions WHERE week = ? ORDER BY rank", WEEK)
    assert again == first and [(a.action_id, a.rank, a.kind) for a in out.actions] == first
    assert _rows(tmp_path, "SELECT COUNT(*) FROM review_weeks WHERE week = ?", WEEK) == [(1,)]
    assert _rows(tmp_path, "SELECT COUNT(*) FROM exploration_arm_status") == [(1,)]
    assert len(sink.issues) == 1
    _run(tmp_path, life=life, sink=sink)                                # a third run posts nothing more
    assert len(sink.issues) == 1


# ------------------------------------------------------------------------------------------------ unpaired
def test_missing_control_reads_unpaired_not_edge(tmp_path):
    """A family without a paired control result reads "unpaired": no edge figure, never a below- or above-control
    action; with enough fills of its own it becomes a backlog suggestion to pair a control."""
    life = _view([_row("orb", _fig(n=80, edge=None)), _row("thin", _fig(n=10, edge=None), band="too_few"),
                  _row("box_break", _fig(edge=-0.2, ci=(-0.3, -0.1)))])
    out = _run(tmp_path, life=life)
    reads = {f.family: f for f in out.families}
    assert reads["orb"].vs_control == "unpaired" and reads["orb"].edge_r is None
    assert reads["thin"].vs_control == "unpaired" and reads["thin"].edge_r is None
    assert reads["box_break"].vs_control == "below" and reads["box_break"].edge_r == -0.2
    kinds = {(a.kind, a.subject) for a in out.actions}
    assert ("unpaired", "orb") in kinds and ("unpaired", "thin") not in kinds
    assert not {k for k, s in kinds if s in ("orb", "thin") and k in ("below_control",)}
    text = " ".join(a.title + " " + a.detail for a in out.actions if a.subject == "orb")
    assert "unpaired" in text and "vs control" not in text


def test_an_edge_interval_that_spans_zero_is_level_and_queues_nothing(tmp_path):
    out = _run(tmp_path, life=_view([_row("orb", _fig(edge=0.05, ci=(-0.1, 0.2)))]))
    assert {f.family: f.vs_control for f in out.families} == {"orb": "level"}
    assert not [a for a in out.actions if a.subject == "orb"]


# ------------------------------------------------------------------------------------------------ other sources
def test_a_read_at_its_registered_checkpoint_is_queued_for_the_evaluator(tmp_path):
    life = _view([_row("orb", _fig(edge=0.0, ci=(-0.1, 0.1)), progress=RankProgress(unit="OBR15", n_counted=300,
                                                                                      n_read_at=300)),
                  _row("kel", _fig(edge=0.0, ci=(-0.1, 0.1)), progress=RankProgress(unit="KEL", n_counted=120,
                                                                                      n_read_at=300))])
    out = _run(tmp_path, life=life)
    due = [a for a in out.actions if a.kind == "read_due"]
    assert [a.subject for a in due] == ["OBR15"] and not due[0].automatic


def test_a_read_is_not_due_at_its_fill_count_until_its_registered_session_day_minimum_is_met(tmp_path):
    """H-81 section 4: "One read, at the 300th admissible family fill on >= 20 session days" (2026-11-14 false
    alarms: H-81:Z7 at 300 fills on 4 days, H-81:Z4 on 8 days)."""
    def prog(unit: str, days: int) -> RankProgress:
        return RankProgress(unit=unit, n_counted=310, n_read_at=300, n_days=days, min_days=20)
    life = _view([_row("hhp", _fig(), progress=prog("H-81:Z7", 4)), _row("rnr", _fig(), progress=prog("H-81:Z4", 8)),
                  _row("nrb", _fig(), progress=prog("H-81:Z6", 20))])
    due = [a.subject for a in _run(tmp_path, life=life).actions if a.kind == "read_due"]
    assert due == ["H-81:Z6"]


def test_loss_drivers_come_from_the_attribution_report_signed_after_the_row_bar(tmp_path):
    def cell(n: int, mean: float, verdict_adj: str, t: float) -> dict[str, Any]:
        return {"n": n, "mean_r_net": mean, "t_stat": t, "verdict_adj": verdict_adj}
    rep = {"as_of": (SLOT - pd.Timedelta(days=1)).isoformat(),
           "breakdowns": {"session": {"asia": cell(90, -0.21, "negative", -3.4),
                                      "london": cell(90, -0.05, "indistinguishable from zero", -1.1)},
                          "exit": {"time": cell(50, -0.3, "negative", -2.9)}}}
    (tmp_path / "attribution.json").write_text(json.dumps(rep))
    out = _run(tmp_path)
    assert [a.subject for a in out.actions if a.kind == "loss_driver"] == ["session=asia", "exit=time"]
    (tmp_path / "attribution.json").write_text(json.dumps({**rep, "as_of": (SLOT - pd.Timedelta(days=30)).isoformat()}))
    assert not [a for a in _run(tmp_path, now=SLOT + pd.Timedelta(days=7)).actions if a.kind == "loss_driver"]


def test_cost_drift_above_the_cost_of_record_interval_is_a_backlog_suggestion(tmp_path):
    costs = [dr.SessionCosts(broker="icm", host=dr.ALL, session="asia", fills=40, round_trip_usd=0.40),
             dr.SessionCosts(broker="icm", host=dr.ALL, session="london", fills=40, round_trip_usd=0.17),
             dr.SessionCosts(broker="icm", host=dr.ALL, session="newyork", fills=5, round_trip_usd=0.90)]
    r = dr.DailyReview(day="2026-11-12", generated_utc=SLOT - pd.Timedelta(days=2), costs=costs)
    write_atomic(dr.review_path(tmp_path, r.day), r.model_dump_json())
    out = _run(tmp_path)
    drift = [a for a in out.actions if a.kind == "cost_drift"]
    assert [a.subject for a in drift] == ["icm asia"]               # london within its interval, newyork too few fills
    assert out.cost_drift["icm asia"].of_record_usd == 0.204


def test_exit_diagnostics_read_mfe_and_mae_and_tolerate_rows_without_them(tmp_path):
    db = Database.open(tmp_path, "aux")
    lo = SLOT - pd.Timedelta(days=3)
    with db.write() as conn:
        for i in range(40):
            body: dict[str, Any] = {"family": "orb", "exit_reason": "stop" if i < 30 else "target"}
            if i < 36:
                body |= {"mfe_r": 1.2 if i < 15 else 0.2, "mae_r": -1.0}
            conn.execute("INSERT INTO exploration_trades (client_order_id, account_id, arm_id, ts_ns, closed, exit_ns,"
                         " body) VALUES (?, 'icm-demo', 'OBR15', ?, 1, ?, ?)",
                         (f"c{i}", int(lo.value), int(lo.value) + i, json.dumps(body)))
    out = _run(tmp_path)
    ex = out.exits["orb"]
    assert (ex.closed, ex.recorded, ex.stop_exits, ex.stops_after_mfe) == (40, 36, 30, 15)
    assert [a.subject for a in out.actions if a.kind == "exit_diagnostic"] == ["orb"]


def test_the_action_list_is_ranked_and_capped_but_never_drops_a_stop(tmp_path):
    _daily(tmp_path, "2026-11-12", {"icm": {"S1A": "breached"}})
    _proposal_file(tmp_path, "2026-11-08", [f"Variant {i}" for i in range(4)])
    rows = [_row(f"fam{i}", _fig(n=80, edge=None)) for i in range(30)]
    out = _run(tmp_path, life=_view(rows))
    assert [a.rank for a in out.actions] == list(range(1, len(out.actions) + 1))
    assert out.actions[0].kind == "stop"
    assert len(out.actions) <= S.weekly_review.max_actions + 1


def test_the_review_issue_is_scrubbed_and_labelled_review(tmp_path):
    sink = Sink()
    life = _view([_row("orb 10.1.2.3", _fig(n=80, edge=None))])
    _run(tmp_path, life=life, sink=sink)
    assert len(sink.issues) == 1
    label, title, body = sink.issues[0]
    assert label == "review" and WEEK in title and "10.1.2.3" not in body


def test_an_action_row_a_newer_release_wrote_still_reads(tmp_path):
    db = Database.open(tmp_path, "aux")
    body = {"action_id": "x1", "week": WEEK, "rank": 1, "kind": "unpaired", "subject": "orb", "title": "t",
            "detail": "d", "source": "ranking", "added_later": 5}
    with db.write() as conn:
        conn.execute("INSERT INTO review_weeks (week, recorded_ns, n_actions) VALUES (?, 1, 1)", (WEEK,))
        conn.execute("INSERT INTO review_actions (action_id, week, rank, kind, automatic, recorded_ns, body) "
                     "VALUES ('x1', ?, 1, 'unpaired', 0, 1, ?)", (WEEK, json.dumps(body)))
    assert [a.subject for a in ra.week_actions(db, WEEK)] == ["orb"]


# ------------------------------------------------------------------------------------------------ the Sunday digest
def test_the_sunday_digest_says_what_changed_this_week(tmp_path):
    from goldbot.telegram.weekly_digest import build_weekly, changed_lines
    _daily(tmp_path, "2026-11-12", {"icm": {"S1A": "breached"}})
    _proposal_file(tmp_path, "2026-11-08", ["Limit-entry variant"])
    _run(tmp_path, life=_view([_row("orb", _fig(n=80, edge=None))]), sink=Sink())
    sunday = pd.Timestamp("2026-11-15 06:45", tz="UTC")
    lines = changed_lines(tmp_path, sunday)
    assert lines[0] == "Changed this week:"
    text = "\n".join(lines)
    assert "1 arm stopped at its registered rule" in text
    assert "S1A" not in text                                         # no arm id in the owner's text
    assert "queued for research" in text and "backlog" in text
    assert "Changed this week:" in build_weekly(tmp_path, sunday)


def test_the_digest_leaves_the_section_out_when_no_review_was_recorded(tmp_path):
    """The digest's rule: a line whose source is not recorded is left out, never estimated."""
    from goldbot.telegram.weekly_digest import changed_lines
    Database.open(tmp_path, "aux")
    assert changed_lines(tmp_path, pd.Timestamp("2026-11-15 06:45", tz="UTC")) == []


# ------------------------------------------------------------------------------------------------ wiring
def test_the_weekly_review_job_runs_on_saturday_after_the_research_director():
    from goldbot.ops.jobs import JOBS
    s, d = S.scheduler.weekly_review, S.scheduler.research_director
    assert "weekly_review" in JOBS and s.kind == "weekly" and s.weekday == d.weekday == 5 and s.at > d.at


def test_review_actions_never_import_the_ranking():
    """ADR 0053 6f: the research package reads the ranking only as figures handed to it (the job builds the
    view); weekly_review and review_actions never import the ranking module or a module that shows it."""
    import ast

    from goldbot.api import strategy_ranking as sr
    banned = {"goldbot.api.strategy_ranking", *sr.RANKING_READERS}
    for mod in (wr, ra):
        tree = ast.parse(Path(str(mod.__file__)).read_text())
        names = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        names |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not names & banned


def test_review_tables_exist_after_migration(tmp_path):
    Database.open(tmp_path, "aux")
    conn = sqlite3.connect(tmp_path / "aux.db")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"review_weeks", "review_actions"} <= names
