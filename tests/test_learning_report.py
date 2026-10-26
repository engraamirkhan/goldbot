"""The owner's daily learning report (docs/ux/daily-learning-report.md, lane L5a): the 06:45 UTC Telegram message that
replaces the old digest's body. It reports the exploration labs from aux.db (the old digest read closed_trades only and
said "Trades closed: none" while the labs traded), in £ and R, with arms blinded until their read (quant review
2026-10-26), no account identifiers, no buttons, within 15 lines in every state, and one broken source costing one
line, never the message."""
from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import types
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.db import Database
from goldbot.engine import exploration as ex
from goldbot.engine.exploration import LEDGER, ExplorationRecord
from goldbot.ops.gates_phase import ClosedTrade, closed_trade_file
from goldbot.ops.health import Check, HealthReport
from goldbot.research.exploration_data import write_weights
from goldbot.telegram.digest import build_digest, report_text
from tests.test_api_entry_status import engine

NOW = pd.Timestamp("2026-10-27 06:45", tz="UTC")     # Tuesday: the report is on Monday 26 Oct (UK on GMT)
DAY = pd.Timestamp("2026-10-26 10:00", tz="UTC")
SEPT = pd.Timestamp("2026-09-15 10:00", tz="UTC")
MOCK_DAILY = """☀️ Mon 26 Oct results · day ended 22:00 UTC (22:00 UK)
✅ Health 41 of 41 ok · Halts: none · Entries open on 2 of 2
Scalp lab: 214 signals · 63 taken · 61 filled · 58 closed −£17.60 · −3.9R
  costs £10.90 (2.4R): spread £6.80 · slippage (entry) £1.40 · commission £2.70
  refused: 41 slots full · 22 spread wide · 9 news window
  down 2.1% (£230) from peak · warns at 6% (£657) · pauses at 8% (£876)
Day/swing lab: 37 signals · 9 taken · 9 filled · 7 closed +£21.60 · +1.4R
  costs £2.10 (0.1R) · refused: 14 slots full · 8 news window · down 0.8% (£88)
Arms (trades): S1A 96 · S1B 88 · S2A 118 · S3B 126 · MB30 41 · +14 more
  none within 50% of a stop · none retired · each read once at 300
Learner 21:30 UTC: ran · shares updated
Month: +£112.40 · +9.8R over 18 trading days · open now 4 trades (0.04 lot)
👉 For you: nothing"""


# ---------------------------------------------------------------------------------------------- fixture state
class Lab:
    """Writes one state directory: engine files, aux.db exploration rows and weights runs."""

    def __init__(self, sd: Path, now: pd.Timestamp = NOW):
        self.sd, self.now, self.n = sd, now, 0
        sd.mkdir(parents=True, exist_ok=True)
        self.db = Database.open(sd, "aux")
        (sd / "supervisor.json").write_text(json.dumps({"ts": now.timestamp() - 2, "halt": False, "reasons": []}))

    def engine(self, account: str, equity: float, hwm: float, open_n: int = 0, lots: float = 0.0, **over: Any) -> Lab:
        e = engine(account=account, ts=self.now.timestamp(), probe_only=True, currency="GBP", equity=equity,
                   balance_closed_hwm=hwm, day_start_equity=equity, week_start_equity=equity, open_positions=open_n,
                   open_lots=lots, lot_terms={"contract_oz": 100.0})
        e.update(over)
        (self.sd / f"engine_{account}.json").write_text(json.dumps(e))
        return self

    def obs(self, account: str, arm: str, at: pd.Timestamp, count: int, *, taken: bool = False,
            why: str | None = None, style: str = "scalp", control: bool = False, **cols: Any) -> Lab:
        with self.db.write() as conn:
            for i in range(count):
                self.n += 1
                row = {"candidate_id": f"c{self.n}", "account_id": account, "arm_id": arm, "family": "f",
                       "style": style, "tf": "5m", "side": 1, "is_control": int(control),
                       "decision_ns": (at + pd.Timedelta(seconds=i)).value, "session_day": f"{at:%Y-%m-%d}",
                       "taken": int(taken), "blocked_reason": why, "branch": "random" if why is None else "blocked",
                       "recorded_ns": at.value, **cols}
                conn.execute(f"INSERT INTO exploration_obs ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                             tuple(row.values()))
        return self

    def trade(self, account: str, arm: str, sent: pd.Timestamp, *, pnl: float | None = 0.0, stop_loss: float = 4.5,
              closed: bool = True, filled: bool = True, exit_at: pd.Timestamp | None = None, **kw: Any) -> Lab:
        self.n += 1
        fields: dict[str, Any] = dict(
            client_order_id=f"ex{self.n:05d}", account_id=account, arm_id=arm, family="f", tf="5m", side=1, lots=0.01,
            signal_utc=sent, sent_utc=sent, atr_usd=4.5, stop_usd=4.5, target_usd=4.5, hold_s=600,
            requested_price=2000.0, spread_usd=0.0, fill_price=2000.0 if filled else None, stop_loss=stop_loss,
            fx_acct_per_usd=0.8, cost=0.5)
        if closed and filled:
            fields.update(exit_utc=exit_at or sent + pd.Timedelta(minutes=5), exit_price=2000.0, exit_reason="stop",
                          pnl=pnl, censored=False)
        fields.update(kw)
        with self.db.write() as conn:
            LEDGER.upsert(conn, ExplorationRecord(**fields))
        return self

    def trades(self, account: str, arm: str, at: pd.Timestamp, count: int, **kw: Any) -> Lab:
        for i in range(count):
            self.trade(account, arm, at + pd.Timedelta(minutes=i), **kw)
        return self

    def weights(self, at: pd.Timestamp, shares: dict[str, float]) -> Lab:
        write_weights(self.db, at.value, shares, dict.fromkeys(shares, "scalp"))
        return self


def health(n: int = 41, *bad: tuple[str, str]) -> HealthReport:
    cs = [Check(name=f"check{i}", status="ok", reason="fine") for i in range(n - len(bad))]
    cs += [Check(name=name, status=status, reason="x") for name, status in bad]  # type: ignore[arg-type]
    worst = "fail" if any(s == "fail" for _, s in bad) else "warn" if bad else "ok"
    return HealthReport(ts=NOW, status=worst, checks=cs)  # type: ignore[arg-type]


def mock_state(sd: Path) -> Lab:
    """The spec's section 2.1 day: two labs, 19 arms with history, a learner run that moved shares."""
    lab = Lab(sd).engine("icm-probe", 10_720, 10_950, 3, 0.03).engine("icm-demo", 10_912, 11_000, 1, 0.01)
    # Scalp lab: 214 signals, 63 taken, 72 refused (41 slots, 22 spread, 9 news), 79 policy skips (never shown)
    lab.obs("icm-probe", "S1A", DAY, 63, taken=True).obs("icm-probe", "S1A", DAY, 41, why="global_cap")
    lab.obs("icm-probe", "S1A", DAY, 22, why="spread_too_wide").obs("icm-probe", "S1A", DAY, 9,
                                                                    why="news_blackout,spread_too_wide")
    lab.obs("icm-probe", "S1A", DAY, 79)
    lab.trade("icm-probe", "S1A", DAY, spread_usd=8.5)                                 # £6.80 spread
    lab.trade("icm-probe", "S1A", DAY, fill_price=2001.75)                             # £1.40 adverse slippage
    lab.trade("icm-probe", "S1A", DAY, commission=-2.70)
    lab.trade("icm-probe", "S1A", DAY, pnl=-17.60)
    lab.trades("icm-probe", "S1A", DAY, 54)
    lab.trades("icm-probe", "S1A", DAY, 3, closed=False).trades("icm-probe", "S1A", DAY, 2, filled=False)
    # Day/swing lab: 37 signals, 9 taken, 22 refused, 6 policy skips; 9 filled, 7 closed, 2 still open
    lab.obs("icm-demo", "MB30", DAY, 9, taken=True, style="intraday").obs("icm-demo", "MB30", DAY, 14, why="pot_slots",
                                                                          style="intraday")
    lab.obs("icm-demo", "MB30", DAY, 8, why="news_blackout", style="intraday").obs("icm-demo", "MB30", DAY, 6,
                                                                                   style="intraday")
    lab.trade("icm-demo", "MB30", DAY, pnl=21.60, stop_loss=15.0, commission=-2.10)
    lab.trades("icm-demo", "MB30", DAY, 6, stop_loss=15.0).trades("icm-demo", "MB30", DAY, 2, closed=False)
    # history: September (outside the month) at 0R, and one October close of +£108.40 for the month line
    lab.trades("icm-probe", "S1A", SEPT, 38).trades("icm-probe", "S1B", SEPT, 88)
    lab.trades("icm-probe", "S2A", SEPT, 117).trades("icm-probe", "S3B", SEPT, 126)
    lab.trades("icm-demo", "MB30", SEPT, 34, stop_loss=15.0)
    for i in range(1, 15):
        lab.trades("icm-probe", f"X{i:02d}", SEPT, 10)
    lab.trade("icm-probe", "S2A", pd.Timestamp("2026-10-05 10:00", tz="UTC"), pnl=108.40, stop_loss=8.8)
    lab.weights(pd.Timestamp("2026-10-25 21:30", tz="UTC"), {"S1A": 0.2, "S1B": 0.2})
    lab.weights(pd.Timestamp("2026-10-26 21:30", tz="UTC"), {"S1A": 0.25, "S1B": 0.15})
    return lab


def daily(sd: Path, now: pd.Timestamp = NOW, report: HealthReport | None = None, **kw: Any) -> str:
    kw.setdefault("settings", load_settings())
    kw.setdefault("roadmap", sd / "no-roadmap.md")
    return build_digest(sd, now, report=health() if report is None else report, **kw)


def lines(text: str) -> list[str]:
    return [x for x in text.splitlines() if x.strip()]


# ---------------------------------------------------------------------------------------------- acceptance 1: the mock
def test_the_daily_report_reproduces_the_spec_mock_from_fixture_state(tmp_path: Path) -> None:
    """Byte for byte but for one word the data cannot support: the exit's requested price is not recorded, so the
    slippage is the entry's and says so ("slippage (entry)", spec section 2.5 needs-field 1)."""
    mock_state(tmp_path)
    assert daily(tmp_path) == MOCK_DAILY


def test_the_report_counts_exploration_trades_the_old_digest_called_none(tmp_path: Path) -> None:
    mock_state(tmp_path)
    text = daily(tmp_path)
    assert "Trades closed: none" not in text and "58 closed" in text


# ---------------------------------------------------------------------------------------------- acceptance 6: blinding
BANNED = re.compile(r"\b(ahead|behind|baseline|best|worst|p-value|significant|win rate|rank|t =)\b", re.I)


def test_no_per_arm_r_share_rank_or_banned_word_appears_before_an_arms_read(tmp_path: Path) -> None:
    """Arms seeded with distinctive R (S1B +7.77R, S2A −4.44R) and shares (0.123456): none of those figures, nor any
    banned word, reaches the message; per arm only its n and band."""
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    lab.trades("icm-probe", "S1B", DAY, 7, pnl=4.995, stop_loss=4.5)        # 7 x 1.11R = +7.77R on S1B alone
    lab.trades("icm-probe", "S2A", DAY, 4, pnl=-4.995, stop_loss=4.5)       # 4 x −1.11R = −4.44R on S2A alone
    lab.obs("icm-probe", "S1B", DAY, 7, taken=True, arm_share=0.123456)
    lab.weights(DAY - pd.Timedelta(days=1), {"S1B": 0.2, "S2A": 0.2})
    lab.weights(DAY + pd.Timedelta(hours=11), {"S1B": 0.123456, "S2A": 0.276544})
    text = daily(tmp_path)
    for figure in ("7.77", "7.8R", "4.44", "4.4R", "1.11", "0.12", "12.3", "0.28", "27.7", "2.77", "+£34", "−£19"):
        assert figure not in text, figure
    arm_lines = [x for x in text.splitlines() if "Arms" in x or "within 50%" in x or "Learner" in x]
    assert arm_lines and not any(BANNED.search(x) or "share " in x for x in arm_lines)
    assert "Arms (trades): S1B 7 · S2A 4" in text and "Learner 08:00 UTC" not in text
    # the lab total (both arms, £ and R) is allowed: it is not a per-arm figure
    assert "11 closed +£14.98 · +3.3R" in text


def test_an_arm_at_its_stop_prints_its_count_only_and_an_arm_near_it_prints_the_band_only(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    lab.trades("icm-probe", "S3B", SEPT, 31, pnl=-4.5, stop_loss=4.5)       # −30R at its 30th trade: H-61 scalp stop
    lab.trades("icm-probe", "S2A", SEPT, 16, pnl=-4.5, stop_loss=4.5)       # −16R: past half of −30R
    lab.trades("icm-probe", "S1A", SEPT, 3, pnl=-4.5, stop_loss=4.5)
    text = daily(tmp_path)
    assert "S3B reached its pre-set stop after 30 trades: retirement due" in text
    assert "1 within 50% of a stop (S2A)" in text
    assert "30R" not in text and "16R" not in text and "−3R" not in text


def test_an_arm_at_300_says_read_due_and_a_control_never_does(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    lab.trades("icm-probe", "S2A", SEPT, 300).trades("icm-probe", "S0", SEPT, 300)
    lab.obs("icm-probe", "S0", SEPT, 1, control=True)
    text = daily(tmp_path)
    assert "S2A reached 300 trades: read due" in text and "S0 reached" not in text


def test_with_no_arm_trade_yet_the_block_is_one_line(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    assert "Arms: none running yet" in daily(tmp_path)


# ---------------------------------------------------------------------------------------------- learner (D4)
def test_the_learner_line_says_only_whether_it_ran(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    lab.weights(pd.Timestamp("2026-10-25 21:30", tz="UTC"), {"S1A": 0.2})
    lab.weights(pd.Timestamp("2026-10-26 21:30", tz="UTC"), {"S1A": 0.2})
    assert "Learner 21:30 UTC: ran · no share changed" in daily(tmp_path)


def test_a_learner_that_did_not_run_says_which_shares_are_in_use(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    assert "⚠️ Learner did not run last night: equal shares in use" in daily(tmp_path)
    lab.weights(pd.Timestamp("2026-10-25 21:30", tz="UTC"), {"S1A": 0.2})
    assert "⚠️ Learner did not run last night: the last run's shares in use" in daily(tmp_path)


# ---------------------------------------------------------------------------------------------- states (section 2.4)
def test_a_lab_paused_at_its_pause_line_replaces_its_result_with_the_pause_copy(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_041, 10_950)                   # down 8.3%, £909
    lab.obs("icm-probe", "S1A", pd.Timestamp("2026-10-26 14:20", tz="UTC"), 3, why="paused_dd")
    text = daily(tmp_path)
    assert "⏸ Scalp lab paused since Mon 26 Oct 14:20 UTC: down 8.3% (£909) from peak" in text
    assert "  arms stopped · cost probe and exits still running · resumes: when the drawdown is back under the " \
           "clear line" in text
    assert "Scalp lab: " not in text


def test_a_lab_at_the_warn_line_moves_its_drawdown_up_as_a_warning(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_249, 10_950)                   # down 6.4%, £701
    lab.obs("icm-probe", "S1A", DAY, 2, taken=True).trades("icm-probe", "S1A", DAY, 2)
    got = lines(daily(tmp_path))
    i = next(i for i, x in enumerate(got) if x.startswith("Scalp lab: "))
    assert got[i + 1] == "  ⚠️ Warning: down 6.4% (£701) from peak · pauses at 8% (£876)"


def test_a_lab_at_the_halt_rail_is_red_on_line_two_and_on_its_own_line(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 9_625, 10_950, stage="halted")         # down 12.1%, £1,325
    got = lines(daily(tmp_path))
    assert got[1].startswith("🛑 Halted: Scalp lab at 12% drawdown, no new trades · exits still run")
    assert "🛑 Scalp lab halted: down 12.1% (£1,325) · re-arm on the dashboard (Risk)" in got


def test_an_owner_halt_is_on_line_two_without_who_set_it(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    (tmp_path / "control.json").write_text(json.dumps({"halted": True, "by": "dashboard:owner@example.com",
                                                       "ts": pd.Timestamp("2026-10-26 09:12", tz="UTC").timestamp()}))
    got = lines(daily(tmp_path))
    assert got[1].startswith("🛑 Halts: owner halt since Mon 26 Oct 09:12 UTC") and "example.com" not in got[1]


def test_no_signals_on_an_open_weekday_is_a_warning(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    assert "Scalp lab: no signals while the market was open ⚠️ Warning: check the feed" in daily(tmp_path)


def test_signals_with_nothing_taken_show_the_count_and_the_refusals(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-demo", 10_000, 10_000).obs("icm-demo", "MB30", DAY, 12, why="news_blackout")
    text = daily(tmp_path)
    assert "Day/swing lab: 12 signals · 0 taken" in text and "refused: 12 news window" in text


def test_taken_but_nothing_closed_prints_no_money_figure(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    lab.obs("icm-probe", "S1A", DAY, 3, taken=True).trades("icm-probe", "S1A", DAY, 3, closed=False)
    text = daily(tmp_path)
    assert "Scalp lab: 3 signals · 3 taken · 3 filled · 0 closed (3 still open)" in text and "£0.00" not in text


def test_a_close_without_a_net_figure_is_shown_apart_as_gross(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    lab.obs("icm-probe", "S1A", DAY, 2, taken=True)
    lab.trade("icm-probe", "S1A", DAY, pnl=3.0).trade("icm-probe", "S1A", DAY, pnl=None, exit_price=2001.375)
    text = daily(tmp_path)
    assert "2 closed +£3.00 · +0.7R · 1 closed without costs recorded (gross +£1.10)" in text


def test_the_weekend_day_is_one_market_closed_line(tmp_path: Path) -> None:
    Lab(tmp_path, pd.Timestamp("2026-10-26 06:45", tz="UTC")).engine("icm-probe", 10_000, 10_000)
    text = daily(tmp_path, pd.Timestamp("2026-10-26 06:45", tz="UTC"))
    assert "Market closed Sat–Sun: no trading. Sunday-evening trades show in Tuesday's report." in text
    assert "Scalp lab" not in text


def test_a_disabled_account_is_left_out_of_the_daily(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 10_000, 10_000).engine("vantage-demo", 10_000, 10_000)
    assert "Vantage lab" not in daily(tmp_path)


def test_health_unknown_and_failing_are_worded_on_line_two(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    assert lines(build_digest(tmp_path, NOW, settings=load_settings(), roadmap=tmp_path / "x"))[1].startswith(
        "❔ Health: unknown (the check did not run) · Halts: none")
    got = lines(daily(tmp_path, report=health(41, ("bridge_heartbeat:icm-probe", "fail"), ("spread_profile", "warn"))))
    assert got[1].startswith("⚠️ Attention: fail bridge_heartbeat · warn spread_profile · Halts: none")


# ---------------------------------------------------------------------------------------------- acceptance 2: budget
def _worst(sd: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Three labs, an engine halt, a pause, a warn, a failing check, an arm at its stop, a live account's line."""
    _enable(monkeypatch, "icm-probe", "icm-demo", "vantage-demo")
    lab = Lab(sd).engine("icm-probe", 10_041, 10_950).engine("icm-demo", 9_625, 10_950, stage="halted")
    lab.engine("vantage-demo", 10_249, 10_950)
    for acct in ("icm-probe", "icm-demo", "vantage-demo"):
        lab.obs(acct, "S1A", DAY, 5, taken=True).obs(acct, "S1A", DAY, 4, why="global_cap")
        lab.obs(acct, "S1A", DAY, 3, why="spread_too_wide").obs(acct, "S1A", DAY, 2, why="stale")
        lab.trades(acct, "S1A", DAY, 4, pnl=-1.0, commission=-0.5, spread_usd=0.3)
    lab.trades("icm-probe", "S3B", SEPT, 31, pnl=-4.5, stop_loss=4.5)
    for i in range(1, 15):
        lab.trades("icm-probe", f"X{i:02d}", SEPT, 5)
    with closed_trade_file(sd, "icm-demo").open("w") as fh:
        fh.write(ClosedTrade(account_id="icm-demo", broker="icm", mode="demo", exit_utc=DAY, ret=0.001, pnl=5.0,
                             equity_before=10_000, position_id=1, r=0.5, pnl_basis="net",
                             pnl_currency="GBP").model_dump_json() + "\n")


def _enable(monkeypatch: pytest.MonkeyPatch, *accounts: str) -> None:
    from goldbot.ops import accounts as acc
    ids = acc.AccountIds(listed=frozenset({*acc.load_accounts(logins=False)}), enabled=frozenset(accounts))
    monkeypatch.setattr(acc, "account_ids", lambda: ids)


def test_the_worst_case_fits_fifteen_lines_and_keeps_every_line_that_matters(tmp_path: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _worst(tmp_path, monkeypatch)
    text = daily(tmp_path, report=health(41, ("bridge_heartbeat", "fail")))
    got = lines(text)
    assert len(got) <= 15, text
    assert got[1].startswith("🛑 Halted: Day/swing lab at 12% drawdown") and "Attention: fail bridge_heartbeat" in got[1]
    assert any(x.startswith("⏸ Scalp lab paused") for x in got)
    assert any(x.startswith("🛑 Day/swing lab halted") for x in got)
    assert "  ⚠️ Warning: down 6.4% (£701) from peak · pauses at 8% (£876)" in got
    assert any("S3B reached its pre-set stop" in x for x in got)
    assert got[-1].startswith("👉 For you:")
    assert not any(x.startswith("October so far") for x in got)          # a priority-2 line went to make room


def test_the_cut_drops_only_what_the_budget_needs_and_says_more_on_the_dashboard() -> None:
    from goldbot.telegram.digest import Line, fit
    rows = [Line("head"), Line("Lab: result", 1, "a"), Line("  costs", 2, "a", True), Line("  refused", 3, "a", True),
            Line("  down", 3, "a", True), Line("Arms", 2), Line("Month", 2), Line("👉 For you")]
    assert fit(rows, 8) == [x.text for x in rows]
    assert fit(rows, 7) == ["head", "Lab: result · more on the dashboard", "  costs", "  refused", "Arms", "Month",
                            "👉 For you"]
    assert fit(rows, 5) == ["head", "Lab: result · more on the dashboard", "  costs", "Arms", "👉 For you"]
    assert fit(rows, 2) == ["head", "Lab: result · more on the dashboard", "👉 For you"]   # priority 1 is never cut


@pytest.mark.parametrize("state", ["mock", "paused", "halted", "warn", "none", "weekend", "owner_halt", "broken"])
def test_every_state_is_within_fifteen_lines_with_health_halts_and_for_you(tmp_path: Path, state: str) -> None:
    now = pd.Timestamp("2026-10-26 06:45", tz="UTC") if state == "weekend" else NOW
    if state == "mock":
        mock_state(tmp_path)
    else:
        lab = Lab(tmp_path, now).engine("icm-probe", {"paused": 10_041, "halted": 9_625, "warn": 10_249}.get(state,
                                                                                                          10_950),
                                        10_950, stage="halted" if state == "halted" else "normal")
        lab.engine("icm-demo", 10_000, 10_000)
    if state == "owner_halt":
        (tmp_path / "control.json").write_text(json.dumps({"halted": True, "ts": NOW.timestamp() - 3600}))
    if state == "broken":
        (tmp_path / "aux.db").write_bytes(b"not a database" * 100)
    got = lines(daily(tmp_path, now))
    assert len(got) <= 15
    assert "Health" in got[1] or "Attention" in got[1]
    assert "Halt" in got[1] and got[-1].startswith("👉 For you:")


# ---------------------------------------------------------------------------------------------- acceptance 3: units
_UNIT_AFTER = re.compile(
    r"^(R\b|%|\s?(lot|lots|trade|trades|signal|signals|taken|filled|closed|still open|slots full|spread wide|"
    r"news window|budget used|signal too old|paused|would hold|same-side|auto entries|stop over|order still|"
    r"other reason|of \d|ok\b|health check|approval|owner decision|more|trading day|usable label|nights|"
    r"h of open|deploy|arms changed|trial))")


def _unitless(text: str) -> list[str]:
    out = []
    body = re.sub(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d{1,2}(?![\d:])( [A-Z][a-z]{2})?\b", "DATE", text)
    body = re.sub(r"\b\d{2}:\d{2} (UTC|UK)\b", "TIME", body)
    body = re.sub(r"read once at \d+$", "read once at READ", body, flags=re.M)   # "(trades)" heads the block
    body = re.sub(r"\(trial \d+ of \d+\)", "(TRIAL)", body)
    body = re.sub(r"Entries open on \d+ of \d+", "Entries open on N", body)   # the spec's copy: of the accounts
    body = re.sub(r"\b\d{2}:\d{2}\b(?=[^\n]*UTC)", "TIME", body)            # a time on a line that names UTC
    body = re.sub(r"\b(S|I|W|MB|MSA|X|P)\d{1,2}[A-Z]?\b(?= \d)", "ARM", body)
    body = re.sub(r"\b(S|I|W|MB|MSA|X|P)\d{1,2}[A-Z]?\b", "ARM", body)
    for line in body.splitlines():
        arms = line.startswith("Arms (trades)")
        for m in re.finditer(r"(?<![£$€\w.,])[+−-]?\d[\d,]*(\.\d+)?", line):
            before = line[max(0, m.start() - 1):m.start()]
            after = line[m.end():]
            if before in "£$€" and before:
                continue
            if arms and line[max(0, m.start() - 4):m.start()].endswith("ARM "):
                continue                                                    # the arms block: "(trades)" is the unit
            if line[max(0, m.start() - 2):m.start()] in ("(£", "($"):
                continue
            if not _UNIT_AFTER.match(after) and not re.match(r"^(\.\d+)?R", after):
                out.append(f"{line!r}: {m.group(0)}{after[:12]!r}")
    return out


@pytest.mark.parametrize("state", ["mock", "paused", "halted", "warn", "none", "weekend"])
def test_every_number_carries_a_unit_and_every_time_its_zone(tmp_path: Path, state: str) -> None:
    now = pd.Timestamp("2026-10-26 06:45", tz="UTC") if state == "weekend" else NOW
    if state == "mock":
        mock_state(tmp_path)
    else:
        Lab(tmp_path, now).engine("icm-probe", {"paused": 10_041, "halted": 9_625, "warn": 10_249}.get(state, 10_950),
                                  10_950, stage="halted" if state == "halted" else "normal")
        Lab(tmp_path, now).obs("icm-probe", "S1A", DAY, 3, why="paused_dd" if state == "paused" else None)
    text = daily(tmp_path, now)
    assert _unitless(text) == []
    for t in re.findall(r"\b\d{2}:\d{2}\b[^\n]{0,8}", text):
        assert "UTC" in t or "UK" in t, t


# ---------------------------------------------------------------------------------------------- acceptance 4: no ids
LOGIN, SERVER, HOST, TG_ID, EMAIL = "51234567", "ICMarketsSC-Demo", "10.1.2.3", "987654321", "owner@example.com"


def test_no_identifier_reaches_the_report_and_an_unmapped_account_is_other(tmp_path: Path) -> None:
    lab = Lab(tmp_path)
    for acct in ("icm-probe", "icm-demo", f"icm-demo-{LOGIN}"):
        lab.engine(acct, 10_000, 10_000, login=int(LOGIN), server=SERVER, host=HOST, telegram_id=int(TG_ID),
                   email=EMAIL)
        lab.obs(acct, "S1A", DAY, 2, taken=True, snapshot_ref=f"{HOST}/{EMAIL}")
        lab.trade(acct, "S1A", DAY, position_id=int(TG_ID))
    (tmp_path / "control.json").write_text(json.dumps({"halted": True, "by": f"dashboard:{EMAIL}", "ts": 1.7e9}))
    report = health(3, (f"engine:icm-demo-{LOGIN}", "fail"), (f"bridge:{SERVER}", "warn"))
    text = daily(tmp_path, report=report)
    for secret in (LOGIN, "ICMarketsSC", HOST, TG_ID, EMAIL, "icm-probe", "icm-demo", "vantage-demo"):
        assert secret not in text, secret
    assert "Other account: 2 signals" in text


# ---------------------------------------------------------------------------------------------- acceptance 5: no buttons
def test_the_bot_sends_the_report_with_no_reply_markup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from goldbot.config import TelegramSettings
    from goldbot.telegram import bot as botmod
    from goldbot.telegram.digest import DigestSchedule
    calls: list[dict[str, Any]] = []

    class _Tg:
        async def send_message(self, chat: int, text: str, **kw: Any) -> None:
            calls.append({"chat": chat, "text": text, **kw})

    b = botmod.TelegramBot.__new__(botmod.TelegramBot)
    b.state_dir, b.owner_ids, b.settings = str(tmp_path), {7}, TelegramSettings()
    b.digest = DigestSchedule(tmp_path)
    b.app = types.SimpleNamespace(bot=_Tg())
    monkeypatch.setattr(botmod, "digest_from_runtime", lambda sd, now, slot: "the report")
    asyncio.run(b._digest_pass())
    assert calls == [{"chat": 7, "text": "the report"}]


# ---------------------------------------------------------------------------------------------- acceptance 7: no sums
def test_exploration_and_other_pnl_are_never_summed_and_currencies_never_mixed(tmp_path: Path) -> None:
    lab = Lab(tmp_path).engine("icm-probe", 10_000, 10_000).engine("icm-demo", 10_000, 10_000, currency="USD")
    lab.obs("icm-probe", "S1A", DAY, 1, taken=True).trade("icm-probe", "S1A", DAY, pnl=10.0)
    lab.obs("icm-demo", "MB30", DAY, 1, taken=True).trade("icm-demo", "MB30", DAY, pnl=-4.0, stop_loss=15.0)
    with closed_trade_file(tmp_path, "icm-probe").open("w") as fh:
        fh.write(ClosedTrade(account_id="icm-probe", broker="icm", mode="demo", exit_utc=DAY, ret=0.001, pnl=50.0,
                             equity_before=10_000, position_id=1, r=0.5, pnl_basis="net",
                             pnl_currency="GBP").model_dump_json() + "\n")
    text = daily(tmp_path)
    month = next(x for x in text.splitlines() if x.startswith("Month: "))
    assert "+£10.00" in month and "−$4.00" in month and "£6.00" not in month and "+£60.00" not in text
    assert "+£50.00" in text                                    # the engine's own trade, on its own line


# ---------------------------------------------------------------------------------------------- acceptance 8: config
def test_the_drawdown_lines_follow_the_configured_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    Lab(tmp_path).engine("icm-probe", 10_720, 10_950, stage="halted")
    monkeypatch.setattr(ex, "DD_WARN", 0.05)
    monkeypatch.setattr(ex, "DD_PAUSE", 0.07)
    s = load_settings()
    s = s.model_copy(update={"risk": s.risk.model_copy(update={"drawdown_stage2": 0.15})})
    text = daily(tmp_path, settings=s)
    assert "at 15% drawdown" in text
    Lab(tmp_path).engine("icm-probe", 10_720, 10_950)
    assert "warns at 5% (£548) · pauses at 7% (£767)" in daily(tmp_path, settings=s)


# ---------------------------------------------------------------------------------------------- acceptance 9: robust
def test_one_broken_source_costs_one_line_and_the_report_still_builds(tmp_path: Path) -> None:
    mock_state(tmp_path)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        conn.execute("DROP TABLE exploration_weights")
    text = daily(tmp_path)
    assert "Learner: could not read (OperationalError)" in text and "Scalp lab: 214 signals" in text
    (tmp_path / "aux.db").write_bytes(b"garbage" * 1000)
    text = daily(tmp_path)
    assert "Labs: could not read (DatabaseError)" in text and text.splitlines()[-1].startswith("👉 For you")


# ---------------------------------------------------------------------------------------------- acceptance 10: when
def test_sunday_sends_the_weekly_and_saturday_sends_fridays_daily(tmp_path: Path) -> None:
    Lab(tmp_path).engine("icm-probe", 10_000, 10_000)
    kw: dict[str, Any] = dict(report=health(), settings=load_settings(), roadmap=tmp_path / "x")
    sunday = pd.Timestamp("2026-10-25 06:45", tz="UTC")
    assert report_text(tmp_path, sunday, sunday, **kw).startswith("📅 Week Mon 19 – Fri 23 Oct")
    saturday = pd.Timestamp("2026-10-24 06:45", tz="UTC")
    assert report_text(tmp_path, saturday, saturday, **kw).startswith("☀️ Fri 23 Oct results")
    late = pd.Timestamp("2026-10-27 12:44", tz="UTC")                          # 6 h late, the slot's day kept
    assert report_text(tmp_path, late, NOW, **kw).startswith("☀️ Mon 26 Oct results")
