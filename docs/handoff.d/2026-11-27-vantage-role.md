- 2026-11-27 R7-VANTAGE-REVIEW decided (research-director): **option (i), vantage-demo stays replay-only, "no broker
  arm by design"**. (ii) is not started (fails the cost card), (iii) is rejected. No config change in this PR.
  - Facts re-derived from config at fa11faf2 (not relayed): vantage-demo lists 70 exploration arms, 68 retired (36
    controls, 20 edge, 12 learning) and 2 running (VEX15, VEX1H), both `control_mode: replay_only`, so **0
    order-capable arms**; `cost_probe.enabled: false`, `scheduled.enabled: false`, `daily_review.probe_plan: {}`.
    vantage-247 is the same (2 replay-only controls, account disabled). Money: £0/day traded, host ~£4.52/month
    (~£0.15/day).
  - Wording fix: it is **not** "cost measurement". With probes off there are no Vantage fills, so no slippage or
    commission is measured; the VEX replay labels record only Vantage's quoted spread at signal time. Report it as
    "replay-only (quoted spread), no broker arm by design".
  - Why not (ii), a GD-1 twin on Vantage (rule 2 cost card): GD-1's own prereg (prereg-amendment-2026-11-20-gd1-asia-
    learning.md s1) puts the edge at ~1-2 bp/day historical drift, **+0.9 bp/day excess over drift, 95 % CI -0.42 to
    +2.25**, "learning, not money", about $0.05/day at 0.01 lot. Vantage's last measured cost: probes -£65.8 on 306
    fills = **£0.215/fill** (re-derived from the 11-07 record in config/accounts.yaml), ~0.7 bp round trip at 1 oz.
    Excess 0.9 bp < 2 x 0.7 = 1.4 bp: not started. It is also the same bet on the same bars as GD1D/GD1P (no
    independent edge evidence), and at ~23 fills per run it never reaches GD-1's 40-fill cost check before
    2027-01-04, so it would not even deliver a decision-grade Vantage cost read. A third copy adds risk, not learning.
  - Why not (iii): scheduled probes are random-time orders with no edge. They were 22.9 % of vantage-demo's loss at
    11-07 (prereg-amendment-2026-11-07-kill-only.md s4), and trading-safety F1 switched them off on 11-22 under "only
    arms with evidence place orders". Nothing consumes a fresh Vantage cost cell today: docs/research/live-candidates.md
    is empty (re-checked), so no venue question is open. Paying to measure a cost nobody reads is a leak (rule 1a).
  - ADR 0106: survival is untouched by every option (demo money); consistency and return favour (i): zero orders
    without evidence beats correlated or random orders on a second venue.
  - Leak to fix (code, not config): Vantage `exploration_cost_cells` last refreshed 11-06/07 and are frozen. Until a
    fix lands, Vantage replay labels are **stale-cost** and excluded from any cross-broker or ICM-vs-Vantage read.
    Owner: data-engineer with quant-reviewer; acceptance: a cell older than 14 days is flagged `stale` on the label
    row and the label falls back to the documented prior, with a test.
  - Pre-registered trigger to reopen (ii), no K until it fires: the **first** entry on docs/research/live-candidates.md
    that names vantage-live as its venue. On the trigger the twin is that candidate's rule (not GD-1 by default), with
    its own card, K row and 1:1 replay control. If there is no trigger by 2027-01-04, the CFO reviews keeping the host
    (£4.52/month) against the quoted-spread labels it produces; vantage-live-obs and vantage-live are unaffected.
  - Change set for an implementer **when the trigger fires** (GD-1 rows as the template; swap in the candidate's
    rule): `config/exploration_arms.yaml` adds `{ id: <X>V, twin_of: <X>D, rule/timeframe/params as the ICM arm,
    role: learning, learning: {prereg, control: <X>CV, loss_cap_gbp, since, until} }` and `{ id: <X>CV, ..., role:
    control, control_mode: replay_only }` (hashes from the loader); `config/accounts.yaml` vantage-demo
    `exploration.arms` gains `<X>V: { style: swing, max_loss: 40.0 }` and `<X>CV: { style: swing, max_loss: 40.0,
    control_share: 0.01 }` (swing pot stays 2 slots); `config/arm_stops.yaml` adds a `<X>V` stop copying the ICM
    arm's kill rule and `follows: { <X>CV: <X>V }`; K rows via `scripts/record_learning_k_rows.py`; a prereg amendment
    naming the venue read. `daily.max_per_day` stays 40 and `probe_plan` stays `{}`
    (tests/test_exploration_daily_budget.py unchanged). Reviewers: quant-reviewer (card, K, control), trading-safety-
    reviewer (new order path on vantage-demo), code-reviewer; sre for the restart. (iii) would also need trading-
    safety to reverse F1 in writing and CFO sign-off on the probe budget; not recommended.
