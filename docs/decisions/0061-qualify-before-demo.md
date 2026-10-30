# 0061: Every strategy qualifies on history before demo

Status: accepted (owner rule, 2026-10-30; implementer, 2026-10-30)

## Context
The owner set this rule on 2026-10-30: "every strategy should have been back tested and qualified before it is tested and
learn from live trades that are taken on demo accounts". Until then a demo arm could be forward-only, or run on history
as a screen with no separate confirmation read, and still trade on demo. That week's history verdicts were H-16, H-80 R2,
H-82, H-83, H-84 and H-17, all FAIL, and autonomous discovery had 0 survivors. So no strategy family had qualified, and
the demo fills were being read as discovery evidence for rules that history had not supported.

## Decision
- Every exploration arm row (config/exploration_arms.yaml, config/spec_arms.yaml) can carry `qualification:
  {screen, screen_window, confirm, confirm_window, verdict, cost_basis}`. This is run policy outside the arm hash, like
  `retired_day`. A strategy arm sends a demo entry only when the record validates and all of these hold:
  - verdict `pass` and cost_basis `measured`;
  - the screen window ends before the confirm window starts, and the confirm window ends before the holdout
    (2025-10-01). The reference windows are screen 2010-01-01..2017-12-31 and confirm 2018-01-01..2025-09-30;
  - screen and confirm are two different references, each a repo doc that exists (an `#anchor` is allowed; a path
    outside the repo is refused) or a trial-registry id `registry:<id>`. A registry id is checked on its form only:
    the registry lives in state, not in the repo.
- Two roles are exempt, and each must be written as an explicit `role`:
  - `control`: the random controls S0, S0R, I0, W0, VI0, VW0, C1H, VC1H, ZCD, ZCV, VS0 and the control twins F1C, G1C,
    F2C, C30C, XC15. The body must be a control (rule `random_bracket` or a `params.control`). A control role on a
    strategy body is refused.
  - `cost`: the cost arms F1A and G1A, which feed the time-structure confirmation reads.
- Enforcement has two layers:
  1. `research.arms.load_arms` (and the spec_arms row loader) stamps `entry_refused` from `qualification_refusal`.
  2. The engine (`engine.exploration.Exploration`) reads every row again (`entry_refused`) and puts each refused arm in
     its no-new-entry set, the same set a spec retirement uses. It journals `exploration_arm_unqualified` with the
     reason "unqualified (owner rule 2026-10-30): ...".
  The row is never dropped. An arm that is no longer configured has its open positions closed at once as orphans, and
  the history matrix must still run an arm in order to qualify it. Open positions keep every exit. A malformed
  record refuses its own arm and never stops the load or the engine.
- spec_rule arms get no exemption. On this base `ops.autodisc_register` registers explore survivors without a confirm
  read, so a discovered arm needs the same qualification once the confirm stage (ADR 0058 phase 3) writes one.
- Health line `qualification`: for each account, the arms that may enter and the strategy arms refused (spec
  retirements are left out because they are refused already). It warns when an exploring account has no arm that may
  enter.

## Consequences
- Demo volume falls to the controls and cost arms until a family qualifies. On 2026-10-30 these may enter:
  - icm-demo: C1H, G1A, G1C, I0, W0, ZCD;
  - icm-probe: F1A, F1C, S0, S0R;
  - vantage-demo: VC1H, VI0, VW0, ZCV.

  Newly refused: 11 on icm-demo, 9 on icm-probe, 2 on vantage-demo. Pots, caps and the RiskGate are unchanged.
- Demo becomes a live-versus-history check of qualified rules, not a discovery venue. A family comes back to demo only
  through an evaluator commit that adds a passing qualification to its rows, which needs both reads.
- F1A and G1A are strategy rules held under the `cost` role, which is a judgement call. They keep running unqualified
  because their fills feed a pre-registered confirmation read. The quant reviewer should confirm that or move them to
  `strategy`.
- `ops.autodisc_register` must write a qualification (its confirm read) before a discovered arm can trade.
