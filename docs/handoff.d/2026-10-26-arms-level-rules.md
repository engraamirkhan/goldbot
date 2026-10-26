- Arm rules `pdc_break` (PDC1/PDC4) and `ob_retest` (OBR15) (2026-10-26, wave 2 lane W2b; ARM-SPEC
  `arms-2026-10-25.md` 8.5, 8.6, 8.13) in goldbot/research/arm_rules_levels.py, registered in `arm_rules.RULE_FUNCS` and
  `arms.RULES`; no config rows. PDC skips the reopen bar (8.13) and trades only a break-close against the reopen bar's
  side; OBR uses pivots confirmed by the close of b-1. Both apply 8.0's 16:55-17:00 / 18:00-18:30 New York no-entry.
  **PDC is NO-GO**: the frozen rule counts 727 (4h) / 689 (1h) events on 2010-2017 against slice G's 600 / 592; the gap
  is key_levels_discovery.py's unregistered 0.05 ATR reference-distance skip (with it 600 / 611). A dated amendment is
  owed before PDC1/PDC4 deploy (strict-xfail test `test_pdc_history_count_reproduces_slice_g_within_ten_percent`).
  G1A/G1C (`level_break`) not built: the engine's 200-bar window cannot supply previous-week/month levels at 1h.
