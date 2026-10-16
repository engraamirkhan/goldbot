- Account classifier: a stored Unknown no longer sticks (implementer, 2026-10-16, SRE review finding).
  `PersistentClassifier.update` (goldbot/execution/classifier.py) treats a stored "unknown" exactly like "never
  classified", so the first known class (raw or standard) is adopted on its first Friday run. Before, a thin first
  run (< 1,000 London/NY ticks) stored "unknown" and a known class then needed two agreeing Fridays, so the demo
  account would have stayed blocked by `account_class_unknown` until Fri 30 Oct instead of 23 Oct. Unchanged: Raw
  <-> Standard still needs two agreeing runs, and a known class falls back to Unknown only after two Unknown runs
  in a row. Entry-rule change: routed to `trading-safety-reviewer`. Tests:
  tests/test_risk_exec_webhook.py::test_a_stored_unknown_is_replaced_by_the_first_known_class_on_its_first_run,
  ::test_a_known_class_needs_two_agreeing_runs_to_change_to_another_known_class,
  ::test_a_known_class_does_not_fall_back_to_unknown_on_one_thin_week.
