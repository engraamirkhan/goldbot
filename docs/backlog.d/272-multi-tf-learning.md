---
number: 272
rank: 96
title: 'Multi-timeframe learning mtf-v1: history-pretrained, live-updated models on 1m-1w (H-102)'
stage: 'Research (ML) · build lanes after quant signature'
size: L
status: proposed
---
- **272. Multi-timeframe learning mtf-v1: history-pretrained, live-updated models on 1m-1w (H-102).**
  - **Card:** `docs/research/prereg-2026-11-25-multi-tf-learning.md`.
  - **Value.** Every timeframe learns, retrains and reports, per the owner directives of 2026-11-25:
    - 1m, 5m, 15m, 30m, 1h, 2h, 4h, 1d and 1w;
    - every model history-pretrained first (rule HP-0, no exceptions), then updated by live labels;
    - a cost-weighted net-R objective;
    - judged only out of sample.
  - **Today's gap:** sr-v2 fits on live rows only (812 with features), and 30m, 2h, 1d and 1w have no labels at all.
  - **Acceptance (build, 0 K):**
    - sr-v3, history-pretrained (build item 1);
    - 2h added to every timeframe table, including `telegram/labs._TF_STYLE`;
    - `history_replay` timeframes come from `config.tf_seconds`, with price-scaled stop clamps and one Friday-cut rule;
    - research-replay rows recording (`config/research/multi_tf_arms.yaml`, which the engine never loads);
    - forward replay labels, and the closed-bar store;
    - `multi_tf_retrain` with daily per-cell triggers at 00:10 UTC, never overlapping `discovery_nightly`;
    - weekly feature parity, history against the engine;
    - per-tf `snapshot_max_decisions` quota;
    - tests T1-T9.
  - **Health and reports:**
    - health shows one line per tf x asset_class: labels, new since the last fit, weights, active models;
    - health warns when an enabled cell is stale;
    - daily and weekly reviews are split by tf x asset_class.
  - **K:** 0 in 2026Q4. 2027Q1: +5 Stage L, one per unit, each only if Stage 0 power passes and H-101 is not null.
  - **Prerequisite:** the free 1m pull for non-gold classes (data-engineer).
