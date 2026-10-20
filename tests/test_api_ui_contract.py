"""The API contract of the UI build plan's lane 1 (docs/design/ui-build-plan-2026-10-19.md): the fields backlog 103, 92,
93, 97 and 98 add, in one contract change.

* 103: `Proposal.account_mode`, `window_s` and `ask_reason` (null on live: "Real money NEVER shows an 'asking because'
  line"); the API never takes an approval of a `terminal_not_demo` card.
* 92: the entry-mode card lists the same ask kinds per account (`ForcedPropose`, `AutoModeView.accounts`).
* 93: `Proposal.spread_normal_points` (median spread for this hour-of-week over the last 20 recorded trading days)
  and what else is open (`open_positions`, `open_risk_amount`).
* 97: `ResearchView.summary`. 98: the supervisor's combined caps in money and when they reset."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import explain, spread_normal
from goldbot.api.app import create_app
from goldbot.api.schema import ResearchSummary
from goldbot.data import ticks
from goldbot.telegram.approvals import ASK_KINDS, AskReason, Proposal, ask_reason_text
from goldbot.telegram.bus import ApprovalBus
from tests.test_api import _owner_headers

UNTIL = datetime(2026, 11, 17, 14, 5, tzinfo=timezone.utc).timestamp()


def _prop(**kw: Any) -> Proposal:
    base: dict[str, Any] = dict(proposal_id="icm-1-a", account_id="icm-demo", agent_id="trend-g0-x", side=1, lots=0.05,
                                entry=2400.25, stop=2396.25, target=2406.25, p=0.62, ev_r=0.31, spread_points=22,
                                top_features=[("atr_14", 0.4)], currency="GBP", risk_amount=8.2,
                                risk_fraction=0.0082, created=time.time())
    base.update(kw)
    return Proposal(**base)


def _engine(state: Path, **over: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"account": "icm-demo", "mode": "demo", "ts": time.time(), "equity": 1000.0, "balance": 1000.0,
                         "currency": "GBP", "day_start_equity": 1000.0, "week_start_equity": 1000.0, "stage": "normal",
                         "approval_mode": "propose", "open_positions": 1, "open_risk": 8.2}
    e.update(over)
    (state / f"engine_{e['account']}.json").write_text(json.dumps(e))
    return e


def _client(state: Path) -> tuple[TestClient, dict[str, str]]:
    app = create_app(state, web_dist=state / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    return c, _owner_headers(c, app)


def _cards(state: Path) -> dict[str, dict[str, Any]]:
    c, h = _client(state)
    return {r["proposal_id"]: r for r in c.get("/api/proposals", headers=h).json()}


# ---------------------------------------------------------------------------------------------- 103: class and reason
@pytest.mark.parametrize("kind", ASK_KINDS)
def test_a_demo_card_carries_its_ask_reason_in_words_for_each_of_the_six_kinds(tmp_path, kind):
    until = UNTIL if kind == "rearm_lock" else None
    reason = AskReason(kind=kind, until=until)
    ApprovalBus(tmp_path).publish(_prop(account_mode="demo", ask_reason=reason, window_s=300))
    r = _cards(tmp_path)["icm-1-a"]
    assert (r["account_mode"], r["window_s"]) == ("demo", 300)
    assert r["ask_reason"]["kind"] == kind and r["ask_reason"]["text"] == ask_reason_text(reason)
    assert (r["ask_reason"]["until"] is not None) is (kind == "rearm_lock")


@pytest.mark.parametrize("mode", ["live", "paper", None])
def test_a_card_that_is_not_demo_never_carries_an_ask_reason_even_if_an_engine_gave_one(tmp_path, mode):
    ApprovalBus(tmp_path).publish(_prop(account_mode=mode, ask_reason=AskReason(kind="owner_propose")))
    r = _cards(tmp_path)["icm-1-a"]
    assert (r["account_mode"], r["ask_reason"], r["window_s"]) == (mode, None, 90)


def test_an_older_engines_card_takes_the_account_mode_from_its_engine_file(tmp_path, enabled_accounts):
    enabled_accounts("icm-demo")
    ApprovalBus(tmp_path).publish(_prop())                                  # no account_mode on the card
    _engine(tmp_path, mode="live")
    assert _cards(tmp_path)["icm-1-a"]["account_mode"] == "live"


@pytest.mark.parametrize("kind, terminal", [("terminal_not_demo", "real"), ("terminal_unknown", None)])
def test_the_api_refuses_to_approve_a_terminal_fault_card_and_takes_a_rejection(tmp_path, kind, terminal):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(account_mode="demo", ask_reason=AskReason(kind=kind, terminal=terminal)))
    p = bus.get("icm-1-a")
    assert p is not None
    c, h = _client(tmp_path)
    r = c.post("/api/decisions", json={"proposal_id": "icm-1-a", "action": "approve", "created": p.created}, headers=h)
    assert r.status_code == 400 and kind in r.json()["detail"]
    assert not list(bus.decisions_dir.glob("*.json"))
    r = c.post("/api/decisions", json={"proposal_id": "icm-1-a", "action": "reject", "reason_code": "other",
                                       "created": p.created}, headers=h)
    assert r.status_code == 200


def test_a_live_card_is_still_approved_only_by_the_owners_decision(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop(account_id="icm-live", proposal_id="icm-live-1", account_mode="live"))
    p = bus.get("icm-live-1")
    assert p is not None
    c, h = _client(tmp_path)
    r = c.post("/api/decisions", json={"proposal_id": "icm-live-1", "action": "approve", "created": p.created},
               headers=h)
    assert r.status_code == 200 and r.json()["outcome"] == "SUBMITTED"          # recorded; the engine applies it


# ---------------------------------------------------------------------------------------------- 93: what else is open
def test_a_pending_card_says_what_else_is_open_on_its_account(tmp_path, enabled_accounts):
    enabled_accounts("icm-demo")
    ApprovalBus(tmp_path).publish(_prop(account_mode="demo"))
    _engine(tmp_path, open_positions=2, open_risk=16.4)
    r = _cards(tmp_path)["icm-1-a"]
    assert (r["open_positions"], r["open_risk_amount"]) == (2, 16.4)


def test_without_an_engine_file_what_is_open_is_unknown_not_zero(tmp_path):
    ApprovalBus(tmp_path).publish(_prop(account_mode="demo"))
    r = _cards(tmp_path)["icm-1-a"]
    assert (r["open_positions"], r["open_risk_amount"], r["spread_normal_points"]) == (None, None, None)


# ---------------------------------------------------------------------------------------------- 93: usual spread
def _hour_ticks(start: pd.Timestamp, spread: float, n: int = 120) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="30s")
    bid = np.full(n, 2400.0)
    return pd.DataFrame({"ts_utc": ts, "bid": bid, "ask": bid + spread})


def _record(state: Path, at: pd.Timestamp, spreads: list[float], account: str = "icm-demo") -> None:
    """One recorded day a week, a week apart, back from `at`: ticks across at's UTC hour with the given spread, plus a
    wide hour on the same days that must not count (another hour-of-week)."""
    hour = at.floor("h")
    for k, s in enumerate(spreads, start=1):
        day = hour - pd.Timedelta(days=7 * k)
        frame = pd.concat([_hour_ticks(day, s), _hour_ticks(day + pd.Timedelta(hours=2), 3.0)])
        ticks.write_day(state, account, day.date(), frame, symbol="XAUUSD", server_tz="UTC", pulled_utc=at)


def test_the_usual_spread_is_the_median_of_each_matching_hours_median_in_points(tmp_path):
    at = pd.Timestamp("2026-10-20 14:20", tz="UTC")
    _record(tmp_path, at, [0.20, 0.30, 0.25, 0.90])
    assert spread_normal.normal_spread_points(tmp_path, "icm-demo", at) == pytest.approx(27.5)


def test_the_usual_spread_needs_two_matching_hours_and_never_reads_today(tmp_path):
    at = pd.Timestamp("2026-10-20 14:20", tz="UTC")
    _record(tmp_path, at, [0.20])
    assert spread_normal.normal_spread_points(tmp_path, "icm-demo", at) is None
    hour = at.floor("h")
    ticks.write_day(tmp_path, "icm-demo", hour.date(), _hour_ticks(hour, 0.5), symbol="XAUUSD", server_tz="UTC",
                    pulled_utc=at)
    assert spread_normal.normal_spread_points(tmp_path, "icm-demo", at) is None


def test_the_usual_spread_is_unknown_for_an_account_without_a_record_or_a_bad_label(tmp_path):
    at = pd.Timestamp("2026-10-20 14:20", tz="UTC")
    assert spread_normal.normal_spread_points(tmp_path, "icm-demo", at) is None
    assert spread_normal.normal_spread_points(tmp_path, "ICM 12345", at) is None


def test_a_pending_card_carries_the_usual_spread_for_its_hour(tmp_path):
    now = pd.Timestamp.now("UTC")
    _record(tmp_path, now, [0.18, 0.18, 0.22])
    ApprovalBus(tmp_path).publish(_prop(account_mode="demo", created=now.timestamp()))
    assert _cards(tmp_path)["icm-1-a"]["spread_normal_points"] == pytest.approx(18.0)


# ---------------------------------------------------------------------------------------------- 92: entry mode
def test_the_entry_mode_card_lists_each_reporting_account_with_its_class_mode_and_reason(tmp_path, enabled_accounts):
    enabled_accounts("icm-demo", "icm-live")
    _engine(tmp_path, approval_mode="auto", ask_reason=None)
    _engine(tmp_path, account="icm-live", mode="live", approval_mode="propose")
    c, h = _client(tmp_path)
    v = c.get("/api/automode", headers=h).json()
    rows = {a["account_id"]: a for a in v["accounts"]}
    assert rows["icm-demo"]["account_mode"] == "demo" and rows["icm-demo"]["effective_mode"] == "auto"
    assert rows["icm-live"]["account_mode"] == "live" and rows["icm-live"]["effective_mode"] == "propose"
    assert rows["icm-live"]["ask_reason"] is None and rows["icm-live"]["last_seen_utc"] is not None
    assert v["window_s"] == 90


@pytest.mark.parametrize("kind", ["owner_propose", "terminal_not_demo", "terminal_unknown", "config_propose"])
def test_a_demo_engines_published_reason_is_a_forced_propose_row(tmp_path, enabled_accounts, kind):
    enabled_accounts("icm-demo")
    reason = AskReason(kind=kind, terminal="real" if kind == "terminal_not_demo" else None)
    _engine(tmp_path, ask_reason=reason.model_dump())
    c, h = _client(tmp_path)
    v = c.get("/api/automode", headers=h).json()
    [row] = [f for f in v["forced_propose"] if f["account_id"] == "icm-demo"]
    assert row["kind"] == kind and row["detail"] == ask_reason_text(reason)
    [acct] = v["accounts"]
    assert acct["ask_reason"]["kind"] == kind


def test_a_live_engines_reason_is_never_listed(tmp_path, enabled_accounts):
    enabled_accounts("icm-live")
    _engine(tmp_path, account="icm-live", mode="live", ask_reason={"kind": "owner_propose"})
    c, h = _client(tmp_path)
    v = c.get("/api/automode", headers=h).json()
    assert v["forced_propose"] == [] and v["accounts"][0]["ask_reason"] is None


def test_a_rearm_lock_read_from_the_risk_file_and_published_by_the_engine_is_listed_once(tmp_path, enabled_accounts):
    enabled_accounts("icm-demo")
    until = pd.Timestamp.now("UTC") + pd.Timedelta(days=3)
    (tmp_path / "risk_icm-demo.json").write_text(json.dumps({"propose_only_until": until.isoformat()}))
    _engine(tmp_path, ask_reason={"kind": "rearm_lock", "until": until.timestamp(), "terminal": None})
    c, h = _client(tmp_path)
    kinds = [f["kind"] for f in c.get("/api/automode", headers=h).json()["forced_propose"]]
    assert kinds == ["rearm_lock"]


# ---------------------------------------------------------------------------------------------- 98: limits in money
def _sup(**over: Any) -> dict[str, Any]:
    s: dict[str, Any] = {"ts": time.time(), "daily_cap": 0.015, "weekly_cap": 0.04, "combined_equity": 1988.0,
                         "combined_day_start_equity": 2000.0, "combined_week_start_equity": 2010.0,
                         "currencies": {"icm-demo": "GBP", "icm-probe": "GBP"}}
    s.update(over)
    return s


def test_the_combined_caps_read_in_money_with_what_is_used_and_when_they_reset(tmp_path):
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup()))
    c, h = _client(tmp_path)
    s = c.get("/api/limits", headers=h).json()["supervisor"]
    assert s["currency"] == "GBP" and s["mixed_currencies"] is False
    assert (s["daily_cap_amount"], s["daily_used_amount"]) == (pytest.approx(30.0), pytest.approx(12.0))
    assert (s["weekly_cap_amount"], s["weekly_used_amount"]) == (pytest.approx(80.4), pytest.approx(22.0))
    day, week = pd.Timestamp(s["day_resets_utc"]), pd.Timestamp(s["week_resets_utc"])
    assert (day.hour, day.minute) == (0, 0) and day > pd.Timestamp.now("UTC")
    assert week.dayofweek == 6 and (week.hour, week.minute) == (0, 0)


def test_a_gain_today_uses_nothing_of_the_combined_cap(tmp_path):
    (tmp_path / "supervisor.json").write_text(json.dumps(_sup(combined_equity=2050.0)))
    c, h = _client(tmp_path)
    s = c.get("/api/limits", headers=h).json()["supervisor"]
    assert (s["daily_used_amount"], s["weekly_used_amount"]) == (0.0, 0.0)


@pytest.mark.parametrize("case", ["mixed", "silent", "missing"])
def test_combined_money_is_null_with_mixed_currencies_or_no_fresh_supervisor(tmp_path, case):
    # built here, not in the parametrize list: a report stamped at collection goes stale (STALE_S) in a long shard
    sup = {"mixed": _sup(currencies={"icm-demo": "GBP", "vantage-demo": "USD"}),   # pounds never added to dollars
           "silent": _sup(ts=time.time() - 600),                                   # a silent supervisor: no money
           "missing": {}}[case]
    (tmp_path / "supervisor.json").write_text(json.dumps(sup))
    c, h = _client(tmp_path)
    s = c.get("/api/limits", headers=h).json()["supervisor"]
    assert all(s[k] is None for k in ("daily_cap_amount", "weekly_cap_amount", "daily_used_amount",
                                      "weekly_used_amount"))
    assert s["mixed_currencies"] is (True if "vantage-demo" in sup.get("currencies", {}) else None)
    assert s["day_resets_utc"] is not None and s["week_resets_utc"] is not None


# ---------------------------------------------------------------------------------------------- 97: research summary
NOW = datetime(2026, 10, 18, 15, 0, tzinfo=timezone.utc)
COSTS: dict[str, Any] = {"swap": {"long_usd_per_lot": -60.0, "short_usd_per_lot": 20.0}, "cost_source": "ICM measured"}
PASS = {"passed": True, "checks": [{"name": "dsr", "passed": True, "detail": "0.97"}]}
FAIL = {"passed": False, "checks": [{"name": "net_r", "passed": False, "detail": "net -0.03 R"}]}


def _row(trial: int, ts: str, gates: dict | None, net_r: float, family: str = "tsmom", **res: Any) -> dict:
    results: dict[str, Any] = {"rule_only": {"net": {"n": 400, "mean_r": net_r}}, **res}
    if gates is not None:
        results["gates"] = gates
    return {"trial": trial, "ts": ts, "agent_id": f"a{trial}", "family": family, "config": {}, "status": "evaluated",
            "rationale": "", "results": results}


def _summary(state: Path, docs: Path, settings: Any = None) -> ResearchSummary:
    s = explain.research_view(state, settings, docs).summary
    assert s is not None                                      # always set by this API
    return s


@pytest.fixture
def research_state(tmp_path, monkeypatch) -> Path:
    monkeypatch.setattr(explain, "_now", lambda: NOW)
    rows = [_row(1, "2026-09-01T10:00:00+00:00", PASS, 0.30, **COSTS),     # all time, passed, the best net R
            _row(2, "2026-10-12T10:00:00+00:00", PASS, 0.12, family="asia_drift", **COSTS),   # this week
            _row(3, "2026-10-13T10:00:00+00:00", PASS, 0.50),              # a pass without costs: never the best
            _row(4, "2026-10-14T10:00:00+00:00", FAIL, -0.05),
            _row(5, "2026-10-15T10:00:00+00:00", None, 0.02),
            _row(6, "2026-10-01T10:00:00+00:00", FAIL, 0.90)]               # a failed trial is never the best
    (tmp_path / "research_registry.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (tmp_path / "research_plan.json").write_text(json.dumps({
        "created_utc": "2026-10-17T00:00:00+00:00", "quarter": "2026Q4", "quarter_budget": 20, "quarter_used": 6,
        "focus": [{"rank": 2, "family": "trend", "budget": 3, "evidence": 0.4, "reasons": []},
                  {"rank": 1, "family": "tsmom", "budget": 8, "evidence": 1.1, "reasons": []}]}))
    return tmp_path


def test_the_research_summary_counts_every_trial_by_verdict_and_the_last_seven_days(research_state, tmp_path):
    s = _summary(research_state, tmp_path / "docs")
    assert (s.passed_total, s.failed_total, s.no_verdict_total, s.not_counted_total) == (2, 2, 1, 1)
    assert (s.since_last_week.run, s.since_last_week.passed, s.since_last_week.failed) == (4, 1, 1)
    assert s.since_last_week.no_verdict == 1 and s.since_last_week.not_counted == 1


def test_the_best_candidate_is_the_counted_pass_with_the_highest_net_r(research_state, tmp_path):
    s = _summary(research_state, tmp_path / "docs")
    assert s.best_candidate is not None and s.best_candidate.trial == 1 and s.best_candidate.net_r == 0.30


def test_with_no_counted_pass_there_is_no_best_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(explain, "_now", lambda: NOW)
    (tmp_path / "research_registry.jsonl").write_text(json.dumps(_row(1, "2026-10-14T10:00:00+00:00", FAIL, 0.9)))
    s = _summary(tmp_path, tmp_path / "docs")
    assert s.best_candidate is None and s.next_trial is None and s.north_star_gbp == 50.0


def test_the_next_trial_is_the_plans_top_ranked_family_without_an_invented_date(research_state, tmp_path):
    s = _summary(research_state, tmp_path / "docs")
    assert s.next_trial is not None and s.next_trial.family == "tsmom" and s.next_trial.planned_date is None
    assert s.next_trial.timeframe is not None


def test_the_north_star_comes_from_settings(research_state, tmp_path):
    from goldbot.config import load_settings
    settings = load_settings()
    assert _summary(research_state, tmp_path / "docs", settings).north_star_gbp == \
        settings.research.north_star_daily_gbp == 50.0


def test_the_research_endpoint_serves_the_summary(research_state):
    c, h = _client(research_state)
    s = c.get("/api/research", headers=h).json()["summary"]
    assert s["passed_total"] == 2 and s["best_candidate"]["trial"] == 1


# ---------------------------------------------------------------------------------------------- the e2e seed
def test_the_e2e_seed_has_a_real_money_card_and_a_demo_card_forced_to_ask_both_open_for_the_whole_run(tmp_path):
    """Review MEDIUM: the browser expires a card from `expires_at` on its own clock, so a 90 s seed card closes before
    the approval story reaches it (server start, first run and sign-in take longer) and a retry cannot help. Both
    cards keep 60 minutes; the 90 s words are held by the unit tests below and in test_card_account_mode."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("e2e_server_seed", Path(__file__).parents[1] / "scripts" / "e2e_server.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.seed_proposals(str(tmp_path))
    cards = _cards(tmp_path)
    live, demo = cards["e2e-long"], cards["e2e-short"]
    assert (live["account_mode"], live["window_s"], live["ask_reason"]) == ("live", 3600, None)
    assert (demo["account_mode"], demo["window_s"], demo["ask_reason"]["kind"]) == ("demo", 3600, "rearm_lock")


@pytest.mark.parametrize("currencies", [None, {}, {"icm-demo": None}])
def test_combined_money_needs_a_known_currency_never_amounts_without_one(tmp_path, currencies):
    sup = _sup()
    if currencies is None:
        del sup["currencies"]
    else:
        sup["currencies"] = currencies
    (tmp_path / "supervisor.json").write_text(json.dumps(sup))
    c, h = _client(tmp_path)
    s = c.get("/api/limits", headers=h).json()["supervisor"]
    assert s["currency"] is None and s["daily_cap"] == 0.015                 # the fraction still shows
    assert all(s[k] is None for k in ("daily_cap_amount", "weekly_cap_amount", "daily_used_amount",
                                      "weekly_used_amount"))


def test_without_settings_the_entry_mode_window_is_unknown_not_a_guessed_90(tmp_path):
    from goldbot.config import load_settings
    assert explain.automode_view(tmp_path, None, True).window_s is None
    settings = load_settings()
    assert explain.automode_view(tmp_path, settings, True).window_s == settings.risk.approval_window_seconds


def test_a_real_money_card_with_the_engines_90_second_window_carries_it_and_reads_90_s(tmp_path):
    from goldbot.telegram.approvals import window_words
    ApprovalBus(tmp_path).publish(_prop(account_id="icm-live", account_mode="live", window_s=90))
    r = _cards(tmp_path)["icm-1-a"]
    assert r["window_s"] == 90 and window_words(r["window_s"]) == "90 s"
    created, expires = r["created"], pd.Timestamp(r["expires_at"]).timestamp()
    assert expires - created == pytest.approx(90)


def test_a_newer_engines_reason_with_an_extra_field_is_read_tolerantly(tmp_path, enabled_accounts):
    """ADR 0010: persisted state is read tolerantly; a field a newer engine adds never hides the reason."""
    enabled_accounts("icm-demo")
    _engine(tmp_path, ask_reason={"kind": "owner_propose", "until": None, "terminal": None, "added_later": 1})
    c, h = _client(tmp_path)
    [acct] = c.get("/api/automode", headers=h).json()["accounts"]
    assert acct["ask_reason"]["kind"] == "owner_propose"
