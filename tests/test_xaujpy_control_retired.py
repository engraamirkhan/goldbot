"""I0-XAUJPY is formally retired on 2026-11-09 (docs/research/prereg-amendment-2026-11-10-execution-controls.md
section 9; prereg-amendment-2026-11-06-control-thinning.md rule 4): its vantage-demo cost cell completed (36 fills of
30), so `thin_cell_complete` already refuses it there, but that count is per account; the spec row's `retired_day`
refuses it "the same way from the start, on every account" (engine `spec_retired`), as S0-XAUJPY's did on 2026-11-07.
Kill-only: the frozen hash, id and K row stay; an open position keeps every exit."""
from __future__ import annotations

from goldbot.engine import exploration
from goldbot.engine.exploration import SPEC_PATH
from goldbot.ops import accounts as acc_mod
from goldbot.research.arms import arm_hash, load_arms

ARM = "I0-XAUJPY"


def test_i0_xaujpy_is_retired_on_2026_11_09_under_the_control_thinning_rule_with_its_hash_unchanged():
    row = {a.id: a for a in load_arms(SPEC_PATH)}[ARM]        # check_hash: only unhashed fields were edited
    assert row.retired and row.retired_day == "2026-11-09"
    assert "control-thinning" in str(row.retired_rule) and "thin_cell_complete" in str(row.retired_rule)
    assert row.config_hash == arm_hash(row) == "4e2532b406cdcf47"


def test_i0_xaujpy_cannot_enter_on_any_account_that_registers_it():
    accs = acc_mod.load_accounts(logins=False)
    hosts = [acc for acc in accs.values() if ARM in acc.exploration.arms]
    assert hosts, "no account registers I0-XAUJPY: the retirement check would prove nothing"
    for acc in hosts:
        cfg = exploration.config_for(acc.model_copy(update={"exploration": acc.exploration.model_copy(
            update={"enabled": True})}))
        assert cfg is not None
        assert ARM in exploration.spec_retired(cfg), f"{ARM} could still enter on {acc.account_id}"
