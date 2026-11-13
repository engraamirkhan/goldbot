"""The kill-only config of 2026-11-07 (docs/research/prereg-amendment-2026-11-07-kill-only.md, plan WS-1): probes on
icm-probe and vantage-demo only in [07:00, 21:00) UTC (Vantage every 60 min), S0-XAUJPY retired with its exits kept,
the ICM commission prior at $4.0 read from settings, and an ARM-SPEC row with a bad hash refused, never fatal."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from goldbot.data.resample import ticks_to_1m
from goldbot.engine import cost_probe, exploration
from goldbot.execution.broker import Tick
from goldbot.ops import accounts as acc_mod
from goldbot.research.arms import GOLD_ONLY_RULE, arm_hash, load_arms
from tests.test_exploration_engine import (
    CLOSE,
    TICKS,
    _arm,
    _at,
    _engine,
    _explore,
    _probe_account,
    _sent,
    qualified,
)
from tests.test_exploration_engine import (
    _rules as _rules,  # noqa: F401  (autouse: the test arms' fixed rules)
)
from tests.test_probe_scheduled import _allow, _bars_until, _scheduled
from tests.test_probe_scheduled import _engine as _probe_engine

ROOT = Path(__file__).parents[1]
SPEC = ROOT / "config" / "exploration_arms.yaml"
DAY = pd.Timestamp("2025-03-04", tz="UTC")                   # a Tuesday
HOURS = ("07:00", "21:00")


# ------------------------------------------------------------------------------------------------ probe hours
@pytest.mark.parametrize("hms, outside", [("06:59:59", True), ("07:00:00", False), ("20:59:59", False),
                                          ("21:00:00", True), ("03:00:00", True)])
def test_probes_send_only_from_0700_inclusive_to_2100_exclusive_utc(tmp_path, hms, outside):
    eng, _, _ = _probe_engine(tmp_path)
    eng.cfg.cost_probe_hours_utc = HOURS
    assert cost_probe.outside_hours(eng, DAY + pd.Timedelta(hms)) is outside


def test_without_probe_hours_every_hour_stays_open(tmp_path):
    eng, _, _ = _probe_engine(tmp_path)
    assert eng.cfg.cost_probe_hours_utc is None and not cost_probe.outside_hours(eng, DAY + pd.Timedelta("03:00:00"))


def test_a_scheduled_probe_outside_the_hours_is_skipped_and_journalled_and_inside_it_is_sent(tmp_path):
    eng, pb, tick = _probe_engine(tmp_path)
    eng.cfg.cost_probe_hours_utc = HOURS
    _allow(eng)
    assert eng.probe is not None
    early = DAY + pd.Timedelta("06:45:00")
    assert cost_probe.schedule_skip(eng, early) == "hours"
    cost_probe.scheduled_step(eng, eng.probe, early, tick)
    assert not _scheduled(eng)
    assert "cost_probe_scheduled_skip:hours" in [d.get("action") for d in eng.decisions]
    inside = DAY + pd.Timedelta("20:00:00")
    eng.on_bar_close(inside, _bars_until(eng, pb, inside))
    assert len(_scheduled(eng)) == 1


def test_a_mirror_probe_outside_the_hours_is_not_sent_and_is_journalled(tmp_path, monkeypatch):
    eng, _, tick = _probe_engine(tmp_path, shadow=True, scheduled=acc_mod.ScheduledProbe())
    eng.cfg.cost_probe_hours_utc = HOURS
    assert eng.probe is not None
    sent: list[object] = []
    monkeypatch.setattr(cost_probe, "_signals", lambda *_: {"agent": ("v2", object(), object())})
    monkeypatch.setattr(cost_probe, "_send", lambda *a, **k: sent.append(a))
    cost_probe._step(eng, eng.probe, DAY + pd.Timedelta("06:45:00"), tick)
    assert not sent and "cost_probe_skip:hours" in [d.get("action") for d in eng.decisions]


def test_an_open_probe_keeps_its_hold_exit_outside_the_hours(tmp_path):
    eng, pb, _ = _probe_engine(tmp_path)
    _allow(eng)
    late = DAY + pd.Timedelta("20:45:00")
    eng.on_bar_close(late, _bars_until(eng, pb, late))
    [pid] = _scheduled(eng)
    eng.cfg.cost_probe_hours_utc = ("07:00", "20:45")         # the window has closed behind the open probe
    opened = eng.open[pid].opened_utc
    assert opened is not None
    eng._retry_closes(opened + pd.Timedelta(seconds=31))
    assert pid not in eng.open and not pb.positions()


@pytest.mark.parametrize("hours", [("21:00", "07:00"), ("7:00", "21:00"), ("07:00", "24:00")])
def test_probe_hours_that_are_not_a_forward_hhmm_window_are_refused(hours):
    with pytest.raises(ValidationError):
        acc_mod.CostProbe(enabled=True, hours_utc=hours)


def _slots(acc: acc_mod.Account, day: pd.Timestamp) -> list[pd.Timestamp]:
    """The 15m closes of `day` a scheduled probe of `acc` may take: its cadence, its hours and its rollover blackout."""
    sch, (a, b) = acc.cost_probe.scheduled, acc.cost_probe.hours_utc or ("00:00", "24:00")
    out = []
    for ts in pd.date_range(day, periods=96, freq="15min"):
        hm, (lo, hi) = ts.tz_convert(sch.blackout_tz).strftime("%H:%M"), sch.blackout
        if cost_probe.scheduled_slot(ts, sch.every_min) and a <= ts.strftime("%H:%M") < b and not lo <= hm < hi:
            out.append(ts)
    return out


def test_the_deployed_probe_accounts_send_probes_only_in_0700_2100_utc_vantage_one_an_hour():
    """Amendment 10.5: no scheduled or mirror probe outside [07:00, 21:00) UTC on vantage-demo and icm-probe; one per
    60 min inside it on vantage-demo (icm-probe keeps the CFO's 120: fewer probes only, never more)."""
    accs = acc_mod.load_accounts(logins=False)
    vd, ip = accs["vantage-demo"].cost_probe, accs["icm-probe"].cost_probe
    assert vd.hours_utc == ip.hours_utc == HOURS
    assert vd.scheduled.every_min == 60 and ip.scheduled.every_min == 120
    slots = _slots(accs["vantage-demo"], DAY)
    assert len(slots) == 14 and all(7 <= ts.hour < 21 for ts in slots)
    assert len({ts.floor("h") for ts in slots}) == 14                      # one per hour
    assert all(7 <= ts.hour < 21 for ts in _slots(accs["icm-probe"], DAY))
    assert accs["icm-demo"].cost_probe.hours_utc is None                   # not in the amendment's scope


# ------------------------------------------------------------------------------------------------ S0-XAUJPY
def test_s0_xaujpy_is_retired_in_the_spec_with_its_id_hash_and_k_unchanged():
    rows = {a.id: a for a in load_arms(SPEC, check_hash=True)}                # the deployed file loads hash-checked
    s0 = rows["S0-XAUJPY"]
    assert s0.retired and s0.retired_day == "2026-11-07" and "prereg-amendment-2026-11-07" in s0.retired_rule
    assert s0.config_hash == arm_hash(s0) == "a54aae448d55ac3d"
    # its learning pair was kept by the 2026-11-07 amendment and retired 2026-11-14 by the gold-only ruling (2026-11-13)
    assert all(rows[i].retired_day == "2026-11-14" and rows[i].retired_rule == GOLD_ONLY_RULE
               for i in ("LB-XAUJPY", "LBC-XAUJPY"))


def test_s0_xaujpy_stays_configured_on_vantage_demo_so_its_open_positions_keep_their_exits():
    acc = acc_mod.load_accounts(logins=False)["vantage-demo"]
    cfg = exploration.config_for(acc)
    assert cfg is not None
    book = exploration.book_config(cfg, "XAUJPY")
    assert book is not None and "S0-XAUJPY" in {a.arm_id for a in book.arms}
    assert "S0-XAUJPY" in exploration.spec_retired(book)


def test_a_retired_arm_with_an_open_position_keeps_its_hold_exit_and_sends_nothing_new(tmp_path):
    arm = _arm("s", tf="1m", style="scalp", hold_bars=5)
    eng, pb = _engine(tmp_path, [arm])
    minute = CLOSE + pd.Timedelta(minutes=1)
    tick = _at(eng, pb, CLOSE)
    eng.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < minute])
    exploration.on_pass(eng, Tick(ts_utc=minute + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask))
    [pid] = list(eng.open)
    retired = arm.model_copy(update={"spec": arm.spec.model_copy(update={"retired_day": "2026-11-07",
                                                                         "retired_rule": "test"})})
    eng2, _ = _engine(tmp_path, [retired], pb=pb)                        # the restart after the retiring deploy
    eng2.open.clear()
    eng2._reconcile(tick.ts_utc)
    assert eng2.open[pid].agent_id == "explore:s"                          # adopted, never closed as an orphan
    nxt = minute + pd.Timedelta(minutes=1)
    eng2.bars_1m = ticks_to_1m(TICKS[TICKS["ts_utc"] < nxt])
    eng2._last_tick = Tick(ts_utc=nxt + pd.Timedelta(seconds=2), bid=tick.bid, ask=tick.ask)
    exploration.on_pass(eng2, eng2._last_tick)
    assert not _sent(eng2) and list(eng2.open) == [pid]
    opened = eng2.open[pid].opened_utc
    assert opened is not None
    eng2._retry_closes(opened + pd.Timedelta(seconds=300))
    assert pid not in eng2.open and not pb.positions()


# ------------------------------------------------------------------------------------------------ commission
def test_the_icm_commission_prior_is_four_dollars_a_lot_a_side_read_from_settings():
    from goldbot.config import load_settings
    assert load_settings().costs.commission_per_lot_side_usd == {"icm": 4.0, "vantage": 3.0}


def test_the_engine_and_the_nightly_cost_table_read_the_commission_from_settings_not_a_literal():
    for rel in ("goldbot/ops/services/engine.py", "goldbot/ops/jobs.py"):
        assert "settings.costs.commission_per_lot_side_usd.get(acc.broker" in (ROOT / rel).read_text(), rel


# ------------------------------------------------------------------------------------------------ a bad hash row
def _spec_with_bad_hash(tmp_path: Path, bad: str) -> Path:
    raw = yaml.safe_load(SPEC.read_text())
    for row in raw["arms"]:
        if row["id"] == bad:
            row["params"] = {**row["params"], "hold_bars": int(row["params"]["hold_bars"]) + 1}   # edited after freeze
    path = tmp_path / "arms.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    return path


def _two_arm_account() -> acc_mod.Account:
    return _probe_account(exploration=_explore(
        arms={"S0": acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.25),
              "S0R": acc_mod.ExplorationArm(max_loss=6.5, style="scalp", control_share=0.25)},
        pots={"scalp": acc_mod.ExplorationPot(gbp=10, max_open=8)}))


def test_a_spec_row_with_a_bad_hash_does_not_abort_the_config_and_is_kept_with_its_entries_refused(tmp_path):
    path = _spec_with_bad_hash(tmp_path, "S0R")
    with pytest.raises(ValueError, match="config_hash"):
        load_arms(path)                                         # the research loader still refuses the file
    cfg = exploration.config_for(_two_arm_account(), spec_path=path, spec_arms_path=tmp_path / "none.yaml")
    assert cfg is not None and [a.arm_id for a in cfg.arms] == ["S0", "S0R"] and cfg.hash_refused == ("S0R",)
    refused = exploration.entry_refused(cfg)
    assert set(refused) == {"S0R"} and refused["S0R"].startswith(exploration.HASH_RULE)


def test_the_engine_starts_journals_the_bad_hash_arm_and_runs_the_others(tmp_path):
    good, bad = qualified(_arm("good")), qualified(_arm("bad"))
    bad = bad.model_copy(update={"spec": bad.spec.model_copy(update={
        "entry_refused": f"{exploration.HASH_RULE}: config_hash 'x' != 'y'"})})
    eng, pb = _engine(tmp_path, [good, bad], qualify=False)
    assert eng.exploration is not None
    eng.exploration.cfg = eng.exploration.cfg.model_copy(update={"hash_refused": ("bad",)})
    fresh = exploration.Exploration(eng, eng.exploration.cfg)     # the start's journal, as the engine builds it
    eng.exploration = fresh
    rows = [d for d in eng.decisions if d.get("action") == "exploration_arm_hash_mismatch"]
    assert [d["arm"] for d in rows] == ["bad"] and rows[0]["rule"].startswith(exploration.HASH_RULE)
    assert "bad" in fresh.retired and "bad" in fresh.arms               # configured: its exits run
    exploration.on_pass(eng, _at(eng, pb, CLOSE))
    assert [d["agent"] for d in _sent(eng)] == ["explore:good"]


def test_one_bad_hash_row_leaves_the_spec_rule_families_resolvable_and_is_not_runnable(tmp_path):
    """A bad-hash row never makes the family loader raise (which would drop every spec_rule arm and orphan-close its
    positions); the bad row is left out of the runnable set, every other row resolves."""
    from goldbot.research import arm_rules_spec
    path = _spec_with_bad_hash(tmp_path, "S0R")
    rows = {a.id for a in arm_rules_spec._family_arms(str(path))}
    assert "S0R" not in rows and "S0" in rows and len(rows) == len(load_arms(SPEC)) - 1
