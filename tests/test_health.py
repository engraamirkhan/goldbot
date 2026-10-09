import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import ROOT, TelegramSettings, load_settings
from goldbot.ops import accounts, health
from goldbot.ops.health import AccountRef, Check, HealthContext, HealthReport, HealthWatch
from goldbot.ops.scheduler import Schedule, Scheduler
from goldbot.telegram.approvals import Proposal
from goldbot.telegram.bus import ApprovalBus

NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")        # Wednesday afternoon: gold market open
SATURDAY = pd.Timestamp("2026-10-10 12:00", tz="UTC")   # weekend: market closed
SETTINGS = load_settings().model_copy(update={"telegram": TelegramSettings(allowed_user_ids=[42])})
ALL_SECRETS = {"telegram-bot-token", "tradingview-webhook-secret", "mt5-icm-demo", "anthropic-api-key", "github-token"}


def make_ctx(tmp_path: Path, now: pd.Timestamp = NOW, secrets: set[str] | None = None, **kw) -> HealthContext:
    have = ALL_SECRETS if secrets is None else secrets
    base: dict[str, Any] = dict(state_dir=tmp_path, now=now, settings=SETTINGS, accounts=[AccountRef(account_id="icm-demo")],
                get_secret=lambda k: "s3cr3t-value" if k in have else None,
                disk_usage=lambda p: (100e9, 50e9, 50e9))
    base.update(kw)
    return HealthContext(**base)


def write(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def ago(s: float, now: pd.Timestamp = NOW) -> float:
    return now.timestamp() - s


# ------------------------------------------------------------------------------------------------ static checks
def test_settings_ok_warn_fail(tmp_path):
    assert health.check_settings(make_ctx(tmp_path)).status == "ok"
    no_ids = load_settings().model_copy(update={"telegram": TelegramSettings()})
    c = health.check_settings(make_ctx(tmp_path, settings=no_ids))
    assert c.status == "warn" and "allowed_user_ids" in c.reason
    c = health.check_settings(make_ctx(tmp_path, settings=None, settings_error="ValidationError: bad key"))
    assert c.status == "fail" and "bad key" in c.reason


def test_accounts_check(tmp_path):
    assert health.check_accounts(make_ctx(tmp_path)).status == "ok"
    assert health.check_accounts(make_ctx(tmp_path, accounts=[])).status == "warn"
    assert health.check_accounts(make_ctx(tmp_path, accounts_error="yaml error")).status == "fail"


def test_secrets_report_names_never_values(tmp_path):
    assert health.check_secrets(make_ctx(tmp_path)).status == "ok"
    c = health.check_secrets(make_ctx(tmp_path, secrets={"telegram-bot-token", "anthropic-api-key", "github-token"}))
    assert c.status == "fail"
    assert "tradingview-webhook-secret" in c.reason and "mt5-icm-demo" in c.reason
    assert "telegram-bot-token" not in c.reason and "s3cr3t" not in c.reason
    c = health.check_secrets(make_ctx(tmp_path, secrets=ALL_SECRETS - {"github-token"}))
    assert c.status == "warn" and "github-token" in c.reason

    def broken(key: str) -> str | None:
        raise RuntimeError("keyring locked")
    assert health.check_secrets(make_ctx(tmp_path, get_secret=broken)).status == "fail"


def test_secrets_use_accounts_get_secret_at_runtime(tmp_path, monkeypatch):
    seen: list[str] = []

    def fake_get_secret(key: str) -> str:
        seen.append(key)
        return "v"
    monkeypatch.setattr(accounts, "get_secret", fake_get_secret)
    monkeypatch.setattr(accounts, "enabled_accounts", lambda mode=None: [accounts.load_accounts()["icm-demo"]])
    ctx = HealthContext.from_runtime(tmp_path, now=NOW)
    assert ctx.settings is not None and [a.account_id for a in ctx.accounts] == ["icm-demo"]
    c = health.check_secrets(ctx)
    assert c.status == "ok" and "mt5-icm-demo" in seen and "v" not in c.reason


def test_from_runtime_reports_broken_settings_and_accounts(tmp_path, monkeypatch):
    bad = tmp_path / "settings.yaml"
    bad.write_text("symbol: XAUUSD\nsymbol: again\n")
    monkeypatch.setattr(accounts, "get_secret", lambda k: None)

    def boom(mode=None):
        raise ValueError("accounts.yaml broken")
    monkeypatch.setattr(accounts, "enabled_accounts", boom)
    ctx = HealthContext.from_runtime(tmp_path, settings_path=bad, now=NOW)
    assert health.check_settings(ctx).status == "fail"
    assert health.check_accounts(ctx).status == "fail" and "broken" in health.check_accounts(ctx).reason


def test_disk_thresholds(tmp_path):
    def disk(total: float, free: float):
        return lambda p: (total, total - free, free)
    assert health.check_disk(make_ctx(tmp_path, disk_usage=disk(100e9, 50e9))).status == "ok"
    assert health.check_disk(make_ctx(tmp_path, disk_usage=disk(100e9, 8e9))).status == "warn"      # < 10%
    assert health.check_disk(make_ctx(tmp_path, disk_usage=disk(1000e9, 1.5e9))).status == "fail"  # < 2 GB
    assert health.check_disk(make_ctx(tmp_path, disk_usage=disk(100e9, 4e9))).status == "fail"     # < 5%

    def err(p: str):
        raise OSError("gone")
    assert health.check_disk(make_ctx(tmp_path, disk_usage=err)).status == "fail"


def test_disk_measures_an_existing_parent_of_a_missing_data_root(tmp_path):
    probed: list[str] = []

    def disk(p: str) -> tuple[float, float, float]:
        probed.append(p)
        return (1e12, 0, 5e11)
    s = SETTINGS.model_copy(update={"data_root": str(tmp_path / "not" / "yet")})
    health.check_disk(make_ctx(tmp_path, settings=s, disk_usage=disk))
    assert probed == [str(tmp_path)]


# ------------------------------------------------------------------------------------------------ supervisor/halt
def test_supervisor(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_supervisor(ctx).status == "fail"                       # never ran
    f = tmp_path / "supervisor.json"
    write(f, {"ts": ago(3), "halt": False, "reasons": []})
    assert health.check_supervisor(ctx).status == "ok"
    write(f, {"ts": ago(30), "halt": False, "reasons": []})
    assert health.check_supervisor(ctx).status == "warn"
    write(f, {"ts": ago(120), "halt": False, "reasons": []})
    c = health.check_supervisor(ctx)
    assert c.status == "fail" and "engines block entries" in c.reason
    write(f, {"ts": ago(2), "halt": True, "reasons": ["combined_daily_cap"]})
    c = health.check_supervisor(ctx)
    assert c.status == "warn" and "combined_daily_cap" in c.reason
    f.write_text("{torn")
    assert health.check_supervisor(ctx).status == "fail"


def test_owner_halt_and_rearm(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_halt(ctx).status == "ok"
    bus = ApprovalBus(tmp_path)
    bus.set_halt(True, by="telegram:42", reason="news")
    c = health.check_halt(ctx)
    assert c.status == "warn" and "telegram:42" in c.reason and "news" in c.reason
    bus.owner_rearm(by="dashboard:owner@x")
    c = health.check_halt(ctx)
    assert c.status == "ok" and "re-arm by dashboard:owner@x" in c.reason
    (tmp_path / "control.json").write_text("not json")
    assert health.check_halt(ctx).status == "fail"


def test_phase_state(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_phase(ctx).status == "ok"
    f = tmp_path / "phase_state.json"
    write(f, {"phase": 2, "gates_passed": ["foundation_to_backtest", "backtest_to_paper"]})
    c = health.check_phase(ctx)
    assert c.status == "ok" and "phase 2" in c.reason
    write(f, {"phase": 3, "gates_passed": ["foundation_to_backtest", "paper_to_tiny_live"]})
    assert health.check_phase(ctx).status == "fail"
    f.write_text("[")
    assert health.check_phase(ctx).status == "fail"


# ------------------------------------------------------------------------------------------------ engines
def engine(tmp_path: Path, age_s: float, now: pd.Timestamp = NOW, **kw) -> None:
    payload = {"account": "icm-demo", "ts": ago(age_s, now), "stage": "normal", "last_tick_age_s": 0.5,
               "blackout": None, "dq_error": False, "dq_checks": []}
    payload.update(kw)
    write(tmp_path / "engine_icm-demo.json", payload)


def test_engine_missing_fails_only_while_market_is_open(tmp_path):
    assert health.check_engine(make_ctx(tmp_path), "icm-demo").status == "fail"
    assert health.check_engine(make_ctx(tmp_path, now=SATURDAY), "icm-demo").status == "warn"


def test_engine_state_age_is_market_aware(tmp_path):
    engine(tmp_path, 60)
    assert health.check_engine(make_ctx(tmp_path), "icm-demo").status == "ok"
    engine(tmp_path, 25 * 60)
    assert health.check_engine(make_ctx(tmp_path), "icm-demo").status == "warn"
    engine(tmp_path, 3600)
    c = health.check_engine(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "market is open" in c.reason
    engine(tmp_path, 20 * 3600, now=SATURDAY)                               # Friday close -> Saturday: fine
    c = health.check_engine(make_ctx(tmp_path, now=SATURDAY), "icm-demo")
    assert c.status == "ok" and "market closed" in c.reason


def test_engine_stage_dq_tick_and_blackout(tmp_path):
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 60, stage="halted")
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "fail" and "HALTED" in c.reason
    engine(tmp_path, 60, stage="size_down")
    assert health.check_engine(ctx, "icm-demo").status == "warn"
    engine(tmp_path, 60, dq_error=True, dq_checks=["gap", "non_monotonic"])
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "fail" and "gap, non_monotonic" in c.reason
    engine(tmp_path, 60, last_tick_age_s=120)
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "warn" and "last tick" in c.reason
    engine(tmp_path, 60, blackout={"title": "US CPI", "ts_utc": NOW.isoformat()})
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "ok" and "US CPI" in c.reason
    (tmp_path / "engine_icm-demo.json").write_text("{")
    assert health.check_engine(ctx, "icm-demo").status == "fail"


def test_engine_stale_bars_and_a_refused_rearm_are_reported(tmp_path):
    ctx = make_ctx(tmp_path)
    engine(tmp_path, 60, stale_bars=True)
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "warn" and "last bar" in c.reason                     # design R25: stale data alerts
    engine(tmp_path, 60, stale_bars=True, now=SATURDAY)
    assert "last bar" not in health.check_engine(make_ctx(tmp_path, now=SATURDAY), "icm-demo").reason
    engine(tmp_path, 60, stage="halted", rearm_refused="re-arm needs 10 trading days ...; 3 so far")
    c = health.check_engine(ctx, "icm-demo")
    assert c.status == "fail" and "3 so far" in c.reason


def test_risk_state(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_risk_state(ctx, "icm-demo").status == "warn"          # engine never ran
    f = tmp_path / "risk_icm-demo.json"
    write(f, {"last_reset": "2026-10-07T00:00:05+00:00", "stage": "normal", "rearm_seen": None})
    assert health.check_risk_state(ctx, "icm-demo").status == "ok"
    write(f, {"last_reset": "2026-10-07T00:00:05+00:00", "stage": "halted", "rearm_seen": None})
    c = health.check_risk_state(ctx, "icm-demo")
    assert c.status == "fail" and "re-arm" in c.reason
    write(f, {"last_reset": "2026-09-28T00:00:05+00:00", "stage": "normal", "rearm_seen": None})
    assert health.check_risk_state(ctx, "icm-demo").status == "warn"          # loss-cap day not rolled for 9 days
    f.write_text("nope")
    assert health.check_risk_state(ctx, "icm-demo").status == "fail"


def test_risk_state_flags_a_rearm_the_engine_never_applied(tmp_path):
    write(tmp_path / "risk_icm-demo.json", {"last_reset": NOW.isoformat(), "stage": "halted", "rearm_seen": "old"})
    write(tmp_path / "control.json", {"halted": False, "rearm_id": "new", "rearm_by": "dashboard:o", "rearm_ts": ago(3600)})
    c = health.check_risk_state(make_ctx(tmp_path), "icm-demo")
    assert c.status == "fail" and "not been applied" in c.reason
    write(tmp_path / "risk_icm-demo.json", {"last_reset": NOW.isoformat(), "stage": "normal", "rearm_seen": "new"})
    assert health.check_risk_state(make_ctx(tmp_path), "icm-demo").status == "ok"
    write(tmp_path / "control.json", {"halted": False, "rearm_id": "newer", "rearm_ts": ago(30)})
    assert health.check_risk_state(make_ctx(tmp_path), "icm-demo").status == "ok"    # just issued: give it time


# ------------------------------------------------------------------------------------------------ scheduler
def sched_file(tmp_path: Path, ts: pd.Timestamp, jobs: dict) -> None:
    write(tmp_path / "scheduler.json", {"ts": ts.isoformat(), "jobs": jobs})


def by_name(checks: list[Check]) -> dict[str, Check]:
    return {c.name: c for c in checks}


def test_scheduler_written_by_the_real_scheduler_is_healthy(tmp_path):
    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: NOW)
    for name in type(SETTINGS.scheduler).model_fields:
        sch.add(name, Schedule(kind="daily", at="23:00"), lambda slot: None)
    out = by_name(health.check_scheduler(make_ctx(tmp_path)))
    assert out["scheduler"].status == "ok"
    assert all(c.status == "ok" for c in out.values()), out
    assert "waiting for its first slot" in out["job:nightly_costs"].reason


def test_scheduler_missing_and_stale(tmp_path):
    assert health.check_scheduler(make_ctx(tmp_path))[0].status == "fail"
    sched_file(tmp_path, NOW - pd.Timedelta(minutes=30), {})
    out = by_name(health.check_scheduler(make_ctx(tmp_path)))
    assert out["scheduler"].status == "fail"
    assert out["job:nightly_costs"].status == "warn"                          # configured, never registered
    (tmp_path / "scheduler.json").write_text("{")
    assert health.check_scheduler(make_ctx(tmp_path))[0].status == "fail"


def test_scheduler_job_states(tmp_path):
    t = lambda s: (NOW - pd.Timedelta(s)).isoformat()   # noqa: E731
    future = (NOW + pd.Timedelta("9h")).isoformat()
    jobs = {
        "nightly_costs": {"last_ok": False, "failures": 3, "last_error": "KeyError: 'ticks'\nTraceback ...",
                          "last_started": t("15h"), "last_finished": t("15h"), "next_slot": future},
        "model_watch": {"last_ok": True, "last_started": t("40h"), "last_finished": t("40h"), "next_slot": t("2h")},
        "calendar_archive": {"last_ok": True, "last_slot": t("8h"), "last_skipped": t("1h"),
                             "last_started": t("32h"), "last_finished": t("32h"), "next_slot": future},
        "agents_daily": {"last_ok": True, "last_started": t("14h"), "last_finished": t("14h"), "next_slot": future},
    }
    sched_file(tmp_path, NOW - pd.Timedelta("10s"), jobs)
    out = by_name(health.check_scheduler(make_ctx(tmp_path)))
    assert out["scheduler"].status == "ok"
    assert out["job:nightly_costs"].status == "fail" and "3 failures" in out["job:nightly_costs"].reason
    assert "KeyError" in out["job:nightly_costs"].reason and "Traceback" not in out["job:nightly_costs"].reason
    assert out["job:model_watch"].status == "fail" and "overdue" in out["job:model_watch"].reason
    assert out["job:calendar_archive"].status == "warn" and "skipped" in out["job:calendar_archive"].reason
    assert out["job:agents_daily"].status == "ok"


def test_scheduler_busy_with_a_long_job_is_not_dead(tmp_path):
    t = lambda s: (NOW - pd.Timedelta(s)).isoformat()   # noqa: E731
    jobs = {"saturday_retrain": {"last_ok": True, "last_started": t("2h"), "last_finished": t("168h"), "next_slot": t("2h")}}
    sched_file(tmp_path, NOW - pd.Timedelta("2h"), jobs)                       # the loop is blocked in the job
    out = by_name(health.check_scheduler(make_ctx(tmp_path)))
    assert out["scheduler"].status == "ok" and "busy" in out["scheduler"].reason
    assert out["job:saturday_retrain"].status == "ok" and "running" in out["job:saturday_retrain"].reason
    jobs["saturday_retrain"]["last_started"] = t("13h")
    sched_file(tmp_path, NOW - pd.Timedelta("13h"), jobs)
    out = by_name(health.check_scheduler(make_ctx(tmp_path)))
    assert out["job:saturday_retrain"].status == "fail" and "never finished" in out["job:saturday_retrain"].reason


# ------------------------------------------------------------------------------------------------ costs/news/spend
def test_cost_table_age(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_costs(ctx, "icm-demo").status == "warn"
    f = tmp_path / "costs_icm-demo.json"
    for days, status in ((1, "ok"), (5, "warn"), (10, "fail")):
        write(f, {"account_id": "icm-demo", "built_utc": (NOW - pd.Timedelta(days=days)).isoformat(),
                  "swap_long_usd_per_lot": -48.0, "swap_short_usd_per_lot": 9.0})
        assert health.check_costs(ctx, "icm-demo").status == status, days
    f.write_text("{}")
    assert health.check_costs(ctx, "icm-demo").status == "fail"


def test_news_feed_health(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_news(ctx).status == "warn"
    f = tmp_path / "news_feeds.json"
    fresh = (NOW - pd.Timedelta(minutes=4)).isoformat()
    write(f, {"a": {"ok": True, "items": 3, "ts": fresh}, "b": {"ok": True, "items": 1, "ts": fresh}})
    assert health.check_news(ctx).status == "ok"
    write(f, {"a": {"ok": True, "ts": fresh}, "b": {"ok": False, "error": "HTTPError: 404", "ts": fresh}})
    c = health.check_news(ctx)
    assert c.status == "warn" and "b: HTTPError: 404" in c.reason
    write(f, {"a": {"ok": False, "error": "x", "ts": fresh}, "b": {"ok": False, "error": "y", "ts": fresh}})
    assert health.check_news(ctx).status == "fail"
    old = (NOW - pd.Timedelta(hours=1)).isoformat()
    write(f, {"a": {"ok": True, "ts": old}})
    assert "news service down" in health.check_news(ctx).reason
    no_feeds = SETTINGS.model_copy(update={"news": SETTINGS.news.model_copy(update={"feeds": {}})})
    assert health.check_news(make_ctx(tmp_path, settings=no_feeds)).status == "ok"


def test_agent_spend_against_cap(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_agent_spend(ctx).status == "ok"
    cap = SETTINGS.agents.monthly_cap_usd
    f = tmp_path / "agent_spend.json"
    for spent, status in ((0.1 * cap, "ok"), (0.85 * cap, "warn"), (cap, "fail")):
        write(f, {"2026-09": 10 * cap, "2026-10": spent})                      # last month's spend does not count
        assert health.check_agent_spend(ctx).status == status
    f.write_text("[1]")
    assert health.check_agent_spend(ctx).status == "fail"
    assert health.check_agent_spend(make_ctx(tmp_path, settings=None)).status == "warn"


# ------------------------------------------------------------------------------------------------ approvals/alerts
def proposal(pid: str, created: float) -> Proposal:
    return Proposal(proposal_id=pid, account_id="icm-demo", agent_id="trend-0", side=1, lots=0.1, entry=2400.0,
                    stop=2390.0, target=2420.0, p=0.6, ev_r=0.3, spread_points=20, top_features=[], created=created)


def test_stuck_approvals(tmp_path):
    ctx = make_ctx(tmp_path)
    assert health.check_approvals(ctx).status == "ok"
    bus = ApprovalBus(tmp_path)
    bus.publish(proposal("p-open", ago(30)))                                 # inside its 90 s window
    assert health.check_approvals(ctx).status == "ok"
    bus.publish(proposal("p-stuck", ago(3600)))
    c = health.check_approvals(ctx)
    assert c.status == "warn" and "p-stuck" in c.reason and "1 pending" in c.reason


def test_alert_loop_freshness(tmp_path):
    assert health.check_alert_loop(make_ctx(tmp_path)).status == "warn"
    HealthWatch(tmp_path).record(HealthReport(ts=NOW - pd.Timedelta(minutes=3), status="ok", checks=[]))
    assert health.check_alert_loop(make_ctx(tmp_path)).status == "ok"
    assert health.check_alert_loop(make_ctx(tmp_path, now=NOW + pd.Timedelta(hours=1))).status == "warn"


# ------------------------------------------------------------------------------------------------ report + CLI
def healthy_state(tmp_path: Path) -> None:
    write(tmp_path / "supervisor.json", {"ts": ago(2), "halt": False, "reasons": []})
    engine(tmp_path, 60)
    write(tmp_path / "risk_icm-demo.json", {"last_reset": NOW.isoformat(), "stage": "normal"})
    sch = Scheduler(tmp_path / "scheduler.json", clock=lambda: NOW)
    for name in type(SETTINGS.scheduler).model_fields:
        sch.add(name, Schedule(kind="daily", at="23:00"), lambda slot: None)
    write(tmp_path / "costs_icm-demo.json", {"built_utc": (NOW - pd.Timedelta(hours=14)).isoformat(),
                                             "swap_long_usd_per_lot": -48.0, "swap_short_usd_per_lot": 9.0})
    write(tmp_path / "news_feeds.json", {"a": {"ok": True, "ts": NOW.isoformat()}})
    HealthWatch(tmp_path).record(HealthReport(ts=NOW, status="ok", checks=[]))


def test_full_report_healthy_and_static_subset(tmp_path):
    healthy_state(tmp_path)
    r = health.run_checks(make_ctx(tmp_path))
    assert r.status == "ok", health.render(r)
    assert {"supervisor", "engine:icm-demo", "risk:icm-demo", "job:monthly_research", "news", "alerts"} <= {c.name for c in r.checks}
    s = health.run_checks(make_ctx(tmp_path), static_only=True)
    assert [c.name for c in s.checks] == ["settings", "accounts", "secrets", "disk"]
    assert health.exit_code(r) == 0
    assert "overall: ok" in health.render(r)


def test_exit_code_ignores_fails_already_in_the_baseline():
    def rep(*fails: str) -> HealthReport:
        return HealthReport(ts=NOW, status="fail" if fails else "ok",
                            checks=[Check(name=n, status="fail", reason="x") for n in fails])
    assert health.exit_code(rep("news")) == 1
    assert health.exit_code(rep("news"), baseline=rep("news")) == 0
    assert health.exit_code(rep("news", "supervisor"), baseline=rep("news")) == 1


def test_cli_json_out_and_baseline(tmp_path, capsys):
    healthy_state(tmp_path)
    ctx = make_ctx(tmp_path)
    out = tmp_path / "logs" / "before.json"
    assert health.main(["--json", "--out", str(out)], ctx=ctx) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "ok" and json.loads(out.read_text())["checks"] == printed["checks"]
    (tmp_path / "supervisor.json").unlink()
    assert health.main([], ctx=ctx) == 1
    assert "FAIL  supervisor" in capsys.readouterr().out
    assert health.main(["--baseline", str(out)], ctx=ctx) == 1                 # new fail vs the baseline
    health.main(["--out", str(out)], ctx=ctx)
    assert health.main(["--baseline", str(out)], ctx=ctx) == 0                 # already failing before
    assert health.main(["--static"], ctx=ctx) == 0


# ------------------------------------------------------------------------------------------------ alert dedupe
def report(**statuses: str) -> HealthReport:
    checks = [Check(name=n.replace("_", ":"), status=s, reason=f"{n} is {s}") for n, s in statuses.items()]  # type: ignore[arg-type]
    return HealthReport(ts=NOW, status=health._worst([c.status for c in checks]), checks=checks)


def test_alerts_only_on_state_change(tmp_path):
    w = HealthWatch(tmp_path)
    first = w.poll(report(supervisor="fail", news="warn", disk="ok"))
    assert first is not None and "FAIL supervisor" in first and "news" not in first
    assert w.poll(report(supervisor="fail", news="warn", disk="ok")) is None         # still failing: no repeat
    assert w.poll(report(supervisor="fail", news="fail", disk="ok")) is not None     # warn -> fail is new
    text = w.poll(report(supervisor="ok", news="fail", disk="ok"))
    assert text is not None and "recovered: supervisor (ok)" in text and "still failing: news" in text
    assert w.poll(report(supervisor="ok", news="fail", disk="ok")) is None
    assert w.poll(report(supervisor="fail", news="fail", disk="ok")) is not None     # flapped back: alert again


def test_alert_state_survives_restart_and_unsent_alerts_are_retried(tmp_path):
    w = HealthWatch(tmp_path)
    r = report(engine_icm="fail")
    assert w.alert(r) is not None                     # send failed: record() not called
    assert HealthWatch(tmp_path).alert(r) is not None  # retried by the next pass / after a restart
    w.record(r)
    assert HealthWatch(tmp_path).poll(r) is None       # persisted in health_last.json
    saved = json.loads((tmp_path / "health_last.json").read_text())
    assert saved["statuses"] == {"engine:icm": "fail"}
    (tmp_path / "health_last.json").write_text("garbage")
    assert HealthWatch(tmp_path).alert(r) is not None   # unreadable state: announce once more rather than stay silent


def test_alert_text_is_bounded():
    many = HealthReport(ts=NOW, status="fail",
                        checks=[Check(name=f"job:{i}", status="fail", reason="x" * 300) for i in range(40)])
    text = health.alert_text({}, many)
    assert text is not None and len(text) <= health.MAX_ALERT


# ------------------------------------------------------------------------------------------------ VPS scripts
OPS = ROOT / "goldbot" / "ops"


def _services(ps1: str) -> set[str]:
    return set(re.findall(r'"(goldbot-[a-z0-9-]+)"', ps1))


def _run_commands() -> set[str]:
    return set(re.findall(r'cmd == "([a-z-]+)"', (OPS / "run.py").read_text()))


def test_update_script_uses_only_bootstrap_services_and_run_commands():
    boot = (OPS / "vps_bootstrap.ps1").read_text()
    upd = (OPS / "vps_update.ps1").read_text()
    boot_svcs = set(re.findall(r'name="(goldbot-[a-z0-9-]+)"', boot))
    assert boot_svcs and _services(upd) == boot_svcs           # every service stopped/started, nothing invented
    stop = re.search(r"\$StopOrder = @\((.*?)\)", upd, re.S)
    start = re.search(r"\$StartOrder = @\((.*?)\)", upd, re.S)
    assert stop and start
    stop_l = re.findall(r'"(goldbot-[a-z0-9-]+)"', stop.group(1))
    start_l = re.findall(r'"(goldbot-[a-z0-9-]+)"', start.group(1))
    assert set(stop_l) == set(start_l) == boot_svcs
    engines = {s for s in boot_svcs if s.startswith("goldbot-engine-")}
    assert set(stop_l[-len(engines):]) == engines               # engines stopped last
    assert start_l[0] == "goldbot-supervisor" and set(start_l[1:1 + len(engines)]) == engines
    cmds = set(re.findall(r"goldbot\.ops\.run ([a-z-]+)", upd))
    assert cmds and cmds <= _run_commands()
    flags = set(re.findall(r"health ((?:--[a-z-]+ ?[^ -]*\s*)+)", upd))
    used = {f for line in flags for f in re.findall(r"--[a-z-]+", line)}
    known = {"--json", "--static", "--out", "--baseline", "--state-dir"}
    assert used and used <= known


def test_bootstrap_service_commands_exist_in_run_py():
    boot = (OPS / "vps_bootstrap.ps1").read_text()
    cmds = set(re.findall(r"goldbot\.ops\.run ([a-z-]+)", boot))
    assert {"supervisor", "engine", "health"} <= cmds <= _run_commands()


@pytest.mark.parametrize("cmd", ["health"])
def test_run_py_dispatches_health(cmd):
    assert cmd in _run_commands()
