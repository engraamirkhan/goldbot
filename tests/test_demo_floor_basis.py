"""The demo floor's basis after a demo reset, a deposit or a top-up (trading-safety M4, ADR 0097).

The floor refuses entries below `min_equity_frac` x `balance_closed_hwm`. That mark moves with capital flows (ADR 0042:
"a withdrawal lowers the mark by exactly the withdrawn amount", demo accounts only; a deposit raises it), so:
* a reset DOWN booked as a balance withdrawal lowers the mark: the floor does not refuse every entry after it;
* a reset UP or a top-up booked as a deposit raises the mark: the floor follows it;
* a withdrawal the deal history does not reconcile leaves the mark (fail-safe): the floor then refuses, and health
  FAILs `demo_floor` (tests/test_health_demo_unlimited.py), so a stuck floor is an alert, never a silent stop."""
from __future__ import annotations

from typing import Any

from tests.test_capital_flows import DEP, M, _at, _balance, _history
from tests.test_hwm_confirmation import T0, _engine


def _unlimited(eng: Any) -> Any:
    eng.cfg.mode = "demo"                                       # lowering on a withdrawal is demo-only (ADR 0042)
    eng.gate.limits = eng.gate.limits.model_copy(update={"demo_unlimited": True, "probe_only": True})
    return eng


def test_a_demo_reset_down_booked_as_a_withdrawal_moves_the_floor_with_it(tmp_path, monkeypatch) -> None:
    eng, pb = _engine(tmp_path)
    _unlimited(eng)
    _history(monkeypatch, pb, (T0 + 10 * M, DEP, -9_000.0))    # 10,000 reset to 1,000
    _balance(monkeypatch, pb, 1_000.0)
    _at(eng, T0 + 11 * M)
    assert eng.state.balance_closed_hwm == 1_000
    assert "demo_equity_floor" not in eng.gate.account_reasons(eng.state)


def test_a_reset_up_or_top_up_booked_as_a_deposit_raises_the_floors_basis(tmp_path, monkeypatch) -> None:
    eng, pb = _engine(tmp_path)
    _unlimited(eng)
    _history(monkeypatch, pb, (T0 + 10 * M, DEP, 10_000.0))
    _balance(monkeypatch, pb, 20_000.0)
    _at(eng, T0 + 11 * M)
    assert eng.state.balance_closed_hwm == 20_000
    eng.state.equity = 3_900.0                                  # below a fifth of the new mark (4,000)
    assert "demo_equity_floor" in eng.gate.account_reasons(eng.state)


def test_a_reset_the_deal_history_does_not_reconcile_keeps_the_mark_and_the_floor_refuses(tmp_path, monkeypatch):
    eng, pb = _engine(tmp_path)
    _unlimited(eng)
    _history(monkeypatch, pb, (T0 + 10 * M, DEP, -9_000.0))
    _balance(monkeypatch, pb, 1_500.0)                          # 10,000 - 9,000 is not 1,500
    _at(eng, T0 + 11 * M)
    assert eng.state.balance_closed_hwm == 10_000
    assert "demo_equity_floor" in eng.gate.account_reasons(eng.state)
