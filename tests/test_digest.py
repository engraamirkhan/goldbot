"""The owner's daily Telegram digest (goldbot/telegram/digest.py): rendered from fixture state with every input and
with none, short and free of identifiers, one broken input never stops it, sent once per day, and a missed slot is
sent on start only when under 6 hours late."""
import json
import re
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import TelegramSettings, load_settings
from goldbot.execution.not_confirmed import Confirmation
from goldbot.ops.gates_phase import ClosedTrade, closed_trade_file
from goldbot.ops.health import Check, HealthReport
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import STATE_FILE, WEEKEND_LINE, DigestSchedule, build_digest, owner_decisions

NOW = pd.Timestamp("2026-10-10 06:45", tz="UTC")              # Saturday morning; "yesterday" is Friday 9 Oct UTC
YDAY = pd.Timestamp("2026-10-09 10:00", tz="UTC")
ACCOUNT = "icm-demo-51234567"                                  # looks like a login: must never reach the message
EMAIL = "owner@example.com"
ROADMAP = """# Roadmap
## 5. Owner decision queue
| # | Decision | Recommendation |
| --- | --- | --- |
| D1 | Event floor | 150 |
| D2 | Gates | approve |
| D3 | Owner steps | one sitting |
## 6. Next
| D9 | not in the queue | - |
"""


def _proposal(pid: str, created: pd.Timestamp, outcome: Outcome | None = None, **kw: Any) -> Proposal:
    return Proposal(proposal_id=pid, account_id=ACCOUNT, agent_id="trend-g1-abc", side=1, lots=0.1, entry=2400,
                    stop=2396, target=2406, p=0.6, ev_r=0.3, spread_points=20, top_features=[("a", 0.1)],
                    created=created.timestamp(), outcome=outcome, decided_channel="dashboard", **kw)


def _trade(exit_utc: pd.Timestamp, pnl: float, r: float | None, pid: int, **kw: Any) -> ClosedTrade:
    """A net row by default (pnl_basis "net"); `kw` overrides any field."""
    fields: dict[str, Any] = dict(account_id=ACCOUNT, broker="icm", mode="demo", exit_utc=exit_utc, ret=0.001, pnl=pnl,
                                  equity_before=10_000, position_id=pid, r=r, pnl_basis="net")
    fields.update(kw)
    return ClosedTrade(**fields)


def _full_state(sd: Path) -> Path:
    bus = ApprovalBus(sd)
    for pid, outcome, extra in (("a1", Outcome.APPROVED, {}), ("a2", Outcome.APPROVED, {"gate_refusal": ["spread"]}),
                                ("r1", Outcome.REJECTED, {"reason_code": "news"}), ("e1", Outcome.EXPIRED, {})):
        bus.archive(_proposal(pid, YDAY, outcome, **extra))
    bus.archive(_proposal("old", YDAY - pd.Timedelta(days=2), Outcome.APPROVED))      # not yesterday
    bus.publish(_proposal("live", pd.Timestamp.now("UTC")))      # pending now (the bus expires by the wall clock)
    with closed_trade_file(sd, ACCOUNT).open("w") as fh:
        for t in (_trade(YDAY, 120.5, 1.5, 1), _trade(YDAY + pd.Timedelta(hours=2), -40.25, -0.5, 2),
                  _trade(YDAY - pd.Timedelta(days=1), 999.0, 9.0, 3)):
            fh.write(t.model_dump_json() + "\n")
    (sd / f"engine_{ACCOUNT}.json").write_text(json.dumps({
        "account": ACCOUNT, "stage": "size_down", "equity": 9_700, "balance_closed_hwm": 10_000,
        "day_start_equity": 9_800, "week_start_equity": 10_000, "open_positions": 1, "open_lots": 0.1,
        "currency": "USD", "mode": "live"}))   # a real-money account: its own proposals and closes are reported (D1)           # the digest prints money in the account's own currency (BACKLOG 28)
    (sd / "control.json").write_text(json.dumps({"halted": True, "by": f"dashboard:{EMAIL}", "ts": YDAY.timestamp(),
                                                 "reason": "news"}))
    (sd / "drift.json").write_text(json.dumps({"system_halt": {"reasons": ["cusum"]}}))
    (sd / "supervisor.json").write_text(json.dumps({"ts": NOW.timestamp(), "halt": True, "reasons": [ACCOUNT]}))
    (sd / "attribution.json").write_text(json.dumps({"min_trades": 30, "t_bar": 3.14, "m": 30, "breakdowns": {
        "session": {"london": {"n": 64, "mean_r_net": 0.21, "verdict": "positive", "verdict_adj": "positive"},
                    "newyork": {"n": 50, "mean_r_net": 0.35, "verdict": "positive",
                                "verdict_adj": "indistinguishable from zero"},
                    "asia": {"n": 12, "mean_r_net": -0.9, "verdict": "noise", "verdict_adj": "noise"}},
        "side": {"short": {"n": 41, "mean_r_net": -0.15, "verdict": "negative", "verdict_adj": "negative"}},
        "exit": {"time": {"n": 41, "mean_r_net": -0.6, "verdict": "negative", "verdict_adj": "negative"}},
        "agent": {ACCOUNT: {"n": 99, "mean_r_net": 5.0, "verdict": "positive", "verdict_adj": "positive"}}},
        "overall": {"n": 155, "mean_r_net": 0.12, "verdict_adj": "positive"}}))
    (sd / "deploy").mkdir()
    (sd / "deploy" / "pending.json").write_text(json.dumps({"sha": "a" * 40}))
    reg = sd / "research_registry.jsonl"
    reg.write_text("\n".join(json.dumps(r) for r in (
        {"trial": 1, "ts": "2026-10-02T00:00:00+00:00", "status": "evaluated", "family": "trend"},
        {"trial": 2, "ts": "2026-10-03T00:00:00+00:00", "status": "preregistered", "family": "tsmom"},
        {"trial": 2, "ts": "2026-10-04T00:00:00+00:00", "status": "evaluated", "family": "tsmom",
         "preregistration": {"trial": 2}},
        {"trial": 3, "ts": "2026-10-05T00:00:00+00:00", "status": "preregistered", "family": "carry"})) + "\n")
    return reg


def _report(*checks: tuple[str, str]) -> HealthReport:
    cs = [Check(name=n, status=s, reason=f"by dashboard:{EMAIL} on {ACCOUNT}") for n, s in checks]  # type: ignore[arg-type]
    worst = "fail" if any(s == "fail" for _, s in checks) else "warn" if any(s == "warn" for _, s in checks) else "ok"
    return HealthReport(ts=NOW, status=worst, checks=cs)  # type: ignore[arg-type]


def _live(sd: Path) -> Path:
    """A real-money engine file: the report shows an engine's own proposals and closes only for real money (D1)."""
    (sd / f"engine_{ACCOUNT}.json").write_text(json.dumps({"account": ACCOUNT, "mode": "live", "currency": "USD"}))
    return sd


def _full_digest(tmp_path: Path) -> str:
    _full_state(tmp_path)
    roadmap = tmp_path / "ROADMAP.md"
    roadmap.write_text(ROADMAP)
    report = _report((f"engine:{ACCOUNT}", "fail"), ("backup_age", "warn"), (f"daily_cap:{ACCOUNT}", "warn"),
                     ("disk", "ok"))
    return build_digest(tmp_path, NOW, report=report, settings=load_settings(), roadmap=roadmap)


def test_digest_renders_every_section_from_fixture_state(tmp_path: Path) -> None:
    """An account that is not a lab (no exploration): its proposals and closes on their own lines, every halt on line
    two ahead of health (the lock-screen preview), the month and the open trades, and what is waiting for the owner."""
    text = _full_digest(tmp_path)
    got = text.splitlines()
    assert got[0] == "☀️ Fri 9 Oct results · day ended 22:00 UTC (23:00 UK)"
    assert got[1].startswith("🛑 Halts: owner halt since Fri 09 Oct 10:00 UTC · drift system halt (review pending) · "
                             "supervisor combined-cap halt · exits still run · Attention: fail engine · warn "
                             "backup_age, daily_cap · Entries")
    assert "• Proposals 4: ✅ 2 approved (1 refused by the risk check) · ❌ 1 rejected · ⌛ 1 expired" in got
    assert "• Closed 2 trades: net +$80.25 · +1.00R (1 won, 1 lost)" in got
    assert "Month: 7 trading days so far · open now 1 trade (0.10 lot)" in got
    assert got[-1] == "👉 For you: 1 approval pending · 3 owner decisions open (see OWNER_GUIDE) · 1 deploy on offer"


def test_digest_is_phone_sized_with_units_and_utc(tmp_path: Path) -> None:
    text = _full_digest(tmp_path)
    assert len(text.splitlines()) <= 15
    assert "UTC" in text and "$" in text and "%" in text and "R" in text


def test_digest_never_prints_identifiers(tmp_path: Path) -> None:
    text = _full_digest(tmp_path)
    assert ACCOUNT not in text and "51234567" not in text and EMAIL not in text
    assert not re.search(r"[\w.]+@[\w.]+", text)                 # no email
    assert not re.search(r"\d{6,}", text)                         # no login, chat or Telegram id
    assert "dashboard:" not in text and "telegram:" not in text and "a" * 40 not in text


def test_digest_with_no_state_at_all_still_renders(tmp_path: Path) -> None:
    text = build_digest(tmp_path / "missing", NOW, roadmap=tmp_path / "nope.md")
    assert text.splitlines()[1] == "❔ Health: unknown (the check did not run) · Halts: none · Entries: no engine reporting"
    assert "Month: 7 trading days so far · open now 0 trades" in text and text.endswith("👉 For you: nothing")
    assert "Attribution" not in text and "Proposals" not in text and "Trades closed" not in text
    assert len(text.splitlines()) <= 15


def test_all_good_when_every_check_is_ok(tmp_path: Path) -> None:
    text = build_digest(tmp_path, NOW, report=_report(("disk", "ok"), ("phase", "ok")))
    assert "✅ Health 2 of 2 ok · Halts: none" in text


def test_one_broken_input_never_stops_the_digest(tmp_path: Path) -> None:
    _full_state(tmp_path)
    (tmp_path / f"engine_{ACCOUNT}.json").write_text("{torn")
    (tmp_path / "control.json").write_text("{torn")
    with closed_trade_file(tmp_path, ACCOUNT).open("a") as fh:
        fh.write("{torn\n")
    text = build_digest(tmp_path, NOW, roadmap=tmp_path / "nope.md")
    assert "Engine state: could not read (a torn engine file)" in text and ACCOUNT not in text
    assert "owner state unreadable (entries blocked)" in text
    assert "Closed 2 trades" in text and "unreadable record lines skipped" in text
    assert "Proposals 4" in text and "For you" in text


def test_owner_decisions_counts_only_the_queue_section(tmp_path: Path) -> None:
    (tmp_path / "r.md").write_text(ROADMAP)
    assert owner_decisions(tmp_path / "r.md") == 3


def test_owner_decisions_counts_both_row_id_styles(tmp_path: Path) -> None:
    rm = tmp_path / "ROADMAP.md"
    rm.write_text("## 6. Owner decision queue\n| # | Decision |\n| --- | --- |\n| OD-1 | a |\n| D2 | b |\n"
                  "| ODD | not a row |\n## 7. Next\n| OD-9 | outside the section |\n", encoding="utf-8")
    assert owner_decisions(rm) == 2


def test_owner_decisions_reads_the_real_roadmap() -> None:
    from goldbot.telegram.digest import DEFAULT_ROADMAP
    assert owner_decisions(DEFAULT_ROADMAP) >= 1


# ------------------------------------------------------------------------------------------- schedule and dedupe
def test_digest_is_sent_once_a_day(tmp_path: Path) -> None:
    s = DigestSchedule(tmp_path, "06:45")
    assert not s.due(NOW - pd.Timedelta(minutes=1))       # the last slot (yesterday's) is ~24 h old: skipped
    assert s.due(NOW)
    s.record(NOW)
    assert not s.due(NOW + pd.Timedelta(minutes=1))
    assert not DigestSchedule(tmp_path, "06:45").due(NOW + pd.Timedelta(hours=1))   # survives a restart
    assert DigestSchedule(tmp_path, "06:45").due(NOW + pd.Timedelta(days=1))        # the next morning
    assert json.loads((tmp_path / STATE_FILE).read_text())["last_slot"] == "2026-10-10"


def test_nothing_is_due_before_the_slot_once_yesterdays_was_sent(tmp_path: Path) -> None:
    s = DigestSchedule(tmp_path, "06:45")
    s.record(NOW - pd.Timedelta(days=1))
    assert not s.due(NOW - pd.Timedelta(minutes=1))
    assert s.due(NOW)


def test_missed_slot_is_sent_on_start_only_under_six_hours_late(tmp_path: Path) -> None:
    s = DigestSchedule(tmp_path, "06:45")
    assert s.due(NOW + pd.Timedelta(hours=5, minutes=59))
    assert not s.due(NOW + pd.Timedelta(hours=6))
    assert not s.due(NOW + pd.Timedelta(hours=12))


def test_late_rule_spans_midnight(tmp_path: Path) -> None:
    s = DigestSchedule(tmp_path, "23:00")
    assert s.due(pd.Timestamp("2026-10-11 02:00", tz="UTC"))      # yesterday's 23:00 slot, 3 h late
    s.record(pd.Timestamp("2026-10-11 02:00", tz="UTC"))
    assert not s.due(pd.Timestamp("2026-10-11 03:00", tz="UTC"))
    assert s.due(pd.Timestamp("2026-10-11 23:00", tz="UTC"))


def test_unreadable_dedupe_file_sends_rather_than_skips(tmp_path: Path) -> None:
    (tmp_path / STATE_FILE).write_text("{torn")
    assert DigestSchedule(tmp_path, "06:45").due(NOW)


def test_digest_at_setting_defaults_and_validates() -> None:
    assert TelegramSettings().digest_at == "06:45"
    assert load_settings().telegram.digest_at == "06:45"
    with pytest.raises(ValueError):
        TelegramSettings(digest_at="6:45")
    with pytest.raises(ValueError):
        TelegramSettings(digest_at="24:00")


# ---------------------------------------------------------------------------------------------- P&L basis (finance audit)
def _closed_day(sd: Path, *trades: ClosedTrade) -> list[str]:
    from goldbot.telegram.digest import trades_line
    with closed_trade_file(sd, ACCOUNT).open("w") as fh:
        for t in trades:
            fh.write(t.model_dump_json() + "\n")
    (sd / f"engine_{ACCOUNT}.json").write_text(json.dumps({"account": ACCOUNT, "currency": "GBP"}))
    return trades_line(sd, YDAY.normalize(), YDAY.normalize() + pd.Timedelta(days=1))


def test_yesterdays_net_sums_net_rows_only_and_names_the_gross_only_trades_apart(tmp_path: Path) -> None:
    [line] = _closed_day(tmp_path, _trade(YDAY, 20.0, 1.0, 1),
                         _trade(YDAY, 9.0, 0.5, 2, pnl_basis=None, commission=1.0, swap=0.0),   # legacy, both costs
                         _trade(YDAY, 50.0, 2.5, 3, pnl_basis="gross", money_source="engine"),
                         _trade(YDAY, -4.0, -0.2, 4, pnl_basis=None))                          # legacy, no costs
    assert line.startswith("• Closed 4 trades: net +£29.00 · +1.50R (2 won, 0 lost)")
    assert line.endswith("· 2 trades gross only (costs not recorded): +£46.00 · +2.30R gross")
    assert "75.00" not in line and "+4.00R" not in line


def test_a_day_of_gross_only_trades_says_net_is_not_recorded(tmp_path: Path) -> None:
    [line] = _closed_day(tmp_path, _trade(YDAY, 50.0, 2.5, 1, pnl_basis="gross"))
    assert line == "• Closed 1 trade: net not recorded · 1 trade gross only (costs not recorded): +£50.00 · +2.50R gross"


# ---------------------------------------------------------------------------------------------- the opportunity day (56)
# "Yesterday" is the opportunity day before the digest's (goldbot/ops/report_days.py): [D-2 22:00, D-1 22:00) UTC for
# a digest built on opportunity day D, the same day the month line counts. The risk line stays on the 00:00 UTC risk day.
def _utc(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def _ny(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="America/New_York").tz_convert("UTC")


def _digest_with(sd: Path, now: pd.Timestamp, *exits: pd.Timestamp, **kw: Any) -> dict[str, str]:
    """The digest at `now` over one net trade per exit, the i-th winning $10*i and 0.1R*i (so each set of trades sums
    differently); its header, trades, month and risk lines by name."""
    with closed_trade_file(sd, ACCOUNT).open("w") as fh:
        for i, x in enumerate(exits, 1):
            fh.write(_trade(x, 10.0 * i, round(0.1 * i, 1), i).model_dump_json() + "\n")
    (sd / f"engine_{ACCOUNT}.json").write_text(json.dumps({
        "account": ACCOUNT, "stage": "normal", "equity": 9_900, "balance_closed_hwm": 10_000,
        "day_start_equity": 10_000, "week_start_equity": 10_000, "currency": "USD", "mode": "live"}))
    kw.setdefault("settings", load_settings())
    lines = build_digest(sd, now, roadmap=sd / "none.md", **kw).splitlines()

    def one(prefix: str) -> str:
        return next((x for x in lines if x.startswith(prefix)), "")
    return {"header": lines[0].removeprefix("☀️ "), "trades": one("• Closed") or one("Market closed"),
            "proposals": one("• Proposals"), "month": next((x for x in lines if " so far: " in x), "")}


def test_yesterday_header_names_the_trading_day_and_its_end_in_utc_and_uk_time(tmp_path: Path) -> None:
    got = _digest_with(tmp_path, _utc("2026-10-16 06:45"))
    assert got["header"] == "Thu 15 Oct results · day ended 22:00 UTC (23:00 UK)"                                  # BST
    got = _digest_with(tmp_path, _utc("2026-11-12 06:45"))
    assert got["header"] == "Wed 11 Nov results · day ended 22:00 UTC (22:00 UK)"   # GMT


def test_the_header_reads_the_owner_zone_and_drops_the_local_time_when_it_cannot_be_resolved(tmp_path: Path) -> None:
    base = load_settings()

    def with_tz(tz: str) -> Any:
        return base.model_copy(update={"telegram": base.telegram.model_copy(update={"owner_tz": tz})})
    now = _utc("2026-10-16 06:45")
    assert _digest_with(tmp_path, now, settings=with_tz("America/New_York"))["header"] == \
        "Thu 15 Oct results · day ended 22:00 UTC (18:00 EDT)"
    assert _digest_with(tmp_path, now, settings=with_tz("Nowhere/Atlantis"))["header"] == \
        "Thu 15 Oct results · day ended 22:00 UTC"
    assert _digest_with(tmp_path, now, settings=None)["header"] == \
        "Thu 15 Oct results · day ended 22:00 UTC (23:00 UK)"                   # Europe/London by default


def test_a_late_send_reports_the_slots_day_before_so_no_day_is_dropped_or_repeated(tmp_path: Path) -> None:
    # digest_at 18:00: the 15 Oct slot sent 4.5 h late at 22:30 (after the roll) still reports 14 Oct, and the 16 Oct
    # slot reports 15 Oct
    sched = DigestSchedule(tmp_path, "18:00")
    late, next_day = _utc("2026-10-15 22:30"), _utc("2026-10-16 18:00")
    assert _digest_with(tmp_path, late, as_of=sched.slot(late))["header"].startswith("Wed 14 Oct results")
    assert _digest_with(tmp_path, next_day, as_of=sched.slot(next_day))["header"].startswith("Thu 15 Oct results")
    assert _digest_with(tmp_path, late)["header"].startswith("Thu 15 Oct results")   # without the slot: by now


def test_a_close_at_2159_is_yesterdays_and_one_at_2200_is_todays(tmp_path: Path) -> None:
    # Fri 16 Oct digest: yesterday is [Wed 14 Oct 22:00, Thu 15 Oct 22:00) UTC
    got = _digest_with(tmp_path, _utc("2026-10-16 06:45"),
                       _utc("2026-10-14 21:59"),       # Wednesday's day: not yesterday
                       _utc("2026-10-14 22:00"),       # first instant of Thursday's day
                       _utc("2026-10-15 21:59"),       # last minute of Thursday's day
                       _utc("2026-10-15 22:00"))       # Friday's day: today, not yesterday
    assert got["trades"] == "• Closed 2 trades: net +$50.00 · +0.50R (2 won, 0 lost)"      # the 2nd and 3rd


def test_a_digest_built_at_2159_and_at_2200_reports_different_yesterdays(tmp_path: Path) -> None:
    exits = (_utc("2026-10-14 12:00"), _utc("2026-10-15 12:00"), _utc("2026-10-15 13:00"))
    early = _digest_with(tmp_path, _utc("2026-10-15 21:59"), *exits)
    late = _digest_with(tmp_path, _utc("2026-10-15 22:00"), *exits)
    assert early["header"] == "Wed 14 Oct results · day ended 22:00 UTC (23:00 UK)" and early["trades"].startswith("• Closed 1 trade:")
    assert late["header"] == "Thu 15 Oct results · day ended 22:00 UTC (23:00 UK)" and late["trades"].startswith("• Closed 2 trades:")


def test_yesterday_and_the_month_so_far_count_the_same_closes(tmp_path: Path) -> None:
    # Fri 2 Oct digest: October's only complete opportunity day is Thu 1 Oct, [Wed 30 Sep 22:00, Thu 1 Oct 22:00)
    got = _digest_with(tmp_path, _utc("2026-10-02 06:45"),
                       _utc("2026-09-30 21:00"),       # September's last day: in neither line
                       _utc("2026-09-30 23:00"),       # 1 Oct's day: in both (the UTC calendar day put it in September)
                       _utc("2026-10-01 23:00"))       # 2 Oct's day: in neither (the UTC calendar day put it yesterday)
    assert got["trades"] == "• Closed 1 trade: net +$20.00 · +0.20R (1 won, 0 lost)"      # the 2nd
    assert got["month"].startswith("October so far: ") and " · 1 trade · +0.20R" in got["month"]


@pytest.mark.parametrize(("friday", "sunday", "label"), [
    ("2026-10-16", "2026-10-18", "summer"),     # US summer time: close 17:00 EDT = 21:00 UTC, reopen 22:00 UTC
    ("2026-10-30", "2026-11-01", "US DST ends"),  # gap week: close 21:00 UTC; reopen 18:00 EST = 23:00 UTC Sun 1 Nov
    ("2026-11-06", "2026-11-08", "winter"),     # close 17:00 EST = 22:00 UTC, reopen 23:00 UTC
])
def test_weekend_digests_put_friday_on_saturday_and_the_sunday_reopen_on_tuesday(tmp_path: Path, friday: str,
                                                                                 sunday: str, label: str) -> None:
    """Friday's opportunity day ends at 22:00 UTC, at or after the Friday close in both US regimes, so Saturday's
    digest reports it. Sunday's opportunity day [Sat 22:00, Sun 22:00) holds no session in either regime (the reopen,
    22:00 or 23:00 UTC, opens Monday's day), so the Monday digest's yesterday is empty and the reopen's closes are
    Tuesday's. Nothing is reported twice or dropped."""
    fri, sun = pd.Timestamp(friday), pd.Timestamp(sunday)
    last_fri = _ny(f"{friday} 16:59")                      # the last minute before the Friday close
    reopen = _ny(f"{sunday} 18:05")                        # five minutes after the Sunday reopen
    mon_noon = _utc(f"{sun + pd.Timedelta(days=1):%Y-%m-%d} 12:00")
    exits = (last_fri, reopen, mon_noon)
    sat = _digest_with(tmp_path, _utc(f"{fri + pd.Timedelta(days=1):%Y-%m-%d} 06:45"), *exits)
    mon = _digest_with(tmp_path, _utc(f"{sun + pd.Timedelta(days=1):%Y-%m-%d} 06:45"), *exits)
    tue = _digest_with(tmp_path, _utc(f"{sun + pd.Timedelta(days=2):%Y-%m-%d} 06:45"), *exits)
    assert sat["header"].startswith(f"{fri:%a} {fri.day} {fri:%b} results") and sat["trades"].startswith("• Closed 1 trade:")
    assert mon["header"].startswith(f"{sun:%a} {sun.day} {sun:%b} results")
    assert mon["trades"] == WEEKEND_LINE and mon["proposals"] == ""      # one line, and Friday is not repeated
    assert tue["trades"].startswith("• Closed 2 trades:")  # the reopen's close and Monday's


def test_a_weekend_day_with_a_close_shows_its_rows_not_the_weekend_line(tmp_path: Path) -> None:
    # a winter Friday close stamped exactly 22:00 UTC is Saturday's opportunity day, reported by Sunday's digest
    got = _digest_with(tmp_path, _utc("2026-11-08 06:45"), _utc("2026-11-06 22:00"))
    assert got["header"].startswith("Sat 7 Nov results")
    assert got["trades"] == "• Closed 1 trade: net +$10.00 · +0.10R (1 won, 0 lost)"
    assert got["proposals"] == ""                                              # "none" lines are not printed


def test_a_weekend_day_with_a_proposal_shows_its_rows_not_the_weekend_line(tmp_path: Path) -> None:
    ApprovalBus(tmp_path).archive(_proposal("w1", _utc("2026-10-10 12:00"), Outcome.REJECTED))   # a Saturday
    got = _digest_with(tmp_path, _utc("2026-10-11 06:45"))
    assert got["proposals"].startswith("• Proposals 1:") and got["trades"] == ""


def test_the_weekend_line_is_plain_and_points_at_tuesday() -> None:
    assert WEEKEND_LINE == "Market closed Sat–Sun: no trading. Sunday-evening trades show in Tuesday's report."


def test_the_digest_counts_risk_check_refusals_broker_and_mt5_box_refusals_and_unconfirmed_orders_apart(
        tmp_path: Path) -> None:
    # BACKLOG 83: a broker or MT5-box refusal and a not-confirmed card are not "refused by the risk check"; a
    # not-confirmed card the broker later showed opened counts as an opened trade (in no bucket)
    bus = ApprovalBus(tmp_path)
    cards: list[tuple[list[str] | None, str | None]] = [
        (None, None), (["spread"], None), (["broker_refused:10019"], None), (["bridge_stale_order"], None),
        (["bridge_notional_ceiling"], None), (["not_confirmed"], "pending"), (["not_confirmed"], "still_unknown"),
        (["not_confirmed"], "no_trade"), (["not_confirmed"], "opened"), (["not_confirmed"], "opened_late")]
    for i, (codes, outcome) in enumerate(cards):
        nc = Confirmation(outcome=outcome) if outcome is not None else None    # type: ignore[arg-type]
        bus.archive(_proposal(f"a{i}", YDAY, Outcome.APPROVED, gate_refusal=codes, not_confirmed=nc))
    text = build_digest(_live(tmp_path), NOW)
    assert ("Proposals 10: ✅ 10 approved (1 refused by the risk check · 4 not placed by the MT5 box, the broker or the engine · "
            "2 not confirmed by the broker) · ❌ 0 rejected · ⌛ 0 expired") in text


def test_the_digest_names_only_the_buckets_that_have_cards(tmp_path: Path) -> None:
    bus = ApprovalBus(tmp_path)
    bus.archive(_proposal("a1", YDAY, Outcome.APPROVED, gate_refusal=["not_confirmed"]))
    bus.archive(_proposal("a2", YDAY, Outcome.APPROVED, gate_refusal=["not_confirmed"],
                          not_confirmed=Confirmation(outcome="opened_closed")))
    assert "✅ 2 approved (1 not confirmed by the broker) · ❌" in build_digest(_live(tmp_path), NOW)
