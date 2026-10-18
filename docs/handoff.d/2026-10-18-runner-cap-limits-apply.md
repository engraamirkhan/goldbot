- ADR 0023 ratchet restored: the ADR 0032 limits code (profile resolve, 8% revert, owner reset, change log, decision
  tagging, published `limits_current`) moved from engine/runner.py into `goldbot/engine/limits_apply.py`
  (`EngineLimits`), with the published `limits`, minimum-lot stop risk and daily loss allowance as pure functions there;
  no behaviour change. runner.py 3,547 -> 3,443 lines, `LINE_CAP` lowered to 3443. `tv_grid.tv_refusal` also refuses
  one grid point listed twice in one `--variants` run (code review LOW).
