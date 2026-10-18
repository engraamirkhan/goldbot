- **BACKLOG 89 (N1 `tod_periodicity`, implemented; awaiting quant-reviewer review).** Opt-in family in
  `goldbot/features/tod_periodicity.py` (`enable()`, as `day_type`), also listed in `pipeline.OPT_IN_FEATURES` so
  `DEFAULT_FEATURE_NAMES` and `f-079deacc61` / `f-51ac5ebabb` are unchanged. Six columns from the same 30-minute UTC
  slot on earlier days, `available_utc` = slot close, joined with `asof_join`; own family, so its own group under O-4's
  `family` map. Choices the amendment left open, for the quant reviewer: observations only from complete slots;
  windows need the full N observations; 5-day staleness cap; `slot_ret_over_atr` = latest observation / (ATR14 / close
  at that slot's last bar); columns are SIGNED (neutral 0, hit rate 0.5). Pin `tod_periodicity` into O-4's feature list
  by 2026-12-11 or it drops. Tests: `tests/test_tod_periodicity.py`.
