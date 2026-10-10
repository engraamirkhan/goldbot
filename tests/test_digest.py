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
from goldbot.ops.gates_phase import ClosedTrade, closed_trade_file
from goldbot.ops.health import Check, HealthReport
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import STATE_FILE, DigestSchedule, build_digest, owner_decisions

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
                    created=created.timestamp(), outcome=outcome, decided_via=f"dashboard:{EMAIL}", **kw)


def _trade(exit_utc: pd.Timestamp, pnl: float, r: float | None, pid: int) -> ClosedTrade:
    return ClosedTrade(account_id=ACCOUNT, broker="icm", mode="demo", exit_utc=exit_utc, ret=0.001, pnl=pnl,
                       equity_before=10_000, position_id=pid, r=r)


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
        "day_start_equity": 9_800, "week_start_equity": 10_000, "open_positions": 1, "open_lots": 0.1}))
    (sd / "control.json").write_text(json.dumps({"halted": True, "by": f"dashboard:{EMAIL}", "ts": YDAY.timestamp(),
                                                 "reason": "news"}))
    (sd / "drift.json").write_text(json.dumps({"system_halt": {"reasons": ["cusum"]}}))
    (sd / "supervisor.json").write_text(json.dumps({"ts": NOW.timestamp(), "halt": True, "reasons": [ACCOUNT]}))
    (sd / "attribution.json").write_text(json.dumps({"min_trades": 30, "breakdowns": {
        "session": {"london": {"n": 64, "mean_r_net": 0.21, "verdict": "positive"},
                    "asia": {"n": 12, "mean_r_net": -0.9, "verdict": "noise"}},
        "exit": {"time": {"n": 41, "mean_r_net": -0.15, "verdict": "indistinguishable from zero"}},
        "agent": {ACCOUNT: {"n": 99, "mean_r_net": 5.0, "verdict": "positive"}}}}))
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


def _full_digest(tmp_path: Path) -> str:
    reg = _full_state(tmp_path)
    roadmap = tmp_path / "ROADMAP.md"
    roadmap.write_text(ROADMAP)
    report = _report((f"engine:{ACCOUNT}", "fail"), ("backup_age", "warn"), (f"daily_cap:{ACCOUNT}", "warn"),
                     ("disk", "ok"))
    return build_digest(tmp_path, NOW, report=report, settings=load_settings(), roadmap=roadmap, registry_path=reg)


def test_digest_renders_every_section_from_fixture_state(tmp_path: Path) -> None:
    text = _full_digest(tmp_path)
    assert text.splitlines()[0] == "☀️ goldbot daily · Sat 10 Oct 2026 · 06:45 UTC"
    assert "⚠️ Attention: fail engine · warn backup_age, daily_cap" in text
    assert "Yesterday (Fri 09 Oct, 00:00-24:00 UTC)" in text
    assert "Proposals 4: ✅ 2 approved (1 refused by the risk check) · ❌ 1 rejected · ⌛ 1 expired" in text
    assert "Closed 2 trades: net +$80.25 · +1.00R (1 won, 1 lost)" in text
    assert "Open now: 1 position (0.10 lots)" in text
    assert "Risk: stage size_down · drawdown 3.0% from peak (halt at 12%) · day loss 1.0% of 2.0% cap · week 3.0% of 5.0% cap" in text
    assert "owner halt since Fri 09 Oct 10:00 UTC" in text and "drift system halt" in text
    assert "supervisor combined-cap halt" in text
    assert "next pre-registered trial #3 (carry) · budget 2026Q4: 2 of 20 trials used" in text
    assert "Attribution: best session london +0.21R/trade net (n 64) · worst exit time -0.15R/trade net (n 41)" in text
    assert "👉 For you: 1 approval pending · 3 owner decisions open (see OWNER_GUIDE) · 1 deploy on offer" in text


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
    assert "❔ Health: unknown" in text
    assert "Proposals: none" in text and "Trades closed: none" in text
    assert "Open now: no engine state yet" in text and "Risk: no engine state yet" in text and "Halts: none" in text
    assert "no pre-registered trial waiting · budget 2026Q4: 0 of 20 trials used" in text
    assert "Attribution" not in text
    assert "0 approvals pending · owner decisions: see OWNER_GUIDE" in text
    assert len(text.splitlines()) <= 15


def test_all_good_when_every_check_is_ok(tmp_path: Path) -> None:
    text = build_digest(tmp_path, NOW, report=_report(("disk", "ok"), ("phase", "ok")))
    assert "✅ All good: 2 health checks ok" in text


def test_one_broken_input_never_stops_the_digest(tmp_path: Path) -> None:
    _full_state(tmp_path)
    (tmp_path / f"engine_{ACCOUNT}.json").write_text("{torn")
    (tmp_path / "attribution.json").write_text("not json")
    (tmp_path / "control.json").write_text("{torn")
    with closed_trade_file(tmp_path, ACCOUNT).open("a") as fh:
        fh.write("{torn\n")
    text = build_digest(tmp_path, NOW, roadmap=tmp_path / "nope.md", registry_path=tmp_path / "research_registry.jsonl")
    assert "Open now: could not read" in text
    assert "Risk: could not read engine state" in text
    assert "owner state unreadable (entries blocked)" in text
    assert "Attribution: could not read" in text
    assert "Closed 2 trades" in text and "unreadable record lines skipped" in text
    assert "Proposals 4" in text and "For you" in text


def test_owner_decisions_counts_only_the_queue_section(tmp_path: Path) -> None:
    (tmp_path / "r.md").write_text(ROADMAP)
    assert owner_decisions(tmp_path / "r.md") == 3


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
