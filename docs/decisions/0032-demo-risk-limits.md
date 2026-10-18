# 0032: Demo risk limits under the owner's delegation

Status: proposed (implementer, 2026-10-18); merge needs the owner's confirmation of the approval below (see
"Consequences")

## Context

On a £1,000 GBP demo account the settings defaults (daily cap 2%, weekly 5%, minimum-lot exception off) refuse most
15m entries: at ATR15 of about $8 one 0.01 lot at a 1.0 x ATR stop risks about £6 (0.6%), which is above the 0.5% risk
per trade, and without the minimum-lot exception the gate refuses it (`min_lot_exceeds_risk`). The supervisor's
combined caps (1.5% / 4%) bind sooner still.

The program director relayed the owner's approval, given in chat on **2026-10-18**: *"approve demo risk limits: daily
cap £50, min-lot cap 1%, weekly 10%, demo only"*. Later the same day the owner asked, also relayed, for the limits
to be **adaptive (learnt over time)**, with the approved values as **ceilings** rather than fixed values.

Panel views:

* **CFO**: for. £50 is the daily loss limit ADR 0004 already set, so the money at risk on a bad day does not change.
* **Research director**: against raising the daily cap. Edge is the binding problem, not the caps; larger caps
  produce more trades without more evidence that the edge is real.
* **Trading safety**: for, on conditions: demo only and enforced in validation; the live and tiny-live paths
  unchanged; the supervisor's caps raised in lockstep only when every enabled account is demo; open risk counted
  toward the £50; a revert on the 8% size-down stage that stays until the owner resets it; every record tagged so
  that gate evidence never mixes profiles; a live terminal behind a demo label still refused.

## Decision

1. **Ceilings, per account, demo only.** `risk_overrides: {profile: demo-2026-10-18, ceilings: {daily: 0.05,
   weekly: 0.10, min_lot_risk_cap: 0.01}}` on `icm-demo` in config/accounts.yaml. `Account` refuses `risk_overrides`
   on any account whose mode is not `demo`, and on any account without `daily_loss_limit_abs`. `RiskCeilings` cannot
   go above the approval (daily 0.05, weekly 0.10, min-lot 0.01). Raising one needs a new owner decision and an ADR,
   not only a config edit. Unknown keys are refused.
2. **A controller seam** (`goldbot/risk/limits_profile.py`): `LimitsController.current(account, evidence) ->
   RiskLimitsCurrent`. `clamp` makes whatever the controller returns safe:
   * reverted: the defaults;
   * a demo account with ceilings: each limit within [default .. ceiling], and `project_open_risk` on;
   * any other account (live, tiny-live, a missing or unknown mode): each limit **at most** its default. A
     controller can never widen live or tiny-live.

   A value that is not a finite number, or a controller that raises, gives the defaults. The profile label is set by
   `clamp`, never taken from the controller. `StaticController` returns the ceilings, so what trades today is what
   the owner approved. An adaptive controller (being designed by the architect) plugs in later; this change has no
   adaptive logic.
3. **The engine reads the current limits on every account refresh** (`Engine._apply_limits`). Every change is
   journalled (`limits_change`), logged, and appended to core.db `limits_changes` with the value, the reason and the
   evidence ids.
4. **The ADR 0004 absolute limit stays binding.** The gate's daily limit is min(daily_cap x day-start equity,
   `daily_loss_limit_abs`): 5% of £1,000 is £50, and above £1,000 the £50 wins. With `project_open_risk` on, the
   open trades' stop risk counts toward the £50.
5. **Supervisor lockstep.** The combined caps rise only when every enabled account in accounts.yaml is demo with
   ceilings (`supervisor_ceilings`). Their ceiling is the CFO envelope, `SUPERVISOR_DEMO_ENVELOPE` = 3.75% daily and
   7.5% weekly, or an account's own ceiling if that is lower. The envelope sits deliberately below the per-account
   5% / 10%, so the combined caps still bind first (trading-safety review of 156cfbf, HIGH). Within that, the caps
   follow the engines' published `limits_current`: the smallest published cap, clamped to [supervisor default ..
   ceiling]. Any engine on the default profile (reverted, say), any
   non-demo engine file, and any engine that publishes nothing put the combined caps back at 1.5% / 4%.
6. **Revert.** When the account enters the 8% size-down stage (also the 12% halt, or the supervisor's combined
   size-down), the account goes back to the default limits. The revert is saved in core.db `limits_state` and
   survives restarts; if it cannot be read, the engine loads as reverted. Only the owner's re-arm (dashboard, owner
   role + TOTP) clears it, and only when it is seen while the stage is normal. A re-arm during size-down or the halt
   is refused and journalled (`limits_reset_refused`). A 15% revert was rejected because the 12% kill switch trips
   first.
7. **Tagging.** `EntryAudit` and so `ClosedTrade` carry `limits_profile` and `limits` (the values at entry), and so
   do journal decisions (`limits_profile`) and shadow trades (`limits_profile`). The engine state file publishes
   `limits_current` and `limits_reverted`.

What stays fixed: the live and tiny-live limits (tiny-live never takes the minimum-lot exception); RiskGate as the
only path to an order; exits never gated; the 8% / 12% stages and the kill switch; `unlock_live` and the typed
phrase; the demo-terminal check at start and per order; the trial budget, the holdout, and the phase-gate thresholds.

## Consequences

* The ICM demo can place a 0.01-lot trade at a 1.0 x ATR15 stop when ATR15 is $8 (about 0.6% risk); live and
  tiny-live still refuse it.
* **Phase-gate evidence: the rule is built and tested, the wiring is not.** Trading safety specified the rule:
  `limits_profile.counts_as_default_evidence(t, settings.risk)` counts a closed trade as paper evidence only when
  all of these hold:
  * it is a demo trade;
  * it is tagged `default`, or it is untagged and closed before `ADR0032_CUTOVER` (2026-10-18 00:00 UTC). An
    untagged trade closed after the cutover is not counted: adopted orphans carry no tag;
  * when its limits are recorded, none of them is above settings.yaml `risk:` (daily, weekly, min-lot).

  `goldbot/ops/gates_phase.evaluate_gates` (the `paper = ...` line) still counts every `mode == "demo"` trade. Wiring
  it in is about three lines: filter `paper` with the rule, report the excluded count in the gate's evidence, and
  keep `evaluate_stop_rule` on all trades. That change belongs to someone with authority over phase-gate code: the
  owner's go-ahead, then quant-reviewer verifies it. The implementer role is barred from editing phase-gate code, and
  a delegation relayed by another agent does not lift that bar. Until the wiring lands, `paper_to_tiny_live` must
  not be recorded over a window that contains `demo-2026-10-18` trades.
* **The approval was relayed.** The approval came to the implementer through the program director, not from the
  owner directly. The owner confirms it when merging; the PR description quotes the words above.
* **A stale engine file** with no `limits_current` (an old file from a disabled account, say) keeps the combined
  caps at their defaults. This fails safe: entries halt at 1.5% combined, not 5%. Clear old `state/engine_*.json`
  files of accounts that no longer run.
* **Rollback.** A release without this change ignores `risk_overrides` and the two core.db tables (migration 3 is
  additive), so it trades the defaults.
