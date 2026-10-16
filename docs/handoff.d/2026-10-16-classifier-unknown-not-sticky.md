- Account classifier: a stored Unknown no longer sticks (implementer, 2026-10-16, SRE review finding;
  trading-safety Low applied). `PersistentClassifier.update` (goldbot/execution/classifier.py) lets a stored
  "unknown" adopt a known class on its first run when that cannot relax the account: the class is Standard, or Raw
  was proven by commission, or the last known class in the kept runs (last 20) is Raw or there is none. Otherwise
  (last measured Standard, now a spread-only Raw) the two-run rule applies. Before, a thin first run (< 1,000
  London/NY ticks) stored "unknown" and a known class then needed two agreeing Fridays, so the demo account would
  have stayed blocked by `account_class_unknown` until Fri 30 Oct instead of 23 Oct. Unchanged: Raw <-> Standard
  still needs two agreeing runs, and a known class falls back to Unknown only after two Unknown runs in a row.
  Entry-rule change: reviewed by `trading-safety-reviewer`. Tests in tests/test_risk_exec_webhook.py:
  ::test_a_stored_unknown_is_replaced_by_the_first_known_class_on_its_first_run,
  ::test_a_stored_unknown_adopts_standard_on_its_first_run,
  ::test_after_falling_from_standard_to_unknown_a_spread_only_raw_run_needs_a_second_agreeing_run,
  ::test_after_falling_from_standard_to_unknown_a_commission_raw_run_is_adopted_at_once,
  ::test_a_known_class_needs_two_agreeing_runs_to_change_to_another_known_class,
  ::test_a_known_class_does_not_fall_back_to_unknown_on_one_thin_week.
