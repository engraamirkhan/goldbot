---
number: 82
rank: 19
title: 'H-02/H-07: macro_spx_dd20 is NaN in train and test for folds without full SP500 coverage'
stage: Research (feature coverage, pre-registration amendment) · LEARN
size: S
status: 'ready (quant finding 3 on the intraday candidates, 2026-10-17) · accountable: implementer · reviews: quant-reviewer · due before the Q1 freeze (2026-12-11)'
---
- **82. H-02/H-07: macro_spx_dd20 is NaN in train and test for folds without full SP500 coverage.** Value: `macro_spx_dd20` is NaN before ~2016; with the 4h rolling 48-month training window, folds whose window only partly covers SP500 see the NaN as an era marker a tree model can learn, so H-07 is not VIX-only in those folds and fold AUC can be biased either way. Acceptance: in any fold whose training window lacks full SP500 coverage, `macro_spx_dd20` is set to NaN in both train and test; `test_spx_dd20_is_nan_in_train_and_test_for_folds_without_full_coverage`; frozen Q4 readings unchanged (document any delta); H-02's dated amendment in preregistration-2027Q1.md names this item.
