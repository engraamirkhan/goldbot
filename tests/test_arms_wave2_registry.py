"""Arms expansion wave 2 (docs/research/arms-2026-10-25.md section 8 with amendments 8.13 and 8.14; the research
director's decisions, arms-expansion-2026-10-26.md section 11): the shipped arm spec and account registry carry every
wave-2 arm, each frozen by its hash under the rule that implements it, on the host and pot 8.11/8.13 name, at the
per-trade cap of its timeframe ("15m £17, 1h £30, swing £40 on shared hosts, scalp £6.50"). Section 11 decision 3:
"the swing pot goes from max_open 1 to 2 on icm-demo and vantage-demo for 4h arms. The £40 cap and the £90 / £82 pots
are unchanged." A row whose live path is still owed by an engine lane is `blocked` with a note naming that lane; a
forward-only arm (time-structure prereg, KA "forward-only", H-80's untouched 2018+ half) never runs on history."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from goldbot.ops import accounts as acc_mod
from goldbot.research import arm_rules, arms

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
BOX = {f"BX{e}{tf}": ("box_next", tf.replace("H", "h") if tf.endswith("H") else f"{tf}m")
       for e in "RTF" for tf in ("15", "30", "1H", "4H")}
# arm id -> (rule, timeframe)
WAVE2 = {"F1A": ("hurst_band_break", "15m"), "F1C": ("hurst_band_break_control", "15m"),
         "F2A": ("htf_stretch_break", "15m"), "F2C": ("htf_stretch_break_control", "15m"),
         "G1A": ("level_break", "1h"), "G1C": ("level_break", "1h"),
         "PDC1": ("pdc_break", "1h"), "PDC4": ("pdc_break", "4h"), "OBR15": ("ob_retest", "15m"),
         "KSQ1": ("squeeze_release", "1h"), "KSQ4": ("squeeze_release", "4h"),
         "DON4": ("donchian_break", "4h"), "DOND": ("donchian_break", "1d"),
         "C30A": ("low30_touch", "15m"), "C30C": ("low30_control", "15m"),
         "WFRI": ("friday_long", "30m"), "KA15": ("clock_anchor", "15m"), "XC15": ("clock_anchor_control", "15m"),
         **BOX}
# blocked row -> the engine lane its note names (state/RESUME.md 2026-10-26: E1 F1/F2 live + WFRI crosses_friday;
# E2 G1 level_break + C30/DOND daily and weekend; E3 BXR trail, BXT/BXF virtual twins, XC15 netting shift)
# lanes E1-E3 merged 2026-10-26; DOND unblocked by ARM-SPEC 8.15 (its exit runs under the Friday cut, as WFRI's)
BLOCKED: dict[str, str] = {}
FORWARD_ONLY = {"F1A", "F1C", "G1A", "G1C", "WFRI", "KA15", "XC15", *BOX}
# account -> arm id -> (style, max_loss, control_share)
HOSTS: dict[str, dict[str, tuple[str, float, float | None]]] = {
    "icm-probe": {"KA15": ("scalp", 6.5, None), "XC15": ("scalp", 6.5, 0.10),
                  "F1A": ("scalp", 6.5, None), "F1C": ("scalp", 6.5, 0.05)},
    "icm-demo": {"F2A": ("intraday", 17.0, None), "F2C": ("intraday", 17.0, 0.05), "OBR15": ("intraday", 17.0, None),
                 "G1A": ("intraday", 17.0, None), "G1C": ("intraday", 17.0, 0.05),
                 "BXR15": ("intraday", 17.0, None), "BXR30": ("intraday", 17.0, None),
                 "BXR1H": ("intraday", 30.0, None), "BXR4H": ("swing", 40.0, None),
                 "PDC4": ("swing", 40.0, None), "C30A": ("swing", 40.0, None), "C30C": ("swing", 40.0, 0.05),
                 "WFRI": ("swing", 40.0, None)},
    "vantage-demo": {"KSQ1": ("intraday", 30.0, None), "PDC1": ("intraday", 30.0, None),
                     "DON4": ("swing", 40.0, None)},
}
# the stop band's top is the per-trade cap at 0.01 lot ($1.329 per £): the arm's $ band, never wider than its cap
BAND_TOP: dict[str | float, float] = {"scalp": 8.64, 17.0: 22.6, 30.0: 39.9, 40.0: 53.2}
PLACEHOLDERS = ("band_hurst_switch", "weekday_hold")


@pytest.fixture(scope="module")
def spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}            # hash-checked on load


@pytest.fixture(scope="module")
def accounts() -> dict[str, acc_mod.Account]:
    return acc_mod.load_accounts(logins=False)


def test_every_wave_two_arm_is_frozen_under_the_rule_that_implements_it(spec) -> None:
    assert set(WAVE2) <= set(spec)
    for arm_id, (rule, tf) in WAVE2.items():
        a = spec[arm_id]
        assert (a.rule, a.timeframe, a.max_open) == (rule, tf, 1), arm_id
        assert a.config_hash == arms.arm_hash(a)


def test_every_runnable_arm_resolves_to_a_rule_function(spec) -> None:
    unresolved = sorted(i for i, a in spec.items() if a.status == "run" and a.rule not in arm_rules.RULE_FUNCS)
    assert unresolved == []


def test_rows_blocked_on_an_engine_lane_name_it_and_every_other_wave_two_arm_runs(spec) -> None:
    for arm_id in WAVE2:
        a = spec[arm_id]
        if arm_id in BLOCKED:
            assert a.status == "blocked" and BLOCKED[arm_id] in a.note, arm_id
        else:
            assert a.status == "run", arm_id


def test_pdc4_and_dond_are_frozen_under_their_exit_amendment_and_follow_the_friday_cut(spec) -> None:
    """ARM-SPEC 8.15: the amended exit is part of the frozen row (a new hash), and only PDC4 / DOND (and WFRI) follow
    the cut; PDC1 and DON4 keep their frozen holds and the plain weekend refusal."""
    for arm_id in ("PDC4", "DOND"):
        p = spec[arm_id].params
        assert "8.15" in p["spec"] and "8.15" in p["weekend_cut"], arm_id
        assert arm_rules.follows_friday_cut(spec[arm_id]), arm_id
    assert {i for i, a in spec.items() if arm_rules.follows_friday_cut(a)} == {"PDC4", "DOND", "WFRI"}
    assert "weekend_cut" not in spec["PDC1"].params and "weekend_cut" not in spec["DON4"].params


def test_no_row_keeps_a_placeholder_rule_and_the_placeholders_are_gone(spec) -> None:
    assert not {a.rule for a in spec.values()} & set(PLACEHOLDERS)
    assert not set(PLACEHOLDERS) & set(arms.RULES)
    assert "level_break" in arms.RULES        # G1A/G1C's generator (lane E2)


def test_forward_only_arms_never_run_on_history(spec) -> None:
    assert {i for i, a in spec.items() if a.forward_only} == FORWARD_ONLY
    runnable = set(arms.runnable_ids(SPEC))
    assert not FORWARD_ONLY & runnable
    assert {"PDC1", "PDC4", "OBR15", "KSQ1", "KSQ4", "DON4"} <= runnable


def test_a_forward_only_arm_keeps_its_hash_whatever_the_flag(spec) -> None:
    a = spec["KA15"]
    assert arms.arm_hash(a) == arms.arm_hash(a.model_copy(update={"forward_only": False}))


def test_the_frozen_holds_and_bands_match_the_spec(spec) -> None:
    holds = {"KSQ1": 4, "KSQ4": 4, "DON4": 24, "DOND": 10, "C30A": 460, "C30C": 460, "F2A": 4, "F2C": 4, "F1A": 4,
             "F1C": 4, "PDC1": 32, "PDC4": 32, "OBR15": 32, "KA15": 2, "XC15": 2}
    for arm_id, hold in holds.items():
        assert spec[arm_id].params["hold_bars"] == hold, arm_id
    tops = {"KSQ1": 39.9, "KSQ4": 53.2, "DON4": 53.2, "DOND": 133.0, "C30A": 53.2, "C30C": 53.2, "PDC1": 39.9,
            "PDC4": 53.2, "OBR15": 22.6, "F2A": 22.6, "F2C": 22.6, "KA15": 8.64, "XC15": 8.64, "F1A": 8.64,
            "F1C": 8.64, "WFRI": 53.2}
    for arm_id, top in tops.items():
        p = spec[arm_id].params
        assert p["max_stop_usd"] == top and p.get("min_stop_usd", 3.0) == 3.0, arm_id
    assert {spec[b].params["exit"] for b in BOX} == {"runner", "struct", "fixed"}
    assert all(spec[f"BX{e}{tf}"].params["exit"] == mode for e, mode in (("R", "runner"), ("T", "struct"),
                                                                           ("F", "fixed"))
               for tf in ("15", "30", "1H", "4H"))


@pytest.mark.parametrize("account", sorted(HOSTS))
def test_each_host_registers_its_wave_two_arms_in_the_right_pot_at_the_cap(spec, accounts, account) -> None:
    reg = accounts[account].exploration.arms
    for arm_id, (style, cap, share) in HOSTS[account].items():
        assert (reg[arm_id].style, reg[arm_id].max_loss, reg[arm_id].control_share) == (style, cap, share), arm_id
        top = BAND_TOP["scalp" if style == "scalp" else cap]
        assert spec[arm_id].params["max_stop_usd"] <= top, arm_id


def test_arms_without_a_host_and_virtual_exits_are_not_registered(accounts) -> None:
    registered = {a for acc in accounts.values() for a in acc.exploration.arms}
    # KSQ4 / DOND wait for H4 vantage-swing; BXT/BXF are virtual exits on BXR's fill path (H-80), never orders
    assert not {"KSQ4", "DOND", *(b for b in BOX if not b.startswith("BXR"))} & registered


@pytest.mark.parametrize(("account", "swing_gbp", "intraday_gbp"), [("icm-demo", 90, 108), ("vantage-demo", 82, 98)])
def test_swing_pot_opens_two_and_no_cap_moves(accounts, account, swing_gbp, intraday_gbp) -> None:
    ex = accounts[account].exploration
    assert (ex.pots["swing"].gbp, ex.pots["swing"].max_open) == (swing_gbp, 2)
    assert (ex.pots["intraday"].gbp, ex.pots["intraday"].max_open) == (intraday_gbp, 9)
    assert ex.max_open_total == 17 and ex.budget_share == 0.9
    for style in ex.pots:
        assert sum(a.control_share or 0.0 for a in ex.arms.values() if a.style == style) <= 1


def test_wave_two_order_id_keys_never_collide(spec) -> None:
    keys = [hashlib.sha1(i.encode()).hexdigest()[:5] for i in spec]
    assert len(set(keys)) == len(keys)


def test_live_accounts_stay_disabled_and_no_login_is_written(accounts) -> None:
    assert not accounts["icm-live"].enabled and not accounts["vantage-live"].enabled
    raw = (SPEC.parent / "accounts.yaml").read_text()
    assert all(line.split("#")[0].strip() == "login: null" for line in raw.splitlines() if "login:" in line.split("#")[0])
