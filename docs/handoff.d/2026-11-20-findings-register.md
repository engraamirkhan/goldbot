- 2026-11-20 findings register (owner: "feedback must close itself"; R174). The system audit found findings unowned
  for weeks (A4 failing a month, #392/#394 without an owner). Every finding and directive is now a row of
  `config/findings.yaml`: owner role (an agent in .claude/agents/), severity, due date, source and a CHECK naming a
  registered check with parameters (goldbot/ops/finding_checks.py: controls_to_signal_ratio, trades_per_day,
  bleeding_arms, mean_net_r, arms_not_trading, failed_but_filled, learner_outputs, health_ok, file_exists, file_text,
  state_field_set, github_issue_closed, github_security, manual). A row closes only when its check passes; `manual`
  is for steps with no data trail and closes only with done_on + evidence in a reviewed PR.
  - Seeded with 51 rows from the 2026-11-20 audit (finance A1-A6/P5-P10, performance leaks, SRE, engineering,
    security, research, team) and learning plan L1-L4: 22 measured, 29 manual. PD + PO confirm owners and due dates.
  - Daily `findings_check` job (07:15 UTC) re-measures every row -> aux.db `findings_history` (migration 41) and
    announces new, regressed, closed and past-due rows (an overdue row again every 7 days) in ONE comment a day on
    #34; critical/high events also one Telegram line. No token or a failed post: events stay pending for the next day.
  - Health `findings` (present once the job has run) WARNs while a critical/high row is past due and not closed; not
    on NOTIFY_ON_WARN (the job already announces it; avoids the standing-warn noise the SRE audit measured).
  - CLI: `python -m goldbot.ops.run findings [--stored] [--no-github] [--json]`.
  - Review round of ee15815e: open security weaknesses are `private: true` rows (SEC-1..7) with generic titles whose
    measurement never reaches #34 or Telegram; the money filter is case-insensitive and catches currency-first and
    "pounds"; a re-run of a slot keeps its pending or posted events; events are marked posted in one transaction right
    after the comment; unknown -> closed is silent; health WARNs (not FAILs) on a register that is not YAML.
  - Not done: the GitHub security check needs an admin token (the brain's reads "can't check", so the repository-settings row SEC-1 turns
    overdue on 2026-11-27 unless verified another way); P6 and P5 stay manual until the exit quote is a recorded field.
