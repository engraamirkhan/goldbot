"""Per-instrument random controls (multi-instrument plan 2026-11-02: "Strategies day one: random controls S0/I0 ...
per instrument (never pooled); no gold ML/specialists/dollar-constant arms").

Each listed instrument has its own S0 (1m) and I0 (15m) random bracket under its own id, `params.instrument` naming it
(hashed: another instrument is another arm), the stop in ATRs (never a dollar constant), registered by lane L3 on the
account that lists the instrument (lane L3), and running: the multi-instrument engine runs each on its own
instrument's book only (engine/exploration.py `book_config`), which refuses its entries while the instrument's terms
are unverified.
"""
from __future__ import annotations

from goldbot.instruments import GOLD, arm_instrument, registry
from goldbot.ops import accounts as acc_mod
from goldbot.research.arms import REPO_ROOT, load_arms

ARMS = REPO_ROOT / "config" / "exploration_arms.yaml"


def _per_instrument() -> dict[str, list]:
    """Each instrument's S0-<key> / I0-<key> random controls (its learning pair, LB-/LBC-<key>, is
    tests/test_learning_mi.py's)."""
    out: dict[str, list] = {}
    for a in load_arms(ARMS):
        if arm_instrument(a.params) != GOLD and a.id.startswith(("S0-", "I0-")):
            out.setdefault(arm_instrument(a.params), []).append(a)
    return out


def test_every_listed_instrument_has_its_own_s0_and_i0_control() -> None:
    accs = acc_mod.load_accounts(logins=False)
    listed = {k for a in accs.values() for k in a.instruments}
    arms = _per_instrument()
    assert set(arms) == listed and len(listed) == 26
    for key, rows in arms.items():
        assert sorted(a.id for a in rows) == [f"I0-{key}", f"S0-{key}"]
        assert all(a.role == "control" and a.rule == "random_bracket" and a.forward_only for a in rows)


def test_instrument_controls_size_their_stop_in_atrs_never_dollars() -> None:
    for rows in _per_instrument().values():
        for a in rows:
            assert "stop_atr" in a.params and not any(k.endswith("_usd") for k in a.params), a.id


def test_instrument_controls_keep_s0_and_i0s_geometry() -> None:
    by_id = {a.id: a for a in load_arms(ARMS)}
    for key in _per_instrument():
        s0, i0 = by_id[f"S0-{key}"], by_id[f"I0-{key}"]
        assert (s0.timeframe, s0.params["target_r"], s0.params["hold_bars"]) == ("1m", 1.0, 30)
        assert (i0.timeframe, i0.params["target_r"], i0.params["hold_bars"], i0.params["stop_atr"]) == (
            "15m", by_id["I0"].params["target_r"], by_id["I0"].params["hold_bars"], by_id["I0"].params["stop_atr"])


def test_instrument_controls_run_now_the_engine_trades_each_on_its_own_instrument() -> None:
    for rows in _per_instrument().values():
        assert all(a.status == "run" and "L3" in a.note for a in rows)


def test_each_instrument_control_is_registered_on_exactly_the_account_that_lists_its_instrument() -> None:
    """Lane L3 registers S0-<key> / I0-<key> on the account listing <key>, nowhere else."""
    accs = acc_mod.load_accounts(logins=False)
    for key, rows in _per_instrument().items():
        owners = [a for a in accs.values() if key in a.instruments]
        hosts = [a.account_id for a in accs.values() if {r.id for r in rows} & set(a.exploration.arms)]
        assert hosts == [a.account_id for a in owners], key
        for a in owners:
            assert {r.id for r in rows} <= set(a.exploration.arms), key


def test_every_instrument_control_is_classified_never_stopped() -> None:
    from goldbot.research.arm_stops import load_stop_rules
    ids = {a.id for rows in _per_instrument().values() for a in rows}
    assert ids <= set(load_stop_rules().none)


def test_every_non_gold_arm_is_an_instrument_control_or_a_learning_pair() -> None:
    for a in load_arms(ARMS):
        if arm_instrument(a.params) != GOLD:
            assert a.id.split("-", 1) in ([p, arm_instrument(a.params)] for p in ("S0", "I0", "LB", "LBC")), a.id


def test_gold_arms_carry_no_instrument() -> None:
    gold = [a for a in load_arms(ARMS) if arm_instrument(a.params) == GOLD]
    assert gold and all("instrument" not in a.params for a in gold)
    assert all(registry().get(k) for k in _per_instrument())
