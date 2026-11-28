---
number: 278
rank: 8
title: 'sr-v3 report: ic_within_instrument is not an IC (Spearman on net with cost on both sides): redefine or rename'
stage: 'Research (sr-v3 trainer) · diagnostics'
size: S
status: proposed
---
- **278. sr-v3 report: `ic_within_instrument` is not an IC: redefine or rename.** Value: `sr_v3_report.rank_diagnostics` (sr_v3_report.py:193-197) reports as `ic_within_instrument` the Spearman correlation of (value − cost(x)) with `net_read`, so cost sits on both sides and the number is not the information coefficient that the card ρ rules use (quant F1 on PR #521: "ρ is the Pearson correlation of the score with gross R (cost excluded)"). Acceptance: either (a) `ic_within_instrument` becomes the Pearson correlation of the gross score (value for gross learners, before cost) with clipped gross R, per instrument, and the old statistic is kept under a new name `net_rank_corr_within_instrument`; or (b) the old field is renamed so no report calls it an IC. A test on a fixture where cost varies and gross is pure noise gives an IC of about 0 under (a). Reports already written are not rewritten; the change is noted in the next training report.
