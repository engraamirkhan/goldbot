"""The news blackout on a demo learning host (owner 2026-11-02: "fix the limits of our system to trade ... for the
system to be not bound to test, learn and improve signals"; the demo-limits ADR). `RiskLimits.blackout_entries`
(default True) decides whether `AccountState.in_blackout` refuses an entry; only a demo probe_only account with
`news_blackout_entries: false` gets False (`ops.services.engine.blackout_entries`). Every other account-level reason,
the weekend's included, still refuses."""
from __future__ import annotations

from goldbot.risk.gate import RiskGate, RiskLimits
from tests.test_gate_exploration import NOW, _arm, _check, _limits, _state


def test_the_default_limits_refuse_an_entry_in_a_news_blackout(tmp_path) -> None:
    assert RiskLimits().blackout_entries is True
    d = _check(_arm(tmp_path), in_blackout=True)
    assert not d.allowed and "news_blackout" in d.reasons


def test_with_blackout_entries_off_an_entry_in_a_news_blackout_is_allowed(tmp_path) -> None:
    d = _check(_arm(tmp_path), _limits(blackout_entries=False), in_blackout=True)
    assert d.allowed, d.reasons


def test_with_blackout_entries_off_the_account_level_list_leaves_the_blackout_out_and_keeps_the_weekend() -> None:
    gate = RiskGate(_limits(blackout_entries=False))
    assert "news_blackout" not in gate.account_reasons(_state(in_blackout=True), NOW)
    assert "weekend" in gate.account_reasons(_state(in_blackout=True, weekend=True), NOW)


def test_with_blackout_entries_off_every_other_halt_still_refuses(tmp_path) -> None:
    for state, reason in ((dict(weekend=True), "weekend"), (dict(in_rollover=True), "rollover"),
                          (dict(owner_halt=True), "owner_halt"), (dict(stale_bars=True), "stale_data")):
        d = _check(_arm(tmp_path), _limits(blackout_entries=False), in_blackout=True, **state)
        assert not d.allowed and reason in d.reasons and "news_blackout" not in d.reasons
