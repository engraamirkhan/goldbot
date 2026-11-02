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

## Amendment 2026-10-30 (trading-safety and quant review)
- **References.** A reference the filesystem cannot read (a NUL byte, a path too long) counts as missing. It refuses
  its arm and never raises, so the engine always starts. A registry reference names a row's `config_hash`. When the
  registry file (state/research_registry.jsonl) is present, the hash must be one of its rows. When the file is absent,
  the reference must carry the evaluator's commit (`registry:<hash>@<40 hex>`).
- **Cost role.** The `cost` role is closed to `COST_ARMS` = {F1A: O-11, G1A: O-12}. Each must name its cost read in
  `cost_read`. This is a row field outside the hash, because the reviewers' `params.cost_read` would change the frozen
  hash. The exemption ends when the cost read has its admissible fills (F1A 200, G1A 100), counted once a minute in
  the host's ledger. The arm and its paired control are then refused ("cost read complete, qualify first"). An
  unreadable count keeps them running and warns.
- **Measured costs.** `cost_basis: measured` must cite `cost_table` (the version) and `cost_cells`. Every cell must be
  measured (not `from_prior`), rest on at least 30 fills, and be charged at the worse of measured and prior. Until such
  cells exist, no arm qualifies.
- **F1A's graduation.** F1A's forward Graduation rule is withdrawn from prereg-2026-10-26-time-structure.md 2.2, with a
  dated note. Its stops stay as safety stops only.

## Consequences
- Demo volume falls to the controls and cost arms until a family qualifies. On 2026-10-30 these may enter:
  - icm-demo: C1H, G1A, G1C, I0, W0, ZCD;
  - icm-probe: F1A, F1C, S0, S0R;
  - vantage-demo: VC1H, VI0, VW0, ZCV.

  Newly refused: 11 on icm-demo, 9 on icm-probe, 2 on vantage-demo. Pots, caps and the RiskGate are unchanged.
- Demo becomes a live-versus-history check of qualified rules, not a discovery venue. A family comes back to demo only
  through an evaluator commit that adds a passing qualification to its rows, which needs both reads.
- F1A and G1A run unqualified only as cost measurements, and only until their cost reads are full. Their demo fills
  are never strategy evidence.
- `ops.autodisc_register` must write a qualification (its confirm read) before a discovered arm can trade.

## Amendment 2026-11-01 (owner instruction 2026-11-01; growth sprint)
Context: the owner's goal of 2026-11-01 was "double the capital per week ... start proving it on demo accounts from
tonight's open". The research director's plan (docs/research/growth-sprint-2026-11-01.md section 3) runs GS1 (C2, the
session-range breakout) on icm-demo before C2's screen and confirm have reported. The owner instruction overrides this
rule for that one experiment.

- **A third exempt role, `owner_experiment`, closed to an explicit list in code:** `research.arms.OWNER_EXPERIMENTS` =
  {(icm-demo, GS1): last day 2026-11-27}, keyed by account and arm. The role on any other id is refused, and GS1
  registered on any other account is refused at load (trading-safety review LOW 3). After the last day (UTC) the arm is refused like
  any unqualified strategy, with the reason "the owner experiment GS1 expired after 2026-11-27".
  - The engine re-reads the expiry once a minute (`exploration.refresh_owner_experiments`), so the expiry needs no
    restart.
  - At start the engine journals each running owner experiment (`exploration_arm_owner_experiment`, with `until`).
    The expiry is journalled as `exploration_arm_unqualified`.
- **GS1C**, GS1's random-entry control, is an ordinary `control` role (rule `session_range_control`, `params.control`).
  It stops with GS1 at the expiry: the engine refuses any control whose `params.control` names an expired owner
  experiment (trading-safety review LOW 2). This holds at load too: after a restart past the expiry, the loader refuses
  the control together with its experiment.
- **Every other arm keeps this rule.** A later experiment needs its own entry in OWNER_EXPERIMENTS and its own dated
  amendment here. A config edit cannot exempt an arm.
- **GS1's fills are labelled "unqualified, owner experiment"** and are never qualification evidence. GS1 comes back
  only through the normal route (a passing screen and confirm on its rows). Its kill rule is the pre-registered excess
  read in section 3 of the growth-sprint doc. The code expiry ends its entries whatever that read says.

Consequences: icm-demo gains GS1 (until 2026-11-27) and GS1C, in a growth pot of £80 carved from its scalp pot inside
the unchanged £198. No cap moves: the daily £220, the weekly 6%, the RiskGate's per-trade maximum and ADR 0043 X3 all
still bind. Open positions keep every exit after the expiry.

## Amendment 2026-11-02 (owner 2026-11-02; the demo learning class, ADR 0072)
Context: the owner, 2026-11-02: "fix the limits of our system to trade ... for the system to be not bound to test,
learn and improve signals". Asked whether a demo "learning" class should exist, the owner answered YES. No strategy has
qualified yet, so this rule had stopped every demo strategy arm.

- **A fourth role, `learning`, with its own bounds, never a qualification.** A `role: learning` row enters on demo
  without a `qualification` when `research.arms.learning_refusal` and the loader's cross-row checks
  (`learning_cross_refusals`) all pass:
  - its `learning.k_id` is its own K row (`learning_k_id`, derived from the arm, its hash and its prereg) and resolves
    in the registry. Without the registry it is refused (fail closed);
  - its `learning.prereg` doc exists in the repo;
  - its `learning.control` names a matched control row (role control; same timeframe and the same fixed bracket: stop,
    target, hold);
  - its `until` is not past and at most 45 days away;
  - it has no qualification block, and no growth sizing or pyramid adds on the account (`exploration.config_for`).
- **Controls.** A control dedicated to learning arms (`params.control: "learning:<ids>"`) is refused with them once
  none of them may enter, at load and once a minute in the engine. A shared control pre-registered on its own (ZCD,
  ZCV) keeps its own role.
- **The engine stops.** `engine/exploration_learning.py` stops a learning arm at whichever comes first:
  - 300 admissible fills: its read is due;
  - its `loss_cap_gbp` (20 x its per-trade cap);
  - its end.

  It also enforces `learning_max_open` and fails closed on an unreadable ledger.
- **Evidence.** Learning fills are stamped `evidence_class: learning`.
  - A qualification citing a learning K row is refused, both by the registry row's family and by the spec's own k_ids.
  - The perf report and the daily review show learning arms in their own section, never in the family rankings.
  - The bandit never allocates them, and their rows train no style model.
  - A learning read can only motivate a pre-registered history qualification (a screen and a confirm).
- **Registered 2026-11-02** (docs/research/learning-2026-11-02.md): the 15 arms the qualification gate refused that have
  a fixed bracket, with 7 new dedicated controls. Left refused, because no random control can match them: BXR15, BXR30,
  BXR1H, BXR4H, XBB15, S1A and S1B.

Consequences: unqualified strategies trade on demo again, labelled and bounded; the rule above stands for every other
arm and for every account that is not a demo probe_only host. Each learning arm needs its K row on the brain first
(`scripts/record_learning_k_rows.py`, inside the quarter's budget).
