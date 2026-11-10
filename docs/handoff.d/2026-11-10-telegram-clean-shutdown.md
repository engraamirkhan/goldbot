- 2026-11-10 (branch fix/telegram-clean-shutdown; follow-up to R119): the telegram service stops cleanly. The pump
  was started with `app.create_task` in post_init and never cancelled, so every stop logged "Task was destroyed but
  it is pending!" (journal 15:25:34 UTC). `post_shutdown` now cancels and awaits the pump (at most HEALTH_STOP_S)
  before the audit drain, so nothing is written after the drain, then stops a running health pass as before. No
  change to proposals, the 90 s confirm window, exits or the outbox. Tests: tests/test_telegram_health_task.py
  (`test_shutdown_cancels_the_pump_so_no_task_is_left_pending`, `test_shutdown_works_for_a_bot_whose_pump_never_started`).
