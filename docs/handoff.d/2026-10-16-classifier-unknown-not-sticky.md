- Account classifier: a stored Unknown no longer sticks (implementer, 2026-10-16, SRE review finding;
  trading-safety Low and Option B applied). `PersistentClassifier.update` (goldbot/execution/classifier.py) lets a
  stored "unknown" adopt a known class on its first run when that cannot relax the account: the class is Standard,
  or Raw was proven by commission, or `state["last_known"]` is Raw or None. Otherwise (last stored Standard, now a
  spread-only Raw) the two-run rule applies. `last_known` is the last raw/standard class actually stored as the
  class (never a pending run) and never expires. Legacy state files without it are migrated on load: a known
  stored class is used as-is; a stored unknown with any standard in the kept runs reads Standard; otherwise None.
  Before, a thin first run (< 1,000 London/NY ticks) stored "unknown" and a known class then needed two agreeing
  Fridays, so the demo account would have stayed blocked by `account_class_unknown` until Fri 30 Oct instead of
  23 Oct. Unchanged: Raw <-> Standard still needs two agreeing runs, and a known class falls back to Unknown only
  after two Unknown runs in a row. Entry-rule change: reviewed by `trading-safety-reviewer`. Tests in
  tests/test_risk_exec_webhook.py:
  ::test_a_stored_unknown_is_replaced_by_the_first_known_class_on_its_first_run,
  ::test_a_stored_unknown_adopts_standard_on_its_first_run,
  ::test_after_falling_from_standard_to_unknown_a_spread_only_raw_run_needs_a_second_agreeing_run,
  ::test_after_falling_from_standard_to_unknown_a_commission_raw_run_is_adopted_at_once,
  ::test_raw_runs_that_were_only_pending_never_count_as_the_last_known_class,
  ::test_a_last_known_standard_does_not_expire_after_more_than_twenty_unknown_runs,
  ::test_a_legacy_state_file_without_last_known_is_migrated_from_its_class_and_runs,
  ::test_a_known_class_needs_two_agreeing_runs_to_change_to_another_known_class,
  ::test_a_known_class_does_not_fall_back_to_unknown_on_one_thin_week.
