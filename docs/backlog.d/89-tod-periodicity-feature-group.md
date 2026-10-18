---
number: 89
rank: 25
title: Opt-in tod_periodicity feature group for O-4 (N1)
stage: Research (features) · LEARN
size: S
status: 'done (implemented 2026-10-18; awaiting quant-reviewer review) · accountable: implementer · reviews: quant-reviewer, code-reviewer · due 2026-12-11 (O-4 group-map pin)'
---
- **89. Opt-in tod_periodicity feature group for O-4 (N1).** Value: adds the N1 hypothesis (`docs/research/candidates/next-2026-10-18.md`) to O-4's 15m discovery at no trial cost (signed amendment under O-4 in `docs/research/preregistration-2027Q1.md`). Acceptance: a new opt-in family `tod_periodicity` with six columns `slot_mean_ret_{5,10,20}d`, `slot_hit_rate_20d`, `slot_t_20d`, `slot_ret_over_atr`, computed per 30-minute UTC slot from the same slot on earlier days only (a 15m bar maps to the slot containing its open), `available_utc` = the prior day's slot close, joined with `asof_join`; `lookahead_check` reports 0 columns; it is **not** in `DEFAULT_FEATURE_NAMES` (test: the H-02/H-01/H-04/C4H feature versions `f-079deacc61` and `f-51ac5ebabb` and the pinned config hashes and H-02 dump in `preregistration-2027Q1.yaml` are unchanged); it is its own group in O-4's map (not inside `activity`) and the group-size test passes. No feature-to-label statistic is computed. Not merged by 2026-12-11 -> O-4 is pinned without it (no delay).
