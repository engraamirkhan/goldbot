- 2026-11-28 main's backend check unblocked (ci #520, #522): GS1's owner experiment ended after 2026-11-27
  (`research.arms.OWNER_EXPERIMENTS`), so from 28 Nov the loader refuses GS1 and every control naming it (GS1C, and
  SRC15 in the replay test) on the wall clock, as designed. Test-only fix, no engine/risk/execution change:
  test_growth_test and test_qualify_before_demo read the expiry (from PR #524); test_engine_replay_identity starts its
  March 2025 replay on March 2025's clock (`arms._now` pinned to the replay's first day), golden unchanged.
