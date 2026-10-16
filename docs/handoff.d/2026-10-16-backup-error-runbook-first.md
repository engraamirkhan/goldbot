- **Backup and restore-drill errors keep the fix and the RUNBOOK pointer (2026-10-16; health text only; SRE
  review).** The scheduler job check cut a failed job's first error line at 160 characters, so a restic-not-configured
  message lost its trailing "RUNBOOK 'Backups'". The backup and drill job errors now start with
  `RUNBOOK 0.6 'Backups and restore':`. Restic not set up reads "RUNBOOK 0.6 'Backups and restore': backups not set up
  on the brain; run goldbot accounts set <key> for: <keys>": no exception class name, no backticks, at most 200
  characters, the most the `backup_age` check shows. The job check shows 300 characters (`JOB_ERROR_CHARS`). Test:
  `test_unconfigured_restic_health_reasons_name_the_fix_command_and_the_runbook` runs both jobs through the real
  scheduler with no keys and checks `job:backup`, `job:restore_drill`, `backup_age` and `restore_drill`. The
  scheduler still prefixes the job check's line with `BackupError:`.
