"""The daily learning-ops review (goldbot/research/daily_review.py; research-director spec 2026-10-26, loop lane L2 made
daily): per broker, host and session the costs, fill rate, refusal reasons, volume against the ~290 a day plan, arm
health against the pre-registered H-61 stops (only `breached` / `ok`) and integrity; the automatic actions (retire an
arm at its stop, refit a cost cell after 200 fills, never cheaper within a day, a `learning-ops` issue on an incident
or two low-volume days, one status comment on #34); and NEVER a per-arm R, rank or sign in the record, the issue text
or the owner's report section."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.config import load_settings
from goldbot.db.connection import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.research import daily_review as dr
from goldbot.research import exploration_data as ex
from goldbot.telegram import labs

S = load_settings()
DAY = "2026-10-22"                                    # a Thursday opportunity day: 2026-10-21 22:00 .. 10-22 22:00 UTC
LO, HI = pd.Timestamp("2026-10-21 22:00", tz="UTC"), pd.Timestamp("2026-10-22 22:00", tz="UTC")
NOW = pd.Timestamp("2026-10-23 06:15", tz="UTC")
LONDON = pd.Timestamp("2026-10-22 09:00", tz="UTC")
NEWYORK = pd.Timestamp("2026-10-22 15:00", tz="UTC")
HOSTS = {"icm-probe": "icm", "icm-demo": "icm", "vantage-demo": "vantage"}
STYLE = {"S1A": "scalp", "S0": "scalp", "I2A": "intraday", "W1A": "swing", "VI2A": "intraday"}
_BANNED_TEXT = re.compile(r"[+\-−]?\d+(\.\d+)?\s?R\b")


class Sink:
    """A recording GitHub sink: what would have been posted."""

    def __init__(self, fail: bool = False) -> None:
        self.comments: list[tuple[int, str]] = []
        self.issues: list[tuple[str, str, str]] = []
        self.fail = fail

    def comment(self, number: int, body: str) -> str:
        if self.fail:
            raise OSError("network down")
        self.comments.append((number, body))
        return f"https://github.invalid/issues/{number}"

    def open_or_comment(self, label: str, title: str, body: str) -> str:
        if self.fail:
            raise OSError("network down")
        self.issues.append((label, title, body))
        return "https://github.invalid/issues/900"


def _db(sd: Path) -> Database:
    return Database.open(sd, "aux")


def _cand(db: Database, cid: str, *, host: str = "icm-probe", arm: str = "S1A", at: pd.Timestamp = LONDON,
          taken: bool = True, blocked: str | None = None, control: bool = False) -> None:
    ex.record_candidate(db, candidate_id=cid, account_id=host, broker=HOSTS[host], arm_id=arm, family="f",
                        style=STYLE.get(arm, "scalp"), tf="1m", side=1, decision_utc=at, stop_dist=2.0,
                        target_dist=2.0, max_hold_s=600, taken=taken, is_control=control,
                        blocked_reason=blocked, branch="random" if taken else "blocked", u=0.5,
                        mu=1.0 if taken else 0.0)


def _trade(db: Database, cid: str, *, host: str = "icm-probe", arm: str = "S1A", sent: pd.Timestamp = LONDON,
           exit_at: pd.Timestamp | None = None, pnl: float | None = 1.0, stop_loss: float = 1.0, side: int = 1,
           requested: float = 2000.0, fill: float | None = 2000.1, exit_price: float = 2002.0,
           reason: str = "target", spread: float = 0.2, commission: float = 0.0525, swap: float = 0.0,
           fx: float = 0.75, censored: bool = False, stop_usd: float = 2.0) -> None:
    rec = ExplorationRecord(client_order_id=cid, account_id=host, arm_id=arm, family="f", tf="1m", side=side,
                            lots=0.01, signal_utc=sent, sent_utc=sent, atr_usd=stop_usd, stop_usd=stop_usd,
                            target_usd=2.0, hold_s=600, requested_price=requested, spread_usd=spread,
                            fill_price=fill, stop_loss=stop_loss,
                            exit_utc=exit_at if exit_at is not None else sent + pd.Timedelta(minutes=5),
                            exit_price=exit_price, exit_reason=reason, swap=swap, commission=commission, pnl=pnl,
                            censored=censored, fx_acct_per_usd=fx)
    with db.write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _store(frames: dict[tuple[str, str], pd.DataFrame] | None = None) -> Any:
    frames = frames or {}
    return SimpleNamespace(read=lambda table, source=None, **_: frames.get((table, str(source)), pd.DataFrame()))


def _run(sd: Path, *, day: str = DAY, now: pd.Timestamp = NOW, store: Any = None, sink: Any = None,
         dry_run: bool = False) -> dr.DailyReview:
    return dr.run_daily_review(settings=S, state_dir=sd, store=store if store is not None else _store(), hosts=HOSTS,
                               day=day, now=now, dry_run=dry_run, sink=sink)


def _losers(db: Database, arm: str, n: int, *, host: str = "icm-probe", start: pd.Timestamp = LONDON,
            pnl: float = -1.0) -> None:
    for i in range(n):
        t = start + pd.Timedelta(minutes=i)
        _cand(db, f"{arm}-{i}", host=host, arm=arm, at=t)
        _trade(db, f"{arm}-{i}", host=host, arm=arm, sent=t, exit_at=t + pd.Timedelta(seconds=30), pnl=pnl)


def _blinded(text: str) -> None:
    assert not _BANNED_TEXT.search(text), text
    assert not labs.BANNED_WORDS.search(text), text


# ---------------------------------------------------------------------------------------------- (1) the figures
def test_costs_are_per_broker_host_and_session_in_dollars_per_oz_with_the_overshoot_as_a_multiple_of_070(tmp_path):
    db = _db(tmp_path)
    for i in range(4):     # London: entry 0.10 adverse, spread 0.20, commission $0.0525 at 0.75 GBP/USD = $0.07/oz
        _cand(db, f"l{i}", at=LONDON + pd.Timedelta(minutes=i))
        _trade(db, f"l{i}", sent=LONDON + pd.Timedelta(minutes=i))
    # New York, a stop exit: the server stop at requested 2000.0 - 2.0 = 1998.0 (cost_probe._place), filled at
    # 1997.3 -> $0.70 overshoot = 1.0 x the scenario
    _cand(db, "n0", at=NEWYORK)
    _trade(db, "n0", sent=NEWYORK, reason="stop", exit_price=1997.3, pnl=-1.2)
    exits = pd.DataFrame({"ts_utc": [LONDON + pd.Timedelta(minutes=5)], "agent_id": ["explore:S1A"], "side": [1],
                          "requested": [2002.0], "filled": [2001.95]})
    review = _run(tmp_path, store=_store({("exit_fills", "icm-probe"): exits}))
    london = review.cost("icm", "icm-probe", "london")
    assert london.fills == 4 and london.spread_usd == pytest.approx(0.2)
    assert london.entry_slip_usd == pytest.approx(0.1) and london.exit_slip_usd == pytest.approx(0.05)
    assert london.commission_usd == pytest.approx(0.07) and london.swap_usd == pytest.approx(0.0)
    assert london.round_trip_usd == pytest.approx(0.2 + 0.1 + 0.05 + 0.07)
    ny = review.cost("icm", "icm-probe", "newyork")
    assert ny.stop_exits == 1 and ny.overshoot_usd == pytest.approx(0.70)
    assert ny.overshoot_x == pytest.approx(1.0)
    assert review.cost("icm", "all", "all").fills == 5                    # the broker's line for the owner's report
    assert review.cost("vantage", "all", "all").fills == 0                 # never pooled across brokers


def test_fill_rate_is_filled_over_taken_candidates_per_host_and_session(tmp_path):
    db = _db(tmp_path)
    for i in range(4):
        _cand(db, f"c{i}", at=LONDON + pd.Timedelta(minutes=i))
    for i in range(3):
        _trade(db, f"c{i}", sent=LONDON + pd.Timedelta(minutes=i))
    _trade(db, "c3", sent=LONDON + pd.Timedelta(minutes=3), fill=None)     # refused by the broker: not a fill
    _cand(db, "b0", at=LONDON, taken=False, blocked="global_cap")         # blocked: not taken, not in the rate
    cell = _run(tmp_path).cost("icm", "icm-probe", "london")
    assert (cell.taken, cell.filled) == (4, 3) and cell.fill_rate == pytest.approx(0.75)


def test_refusal_skip_and_stale_reasons_are_counted_from_the_journal_and_the_candidates(tmp_path):
    db = _db(tmp_path)
    for i, why in enumerate(["global_cap", "global_cap", "stale", "spread_too_wide,news_blackout"]):
        _cand(db, f"b{i}", at=LONDON + pd.Timedelta(minutes=i), taken=False, blocked=why)
    journal = pd.DataFrame({"ts_utc": [LONDON, LONDON, NEWYORK, NEWYORK, NEWYORK],
                            "action": ["exploration_stale", "gate:spread,news", "exploration:executed:auto",
                                       "skipped: cap", "exploration_refused:ledger_unreadable"]})
    review = _run(tmp_path, store=_store({("decisions", "icm-probe"): journal}))
    assert review.refusals["icm-probe"] == {"obs:global_cap": 2, "obs:stale": 1, "obs:spread_too_wide": 1,
                                            "journal:exploration_stale": 1, "journal:gate:spread": 1,
                                            "journal:skipped: cap": 1,
                                            "journal:exploration_refused:ledger_unreadable": 1}


def test_candidates_and_fills_per_style_are_set_against_the_290_a_day_plan(tmp_path):
    db = _db(tmp_path)
    for i in range(6):
        _cand(db, f"s{i}", at=LONDON + pd.Timedelta(minutes=i))
    _trade(db, "s0")
    _cand(db, "i0", host="icm-demo", arm="I2A")
    _cand(db, "v0", host="vantage-demo", arm="VI2A")
    review = _run(tmp_path)
    plan = S.daily_review.volume_plan
    assert sum(plan.values()) == 290
    vol = {v.style: v for v in review.volume}
    assert (vol["scalp"].candidates, vol["scalp"].fills, vol["scalp"].plan) == (6, 1, plan["scalp"])
    assert vol["intraday"].candidates == 2 and vol["swing"].candidates == 0
    assert review.volume_total == 8 and review.plan_total == 290
    assert review.of_plan == pytest.approx(8 / 290, abs=1e-6)


@pytest.mark.parametrize(("arm", "host", "n", "breached"), [
    ("S1A", "icm-probe", 30, True),          # scalp: -30 R within the first 300 trades
    ("S1A", "icm-probe", 29, False),
    ("I2A", "icm-demo", 15, True),           # intraday: -15 R within 100
    ("W1A", "icm-demo", 10, True),           # swing: -10 R within 40
    ("W1A", "icm-demo", 9, False),
])
def test_arm_health_says_only_breached_or_ok_against_the_pre_registered_h61_stops(tmp_path, arm, host, n, breached):
    _losers(_db(tmp_path), arm, n, host=host)
    review = _run(tmp_path)
    assert review.arm_health[HOSTS[host]] == {arm: "breached" if breached else "ok"}


def test_a_stop_reached_after_its_first_trades_is_not_a_breach(tmp_path):
    db = _db(tmp_path)
    _losers(db, "W1A", 40, host="icm-demo", pnl=0.0)                         # 40 flat trades: the window has passed
    for i in range(12):
        t = LONDON + pd.Timedelta(hours=1, minutes=i)
        _cand(db, f"late{i}", host="icm-demo", arm="W1A", at=t)
        _trade(db, f"late{i}", host="icm-demo", arm="W1A", sent=t, exit_at=t + pd.Timedelta(seconds=30), pnl=-1.0)
    assert _run(tmp_path).arm_health["icm"]["W1A"] == "ok"


# ---------------------------------------------------------------------------------------------- (2) the actions
def test_an_arm_is_retired_the_day_its_stop_is_breached_and_stays_retired(tmp_path):
    _losers(_db(tmp_path), "S1A", 30)
    review = _run(tmp_path)
    assert [(r.broker, r.arm_id, r.day) for r in review.retired] == [("icm", "S1A", DAY)]
    assert dr.retired_arms(_db(tmp_path), "icm") == {"S1A"}
    assert dr.retired_arms(_db(tmp_path), "vantage") == set()             # a broker's arms only
    again = _run(tmp_path, day="2026-10-23", now=NOW + pd.Timedelta(days=1))
    assert again.retired == [] and dr.retired_arms(_db(tmp_path), "icm") == {"S1A"}   # permanent, recorded once


def test_a_dry_run_retires_nothing_refits_nothing_and_posts_nothing(tmp_path):
    db = _db(tmp_path)
    _losers(db, "S1A", 30)
    sink = Sink()
    review = _run(tmp_path, sink=sink, dry_run=True)
    assert review.dry_run and [r.arm_id for r in review.retired] == ["S1A"]   # what WOULD be retired
    assert dr.retired_arms(db, "icm") == set() and not sink.comments and not sink.issues
    assert not dr.review_path(tmp_path, DAY).exists()


def test_the_engine_sends_no_new_candidate_of_a_retired_arm_and_keeps_managing_its_open_position(tmp_path,
                                                                                                  monkeypatch):
    from goldbot.engine import cost_probe
    from goldbot.research import arm_rules
    from tests.test_exploration_engine import CLOSE, LOSS, SHORT, _arm, _at, _engine, _rule
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))          # the engine tests' fixed rule and loss
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)
    # a short whose own stop and target stay out of the fixture's 15-minute path, so only an orphan close could end it
    arm = _arm("i-long", side=-1, stop_usd=15.0, target_r=3.0, hold_bars=8)
    eng, pb = _engine(tmp_path, [arm])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)                                               # one open position before the retirement
    dr.retire(_db(tmp_path), broker="icm", arm_id="i-long", day=DAY, now=NOW)
    later = CLOSE + pd.Timedelta(minutes=15)
    exploration.on_pass(eng, _at(eng, pb, later))
    assert [d["arm"] for d in eng.decisions if d.get("action") == "exploration_arm_retired"] == ["i-long"]
    assert len([d for d in eng.decisions if str(d.get("action", "")).startswith("exploration:")]) == 1
    assert len([p for p in pb.positions()]) == 1 and pid in eng.open     # exits untouched: not closed as an orphan
    recorded = ex.read_obs(_db(tmp_path))
    assert (recorded["decision_utc"] == later).sum() == 0                  # no candidate of the retired arm


def test_a_cost_cell_is_refit_after_200_fills_since_the_last_fit_and_never_cheaper_within_a_day(tmp_path):
    db = _db(tmp_path)
    for i in range(199):
        t = LONDON + pd.Timedelta(seconds=10 * i)
        _trade(db, f"f{i}", sent=t, requested=2000.0, fill=2000.4)
    assert _run(tmp_path, dry_run=False).refits == []                    # 199: not yet
    t = LONDON + pd.Timedelta(seconds=10 * 199)
    _trade(db, "f199", sent=t, requested=2000.0, fill=2000.4)
    [cell] = _run(tmp_path).refits
    assert (cell.broker, cell.session, cell.fills) == ("icm", "london", 200)
    assert cell.slippage_usd == pytest.approx(0.4)
    assert _run(tmp_path).refits == []                                   # no new fills since that fit
    assert dr.cost_cells(db, "icm", NOW)["london"] == pytest.approx(0.4)
    assert dr.cost_cells(db, "vantage", NOW) == {}
    # a cheaper fit within the same day never lowers the cell; the next day's fit may
    dr.write_cell(db, broker="icm", session="london", day=DAY, slippage_usd=0.2, fills=200, through=HI, now=NOW)
    assert dr.cost_cells(db, "icm", NOW)["london"] == pytest.approx(0.4)
    later = NOW + pd.Timedelta(days=1)
    dr.write_cell(db, broker="icm", session="london", day="2026-10-23", slippage_usd=0.2, fills=200,
                  through=HI + pd.Timedelta(days=1), now=later)
    assert dr.cost_cells(db, "icm", later)["london"] == pytest.approx(0.2)
    assert dr.cost_cells(db, "icm", NOW)["london"] == pytest.approx(0.4)     # point in time


def test_the_refit_floor_is_the_research_slippage_floor(tmp_path):
    db = _db(tmp_path)
    for i in range(200):
        _trade(db, f"f{i}", sent=LONDON + pd.Timedelta(seconds=10 * i), requested=2000.0, fill=1999.9)  # favourable
    [cell] = _run(tmp_path).refits
    assert cell.slippage_usd == pytest.approx(ex.SLIPPAGE_FLOOR_USD)      # never credited, never below the floor


def test_label_replay_costs_take_a_refit_cell_only_when_it_is_dearer(tmp_path):
    from goldbot.ops import jobs
    db = _db(tmp_path)
    for i in range(200):
        _trade(db, f"f{i}", sent=LONDON + pd.Timedelta(seconds=10 * i), requested=2000.0, fill=2001.0)
    _run(tmp_path)
    ctx: Any = SimpleNamespace(settings=S, store=_store(), state_dir=tmp_path,
                          accounts=[SimpleNamespace(account_id=a, broker=b, server_tz="Europe/Athens")
                                    for a, b in HOSTS.items()])
    before = jobs.exploration_costs(ctx, LONDON, "icm")                   # the cell is not effective yet
    after = jobs.exploration_costs(ctx, NOW, "icm")
    assert before.slippage_usd == S.costs.slippage_prior_usd
    assert after.slippage_usd == pytest.approx(1.0) and "cells" in after.version
    assert jobs.exploration_costs(ctx, NOW, "vantage").slippage_usd == S.costs.slippage_prior_usd


def test_an_incident_opens_or_comments_on_a_learning_ops_issue_with_the_counted_cause(tmp_path):
    journal = pd.DataFrame({"ts_utc": [LONDON, LONDON], "action": ["exploration_error", "exploration_error"]})
    sink = Sink()
    review = _run(tmp_path, store=_store({("decisions", "icm-probe"): journal}), sink=sink)
    assert any(i.kind == "engine" and i.count == 2 for i in review.incidents)
    [(label, title, body)] = sink.issues
    assert label == "learning-ops" and DAY in title
    assert "2 exploration_error" in body
    _blinded(title + body)


def test_volume_under_half_the_plan_on_two_trading_days_running_opens_an_issue_with_the_counted_cause(tmp_path):
    db = _db(tmp_path)
    for i in range(10):
        _cand(db, f"x{i}", at=LONDON + pd.Timedelta(minutes=i), taken=False, blocked="global_cap")
    sink = Sink()
    first = _run(tmp_path, day="2026-10-21", now=NOW - pd.Timedelta(days=1), sink=sink)
    assert first.volume_low_days == 1 and not sink.issues
    second = _run(tmp_path, sink=sink)
    assert second.volume_low_days == 2
    [(_, title, body)] = sink.issues
    assert "volume" in title.lower() and "global_cap" in body and "of 290 planned" in body
    _blinded(body)


def test_a_weekend_day_neither_counts_as_low_volume_nor_breaks_the_run(tmp_path):
    sink = Sink()
    _run(tmp_path, day="2026-10-23", now=NOW + pd.Timedelta(days=1), sink=sink)            # Friday: low (1)
    sat = _run(tmp_path, day="2026-10-24", now=NOW + pd.Timedelta(days=2), sink=sink)
    sun = _run(tmp_path, day="2026-10-25", now=NOW + pd.Timedelta(days=3), sink=sink)
    assert (sat.volume_low_days, sun.volume_low_days) == (1, 1) and not sink.issues
    mon = _run(tmp_path, day="2026-10-26", now=NOW + pd.Timedelta(days=4), sink=sink)
    assert mon.volume_low_days == 2 and len(sink.issues) == 1


def test_without_a_token_the_issue_body_is_written_to_state_and_nothing_crashes(tmp_path, caplog):
    journal = pd.DataFrame({"ts_utc": [LONDON], "action": ["exploration_error"]})
    review = _run(tmp_path, store=_store({("decisions", "icm-probe"): journal}), sink=None)
    out = dr.outbox_dir(tmp_path)
    assert {p.name for p in out.iterdir()} == {f"{DAY}-learning-ops.md", f"{DAY}-status.md"}
    assert "no token" in caplog.text and review.incidents


def test_a_failed_post_keeps_the_body_in_state_and_the_review_still_completes(tmp_path):
    journal = pd.DataFrame({"ts_utc": [LONDON], "action": ["exploration_error"]})
    review = _run(tmp_path, store=_store({("decisions", "icm-probe"): journal}), sink=Sink(fail=True))
    assert dr.review_path(tmp_path, DAY).exists() and review.incidents
    assert (dr.outbox_dir(tmp_path) / f"{DAY}-learning-ops.md").exists()


def test_one_status_comment_on_issue_34_a_day_even_when_the_review_runs_twice(tmp_path):
    sink = Sink()
    _run(tmp_path, sink=sink)
    _run(tmp_path, sink=sink)
    assert [n for n, _ in sink.comments] == [34]
    _blinded(sink.comments[0][1])


# ---------------------------------------------------------------------------------------------- (3) never per-arm R
def test_the_review_record_refuses_any_per_arm_r_rank_or_sign_field():
    base = dr.DailyReview(day=DAY, generated_utc=NOW, dry_run=True)
    for bad in ("mean_r", "rank", "sign", "pnl", "share"):
        with pytest.raises(ValidationError):
            dr.DailyReview(**{**base.model_dump(), bad: 1})                # unknown fields are refused
    with pytest.raises(ValidationError, match="per-arm"):
        dr.DailyReview(**{**base.model_dump(), "refusals": {"icm-probe": {"S1A_r": 3}}})
    with pytest.raises(ValidationError):
        dr.DailyReview(**{**base.model_dump(), "arm_health": {"icm": {"S1A": "-31R"}}})


def test_the_record_issue_and_report_section_carry_no_arm_r_rank_or_sign(tmp_path):
    db = _db(tmp_path)
    _losers(db, "S1A", 30)
    journal = pd.DataFrame({"ts_utc": [LONDON], "action": ["exploration_error"]})
    sink = Sink()
    review = _run(tmp_path, store=_store({("decisions", "icm-probe"): journal}), sink=sink)
    stored = dr.review_path(tmp_path, DAY).read_text()
    for key in re.findall(r'"([^"]+)":', stored):
        assert not dr.banned_key(key), key
    assert "S1A" not in "".join(b for _, _, b in sink.issues) + "".join(b for _, b in sink.comments)
    for text in [b for _, _, b in sink.issues] + [b for _, b in sink.comments] + dr.report_lines(review):
        _blinded(text)
    assert any("1 arm retired at its stop" in x for x in dr.report_lines(review))


# ---------------------------------------------------------------------------------------------- (4) outputs
def test_the_review_is_written_schema_valid_to_state_and_to_program_db_when_that_store_exists(tmp_path):
    from goldbot.ledger.store import Ledger
    led = Ledger.open(tmp_path)
    _run(tmp_path)
    got = dr.load_review(tmp_path, DAY)
    assert got is not None and got.day == DAY
    [row] = led.query("artifacts", include_private=True)
    assert row["key"] == f"daily_review/{DAY}" and row["privacy"] == "private"


def test_no_program_db_is_created_when_the_store_does_not_exist(tmp_path):
    _run(tmp_path)
    assert not (tmp_path / "program.db").exists()


def test_integrity_counts_gaps_duplicates_clock_faults_and_failing_health_as_incidents(tmp_path):
    minutes = pd.date_range(LONDON, LONDON + pd.Timedelta(minutes=59), freq="1min")
    bars = pd.DataFrame({"ts_utc": list(minutes) + [minutes[0]]})        # one hour of bars, one duplicate
    journal = pd.DataFrame({"ts_utc": [LONDON], "action": ["exploration_clock_ahead"]})
    (tmp_path / "health_last.json").write_text(json.dumps({"ts": NOW.isoformat(),
                                                           "statuses": {"bridge:icm-probe": "fail", "disk": "ok"}}))
    review = _run(tmp_path, store=_store({("bars_1m", "icm-probe"): bars, ("decisions", "icm-probe"): journal}))
    integ = {i.host: i for i in review.integrity}["icm-probe"]
    assert integ.bars_duplicate == 1 and integ.bars_missing_min is not None
    assert integ.bars_missing_min > S.daily_review.max_gap_minutes
    assert integ.clock_events == 1
    kinds = {i.kind for i in review.incidents}
    assert {"data_gap", "data_duplicate", "clock", "health"} <= kinds


def test_the_owner_report_carries_the_blinded_review_lines(tmp_path):
    from goldbot.telegram.digest import build_digest
    db = _db(tmp_path)
    for i in range(3):
        _cand(db, f"l{i}", at=LONDON + pd.Timedelta(minutes=i))
        _trade(db, f"l{i}", sent=LONDON + pd.Timedelta(minutes=i))
    _run(tmp_path)
    text = build_digest(tmp_path, NOW + pd.Timedelta(minutes=30), settings=S)
    line = next(x for x in text.splitlines() if x.startswith("Review:"))
    assert "ICM $0.37/oz" in line and "3 of 290 planned" in line
    _blinded(line)


def test_the_monday_rollup_writes_trends_and_at_most_four_variant_proposals_for_the_research_director(tmp_path):
    db = _db(tmp_path)
    for k, day in enumerate(["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]):
        lo = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=9)
        for i in range(3):
            _cand(db, f"{day}-b{i}", at=lo + pd.Timedelta(minutes=i), taken=False, blocked="global_cap")
        _cand(db, f"{day}-n", at=lo + pd.Timedelta(hours=6))
        _trade(db, f"{day}-n", sent=lo + pd.Timedelta(hours=6), reason="stop", exit_price=1997.0, pnl=-1.3)
        _run(tmp_path, day=day, now=pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1, hours=6, minutes=15))
    sun = _run(tmp_path, day="2026-10-25", now=pd.Timestamp("2026-10-26 06:15", tz="UTC"))
    assert sun.weekly is not None
    week = dr.load_weekly(tmp_path, "2026-10-25")
    assert week is not None and [d.day for d in week.days] == ["2026-10-19", "2026-10-20", "2026-10-21",
                                                               "2026-10-22", "2026-10-23", "2026-10-25"]
    assert 1 <= len(week.proposals) <= 4 and all(p.status == "proposed, not run" for p in week.proposals)
    assert any("stop" in p.title.lower() for p in week.proposals)       # overshoot above the $0.70 scenario
    _blinded(week.model_dump_json())


# ---------------------------------------------------------------------------------------------- wiring
def test_daily_review_is_scheduled_at_0615_after_the_learner_and_before_the_owner_report():
    from goldbot.ops.jobs import JOBS
    s = S.scheduler.daily_review
    assert "daily_review" in JOBS and (s.kind, s.at) == ("daily", "06:15")
    hh, mm = (int(x) for x in s.at.split(":"))
    dh, dm = (int(x) for x in S.telegram.digest_at.split(":"))
    assert hh * 60 + mm < dh * 60 + dm


def test_the_dry_run_cli_reviews_a_fixture_day_and_writes_and_posts_nothing(tmp_path, capsys, monkeypatch):
    from goldbot.research.daily_review import main
    db = _db(tmp_path)
    _losers(db, "S1A", 30)
    monkeypatch.setattr(dr, "_hosts", lambda: HOSTS)
    monkeypatch.setattr(dr, "_store_for", lambda settings: _store())
    assert main(["--dry-run", "--date", DAY, "--state", str(tmp_path)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["day"] == DAY and out["dry_run"] is True
    assert out["arm_health"]["icm"]["S1A"] == "breached"
    assert dr.retired_arms(db, "icm") == set() and not dr.review_path(tmp_path, DAY).exists()
    assert not dr.outbox_dir(tmp_path).exists()


def test_the_cli_refuses_a_malformed_date(tmp_path, capsys):
    from goldbot.research.daily_review import main
    assert main(["--dry-run", "--date", "22-10-2026", "--state", str(tmp_path)]) == 2


def test_the_job_reviews_the_day_before_its_slot_and_without_a_token_keeps_the_text_in_state(tmp_path):
    from goldbot.ops.jobs import daily_review
    accounts = [SimpleNamespace(account_id=a, broker=b, exploration=SimpleNamespace(enabled=True))
                for a, b in HOSTS.items()]
    ctx: Any = SimpleNamespace(settings=S, store=_store(), state_dir=tmp_path, accounts=accounts,
                               get_secret=lambda key: None)
    got = daily_review(ctx, NOW)
    assert got["day"] == DAY and got["posted"] == {"status": "outbox"}
    assert dr.review_path(tmp_path, DAY).exists()
    ctx.accounts = [SimpleNamespace(account_id="icm-demo", broker="icm", exploration=SimpleNamespace(enabled=False))]
    assert daily_review(ctx, NOW) == {"skipped": "no enabled account has exploration on"}
