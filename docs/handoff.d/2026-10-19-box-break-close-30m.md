- Owner directive 2026-10-19 (more demo learning trades): `box_break_close` also runs on 30m (shadow roster amendment
  2026-10-19b, docs/research/shadow-roster-2026-10-19.md; roster 4 of 40, K +1). 30m is a known timeframe end to end
  (`TF_SECONDS`, store table `bars_30m`, release resample `HIGHER_TFS`, scripts/build_bars.py); time exit 72 bars
  (36 h). Not 5m: the engine's base clock is 15m, so a 5m agent would decide one bar in three; it needs a 5m base clock
  first (engine/exit-path lane, trading-safety). No money-flow rule agent exists to extend. Deploy: re-sync release bars
  on the brain so `bars_30m` exists (the replica bootstrap reads it); the engine service creates the 30m RuleModel
  challenger itself at load (`ensure_rule_challenger`).
