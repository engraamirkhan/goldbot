- 2026-11-12 (branch fix/restart-gap-other-shadow-reads; backlog 174, R132): the last shadow-book readers follow the
  R129 rule for `restart_gap` trades (statistics leave them out; risk guards keep their losses, drop their wins).
  Auto-mode evidence (`automode.match_outcomes`) leaves them out, win or loss, and says how many in its reasons; the performance report counts the
  proposal unmatched with "shadow path incomplete across a restart (restart_gap)", out of its R, money and
  calibration; the ruin estimate (`ruin.r_from_shadow_book`) keeps a gap loss and drops a gap win, counted and noted in the report. Finding on the
  exploration 1m/5m minute pass: its last decided minute was memory-only, so a restart inside that minute decided it
  again and a signal refused there could pass at a later price (reproduced in the test). Fixed: on the host clock an
  engine start counts every complete minute as decided, with or without an orders file (`exploration.start_minute`,
  journalled `exploration_start_minute`);
  at most one minute's signals are lost per restart. Replays on the tick clock are unchanged.
