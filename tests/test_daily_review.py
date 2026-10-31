"""The daily learning-ops review (goldbot/research/daily_review.py; research-director spec 2026-10-26, loop lane L2 made
daily): per broker, host and session the costs, fill rate, refusal reasons, volume against the ~290 a day plan, arm
health against the pre-registered H-61 stops (only `breached` / `ok`) and integrity; the automatic actions (retire an
arm at its stop, refit a cost cell after 200 fills, never cheaper within a day, a `learning-ops` issue on an incident
or two low-volume days, one status comment on #34); and never a login or a host in a text it writes or sends (backlog
139 narrowed the old blinding to that; results are the strategy ranking's, ADR 0053). Its own texts still carry no
per-arm figure: arm health is `breached` or `ok`."""
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

pytestmark = pytest.mark.clock_at("2026-10-30T12:00:00Z")

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
         dry_run: bool = False, trials_left: int | None = 4) -> dr.DailyReview:
    return dr.run_daily_review(settings=S, state_dir=sd, store=store if store is not None else _store(), hosts=HOSTS,
                               day=day, now=now, dry_run=dry_run, sink=sink, trials_left=trials_left)


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


def test_candidates_and_fills_per_broker_and_style_are_set_against_the_plan_290_a_day_on_icm(tmp_path):
    db = _db(tmp_path)
    for i in range(6):
        _cand(db, f"s{i}", at=LONDON + pd.Timedelta(minutes=i))
    _trade(db, "s0")
    _cand(db, "i0", host="icm-demo", arm="I2A")
    _cand(db, "v0", host="vantage-demo", arm="VI2A")
    review = _run(tmp_path)
    cfg = S.daily_review
    assert sum(cfg.volume_plan["icm"].values()) + cfg.probe_plan["icm"] == 290      # the prereg's ~290 a day
    vol = {(v.broker, v.style): v for v in review.volume}
    icm = vol["icm", "scalp"]
    assert (icm.candidates, icm.fills, icm.plan) == (6, 1, 160)
    assert vol["icm", "intraday"].candidates == 1 and vol["vantage", "intraday"].candidates == 1  # never pooled
    assert vol["icm", "probe"].plan == 90 and vol["icm", "swing"].candidates == 0
    assert review.volume_total == 8 and review.plan_total == 290 + 80
    assert review.of_plan == pytest.approx(8 / 370, abs=1e-6)


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
    again = _run(tmp_path).refits                                         # a rerun: no new fills since that fit
    assert [(c.session, c.fills) for c in again] == [("london", 200)]
    assert again[0].slippage_usd == pytest.approx(0.4)
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
    assert "volume" in title.lower() and "global_cap" in body and "of 370 planned" in body
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


# ---------------------------------------------------------------------------------------------- (3) texts scrubbed
def test_the_review_record_refuses_unknown_fields_and_a_health_word_other_than_breached_or_ok():
    base = dr.DailyReview(day=DAY, generated_utc=NOW, dry_run=True)
    with pytest.raises(ValidationError):
        dr.DailyReview(**{**base.model_dump(), "mean_r": 1})                # unknown fields are refused
    with pytest.raises(ValidationError):
        dr.DailyReview(**{**base.model_dump(), "arm_health": {"icm": {"S1A": "-31R"}}})


def test_a_line_carrying_a_login_or_a_host_is_dropped_before_it_is_written_or_sent():
    """Backlog 139 (3): the old blinding check is narrowed to identifiers: the issue texts go to a public repo."""
    text = "costs ICM $0.37/oz\naccount 51234567 halted\nbridge 10.0.0.12 down\nvolume 1,234,567 of 2,000,000\n+0.3R ok"
    assert dr.scrub(text).splitlines() == ["costs ICM $0.37/oz", "volume 1,234,567 of 2,000,000", "+0.3R ok"]


def test_the_record_issue_and_report_section_carry_no_arm_r_rank_or_sign(tmp_path):
    db = _db(tmp_path)
    _losers(db, "S1A", 30)
    journal = pd.DataFrame({"ts_utc": [LONDON], "action": ["exploration_error"]})
    sink = Sink()
    review = _run(tmp_path, store=_store({("decisions", "icm-probe"): journal}), sink=sink)
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


def test_the_broker_bar_late_rate_and_the_minute_passs_waits_are_reported_per_host(tmp_path):
    """Quant C1 / trading-safety LOW 2: the minutes the 1m/5m pass decided without their broker bar (`late`, the volume
    arms held out) over the host's 1m bars, and how long it waited (`hold_ms`), so a rise in `exploration_stale`
    can be traced to the wait."""
    minutes = pd.date_range(LONDON, LONDON + pd.Timedelta(minutes=99), freq="1min")
    bars = pd.DataFrame({"ts_utc": minutes})
    waits = [(400, False), (1500, False), (3000, True), (3000, True)]
    journal = pd.DataFrame({"ts_utc": [LONDON + pd.Timedelta(minutes=i) for i in range(len(waits))],
                            "action": [exploration.WAIT_ACTION] * len(waits),
                            "detail": [json.dumps({"hold_ms": h, "late": late}) for h, late in waits]})
    review = _run(tmp_path, store=_store({("bars_1m", "icm-probe"): bars, ("decisions", "icm-probe"): journal}))
    [w] = [w for w in review.broker_bars if w.host == "icm-probe"]
    assert (w.minutes, w.waited, w.late, w.late_rate, w.hold_ms_max) == (100, 4, 2, 0.02, 3000)
    line = next(x for x in dr.report_lines(review) if "broker bar" in x)
    assert "2 of 100 minutes" in line and "2%" in line
    assert all(w.late == 0 for w in review.broker_bars if w.host != "icm-probe")


def _probe(db: Database, cid: str, *, host: str = "icm-probe", sent: pd.Timestamp = LONDON,
           fill: float | None = 2000.1) -> None:
    from goldbot.engine import cost_probe
    with db.write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id=cid, account_id=host, agent_id=cost_probe.SCHEDULED, side=1, lots=0.01,
            requested_price=2000.0, spread_usd=0.2, sent_utc=sent, fill_price=fill, stop_loss=2.3,
            purpose="cost_measurement", fx_acct_per_usd=0.75))


def test_the_day_fill_count_adds_the_cost_probes_on_a_line_of_their_own(tmp_path):
    """volume-500 plan section 1: "Reports show the count split into strategy arms, controls and probes, so the total
    cannot be padded": the day's fills are arm fills + probe fills, and the probes are a separate line."""
    db = _db(tmp_path)
    for i in range(3):
        _cand(db, f"s{i}", at=LONDON + pd.Timedelta(minutes=i))
        _trade(db, f"s{i}", sent=LONDON + pd.Timedelta(minutes=i))
    for i in range(2):
        _probe(db, f"cp{i}", sent=LONDON + pd.Timedelta(minutes=10 + i))
    _probe(db, "cp-unfilled", sent=LONDON + pd.Timedelta(minutes=20), fill=None)
    review = _run(tmp_path)
    assert (review.fills_total, review.fills_probes) == (5, 2)
    line = next(x for x in dr.report_lines(review) if x.startswith("Review: fills"))
    assert "fills 5" in line and "arms 3" in line and "probes 2" in line
    _blinded(line)


def test_the_owner_report_carries_the_blinded_review_lines(tmp_path):
    from goldbot.telegram.digest import build_digest
    db = _db(tmp_path)
    for i in range(3):
        _cand(db, f"l{i}", at=LONDON + pd.Timedelta(minutes=i))
        _trade(db, f"l{i}", sent=LONDON + pd.Timedelta(minutes=i))
    _run(tmp_path)
    text = build_digest(tmp_path, NOW + pd.Timedelta(minutes=30), settings=S)
    line = next(x for x in text.splitlines() if x.startswith("Review:"))
    assert "ICM $0.37/oz" in line and "3 of 370 planned" in line
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
                               get_secret=lambda key: None, trials=SimpleNamespace(budget_used=lambda q: 0))
    got = daily_review(ctx, NOW)
    assert got["day"] == DAY and got["posted"] == {"status": "outbox"}
    assert dr.review_path(tmp_path, DAY).exists()
    ctx.accounts = [SimpleNamespace(account_id="icm-demo", broker="icm", exploration=SimpleNamespace(enabled=False))]
    assert daily_review(ctx, NOW) == {"skipped": "no enabled account has exploration on"}


# ---------------------------------------------------------------------------------------------- review round 1
def test_a_control_arm_past_the_stop_line_is_never_breached_nor_retired(tmp_path):
    _losers(_db(tmp_path), "S0", 40)                     # a random bracket (spec rule random_bracket): the null
    review = _run(tmp_path)
    assert review.arm_health["icm"]["S0"] == "ok" and review.retired == []
    assert dr.retired_arms(_db(tmp_path), "icm") == set()
    assert {"S0", "S0R", "I0", "W0", "VI0", "VW0"} <= labs.control_arm_ids()
    with _db(tmp_path).connection() as conn:
        [st] = labs.arm_statuses(conn, ["icm-probe"])
    assert st.control and st.band == "running"            # the owner's report never says it was retired


def _weekly_fixture(tmp_path: Path) -> None:
    db = _db(tmp_path)
    for day in ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"]:
        lo = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=9)
        _cand(db, f"{day}-n", at=lo + pd.Timedelta(hours=6))
        _trade(db, f"{day}-n", sent=lo + pd.Timedelta(hours=6), reason="stop", exit_price=1997.0, pnl=-1.3)
        _run(tmp_path, day=day, now=pd.Timestamp(day, tz="UTC") + pd.Timedelta(days=1, hours=6, minutes=15))


@pytest.mark.parametrize(("left", "n"), [(0, 0), (None, 0), (1, 1), (10, 1)])
def test_variant_proposals_each_cost_a_trial_and_none_are_made_once_the_quarters_budget_is_spent(tmp_path, left, n):
    _weekly_fixture(tmp_path)
    dr.run_daily_review(settings=S, state_dir=tmp_path, store=_store(), hosts=HOSTS, day="2026-10-25",
                        now=pd.Timestamp("2026-10-26 06:15", tz="UTC"), trials_left=left)
    week = dr.load_weekly(tmp_path, "2026-10-25")
    assert week is not None and week.trials_left == left and len(week.proposals) == n
    assert all(p.cost == "one registry trial if pre-registered" for p in week.proposals)
    assert not any("timeframe" in p.title.lower() for p in week.proposals)


def test_the_trials_left_are_the_quarters_budget_less_its_registry_trials():
    q4 = pd.Timestamp("2026-10-26", tz="UTC")
    full = SimpleNamespace(budget_used=lambda q: S.research.budget_for(q))
    assert dr.trials_left(S, full, q4) == 0                                 # Q4: 69 of 69 used
    assert dr.trials_left(S, SimpleNamespace(budget_used=lambda q: 5), q4) == S.research.budget_for("2026Q4") - 5
    assert dr.trials_left(S, None, q4) is None                              # unreadable: no proposal


def test_a_rerun_of_the_same_day_reports_its_retirements_and_refits_and_keeps_what_was_posted(tmp_path):
    db = _db(tmp_path)
    _losers(db, "S1A", 30)
    for i in range(200):          # another arm's fills: the refit pools a broker's session, never an arm
        _trade(db, f"f{i}", arm="S2A", sent=LONDON + pd.Timedelta(seconds=10 * i), requested=2000.0, fill=2000.4)
    sink = Sink()
    first = _run(tmp_path, sink=sink)
    again = _run(tmp_path, sink=sink)
    assert [r.arm_id for r in again.retired] == [r.arm_id for r in first.retired] == ["S1A"]
    assert [(c.broker, c.session) for c in again.refits] == [(c.broker, c.session) for c in first.refits]
    assert again.posted == first.posted == {"status": "posted"} and len(sink.comments) == 1
    stored = dr.load_review(tmp_path, DAY)
    assert stored is not None and stored.retired and stored.refits


def test_the_job_summary_carries_the_posted_status_and_a_posted_text_leaves_the_outbox(tmp_path):
    _run(tmp_path, sink=None)                                               # no token: the text waits in outbox/
    assert (dr.outbox_dir(tmp_path) / f"{DAY}-status.md").exists()
    review = _run(tmp_path, sink=Sink())                                    # the next run posts it
    assert dr.summary(review)["posted"] == {"status": "posted"}
    assert not (dr.outbox_dir(tmp_path) / f"{DAY}-status.md").exists()


def test_the_owner_report_warns_when_issue_texts_wait_in_the_outbox_for_two_days(tmp_path):
    import os

    from goldbot.telegram.digest import review_section
    _run(tmp_path, sink=None)
    assert not [x for x in review_section(tmp_path, DAY) if "not posted" in x.text]
    old = pd.Timestamp.now("UTC").timestamp() - 3 * 86400
    for f in dr.outbox_dir(tmp_path).iterdir():
        os.utime(f, (old, old))
    [line] = [x for x in review_section(tmp_path, DAY) if "not posted" in x.text]
    assert line.text == "⚠️ Review: 1 issue text not posted to GitHub for 2+ days (state outbox)" and line.prio == 1


def test_after_a_restart_no_entry_goes_out_until_the_retired_arms_are_read_and_exits_are_unaffected(tmp_path,
                                                                                                   monkeypatch):
    from goldbot.engine import cost_probe
    from goldbot.research import arm_rules
    from tests.test_exploration_engine import CLOSE, LOSS, SHORT, _arm, _at, _engine, _rule
    monkeypatch.setitem(arm_rules.RULE_FUNCS, SHORT, _rule(-1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)
    arm = _arm("i-long", side=-1, stop_usd=15.0, target_r=3.0, hold_bars=8)
    eng, pb = _engine(tmp_path, [arm])
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    [pid] = list(eng.open)
    with _db(tmp_path).write() as conn:                                     # the status table cannot be read
        conn.execute("ALTER TABLE exploration_arm_status RENAME TO gone")
    eng2, _ = _engine(tmp_path, [arm], pb=pb)                               # a restart
    eng2.open.clear()
    eng2._reconcile(CLOSE)
    later = CLOSE + pd.Timedelta(minutes=15)
    exploration.on_pass(eng2, _at(eng2, pb, later))
    assert not [d for d in eng2.decisions if str(d.get("action", "")).startswith("exploration:")]
    blocked = ex.read_obs(_db(tmp_path))
    assert set(blocked.loc[blocked["decision_utc"] == later, "blocked_reason"]) == {"arm_status_unread"}
    eng2._retry_closes(later)
    assert pid in eng2.open and [p.position_id for p in pb.positions()] == [pid]   # adopted and managed, not closed
    with _db(tmp_path).write() as conn:
        conn.execute("ALTER TABLE gone RENAME TO exploration_arm_status")
    exploration.on_pass(eng2, _at(eng2, pb, CLOSE + pd.Timedelta(minutes=30)))
    assert eng2.exploration is not None and eng2.exploration.retired_ok


def test_a_target_leg_never_offsets_a_stops_cost_in_the_daily_review():
    at = LONDON + pd.Timedelta(minutes=5)
    legs = pd.DataFrame({"ts_utc": [at, at], "agent_id": ["explore:S1A"] * 2, "side": [1, 1],
                         "kind": ["stop", "target"], "requested": [2002.0, 2002.0], "filled": [2001.9, 2003.0]})
    day = dr._Day.__new__(dr._Day)
    day.exits = {"icm-probe": legs}
    assert [v for _, v in dr._exit_slips(day, "icm-probe")] == [pytest.approx(0.1)]


def test_only_the_journal_and_the_exit_legs_are_read_across_symbols_never_the_bars():
    seen: dict[str, Any] = {}

    def read(table: str, **kw: Any) -> pd.DataFrame:
        seen[table] = kw.get("symbol", "XAUUSD")
        return pd.DataFrame()
    for t in ("decisions", "exit_fills", "bars_1m"):
        dr._frame(SimpleNamespace(read=read), t, "h", LONDON, LONDON)
    assert seen == {"decisions": None, "exit_fills": None, "bars_1m": "XAUUSD"}


def test_a_host_whose_gold_symbol_has_a_suffix_still_has_its_exit_legs_read(tmp_path):
    """Backlog 141: `Store.read` defaulted to symbol XAUUSD, so Vantage's legs (written under XAUUSD+) read as none."""
    from goldbot.data.store import Store
    from goldbot.research.daily_review import _frame
    store = Store(tmp_path / "data")
    legs = pd.DataFrame({"ts_utc": [LONDON], "position_id": [1], "agent_id": ["explore:Z7V"], "side": [1],
                         "requested": [2002.0], "filled": [2001.95]})
    store.append("exit_fills", legs, source="vantage-demo", symbol="XAUUSD+", dedupe=False)
    got = _frame(store, "exit_fills", "vantage-demo", LONDON - pd.Timedelta(hours=1), LONDON + pd.Timedelta(hours=1))
    assert len(got) == 1 and got["filled"].iloc[0] == 2001.95
