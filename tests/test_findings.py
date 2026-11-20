"""The findings register (owner 2026-11-20, "feedback must close itself"; goldbot/ops/findings.py and
goldbot/ops/finding_checks.py): the shipped register loads, is public-safe and names only registered checks and agent
owners; each check measures what its row claims; a row's status and event follow its verdict and due date; the daily
job stores history in aux.db and announces once a day on the status issue; health WARNs on a past-due high row."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import ROOT, load_settings
from goldbot.ops import finding_checks as fc
from goldbot.ops import findings as fm
from goldbot.ops.learning_ledger import LearningClose, LearningCloses

SETTINGS = load_settings()
NOW = pd.Timestamp("2026-11-20 07:15", tz="UTC")          # Friday


def close(account: str = "icm-demo", kind: str = "exploration", arm: str | None = "Z3D", r: float | None = 0.1,
          opened: pd.Timestamp | None = None, censored: bool = False) -> LearningClose:
    t = opened if opened is not None else NOW - pd.Timedelta(hours=20)
    return LearningClose(account=account, kind=kind, family="f", arm_id=arm, opened_utc=t,
                         exit_utc=t + pd.Timedelta(minutes=30), side="long", r=r, net=True, censored=censored)


def ctx(tmp: Path, rows: list[LearningClose] | None = None, *, unreadable: list[str] | None = None,
        records: dict[str, Any] | None = None, github: Any = None, repo_root: Path | None = None,
        now: pd.Timestamp = NOW) -> fc.FindingsContext:
    return fc.FindingsContext(state_dir=tmp, repo_root=repo_root or tmp, now=now, settings=SETTINGS, github=github,
                              closes_loader=lambda: LearningCloses(rows=rows or [], unreadable=unreadable or []),
                              records_loader=lambda: records or {})


def run(name: str, c: fc.FindingsContext, **params: Any) -> fc.CheckResult:
    spec = fc.CHECKS[name]
    return spec.fn(c, spec.params.model_validate(params))


# --------------------------------------------------------------------------------------------- the register
def test_the_shipped_register_loads_with_registered_checks_and_agent_owners():
    reg = fm.load_register(ROOT / SETTINGS.findings.path)
    assert len(reg.findings) >= 40
    roles = {p.stem for p in (ROOT / ".claude" / "agents").glob("*.md")}
    assert {f.owner for f in reg.findings} <= roles
    assert {f.check.fn for f in reg.findings} <= set(fc.CHECKS)
    measured = [f for f in reg.findings if f.check.fn != "manual"]
    assert len(measured) >= 20, "a register of manual rows measures nothing"


def test_the_shipped_register_seeds_the_audit_headline_findings():
    ids = {f.id: f for f in fm.load_register(ROOT / SETTINGS.findings.path).findings}
    assert ids["A4-CONTROLS"].check.fn == "controls_to_signal_ratio"
    assert ids["A4-VOLUME"].check.params == {"min": 150, "max": 250, "days": 5}
    assert ids["RES-LEARNER"].check.fn == "learner_outputs"
    assert ids["SEC-1"].check.fn == "github_security"
    assert ids["ENG-BRIDGE-BIND"].severity == "critical"
    assert {"LOOP-392", "P10-OCT-REVIEW"} <= set(ids)


def _write(tmp: Path, text: str) -> Path:
    p = tmp / "findings.yaml"
    p.write_text(text, encoding="utf-8")
    return p


ROW = """version: 1
findings:
  - id: X-ONE
    title: A test finding with a title
    owner: sre
    severity: high
    due: 2026-11-19
    source: a test
    check: {fn: manual, params: {}}
"""


def test_a_row_naming_an_unknown_check_is_refused_at_load(tmp_path):
    with pytest.raises(ValueError, match="unknown finding check"):
        fm.load_register(_write(tmp_path, ROW.replace("fn: manual", "fn: no_such_check")), agents_dir=None)


def test_a_row_with_a_bad_check_parameter_is_refused_at_load(tmp_path):
    bad = ROW.replace("{fn: manual, params: {}}", "{fn: trades_per_day, params: {min: 300, max: 250}}")
    with pytest.raises(ValueError, match="min must not exceed max"):
        fm.load_register(_write(tmp_path, bad), agents_dir=None)


def test_a_finding_id_used_twice_is_refused(tmp_path):
    twice = ROW + ROW.split("findings:\n", 1)[1]
    with pytest.raises(ValueError, match="appears twice"):
        fm.load_register(_write(tmp_path, twice), agents_dir=None)


def test_an_owner_that_is_not_an_agent_role_is_refused(tmp_path):
    agents = tmp_path / "agents"
    agents.mkdir()
    (agents / "program-director.md").write_text("x")
    with pytest.raises(ValueError, match="not agent roles"):
        fm.load_register(_write(tmp_path, ROW), agents_dir=agents)


@pytest.mark.parametrize("line", ["    source: login 51234567 on the box", "    source: lost 120 GBP",
                                  "    source: costs £ 33 a month", "    source: host 10.1.2.3"])
def test_a_register_carrying_a_login_host_or_money_figure_is_refused(tmp_path, line):
    with pytest.raises(ValueError, match="not public-safe"):
        fm.load_register(_write(tmp_path, ROW.replace("    source: a test", line)), agents_dir=None)


def test_a_manual_close_needs_both_a_date_and_evidence(tmp_path):
    half = ROW.replace("params: {}", "params: {done_on: 2026-11-20}")
    with pytest.raises(ValueError, match="needs both"):
        fm.load_register(_write(tmp_path, half), agents_dir=None)


# --------------------------------------------------------------------------------------------- the checks
def test_controls_ratio_fails_an_account_with_more_broker_controls_than_signal_trades(tmp_path):
    rows = [close("icm-demo", "control")] * 3 + [close("icm-demo")] + [close("icm-probe", "control")] + \
        [close("icm-probe")] + [close("icm-probe", "cost_probe")] * 5
    got = run("controls_to_signal_ratio", ctx(tmp_path, rows), max_ratio=1.0, days=7)
    assert got.verdict == "fail" and "icm-demo 3:1" in got.reason and "icm-probe" not in got.reason


def test_controls_ratio_passes_at_one_to_one_and_ignores_trades_outside_the_window(tmp_path):
    old = NOW - pd.Timedelta(days=9)
    rows = [close("icm-demo", "control"), close("icm-demo"), close("icm-demo", "control", opened=old)]
    assert run("controls_to_signal_ratio", ctx(tmp_path, rows), max_ratio=1.0, days=7).verdict == "pass"


def test_controls_ratio_fails_controls_with_no_signal_trade(tmp_path):
    got = run("controls_to_signal_ratio", ctx(tmp_path, [close("vantage-demo", "control")]))
    assert got.verdict == "fail"


def test_a_trade_check_on_an_unreadable_ledger_is_cant_check_never_a_pass(tmp_path):
    c = ctx(tmp_path, [], unreadable=["exploration"])
    needs: dict[str, dict[str, Any]] = {"bleeding_arms": {"max_mean_r": -0.2, "min_n": 5},
                                        "trades_per_day": {"min": 1, "max": 9}, "arms_not_trading": {"arms": ["X"]}}
    for name in ("controls_to_signal_ratio", "bleeding_arms", "trades_per_day", "arms_not_trading", "mean_net_r"):
        assert run(name, c, **needs.get(name, {})).verdict == "cant_check", name


def test_an_empty_trade_window_is_cant_check_never_a_pass(tmp_path):
    old = NOW - pd.Timedelta(days=30)
    c = ctx(tmp_path, [close(kind="control", opened=old)], records={})
    assert run("controls_to_signal_ratio", c).verdict == "cant_check"
    assert run("bleeding_arms", c, max_mean_r=-0.25, min_n=5).verdict == "cant_check"
    assert run("arms_not_trading", c, arms=["VEX15"]).verdict == "cant_check"
    assert run("failed_but_filled", c).verdict == "cant_check"


def test_trades_per_day_fails_a_weekday_outside_the_band_and_skips_the_weekend(tmp_path):
    thu, wed = NOW - pd.Timedelta(days=1), NOW - pd.Timedelta(days=2)
    sat = pd.Timestamp("2026-11-14 10:00", tz="UTC")
    rows = [close(opened=thu)] * 3 + [close(opened=wed)] * 1 + [close(opened=sat)] * 50 + [close(opened=NOW)] * 50
    got = run("trades_per_day", ctx(tmp_path, rows), min=2, max=4, days=2)
    assert got.verdict == "fail" and "11-18 1" in got.reason and "11-19" not in got.reason
    assert got.value == "11-18 1, 11-19 3"
    rows += [close(opened=wed)]
    assert run("trades_per_day", ctx(tmp_path, rows), min=2, max=4, days=2).verdict == "pass"


def test_bleeding_arms_fails_an_arm_still_trading_below_its_floor_and_ignores_a_stopped_one(tmp_path):
    recent, stale = NOW - pd.Timedelta(hours=5), NOW - pd.Timedelta(days=5)
    rows = [close(arm="BAD", r=-0.6, opened=recent)] * 6 + [close(arm="OLD", r=-0.9, opened=stale)] * 6 + \
        [close(arm="OK", r=0.2, opened=recent)] * 6 + [close(arm="THIN", r=-1.0, opened=recent)] * 2
    got = run("bleeding_arms", ctx(tmp_path, rows), max_mean_r=-0.25, min_n=5, days=14, active_days=2)
    assert got.verdict == "fail" and got.value == "BAD@icm-demo -0.60 R n 6"


def test_bleeding_arms_leaves_out_censored_trades(tmp_path):
    rows = [close(arm="A", r=-1.0, censored=True)] * 6 + [close(arm="A", r=0.1)] * 5
    assert run("bleeding_arms", ctx(tmp_path, rows), max_mean_r=-0.25, min_n=5).verdict == "pass"


def test_mean_net_r_is_cant_check_when_too_thin_and_fails_a_losing_book(tmp_path):
    assert run("mean_net_r", ctx(tmp_path, [close(r=1.0)] * 5), min_n=10).verdict == "cant_check"
    got = run("mean_net_r", ctx(tmp_path, [close(r=-0.1)] * 12), min_n=10, min_mean_r=0.0)
    assert got.verdict == "fail" and "-0.100 R n 12" in got.reason
    assert run("mean_net_r", ctx(tmp_path, [close(r=0.05)] * 12), min_n=10).verdict == "pass"


def test_arms_not_trading_fails_while_a_named_arm_still_fills(tmp_path):
    rows = [close(arm="VEX15", kind="control")] * 2 + [close(arm="Z3D")]
    got = run("arms_not_trading", ctx(tmp_path, rows), arms=["VEX15", "IEX1H"], days=2)
    assert got.verdict == "fail" and got.value == "VEX15 2"
    assert run("arms_not_trading", ctx(tmp_path, rows), arms=["IEX1H"], days=2).verdict == "pass"


def test_failed_but_filled_counts_adopted_and_opened_closed_sends_in_the_window(tmp_path):
    from types import SimpleNamespace
    recs = {"a": SimpleNamespace(account_id="vantage-demo", resolution="adopted", sent_utc=NOW - pd.Timedelta(days=1)),
            "b": SimpleNamespace(account_id="vantage-demo", resolution="opened_closed", sent_utc=NOW - pd.Timedelta(days=2)),
            "c": SimpleNamespace(account_id="icm-demo", resolution="unfilled", sent_utc=NOW - pd.Timedelta(days=1)),
            "d": SimpleNamespace(account_id="icm-demo", resolution="adopted", sent_utc=NOW - pd.Timedelta(days=40)),
            "e": SimpleNamespace(account_id="icm-demo", resolution=None, sent_utc=NOW - pd.Timedelta(days=1))}
    got = run("failed_but_filled", ctx(tmp_path, records=recs), days=30, max_count=0)
    assert got.verdict == "fail" and got.value == "vantage-demo 2"
    assert run("failed_but_filled", ctx(tmp_path, records=recs), days=30, max_count=2).verdict == "pass"


def test_learner_outputs_fails_on_empty_model_tables_and_passes_once_weights_are_written(tmp_path):
    assert run("learner_outputs", ctx(tmp_path)).verdict == "cant_check"
    from goldbot.db.connection import Database
    db = Database.open(tmp_path, "aux")
    got = run("learner_outputs", ctx(tmp_path), tables=["exploration_weights"])
    assert got.verdict == "fail" and got.value == "exploration_weights 0"
    with db.write() as conn:
        conn.execute("INSERT INTO exploration_weights VALUES (1, 'Z3D', 'scalp', 0.5, '{}')")
    assert run("learner_outputs", ctx(tmp_path), tables=["exploration_weights"]).verdict == "pass"
    assert run("learner_outputs", ctx(tmp_path), tables=["no_such_table"]).verdict == "fail"


def test_learner_outputs_refuses_a_table_name_that_is_not_an_identifier():
    with pytest.raises(ValueError):
        fc.parse_params("learner_outputs", {"tables": ["x; DROP TABLE y"]})


def test_file_exists_passes_on_a_matching_file_under_the_checkout(tmp_path):
    (tmp_path / "docs" / "incidents").mkdir(parents=True)
    c = ctx(tmp_path, repo_root=tmp_path)
    assert run("file_exists", c, pattern="docs/incidents/2026-11-*vantage*.md").verdict == "fail"
    (tmp_path / "docs" / "incidents" / "2026-11-19-vantage-quote-stall.md").write_text("x")
    got = run("file_exists", c, pattern="docs/incidents/2026-11-*vantage*.md")
    assert got.verdict == "pass" and got.value == "docs/incidents/2026-11-19-vantage-quote-stall.md"


@pytest.mark.parametrize("pattern", ["/etc/*", "../outside/*.md"])
def test_file_checks_refuse_a_pattern_outside_the_checkout(pattern):
    with pytest.raises(ValueError):
        fc.parse_params("file_exists", {"pattern": pattern})
    with pytest.raises(ValueError):
        fc.parse_params("file_text", {"patterns": [pattern], "regex": "x", "expect": "present"})


def test_file_text_absent_fails_while_an_adr_is_still_proposed(tmp_path):
    adr = tmp_path / "docs" / "decisions"
    adr.mkdir(parents=True)
    (adr / "0016-x.md").write_text("# ADR\n\nStatus: proposed (implementer)\n")
    c = ctx(tmp_path, repo_root=tmp_path)
    params = {"patterns": ["docs/decisions/0016-*.md"], "regex": "(?im)^status: proposed", "expect": "absent"}
    assert run("file_text", c, **params).verdict == "fail"
    (adr / "0016-x.md").write_text("# ADR\n\nStatus: accepted (cto, 2026-11-25)\n")
    assert run("file_text", c, **params).verdict == "pass"
    assert run("file_text", c, **{**params, "patterns": ["nothing/*.md"]}).verdict == "cant_check"


def test_state_field_set_reads_an_existing_state_file(tmp_path):
    c = ctx(tmp_path)
    assert run("state_field_set", c, file="backup_last.json", field="last_prune_ts").verdict == "cant_check"
    (tmp_path / "backup_last.json").write_text(json.dumps({"last_prune_ts": None}))
    assert run("state_field_set", c, file="backup_last.json", field="last_prune_ts").verdict == "fail"
    (tmp_path / "backup_last.json").write_text(json.dumps({"last_prune_ts": "2026-11-28T10:00:00Z"}))
    assert run("state_field_set", c, file="backup_last.json", field="last_prune_ts").verdict == "pass"
    with pytest.raises(ValueError):
        fc.parse_params("state_field_set", {"file": "../core.db", "field": "x"})


def test_health_ok_reads_the_last_health_statuses_by_pattern(tmp_path):
    c = ctx(tmp_path)
    assert run("health_ok", c, names=["reconcile:*"]).verdict == "cant_check"
    (tmp_path / "health_last.json").write_text(json.dumps(
        {"ts": NOW.isoformat(), "statuses": {"reconcile:icm-demo": "ok", "reconcile:vantage-demo": "warn"}}))
    got = run("health_ok", c, names=["reconcile:*"])
    assert got.verdict == "fail" and "reconcile:vantage-demo warn" in got.reason
    assert run("health_ok", c, names=["reconcile:*"], allow_warn=True).verdict == "pass"
    assert run("health_ok", c, names=["quote_age:*"]).verdict == "cant_check"


class Reader:
    def __init__(self, issue: dict[str, Any] | None = None, repo: dict[str, Any] | None = None):
        self._issue, self._repo = issue or {}, repo or {}

    def issue(self, number: int) -> dict[str, Any]:
        return self._issue

    def repository(self) -> dict[str, Any]:
        return self._repo


def test_github_checks_are_cant_check_without_a_token(tmp_path):
    assert run("github_issue_closed", ctx(tmp_path), number=392).verdict == "cant_check"
    assert run("github_security", ctx(tmp_path), features=["secret_scanning"]).verdict == "cant_check"


def test_github_issue_closed_passes_only_on_a_closed_issue(tmp_path):
    assert run("github_issue_closed", ctx(tmp_path, github=Reader({"state": "open"})), number=392).verdict == "fail"
    assert run("github_issue_closed", ctx(tmp_path, github=Reader({"state": "closed"})), number=392).verdict == "pass"


def test_github_security_fails_a_disabled_feature_and_cannot_check_without_admin_rights(tmp_path):
    feats = ["secret_scanning", "secret_scanning_push_protection"]
    repo = {"security_and_analysis": {"secret_scanning": {"status": "enabled"},
                                      "secret_scanning_push_protection": {"status": "disabled"}}}
    got = run("github_security", ctx(tmp_path, github=Reader(repo=repo)), features=feats)
    assert got.verdict == "fail" and "secret_scanning_push_protection" in got.reason
    assert run("github_security", ctx(tmp_path, github=Reader(repo={})), features=feats).verdict == "cant_check"


def test_a_check_that_raises_is_cant_check_never_a_pass(tmp_path):
    reg = fm.Register.model_validate({"version": 1, "findings": [
        {"id": "X-ONE", "title": "raises", "owner": "sre", "severity": "high", "due": "2026-12-01", "source": "test",
         "check": {"fn": "controls_to_signal_ratio", "params": {}}}]})

    def boom() -> LearningCloses:
        raise RuntimeError("torn")
    c = fc.FindingsContext(state_dir=tmp_path, repo_root=tmp_path, now=NOW, closes_loader=boom)
    assert fm.evaluate_one(reg.findings[0], c).verdict == "cant_check"


# --------------------------------------------------------------------------------------------- status and events
def finding(due: str = "2026-11-27", severity: fm.Severity = "high") -> fm.Finding:
    return fm.Finding(id="A4-CONTROLS", title="controls one to one", owner="program-director", severity=severity,
                      due=pd.Timestamp(due).date(), source="test", check=fm.CheckRef(fn="manual"))


FAIL = fc.CheckResult(verdict="fail", reason="2:1")
PASS = fc.CheckResult(verdict="pass", reason="1:1")
CANT = fc.CheckResult(verdict="cant_check", reason="no data")


def test_a_first_failing_run_is_new_and_the_next_identical_run_is_silent():
    first = fm.classify(finding(), FAIL, NOW, None, 7)
    assert (first.status, first.event) == ("open", "new")
    again = fm.classify(finding(), FAIL, NOW + pd.Timedelta(days=1), first, 7)
    assert (again.status, again.event) == ("open", None) and again.announced_utc == first.announced_utc


def test_a_closed_row_that_fails_again_is_regressed_and_a_fixed_row_is_closed():
    closed = fm.classify(finding(), PASS, NOW, fm.classify(finding(), FAIL, NOW, None, 7), 7)
    assert (closed.status, closed.event) == ("closed", "closed")
    back = fm.classify(finding(), FAIL, NOW + pd.Timedelta(days=1), closed, 7)
    assert (back.status, back.event) == ("open", "regressed")
    assert fm.classify(finding(), PASS, NOW, None, 7).event is None       # closed from the start: nothing to say


def test_a_failing_row_past_due_raises_past_due_once_then_a_reminder_after_remind_days():
    t0 = pd.Timestamp("2026-11-26 07:15", tz="UTC")
    s = fm.classify(finding(), FAIL, t0, None, 7)
    s = fm.classify(finding(), FAIL, t0 + pd.Timedelta(days=2), s, 7)
    assert (s.status, s.event) == ("overdue", "past_due")
    quiet = fm.classify(finding(), FAIL, t0 + pd.Timedelta(days=5), s, 7)
    assert quiet.event is None
    later = fm.classify(finding(), FAIL, t0 + pd.Timedelta(days=9), quiet, 7)
    assert later.event == "reminder"


def test_a_check_that_cannot_read_its_data_is_unknown_before_due_and_overdue_after():
    before = fm.classify(finding(), CANT, NOW, None, 7)
    assert (before.status, before.event) == ("unknown", None)
    after = fm.classify(finding(due="2026-11-19"), CANT, NOW, None, 7)
    assert (after.status, after.event) == ("overdue", "new")
    first_fail = fm.classify(finding(), FAIL, NOW, before, 7)
    assert first_fail.event == "new"


def test_missing_data_after_a_measurement_keeps_the_last_status_and_raises_no_event():
    closed = fm.classify(finding(), PASS, NOW, None, 7)
    kept = fm.classify(finding(), CANT, NOW + pd.Timedelta(days=1), closed, 7)
    assert (kept.status, kept.event, kept.verdict) == ("closed", None, "cant_check")
    assert kept.reason.startswith("can't check now (no data); last measured closed")
    opened = fm.classify(finding(), FAIL, NOW, None, 7)
    still = fm.classify(finding(), CANT, NOW + pd.Timedelta(days=1), opened, 7)
    assert (still.status, still.event) == ("open", None)
    late = fm.classify(finding(), CANT, pd.Timestamp("2026-11-28 07:15", tz="UTC"), still, 7)
    assert (late.status, late.event) == ("overdue", "past_due")


def test_the_due_day_itself_is_not_overdue():
    assert fm.classify(finding(due="2026-11-20"), FAIL, NOW, None, 7).status == "open"


# --------------------------------------------------------------------------------------------- the job
class Sink:
    def __init__(self, fail: bool = False):
        self.posts: list[tuple[int, str]] = []
        self.fail = fail

    def comment(self, number: int, body: str) -> str:
        if self.fail:
            raise RuntimeError("HTTP 502")
        self.posts.append((number, body))
        return "url"

    def open_or_comment(self, label: str, title: str, body: str) -> str:
        raise AssertionError("findings only comment on the status issue")


REG = """version: 1
findings:
  - id: A4-CONTROLS
    title: Controls one per signal trade
    owner: program-director
    severity: high
    due: 2026-11-27
    source: test
    check: {fn: controls_to_signal_ratio, params: {max_ratio: 1.0, days: 7}}
  - id: SEC-GITLEAKS
    title: gitleaks hook present
    owner: security-reviewer
    severity: medium
    due: 2026-11-19
    source: test
    check: {fn: manual, params: {}}
  - id: L1-DONE
    title: Done long ago
    owner: ml-researcher
    severity: low
    due: 2026-11-21
    source: test
    check: {fn: manual, params: {done_on: 2026-11-20, evidence: "trial 3439"}}
"""


def job(tmp: Path, rows: list[LearningClose], sink: Any, now: pd.Timestamp = NOW, settings: Any = SETTINGS
        ) -> dict[str, Any]:
    reg = tmp / "findings.yaml"
    if not reg.exists():
        reg.write_text(REG, encoding="utf-8")
    return fm.run_findings_check(tmp, now, settings, sink, register=reg, agents_dir=None,
                                 closes_loader=lambda: LearningCloses(rows=rows, unreadable=[]),
                                 records_loader=dict)


def history(tmp: Path) -> list[tuple[str, str, str | None, int]]:
    con = sqlite3.connect(tmp / "aux.db")
    try:
        return con.execute("SELECT finding_id, status, event, posted FROM findings_history ORDER BY run_ns, finding_id"
                           ).fetchall()
    finally:
        con.close()


BAD = [close(kind="control")] * 3 + [close()]


def test_the_job_stores_every_row_and_posts_one_comment_on_the_status_issue(tmp_path):
    sink = Sink()
    out = job(tmp_path, BAD, sink)
    assert history(tmp_path) == [("A4-CONTROLS", "open", "new", 1), ("L1-DONE", "closed", None, 0),
                                 ("SEC-GITLEAKS", "overdue", "new", 1)]
    assert len(sink.posts) == 1 and sink.posts[0][0] == 34
    body = sink.posts[0][1]
    assert "| new | A4-CONTROLS" in body and "| new | SEC-GITLEAKS" in body and "L1-DONE" not in body
    assert "1 overdue, 1 open, 0 can't check, 1 closed" in body
    assert out["events"] == {"A4-CONTROLS": "new", "SEC-GITLEAKS": "new"}


def test_the_job_posts_at_most_once_a_day_and_carries_new_events_to_the_next_day(tmp_path):
    sink = Sink()
    job(tmp_path, BAD, sink)
    out = job(tmp_path, [close(kind="control"), close()], sink, now=NOW + pd.Timedelta(hours=2))   # A4 closes
    assert len(sink.posts) == 1 and "wait for tomorrow" in out["posted"]
    job(tmp_path, [close(kind="control"), close()], sink, now=NOW + pd.Timedelta(days=1))
    assert len(sink.posts) == 2 and "| closed | A4-CONTROLS" in sink.posts[1][1]
    assert "Still overdue: SEC-GITLEAKS" in sink.posts[1][1]


def test_an_unchanged_register_posts_nothing_the_next_day(tmp_path):
    sink = Sink()
    job(tmp_path, BAD, sink)
    out = job(tmp_path, BAD, sink, now=NOW + pd.Timedelta(days=1))
    assert len(sink.posts) == 1 and out["posted"] == "nothing to announce"


def test_a_failed_post_or_no_token_keeps_the_events_pending_for_the_next_run(tmp_path):
    out = job(tmp_path, BAD, Sink(fail=True))
    assert "post failed" in out["posted"]
    assert job(tmp_path, BAD, None, now=NOW + pd.Timedelta(hours=1))["posted"] == "no token: events pending"
    sink = Sink()
    job(tmp_path, BAD, sink, now=NOW + pd.Timedelta(hours=2))
    assert len(sink.posts) == 1 and "| new | A4-CONTROLS" in sink.posts[0][1]
    assert all(posted == 1 for _, _, event, posted in history(tmp_path) if event is not None)


def test_a_high_event_queues_one_telegram_line_and_a_medium_one_does_not(tmp_path):
    job(tmp_path, BAD, Sink())
    rows = [json.loads(x) for x in (tmp_path / "agent_runs.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["role"] == "findings" and "A4-CONTROLS new" in rows[0]["detail"]
    assert "SEC-GITLEAKS" not in rows[0]["detail"]


def test_telegram_stays_off_when_the_setting_is_off(tmp_path):
    quiet = SETTINGS.model_copy(update={"findings": SETTINGS.findings.model_copy(update={"telegram": False})})
    job(tmp_path, BAD, Sink(), settings=quiet)
    assert not (tmp_path / "agent_runs.jsonl").exists()


def test_the_comment_is_scrubbed_of_a_login_like_number(tmp_path):
    rows = [fm.classify(finding(), fc.CheckResult(verdict="fail", reason="account 51234567 over"), NOW, None, 7)]
    assert "51234567" not in fm.comment_body(rows, rows, NOW)


def test_a_status_row_a_newer_release_wrote_still_reads(tmp_path):
    job(tmp_path, BAD, Sink())
    before = fm.latest(tmp_path)
    con = sqlite3.connect(tmp_path / "aux.db")
    rows = con.execute("SELECT finding_id, run_ns, body FROM findings_history").fetchall()
    for fid, ns, body in rows:
        doc = json.loads(body)
        doc["from_a_newer_release"] = 1
        con.execute("UPDATE findings_history SET body = ? WHERE finding_id = ? AND run_ns = ?", (json.dumps(doc), fid, ns))
    con.commit()
    con.close()
    assert fm.latest(tmp_path) == before
    assert fm.stored_latest(tmp_path) == before


def test_a_register_that_does_not_load_fails_the_job(tmp_path):
    (tmp_path / "findings.yaml").write_text(REG.replace("fn: manual", "fn: nope"), encoding="utf-8")
    with pytest.raises(ValueError):
        job(tmp_path, BAD, Sink())


def test_findings_check_is_a_registered_daily_scheduler_job():
    from goldbot.ops.jobs import JOBS
    assert "findings_check" in JOBS
    sched = SETTINGS.scheduler.findings_check
    assert sched.kind == "daily" and sched.run_if_never_run


# --------------------------------------------------------------------------------------------- health and CLI
def _hctx(tmp: Path, now: pd.Timestamp = NOW) -> Any:
    from tests.test_health import make_ctx
    return make_ctx(tmp, now=now)


def test_health_findings_is_absent_until_the_job_has_stored_a_run(tmp_path):
    from goldbot.ops.checks import findings as hf
    assert not hf.findings_stored(_hctx(tmp_path))


def test_health_findings_warns_while_a_high_row_is_past_due_and_not_closed(tmp_path, monkeypatch):
    from goldbot.ops.checks import findings as hf
    reg = tmp_path / "findings.yaml"
    reg.write_text(REG, encoding="utf-8")
    monkeypatch.setattr(fm, "register_path", lambda settings: reg)
    job(tmp_path, BAD, Sink())
    assert hf.findings_stored(_hctx(tmp_path))
    got = hf.check_findings(_hctx(tmp_path))
    assert got.status == "ok" and "1 overdue" in got.reason              # only the medium row is past due
    late = hf.check_findings(_hctx(tmp_path, now=pd.Timestamp("2026-11-28 09:00", tz="UTC")))
    assert late.status == "warn" and "A4-CONTROLS (high, program-director, due 2026-11-27)" in late.reason
    fixed = pd.Timestamp("2026-11-27 12:00", tz="UTC")
    job(tmp_path, [close(kind="control", opened=fixed), close(opened=fixed)], Sink(),
        now=pd.Timestamp("2026-11-28 07:15", tz="UTC"))
    assert hf.check_findings(_hctx(tmp_path, now=pd.Timestamp("2026-11-28 09:00", tz="UTC"))).status == "ok"


def test_health_findings_cannot_check_an_unreadable_register(tmp_path, monkeypatch):
    from goldbot.ops.checks import findings as hf
    monkeypatch.setattr(fm, "register_path", lambda settings: tmp_path / "missing.yaml")
    got = hf.check_findings(_hctx(tmp_path))
    assert got.status == "warn" and got.reason.startswith("can't check")


def test_the_cli_prints_one_line_per_finding_from_the_last_stored_run(tmp_path, capsys):
    job(tmp_path, BAD, Sink())
    assert fm.main(["--state", str(tmp_path), "--register", str(tmp_path / "findings.yaml"), "--stored"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[2].startswith("SEC-GITLEAKS") and out[3].startswith("A4-CONTROLS") and out[4].startswith("L1-DONE")
    assert "1 overdue, 1 open, 0 can't check, 1 closed (3 findings)" in out[-1]


def test_findings_is_a_registered_ops_command():
    from goldbot.ops.commands.registry import find
    assert find("findings") is not None


# --------------------------------------------------------------------------------------------- review round of ee15815e
def test_open_security_rows_are_private_and_name_no_weakness():
    reg = fm.load_register(ROOT / SETTINGS.findings.path)
    sec = [f for f in reg.findings if f.id.startswith("SEC-") or f.owner == "security-reviewer"]
    assert sec and all(f.private for f in sec)
    for f in sec:
        assert "private notes" in f.title
        for word in ("password", "token", "ssh", "root", "email", "branch", "scanning", "tunnel", "mode"):
            assert word not in f.title.lower() and word not in f.source.lower(), (f.id, word)


def test_a_private_row_shows_no_measurement_in_the_comment_or_telegram():
    f = finding().model_copy(update={"private": True})
    rows = [fm.classify(f, fc.CheckResult(verdict="fail", reason="not enabled: secret_scanning"), NOW, None, 7)]
    body = fm.comment_body(rows, rows, NOW)
    assert "secret_scanning" not in body and "see private notes" in body
    assert "secret_scanning" not in (fm.telegram_text(rows) or "")


@pytest.mark.parametrize("line", ["    source: lost 120 gbp", "    source: GBP 120 lost", "    source: 40 pounds a month",
                                  "    source: usd 5 a box"])
def test_money_in_any_case_or_currency_first_is_refused(tmp_path, line):
    with pytest.raises(ValueError, match="not public-safe"):
        fm.load_register(_write(tmp_path, ROW.replace("    source: a test", line)), agents_dir=None)


def test_re_running_the_same_slot_keeps_a_pending_event(tmp_path):
    job(tmp_path, BAD, None)                                   # no token: events pending
    job(tmp_path, BAD, None)                                   # the same slot again (a scheduler retry)
    assert ("A4-CONTROLS", "open", "new", 0) in history(tmp_path)
    sink = Sink()
    job(tmp_path, BAD, sink, now=NOW + pd.Timedelta(days=1))
    assert len(sink.posts) == 1 and "| new | A4-CONTROLS" in sink.posts[0][1]


def test_re_running_a_posted_slot_does_not_post_its_events_again(tmp_path):
    sink = Sink()
    job(tmp_path, BAD, sink)
    job(tmp_path, BAD, sink)                                   # the same slot again after the post
    job(tmp_path, BAD, sink, now=NOW + pd.Timedelta(days=1))
    assert len(sink.posts) == 1


def test_events_are_marked_posted_as_soon_as_the_comment_succeeds(tmp_path, monkeypatch):
    def broken(*a: Any, **k: Any) -> bool:
        raise RuntimeError("disk full")
    monkeypatch.setattr("goldbot.ops.limits_job.queue_notice", broken)
    sink = Sink()
    with pytest.raises(RuntimeError):
        job(tmp_path, BAD, sink)
    job(tmp_path, BAD, sink, now=NOW + pd.Timedelta(days=1))
    assert len(sink.posts) == 1


def test_a_row_never_announced_open_closes_without_an_event():
    unknown = fm.classify(finding(), CANT, NOW, None, 7)
    assert fm.classify(finding(), PASS, NOW + pd.Timedelta(days=1), unknown, 7).event is None


def test_health_findings_warns_on_a_register_that_is_not_yaml(tmp_path, monkeypatch):
    from goldbot.ops.checks import findings as hf
    bad = tmp_path / "findings.yaml"
    bad.write_text("version: 1\nfindings: [\n  {id: : }", encoding="utf-8")
    monkeypatch.setattr(fm, "register_path", lambda settings: bad)
    got = hf.check_findings(_hctx(tmp_path))
    assert got.status == "warn" and got.reason.startswith("can't check")
