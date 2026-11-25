---
number: 270
rank: 95
title: 'Daily-context inputs need 100 daily bars: engine memory gives ~80 (sr + zoo new spec)'
stage: 'Research (ML) · new spec for sr and zoo, quant review'
size: S
status: proposed
---
- **270. Daily-context inputs need 100 daily bars: engine memory gives ~80 (sr + zoo new spec).**
  - **Problem.** `d1_atr_ratio_14_100` and `d1_rv_ratio` are in sr-v2's frozen 40 inputs and H-101's, and they are always NaN. `feature_snapshots.decision_features` reads `ENGINE_MINUTES` = 90,000 minutes, which is about 80 daily bars, and these inputs need 100. So only 38 inputs carry information, in production and in history alike. Found while implementing H-101 (PR #444).
  - **Value.** Two daily-regime inputs become usable, or are replaced.
  - **Acceptance.**
    - A new spec version (sr-v3 / zoo-v2, never an edit of a frozen spec) either reads enough daily history for its d1 context (a separate daily-bar memory, point in time) or swaps the two inputs for computable ones.
    - The quant signs the change.
    - A test asserts that every input of the new spec is non-NaN on a full-memory decision.
    - Nothing in the engine's trading path changes.
