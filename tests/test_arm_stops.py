"""The pre-registered stop rules of the exploration arms as data (config/arm_stops.yaml, goldbot/research/arm_stops.py):
a W1/W2/Z family stops on its POOLED running net R (-30 R within 300 fills, across strata and brokers), futility is
tested only where it is registered (W2, Z on >= 20 session days, F1A/G1A), the H-61 arms keep their per-style stop,
controls never stop (F1C and G1C only stop WITH their arm, as registered), and the H-81 strata carry a per-stratum
cost stop (UB cost/R > 0.10 halves the share, > 0.20 suspends). A stop only refuses new entries: it is the same
`exploration_arm_status` row the engine already reads, which never touches an open position."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest
import yaml

from goldbot.db.connection import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.research import arm_stops
from goldbot.research import daily_review as dr
from goldbot.research import exploration_data as ex
from goldbot.telegram import labs
from tests.test_daily_review import HOSTS, _run

START = pd.Timestamp("2026-09-01 09:00", tz="UTC")        # well before the review day's end (the `until` cut)
TF = {"scalp": "1m", "intraday": "15m", "swing": "4h"}


def _db(sd: Path) -> Database:
    return Database.open(sd, "aux")


def _fills(db: Database, arm: str, n: int, *, host: str = "icm-demo", style: str = "intraday", pnl: float = -1.0,
           pnls: list[float] | None = None, days: int = 1, start: pd.Timestamp = START, control: bool = False,
           cost: float | None = None, stop_loss: float = 1.0, tag: str = "") -> None:
    """`n` admissible closes of `arm` in exit order, spread round-robin over `days` UTC days from `start`."""
    for i in range(n):
        at = start + pd.Timedelta(days=i % days, minutes=i // days)
        cid = f"{arm}{tag}-{i}"
        ex.record_candidate(db, candidate_id=cid, account_id=host, broker=HOSTS[host], arm_id=arm, family="f",
                            style=style, tf=TF[style], side=1, decision_utc=at, stop_dist=2.0, target_dist=2.0,
                            max_hold_s=600, taken=True, is_control=control, blocked_reason=None, branch="random",
                            u=0.5, mu=1.0)
        rec = ExplorationRecord(client_order_id=cid, account_id=host, arm_id=arm, family="f", tf=TF[style], side=1,
                                lots=0.01, signal_utc=at, sent_utc=at, atr_usd=2.0, stop_usd=2.0, target_usd=2.0,
                                hold_s=600, requested_price=2000.0, spread_usd=0.2, fill_price=2000.1,
                                stop_loss=stop_loss, cost=cost, exit_utc=at + pd.Timedelta(seconds=30),
                                exit_price=2001.0, exit_reason="target", swap=0.0, commission=0.0,
                                pnl=pnls[i] if pnls is not None else pnl, censored=False, fx_acct_per_usd=0.75)
        with db.write() as conn:
            exploration.LEDGER.upsert(conn, rec)


def _status(sd: Path, accounts: list[str]) -> dict[str, labs.ArmStatus]:
    with _db(sd).connection() as conn:
        return {s.arm_id: s for s in labs.arm_statuses(conn, accounts)}


# ---------------------------------------------------------------------------------------------- family R stop
def test_a_w1_family_stops_on_its_pooled_minus_30_r_within_300_fills_and_every_member_is_retired(tmp_path):
    db = _db(tmp_path)
    _fills(db, "XIMNY", 15)
    _fills(db, "XIMLN", 15, start=START + pd.Timedelta(hours=6))      # neither alone is near -30 R
    st = _status(tmp_path, ["icm-demo"])
    assert st["XIMNY"].band == st["XIMLN"].band == "at_stop"
    assert st["XIMLN"].stop_at == 30                                    # the family's pooled fill count
    review = _run(tmp_path)
    # a family stops as a whole: every member on every broker reviewed (fills or not)
    assert {(r.broker, r.arm_id) for r in review.retired} == {(b, a) for b in ("icm", "vantage")
                                                              for a in ("XIMNY", "XIMLN")}


def test_a_w1_family_one_r_short_of_its_stop_is_not_stopped(tmp_path):
    db = _db(tmp_path)
    _fills(db, "XMR15", 15)
    _fills(db, "XMR1H", 14, start=START + pd.Timedelta(hours=6))
    st = _status(tmp_path, ["icm-demo"])
    assert st["XMR15"].band == st["XMR1H"].band == "within_50pct"
    assert _run(tmp_path).retired == []


def test_a_w1_single_arm_on_15m_is_no_longer_stopped_by_the_intraday_h61_rule(tmp_path):
    """XBB15 is a 15m (intraday) arm: H-61 would stop it at -15 R within 100; W1 registers -30 R within 300."""
    db = _db(tmp_path)
    _fills(db, "XBB15", 15)
    assert _status(tmp_path, ["icm-demo"])["XBB15"].band == "within_50pct"
    _fills(db, "XBB15", 15, start=START + pd.Timedelta(hours=6), tag="b")
    assert _status(tmp_path, ["icm-demo"])["XBB15"].band == "at_stop"


def test_a_z_family_pools_its_strata_across_brokers_and_stops_on_both(tmp_path):
    db = _db(tmp_path)
    _fills(db, "Z3D", 15, style="scalp")                                # icm-demo
    _fills(db, "Z3V", 15, host="vantage-demo", style="scalp", start=START + pd.Timedelta(hours=6))
    assert _status(tmp_path, ["icm-demo"])["Z3D"].band == "at_stop"
    assert _status(tmp_path, ["vantage-demo"])["Z3V"].band == "at_stop"
    review = _run(tmp_path)
    assert {(r.broker, r.arm_id) for r in review.retired} == {(b, a) for b in ("icm", "vantage")
                                                              for a in ("Z3D", "Z3V")}


def test_a_family_past_its_within_window_is_not_stopped_by_later_losses(tmp_path):
    db = _db(tmp_path)
    _fills(db, "XEQ15", 300, pnl=0.0, days=20)
    _fills(db, "XEQ15", 40, start=START + pd.Timedelta(days=30), tag="late")
    assert _status(tmp_path, ["icm-demo"])["XEQ15"].band == "running"


# ---------------------------------------------------------------------------------------------- futility
FLAT = [-0.04 if i % 2 else -0.06 for i in range(300)]                 # mean -0.05 R: UB well below +0.03 R


def test_futility_stops_a_w2_arm_at_its_300th_fill_when_the_upper_bound_is_below_three_hundredths_of_r(tmp_path):
    db = _db(tmp_path)
    _fills(db, "OBR15", 299, pnls=FLAT, days=20)
    assert _status(tmp_path, ["icm-demo"])["OBR15"].band != "at_stop"   # no read before the 300th fill
    _fills(db, "OBR15", 1, pnls=[-0.05], start=START + pd.Timedelta(days=25), tag="last")
    st = _status(tmp_path, ["icm-demo"])["OBR15"]
    assert (st.band, st.stop_at, st.stop_r) == ("at_stop", 300, None)  # no R shown for a futility stop
    assert {r.arm_id for r in _run(tmp_path).retired} == {"OBR15"}


def test_futility_does_not_stop_a_w2_arm_whose_upper_bound_clears_three_hundredths_of_r(tmp_path):
    _fills(_db(tmp_path), "OBR15", 300, pnls=[1.0 if i % 2 else -0.9 for i in range(300)], days=20)
    assert _status(tmp_path, ["icm-demo"])["OBR15"].band == "running"


def test_futility_is_never_applied_where_none_is_registered(tmp_path):
    """W1 registers no futility rule (arms-expansion 2.1): the same flat 300 fills keep it running."""
    _fills(_db(tmp_path), "XBB15", 300, pnls=FLAT, days=20)
    assert _status(tmp_path, ["icm-demo"])["XBB15"].band != "at_stop"


def test_a_z_familys_futility_read_waits_for_20_session_days(tmp_path):
    db = _db(tmp_path)
    _fills(db, "Z1P", 300, pnls=FLAT, days=10, host="icm-probe", style="scalp")
    assert _status(tmp_path, ["icm-probe"])["Z1P"].band != "at_stop"
    _fills(db, "Z1P", 10, pnl=-0.05, days=10, start=START + pd.Timedelta(days=12), host="icm-probe", style="scalp",
           tag="more")
    st = _status(tmp_path, ["icm-probe"])["Z1P"]
    assert (st.band, st.stop_at) == ("at_stop", 310)                   # read at 310: the first fill on day 20


def test_the_upper_bound_is_day_clustered():
    """CR1 over UTC days: identical days collapse to their day means, so one day's 300 fills give no bound."""
    days = [pd.Timestamp("2026-10-01", tz="UTC")] * 300
    assert arm_stops.upper_bound([0.1] * 150 + [-0.1] * 150, days) is None
    two = [pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-02", tz="UTC")] * 2
    ub = arm_stops.upper_bound([1.0, -1.0, 1.0, -1.0], two)
    assert ub is not None and ub == pytest.approx(0.0 + 1.6448536 * 1.0)


# ---------------------------------------------------------------------------------------------- unchanged arms
def test_an_h61_arm_keeps_its_per_style_stop(tmp_path):
    db = _db(tmp_path)
    _fills(db, "I2A", 15)                                               # intraday: -15 R within 100
    _fills(db, "S1A", 29, host="icm-probe", style="scalp")              # scalp: -30 R within 300
    _fills(db, "BXR15", 15)                                             # H-80: "the H-61 per-arm stop ... unchanged"
    assert _status(tmp_path, ["icm-demo"])["I2A"].band == "at_stop"
    assert _status(tmp_path, ["icm-demo"])["BXR15"].band == "at_stop"
    assert _status(tmp_path, ["icm-probe"])["S1A"].band == "within_50pct"


def test_controls_never_stop_even_where_their_family_is_registered(tmp_path):
    db = _db(tmp_path)
    _fills(db, "ZCD", 40, style="scalp", control=True)
    _fills(db, "XC15", 40, host="icm-probe", style="scalp", control=True)
    assert _status(tmp_path, ["icm-demo"])["ZCD"].band == "running"
    assert _status(tmp_path, ["icm-probe"])["XC15"].band == "running"
    assert _run(tmp_path).retired == []


def test_a_follower_control_stops_with_its_arm_and_never_on_its_own(tmp_path):
    db = _db(tmp_path)
    _fills(db, "F1C", 40, host="icm-probe", style="scalp", control=True)
    assert _status(tmp_path, ["icm-probe"])["F1C"].band == "running"   # its own losses never stop it
    _fills(db, "F1A", 30, host="icm-probe", style="scalp", pnl=-1.0, start=START + pd.Timedelta(hours=6))
    st = _status(tmp_path, ["icm-probe"])
    assert st["F1A"].band == st["F1C"].band == "at_stop"
    assert {r.arm_id for r in _run(tmp_path).retired} == {"F1A", "F1C"}


# ---------------------------------------------------------------------------------------------- cost stop (H-81)
@pytest.mark.parametrize(("n", "cost", "want"), [(30, 0.05, "ok"), (30, 0.15, "halved"), (30, 0.25, "suspended"),
                                                 (29, 0.25, "ok")])
def test_the_h81_cost_stop_halves_above_010_and_suspends_above_020_cost_per_r_from_30_fills(tmp_path, n, cost,
                                                                                           want):
    _fills(_db(tmp_path), "Z1P", n, host="icm-probe", style="scalp", pnl=0.5, cost=cost, days=5)
    with _db(tmp_path).connection() as conn:
        assert arm_stops.cost_states(conn, ["Z1P"]) == {"Z1P": want}


def test_the_cost_stop_applies_only_to_the_registered_strata(tmp_path):
    db = _db(tmp_path)
    _fills(db, "S1A", 40, host="icm-probe", style="scalp", pnl=0.5, cost=0.5, days=5)
    _fills(db, "ZCD", 40, style="scalp", pnl=0.5, cost=0.5, days=5, control=True)
    with _db(tmp_path).connection() as conn:
        assert arm_stops.cost_states(conn, ["S1A", "ZCD"]) == {}


def test_a_suspended_stratum_is_retired_by_the_review_under_the_cost_rule_and_a_halved_one_is_not(tmp_path):
    db = _db(tmp_path)
    _fills(db, "Z1P", 30, host="icm-probe", style="scalp", pnl=0.5, cost=0.25, days=5)
    _fills(db, "Z2P", 30, host="icm-probe", style="scalp", pnl=0.5, cost=0.15, days=5)
    review = _run(tmp_path)
    assert [(r.broker, r.arm_id) for r in review.retired] == [("icm", "Z1P")]
    with _db(tmp_path).connection() as conn:
        [rule] = [str(r) for (r,) in conn.execute("SELECT rule FROM exploration_arm_status WHERE arm_id = 'Z1P'")]
    assert "cost stop" in rule


def test_the_engine_halves_a_cost_halved_stratums_share_and_zeroes_a_suspended_one(tmp_path):
    from tests.test_exploration_engine import CLOSE, _arm, _engine
    eng, _ = _engine(tmp_path, [_arm("Z1P", style="scalp"), _arm("Z2P", style="scalp"), _arm("S1A", style="scalp")])
    assert eng.exploration is not None
    full = exploration.shares(eng.exploration, CLOSE)
    _fills(eng.exploration.db, "Z1P", 30, host="icm-probe", style="scalp", pnl=0.5, cost=0.15, days=5)
    _fills(eng.exploration.db, "Z2P", 30, host="icm-probe", style="scalp", pnl=0.5, cost=0.25, days=5)
    eng.exploration.weights_cache = None
    got = exploration.shares(eng.exploration, CLOSE)
    assert got == pytest.approx({"Z1P": full["Z1P"] / 2, "Z2P": 0.0, "S1A": full["S1A"]})


# ---------------------------------------------------------------------------------------------- the table itself
def test_every_arm_of_the_spec_and_of_the_accounts_is_classified_exactly_once():
    from goldbot.engine.exploration import SPEC_PATH
    from goldbot.research.arms import load_arms
    rules = arm_stops.load_stop_rules()
    spec = {a.id for a in load_arms(SPEC_PATH, check_hash=False)}
    accs = yaml.safe_load((SPEC_PATH.parent / "accounts.yaml").read_text())["accounts"]
    registered = {arm for a in accs.values() for arm in ((a.get("exploration") or {}).get("arms") or {})}
    in_units = [a for u in rules.units.values() for a in u.arms]
    lists = [*in_units, *rules.follows, *rules.h61, *rules.none]
    assert len(lists) == len(set(lists)), "an arm is classified twice"
    assert (spec | registered) - set(lists) == set(), "an arm has no registered stop rule"
    assert set(rules.follows.values()) <= set(in_units)
    assert set(rules.cost_stop.arms) <= {a for u in rules.units.values() if u.source == "Z" for a in u.arms}
    assert labs.control_arm_ids() <= set(rules.none)


def test_the_stop_families_and_the_pooled_read_units_have_the_same_members():
    """One family membership for the stop and the read: "a family continues or stops as a whole" (arms-expansion 2.1)
    and is read pooled ("the n is the family's pooled count", config/arm_reads.yaml). Every multi-arm stop unit is a
    read unit with the same arms, and every pooled read unit is a stop unit with the same arms, except H-80, whose
    BXR arms keep "the H-61 per-arm cumulative-R stop ... unchanged" and whose BXT/BXF virtual twins never stop."""
    from goldbot.api.strategy_labels import READS_PATH, _reads_file
    rules = arm_stops.load_stop_rules()
    read_units = _reads_file(READS_PATH).read_units
    assert read_units, "config/arm_reads.yaml read no unit (fail closed)"
    stop_families = {frozenset(u.arms) for u in rules.units.values() if len(u.arms) > 1}
    read_families = {frozenset(u.arms): u for u in read_units.values()}
    assert stop_families - set(read_families) == set(), "a stop family is not read pooled with the same arms"
    for arms, u in read_families.items():
        if arms in stop_families:
            continue
        counted = set(u.counted or u.arms)
        assert counted <= set(rules.h61) and arms - counted <= set(rules.none), \
            f"read unit {sorted(arms)} is no stop family and not the H-80 per-arm case"


def test_a_rules_file_with_an_arm_in_two_units_is_refused(tmp_path):
    p = tmp_path / "stops.yaml"
    p.write_text(yaml.safe_dump({"units": {"A": {"arms": ["X"], "stop_r": -30.0, "within": 300, "source": "W1"},
                                           "B": {"arms": ["X"], "stop_r": -30.0, "within": 300, "source": "W1"}}}))
    with pytest.raises(ValueError, match="X"):
        arm_stops.load_stop_rules(p)


def test_a_retirement_by_a_family_stop_is_the_same_entries_only_row_the_engine_reads(tmp_path):
    """The family stop writes `exploration_arm_status` like the H-61 stop: the engine's `refresh_retired` refuses new
    entries from it and keeps every exit (tests/test_daily_review.py proves the exits untouched)."""
    db = _db(tmp_path)
    _fills(db, "XIMNY", 30)
    _run(tmp_path)
    assert dr.retired_arms(db, "icm") == {"XIMNY", "XIMLN"}


def test_a_cost_stop_read_error_keeps_the_scales_last_read(tmp_path, monkeypatch):
    from tests.test_exploration_engine import CLOSE, _arm, _engine
    eng, _ = _engine(tmp_path, [_arm("Z1P", style="scalp"), _arm("S1A", style="scalp")])
    assert eng.exploration is not None
    _fills(eng.exploration.db, "Z1P", 30, host="icm-probe", style="scalp", pnl=0.5, cost=0.15, days=5)
    halved = exploration.shares(eng.exploration, CLOSE)["Z1P"]

    def down(*a: object, **k: object) -> dict[str, str]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(arm_stops, "cost_states", down)
    eng.exploration.weights_cache = None
    assert exploration.shares(eng.exploration, CLOSE)["Z1P"] == pytest.approx(halved)


# ---------------------------------------------------------------------------------------------- review round 1
def test_futility_weights_each_stratum_equally_not_by_its_fill_count(tmp_path):
    """arms-expansion 2.1 (2), arms 8.0, Z 4: "Each stratum's mean is weighted equally (not by fill count)". PDC1's
    270 fills at -0.05 R and PDC4's 30 at +0.5 R pool by fills to about +0.005 R (UB below +0.03 R: futile), but the
    equal-weight mean is about +0.22 R: no futility stop."""
    db = _db(tmp_path)
    _fills(db, "PDC1", 270, pnls=[-0.04 if i % 2 else -0.06 for i in range(270)], days=20)
    _fills(db, "PDC4", 30, pnls=[0.49 if i % 2 else 0.51 for i in range(30)], days=20, style="swing",
           start=START + pd.Timedelta(hours=8))
    st = _status(tmp_path, ["icm-demo"])
    assert st["PDC1"].band != "at_stop" and st["PDC4"].band != "at_stop"


def test_the_equal_weight_upper_bound_is_the_mean_of_the_stratum_means():
    d1, d2 = pd.Timestamp("2026-10-01", tz="UTC"), pd.Timestamp("2026-10-02", tz="UTC")
    vals, days, strata = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0], [d1, d2] * 4, ["a", "a", "a", "a", "a", "a", "b", "b"]
    ub = arm_stops.upper_bound(vals, days, strata)
    pooled = arm_stops.upper_bound(vals, days)
    assert ub is not None and pooled is not None
    assert ub > pooled and ub >= (1 / 6 + 0.5) / 2           # centre: (a 1/6 + b 1/2) / 2, not 2/8 by fills


def test_a_family_member_with_no_fills_is_retired_with_its_family_on_every_broker(tmp_path):
    """Quant 2 / trading-safety c: Z3V (vantage, stage V2) has no fill when Z3 stops on Z3D's: it must never trade
    inside a stopped family, so it is retired too."""
    db = _db(tmp_path)
    _fills(db, "Z3D", 30, style="scalp")
    _fills(db, "XIMNY", 30)
    retired = {(r.broker, r.arm_id) for r in _run(tmp_path).retired}
    assert {("vantage", "Z3V"), ("icm", "Z3V"), ("icm", "XIMLN"), ("icm", "Z3D"), ("icm", "XIMNY")} <= retired


def test_a_follower_with_no_fills_is_retired_with_its_arm(tmp_path):
    _fills(_db(tmp_path), "F1A", 30, host="icm-probe", style="scalp")
    assert ("icm", "F1C") in {(r.broker, r.arm_id) for r in _run(tmp_path).retired}


def test_wfri_stops_at_its_26th_friday_when_the_mean_is_at_or_below_minus_20_bp(tmp_path):
    """time-structure 4: "At 26 Fridays: safety check only. Stop if the mean <= -20 bp per Friday or the cumulative
    net <= -10 R." -3.2 at 0.01 lot on $2,000.1 at 0.75 = -21.3 bp net a Friday; -0.16 R each, far from H-61's -10 R."""
    db = _db(tmp_path)
    _fills(db, "WFRI", 25, style="swing", pnl=-3.2, stop_loss=20.0, days=25)
    assert _status(tmp_path, ["icm-demo"])["WFRI"].band != "at_stop"
    _fills(db, "WFRI", 1, style="swing", pnl=-3.2, stop_loss=20.0, start=START + pd.Timedelta(days=30), tag="26")
    st = _status(tmp_path, ["icm-demo"])["WFRI"]
    assert (st.band, st.stop_at) == ("at_stop", 26)
    assert {r.arm_id for r in _run(tmp_path).retired} == {"WFRI"}


def test_wfri_above_minus_20_bp_at_26_fridays_runs_on_and_keeps_h61s_swing_stop(tmp_path):
    db = _db(tmp_path)
    _fills(db, "WFRI", 26, style="swing", pnl=-2.7, stop_loss=20.0, days=26)          # -18 bp
    assert _status(tmp_path, ["icm-demo"])["WFRI"].band != "at_stop"
    _fills(db, "WFRI", 10, style="swing", pnl=-20.0, stop_loss=20.0, start=START + pd.Timedelta(days=40), tag="b")
    assert _status(tmp_path, ["icm-demo"])["WFRI"].band == "at_stop"                    # -10 R within 40


def test_a_broken_stop_rules_file_refuses_exploration_entries_under_its_own_reason(tmp_path, monkeypatch):
    from tests.test_exploration_engine import CLOSE, _arm, _at, _engine
    eng, pb = _engine(tmp_path, [_arm("s1")])
    bad = tmp_path / "arm_stops.yaml"
    bad.write_text("units: {A: {arms: [X], stop_r: 5}}\n")
    monkeypatch.setattr(arm_stops, "RULES_PATH", bad)
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    acts = [str(d.get("action")) for d in eng.decisions]
    assert "exploration_refused:stop_rules_unreadable" in acts
    assert "exploration_refused:ledger_unreadable" not in acts and not pb.positions()


def test_a_suspended_stratum_is_refused_every_entry_including_the_days_first(tmp_path, monkeypatch):
    from goldbot.engine import cost_probe
    from goldbot.research import arm_rules
    from tests.test_exploration_engine import CLOSE, LONG, LOSS, _arm, _at, _engine, _rule
    monkeypatch.setitem(arm_rules.RULE_FUNCS, LONG, _rule(1))
    monkeypatch.setattr(cost_probe, "stop_loss", lambda *a, **k: LOSS)
    eng, pb = _engine(tmp_path, [_arm("Z1P", style="scalp")])
    assert eng.exploration is not None
    _fills(eng.exploration.db, "Z1P", 30, host="icm-probe", style="scalp", pnl=0.5, cost=0.25, days=5,
           start=pd.Timestamp("2025-02-01 09:00", tz="UTC"))
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert not pb.positions()
    assert "exploration_refused:cost_suspended" in [str(d.get("action")) for d in eng.decisions]
