"""ADR 0032 in the phase gates: only trades decided under the default limits are paper evidence.

`evaluate_gates` counts a demo trade toward `paper_trades` (and the paper expectancy) only when
`limits_profile.counts_as_default_evidence` says so: tagged `default` with no limit above settings.yaml `risk:`, or
untagged and closed before ADR0032_CUTOVER. Demo-profile trades and untagged trades after the cutover (adopted
orphans) are left out, and the evidence text says how many. The stop rule still reads every trade."""
from pathlib import Path

import pandas as pd

from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade, GateReport, evaluate_gates
from goldbot.risk.limits_profile import ADR0032_CUTOVER, DEFAULT_PROFILE, LimitsTag
from tests.test_phase_gates import CFG, SETTINGS, full_state, item_of, write_trades

PROFILE = "demo-2026-10-18"
LATER = ADR0032_CUTOVER + pd.Timedelta(days=30)
DEFAULTS = LimitsTag(daily_cap=SETTINGS.risk.daily_cap, weekly_cap=SETTINGS.risk.weekly_cap,
                     min_lot_risk_cap=SETTINGS.risk.min_lot_risk_cap, project_open_risk=SETTINGS.risk.project_open_risk)
RAISED = LimitsTag(daily_cap=0.05, weekly_cap=0.10, min_lot_risk_cap=0.01, project_open_risk=True)


def _paper_and_live(state: Path) -> tuple[list[ClosedTrade], list[ClosedTrade]]:
    trades = gates_phase.load_closed_trades(state)[0]
    return [t for t in trades if t.mode == "demo"], [t for t in trades if t.mode == "live"]


def _post_cutover(t: ClosedTrade, i: int, profile: str | None, limits: LimitsTag | None) -> ClosedTrade:
    return t.model_copy(update={"exit_utc": ADR0032_CUTOVER + pd.Timedelta(hours=i + 1), "limits_profile": profile,
                                "limits": limits, "position_id": 10_000 + i})


def _report(state: Path, trials: Path) -> GateReport:
    return evaluate_gates(state, SETTINGS, LATER, trials_path=trials)


def _paper_item(rep: GateReport) -> gates_phase.GateItem:
    return item_of(rep, "paper_to_tiny_live", "paper_trades")


def test_untagged_trades_closed_before_the_cutover_still_count(tmp_path):
    state, trials = full_state(tmp_path)
    paper, _ = _paper_and_live(state)
    assert all(t.limits_profile is None and t.exit_utc < ADR0032_CUTOVER for t in paper)
    it = _paper_item(_report(state, trials))
    assert it.met and it.progress is not None and it.progress.value == len(paper)
    assert "ADR 0032" not in it.evidence                       # nothing excluded, nothing said


def test_default_profile_trades_count_and_demo_profile_trades_do_not(tmp_path):
    state, trials = full_state(tmp_path)
    paper, live = _paper_and_live(state)
    # all but 10 of the paper record before the cutover, then 10 default-profile and 40 demo-profile trades after it
    keep = paper[: CFG.paper_min_trades - 10]
    default = [_post_cutover(t, i, DEFAULT_PROFILE, DEFAULTS) for i, t in enumerate(paper[:10])]
    demo = [_post_cutover(t, 100 + i, PROFILE, RAISED) for i, t in enumerate(paper[10:50])]
    write_trades(state, keep + default + demo + live)
    it = _paper_item(_report(state, trials))
    assert it.progress is not None and it.progress.value == CFG.paper_min_trades and it.met
    assert "40 under raised demo limits not counted, ADR 0032" in it.evidence
    # without the default-profile ten the count falls short, however many demo-profile trades there are
    write_trades(state, keep + demo + live)
    it = _paper_item(_report(state, trials))
    assert not it.met and it.progress is not None and it.progress.value == CFG.paper_min_trades - 10


def test_untagged_trades_closed_after_the_cutover_do_not_count(tmp_path):
    state, trials = full_state(tmp_path)
    paper, live = _paper_and_live(state)
    keep = paper[: CFG.paper_min_trades - 5]
    orphans = [_post_cutover(t, i, None, None) for i, t in enumerate(paper[:5])]   # adopted orphans carry no tag
    write_trades(state, keep + orphans + live)
    it = _paper_item(_report(state, trials))
    assert not it.met and it.progress is not None and it.progress.value == CFG.paper_min_trades - 5
    assert "5 under raised demo limits not counted" in it.evidence


def test_a_default_tagged_trade_with_limits_above_the_settings_does_not_count(tmp_path):
    state, trials = full_state(tmp_path)
    paper, live = _paper_and_live(state)
    keep = paper[: CFG.paper_min_trades - 1]
    odd = [_post_cutover(paper[0], 0, DEFAULT_PROFILE, RAISED)]
    write_trades(state, keep + odd + live)
    it = _paper_item(_report(state, trials))
    assert not it.met and "1 under raised demo limits not counted" in it.evidence


def test_the_stop_rule_still_sees_demo_profile_trades(tmp_path):
    state, trials = full_state(tmp_path)
    paper, live = _paper_and_live(state)
    big = _post_cutover(paper[0], 0, PROFILE, RAISED).model_copy(
        update={"pnl": -0.2 * paper[0].equity_before, "ret": -0.02})            # a 20% single loss on the demo profile
    write_trades(state, paper + [big] + live)
    rep = _report(state, trials)
    assert rep.stop_rule.worst_loss_frac is not None and rep.stop_rule.worst_loss_frac >= 0.2 - 1e-9
    assert "single_loss" in rep.stop_rule.breach
