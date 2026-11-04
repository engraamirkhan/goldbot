"""Multi-instrument learning arms (docs/research/learning-mi-2026-11-02.md; owner 2026-11-02: "all listed instruments
live on DEMO", demo strategies as a K-counted learning class: ADR 0061 amendment, ADR 0072).

One opening-range breakout per instrument in the instrument's OWN session (`session_range_break` with
`session_local`), stop 2 x ATR(14) of its own 15m bars, 1.5 R, 16 bars (8 for the grains), each with a dedicated
seeded control (`session_range_control`: one entry per eligible session, the same sessions and bracket). All 26 are
in the spec and registered on the account that lists the instrument (research lead 2026-11-02), with the learning and
intraday slots raised to hold them; the host total, per-trade caps, margin and notional still bind. Blocked with
S0-/I0-<key> until lane L3 runs an arm on its own instrument.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.instruments import GOLD, arm_instrument
from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules_growth as G
from goldbot.research import arms

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "config" / "exploration_arms.yaml"
PREREG = "docs/research/learning-mi-2026-11-02.md"
# the pairs registered on 2026-11-02, by account, in the doc's rank order (section 5)
MI_1102: dict[str, tuple[str, ...]] = {
    "icm-probe": ("US500", "DE40", "XTIUSD", "EURUSD", "USDJPY", "JP225", "XAGUSD", "USTEC", "GBPUSD", "UK100", "XPTUSD",
                  "AUDUSD", "USDCAD", "XBRUSD", "USDCHF", "NZDUSD", "XNGUSD", "XAUEUR", "XAUAUD"),
    "vantage-demo": ("UST10Y", "BUND10Y", "COPPER", "WHEAT", "SOYBEAN", "COFFEE", "SUGAR")}
# owner decision 2026-11-04 (section 11): these instruments are no longer traded; their pairs are retired in the spec
# (rows, ids and K stay) and registered nowhere. The 10 added instruments' pairs are vantage-demo's, since 2026-11-04
REMOVED = {"XPTUSD", "XAUAUD", "JP225", "XBRUSD", "XNGUSD", "UST10Y", "BUND10Y", "COPPER", "WHEAT", "SOYBEAN", "SUGAR",
           "COFFEE"}
ADDED_1104 = ("GBPJPY", "EURGBP", "EURJPY", "AUDJPY", "EURAUD", "GBPAUD", "BTCUSD", "XAUJPY", "USINDEX", "US30")
# the pairs registered now, by account: every listed instrument
MI_LEARNING: dict[str, tuple[str, ...]] = {
    "icm-probe": tuple(k for k in MI_1102["icm-probe"] if k not in REMOVED), "vantage-demo": ADDED_1104}
SINCE = {**{k: "2026-11-02" for keys in MI_1102.values() for k in keys}, **{k: "2026-11-04" for k in ADDED_1104}}
GRAINS = {"WHEAT", "SOYBEAN"}                  # CBOT day session closes 13:20 Chicago: 8 bars
# every pair row in the spec (retired ones included: a row is never deleted)
MI_ARMS = {f"LB-{k}" for k in SINCE}
MI_CONTROLS = {f"LBC-{k}" for k in SINCE}
LIVE_PAIRS = {f"{g}-{k}" for keys in MI_LEARNING.values() for k in keys for g in ("LB", "LBC")}
# account -> the pair ids registered there (blocked until lane L3: the engine drops them, logged)
MI_ON: dict[str, set[str]] = {acc: {f"{g}-{k}" for k in keys for g in ("LB", "LBC")} for acc, keys in MI_LEARNING.items()}
MAX_LOSS = 8.0


@pytest.fixture(autouse=True)
def _today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arms, "_now", lambda: pd.Timestamp("2026-11-02", tz="UTC"))


def _spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}


def _k_script() -> Any:
    import importlib.util
    mod_spec = importlib.util.spec_from_file_location("record_learning_k_rows",
                                                      ROOT / "scripts" / "record_learning_k_rows.py")
    assert mod_spec is not None and mod_spec.loader is not None
    mod = importlib.util.module_from_spec(mod_spec)
    mod_spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------------------------------------ the spec rows
def test_the_spec_loads_with_every_hash_and_holds_exactly_the_registered_pairs() -> None:
    spec = _spec()                                       # load_arms checks every config_hash
    mi = {i for i, a in spec.items() if arm_instrument(a.params) != GOLD and i.startswith(("LB-", "LBC-"))}
    assert mi == MI_ARMS | MI_CONTROLS
    for arm_id in MI_ARMS:
        a = spec[arm_id]
        assert (a.rule, a.timeframe, a.role, a.max_open) == ("session_range_break", "15m", "learning", 1)
        assert a.params["stop_atr"] == 2.0 and a.params["target_r"] == 1.5 and "stop_usd" not in a.params
        assert a.params["hold_bars"] == (8 if a.params["instrument"] in GRAINS else 16)
        assert set(a.params["session_local"]) == G.SESSION_LOCAL_KEYS


def test_every_learning_row_resolves_its_own_matched_control_on_the_same_instrument_and_session() -> None:
    spec = _spec()
    for arm_id in MI_ARMS:
        a = spec[arm_id]
        block = arms.Learning.model_validate(a.learning)
        c = spec[block.control]
        assert block.control == "LBC-" + arm_id.removeprefix("LB-")
        assert arms.control_match_refusal(a, c) is None
        assert c.rule == "session_range_control" and c.role == "control" and c.forward_only
        assert arms.dedicated_control_of(c) == (arm_id,)
        same = {k: v for k, v in c.params.items() if k != "control"}
        assert same == a.params                                       # instrument, session, bracket: identical


def test_each_learning_block_is_preregistered_with_its_own_k_row_a_45_day_end_and_twenty_times_its_cap() -> None:
    spec = _spec()
    accounts = acc_mod.load_accounts(logins=False)
    text = (ROOT / PREREG).read_text()
    on = {k: acc_id for acc_id, keys in MI_LEARNING.items() for k in keys}
    for key, since in SINCE.items():
        a = spec[f"LB-{key}"]
        block = arms.Learning.model_validate(a.learning)
        assert block.prereg == PREREG and (block.since, block.until) == (since, "2026-12-17")
        assert block.k_id == arms.learning_k_id(a.id, arms.arm_hash(a), PREREG)
        cap = accounts[on[key]].exploration.arms[a.id].max_loss if key in on else MAX_LOSS   # a removed one: nowhere
        assert cap == MAX_LOSS and block.loss_cap_gbp == pytest.approx(20 * cap)
        c = spec[block.control]
        assert a.id in text and block.k_id in text and c.id in text
        assert arms.learning_k_id(c.id, arms.arm_hash(c), PREREG) in text


def test_the_pairs_run_now_lane_l3_routes_each_to_its_own_instrument() -> None:
    """Lane L3's engine runs a non-gold row on its own instrument's book only, so both rows of a pair run (status is
    not hashed: the unblock changes no K row); each routes to its own instrument, never gold."""
    from goldbot.engine import exploration as X
    spec = _spec()
    for arm_id in MI_ARMS:
        key = arm_id.removeprefix("LB-")
        c = spec["LBC-" + key]
        assert spec[arm_id].status == c.status == "run"
        assert "L3" in spec[arm_id].note and "L3" in c.note
        for row in (spec[arm_id], c):
            arm = X.Arm(spec=row, style="intraday", control_share=0.02 if row is c else None)
            cfg = X.ExplorationConfig(enabled=True, pots={"intraday": X.Pot(max_open=1)}, arms=(arm,),
                                      pot_budgets=False)
            own, gold = X.book_config(cfg, key), X.book_config(cfg, "XAUUSD")
            assert arm.instrument == key and own is not None and gold is not None
            assert own.arms == (arm,) and gold.arms == ()


def test_every_listed_instrument_has_its_pair_in_the_spec_and_in_the_docs_table() -> None:
    accs = acc_mod.load_accounts(logins=False)
    listed = {k for a in accs.values() for k in a.instruments}
    registered = {k for keys in MI_LEARNING.values() for k in keys}
    assert len(listed) == 24 and registered == listed
    text = (ROOT / PREREG).read_text()
    for key in listed:                     # the 2026-11-02 pairs in section 5, the 2026-11-04 ones in section 11
        section = text.split("## 11." if key in ADDED_1104 else "## 5.")[1].split("\n## ")[0]
        assert f"| {key} |" in section and f"LB-{key}" in section


# ------------------------------------------------------------------------------------------------ the accounts
def test_each_pair_is_registered_on_the_demo_account_that_lists_its_instrument() -> None:
    accs = acc_mod.load_accounts(logins=False)
    for acc_id, keys in MI_LEARNING.items():
        acc = accs[acc_id]
        assert acc.mode == "demo" and acc.probe_only and acc.exploration.learning_max_open >= 1
        for key in keys:
            assert key in acc.instruments
            arm, ctl = acc.exploration.arms[f"LB-{key}"], acc.exploration.arms[f"LBC-{key}"]
            assert arm.style == ctl.style == "intraday" and arm.style in acc.exploration.pots
            assert arm.sizing == "min_lot" and arm.adds == 0 and arm.control_share is None
            assert ctl.control_share is not None and ctl.max_loss == arm.max_loss


def test_no_pair_lands_on_a_live_or_observe_only_account_or_another_brokers() -> None:
    every = MI_ARMS | MI_CONTROLS
    for acc in acc_mod.load_accounts(logins=False).values():
        mine = every & set(acc.exploration.arms)
        if acc.mode != "demo" or acc.kind != "trading" or not acc.probe_only:
            assert not mine, acc.account_id
        for arm_id in mine:
            assert arm_id.split("-", 1)[1] in acc.instruments, (acc.account_id, arm_id)


def test_only_slot_counts_were_raised_for_the_pairs_and_every_cap_still_binds() -> None:
    """Research lead 2026-11-02: icm-probe learning_max_open 6 -> 14 and intraday pot 4 -> 12, vantage-demo intraday
    pot 6 -> 10. The host total (23 of 24), the per-trade caps, the day, the margin floor and the notional ceiling are
    unchanged; the read reports each arm's slot refusals as a bias diagnostic (section 7)."""
    accs = acc_mod.load_accounts(logins=False)
    probe, van = accs["icm-probe"], accs["vantage-demo"]
    assert (probe.exploration.learning_max_open, probe.exploration.pots["intraday"].max_open) == (14, 12)
    assert (van.exploration.learning_max_open, van.exploration.pots["intraday"].max_open) == (6, 10)
    for acc in (probe, van):
        ex = acc.exploration
        assert ex.max_open_total == 23 and acc.max_positions == 24 and not ex.pot_budgets
        assert all(ex.arms[i].max_loss == MAX_LOSS for i in MI_ON[acc.account_id])
    assert (probe.daily_loss_limit_abs, van.daily_loss_limit_abs) == (220, 200)


def test_each_arm_carries_h61s_intraday_stop_and_each_control_none_of_its_own() -> None:
    from goldbot.research.arm_stops import load_stop_rules
    rules = load_stop_rules()
    assert MI_ARMS <= set(rules.h61) and MI_CONTROLS <= set(rules.none)
    assert not (MI_ARMS | MI_CONTROLS) & {a for u in rules.units.values() for a in u.arms}


# ------------------------------------------------------------------------------------------------ the K rows
def test_the_k_row_script_writes_one_row_per_pair_member_with_the_others(tmp_path) -> None:
    rows = _k_script().k_rows(SPEC)
    mi = {r["arm"] for r in rows.values() if r["arm"] in MI_ARMS | MI_CONTROLS}
    assert mi == MI_ARMS | MI_CONTROLS
    assert sum(1 for r in rows.values() if r["config"]["prereg"] == PREREG) == 72      # 52 (11-02) + 20 (11-04)
    for h, r in rows.items():
        assert h == arms.learning_k_id(r["arm"], r["config"]["arm_config_hash"], r["config"]["prereg"])


def test_with_their_k_rows_the_pairs_pass_now_lane_l3_runs_them(tmp_path, monkeypatch) -> None:
    """Lane L3 set both rows of every pair to run (nothing hashed): with the K rows in, nothing refuses a pair of a
    traded instrument; a removed instrument's pair (retired 2026-11-04) stays refused."""
    monkeypatch.setattr(arms, "_now", lambda: pd.Timestamp("2026-11-04", tz="UTC"))   # the added pairs' first day
    reg = tmp_path / "research_registry.jsonl"
    reg.write_text("".join(json.dumps({"config_hash": h, "family": arms.LEARNING_FAMILY}) + "\n"
                           for h in _k_script().k_rows(SPEC)))
    monkeypatch.setattr(arms, "REGISTRY_PATH", reg)
    got = {a.id: a for a in arms.load_arms(SPEC, root=ROOT, registry=reg)}
    for arm_id in LIVE_PAIRS:
        assert got[arm_id].entry_refused == "" and got[arm_id].status == "run", (arm_id, got[arm_id].entry_refused)
    for arm_id in (MI_ARMS | MI_CONTROLS) - LIVE_PAIRS:
        assert got[arm_id].retired_day == "2026-11-04", arm_id


def test_without_their_k_rows_the_pairs_are_refused_fail_closed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(arms, "REGISTRY_PATH", tmp_path / "missing.jsonl")
    spec = _spec()
    for arm_id in MI_ARMS | MI_CONTROLS:
        assert spec[arm_id].entry_refused.startswith(arms.LEARNING_RULE), arm_id


def test_the_budget_report_counts_the_rows_still_missing_against_the_quarter(tmp_path) -> None:
    mod = _k_script()
    reg = tmp_path / "research_registry.jsonl"
    report = mod.budget_report(reg, SPEC, cap=3500, quarter="2026Q4")
    assert report["missing"] == len(mod.k_rows(SPEC)) and report["used"] == 0
    assert report["fits"] and report["cap"] == 3500 and report["quarter"] == "2026Q4"
    assert report["by_prereg"] == {"docs/research/learning-2026-11-02.md": 30, PREREG: 72}
    assert report["left_after"] == 3500 - 102
    assert mod.budget_report(reg, SPEC, cap=10, quarter="2026Q4")["fits"] is False


# ------------------------------------------------------------------------------------------------ the rule
def _bars(start: str, n: int, close: np.ndarray) -> pd.DataFrame:
    ts = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    c = np.asarray(close, dtype=float)
    return pd.DataFrame({"ts_utc": ts, "open": c, "high": c + 0.1, "low": c - 0.1, "close": c})


def _run(arm: arms.Arm, m: pd.DataFrame) -> pd.DataFrame:
    class Ctx:
        mid = m
        close_ts = pd.DatetimeIndex(m["ts_utc"]) + pd.Timedelta(minutes=15)
        blackout_lo = np.zeros(0, dtype=np.int64)
        blackout_hi = np.zeros(0, dtype=np.int64)
    return (G.session_range_control if arm.rule == "session_range_control" else G.session_range_break)(arm, Ctx())  # type: ignore[arg-type]


def _arm(rule: str = "session_range_break", **extra: Any) -> arms.Arm:
    p: dict[str, Any] = {"instrument": "US500", "session_local": {"zone": "America/New_York", "range_from": "09:30",
                         "range_to": "10:30", "break_to": "12:30"}, "stop_atr": 2.0, "target_r": 1.5, "hold_bars": 16,
                         **extra}
    if rule == "session_range_control":
        p.setdefault("control", "learning:LB-T")
    return arms.Arm(id="LB-T" if rule == "session_range_break" else "LBC-T", rule=rule, timeframe="15m", params=p)


def test_the_own_session_breakout_takes_the_first_close_beyond_the_range_inside_the_break_window() -> None:
    # Tuesday 2026-11-03 (EST): the range is 14:30-15:30 UTC, the break window closes in (15:30, 17:30] UTC
    n = 96
    close = np.full(n, 100.0)
    start = pd.Timestamp("2026-11-03 00:00", tz="UTC")
    i_break = int((pd.Timestamp("2026-11-03 16:00", tz="UTC") - start) / pd.Timedelta(minutes=15))
    close[i_break:] = 101.0                                            # above the range's high (100.1)
    close[i_break + 2:] = 103.0                                        # a later, bigger close: never a second entry
    out = _run(_arm(), _bars("2026-11-03 00:00", n, close))
    assert len(out) == 1 and int(out["idx"].iloc[0]) == i_break and int(out["side"].iloc[0]) == 1
    assert out["target_r"].iloc[0] == 1.5 and out["max_bars"].iloc[0] == 15


def test_a_break_before_the_range_ends_or_after_the_window_never_enters() -> None:
    n = 96
    start = pd.Timestamp("2026-11-03 00:00", tz="UTC")
    late = int((pd.Timestamp("2026-11-03 17:45", tz="UTC") - start) / pd.Timedelta(minutes=15))
    close = np.full(n, 100.0)
    close[late:] = 101.0
    assert _run(_arm(), _bars("2026-11-03 00:00", n, close)).empty


def test_the_control_enters_once_per_eligible_session_at_its_seeded_close_inside_the_window() -> None:
    n = 96 * 3
    out = _run(_arm("session_range_control"), _bars("2026-11-03 00:00", n, np.full(n, 100.0)))
    m = _bars("2026-11-03 00:00", n, np.full(n, 100.0))
    close = pd.DatetimeIndex(m["ts_utc"]) + pd.Timedelta(minutes=15)
    assert len(out) == 3                                               # Tue, Wed, Thu
    for i in out["idx"]:
        t = close[int(i)].tz_convert("America/New_York")
        assert (t.hour, t.minute) > (10, 30) and (t.hour, t.minute) <= (12, 30)


def test_the_session_follows_its_own_citys_clock_across_the_dst_switch() -> None:
    a = _arm()
    sessions = G.arm_sessions(a.params)
    assert [s for s, _ in sessions] == [G.OWN]
    summer = G._bounds(pd.Timestamp("2026-10-27", tz="UTC"), sessions[0][1])
    winter = G._bounds(pd.Timestamp("2026-11-03", tz="UTC"), sessions[0][1])
    assert summer[0].hour == 13 and winter[0].hour == 14                # 09:30 New York: EDT then EST


@pytest.mark.parametrize("bad, why", [
    ({"sessions": ["london_ny"]}, "not both"),
    ({"session_local": {"zone": "Mars/Base", "range_from": "09:30", "range_to": "10:30", "break_to": "12:30"}},
     "unknown time zone"),
    ({"session_local": {"zone": "America/New_York", "range_from": "09:35", "range_to": "10:30", "break_to": "12:30"}},
     "15m grid"),
    ({"session_local": {"zone": "America/New_York", "range_from": "09:30", "range_to": "09:45", "break_to": "12:30"}},
     "at least"),
    ({"session_local": {"zone": "America/New_York", "range_from": "10:30", "range_to": "09:30", "break_to": "12:30"}},
     "range_from < range_to"),
    ({"session_local": {"zone": "America/New_York", "range_from": "09:30", "range_to": "10:30"}}, "exactly"),
    # trading-safety review of #310, L4: 02:30 New York does not exist on the spring-forward Sunday
    ({"session_local": {"zone": "America/New_York", "range_from": "02:30", "range_to": "04:00", "break_to": "06:00"}},
     "does not exist"),
    ({"session_local": {"zone": "Europe/London", "range_from": "00:00", "range_to": "01:15", "break_to": "03:00"}},
     "does not exist"),
])
def test_a_malformed_own_session_is_refused_at_load(bad: dict[str, Any], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        _arm(**bad)


def test_gs1_and_gs1c_keep_their_named_sessions_rows_and_hashes() -> None:
    spec = _spec()
    for arm_id, h in (("GS1", "04d238be1c1b9169"), ("GS1C", "4be30e28168b698f")):
        assert "session_local" not in spec[arm_id].params and arms.arm_hash(spec[arm_id]) == h
        assert [s for s, _ in G.arm_sessions(spec[arm_id].params)] == ["asia_london", "london_ny"]
