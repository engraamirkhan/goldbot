- **Backup and restore-drill errors keep the fix and the RUNBOOK pointer (2026-10-16; health text only; SRE
  review).** The scheduler job check cut a failed job's first error line at 160 characters, so a restic-not-configured
  message lost its trailing "RUNBOOK 'Backups'". The backup and drill job errors now start with `RUNBOOK 'Backups':`,
  the not-configured message names `goldbot accounts set <key>` before the key list, and the job check shows 300
  characters (`JOB_ERROR_CHARS`). Test: `test_unconfigured_restic_health_reasons_name_the_fix_command_and_the_runbook`
  runs both jobs through the real scheduler with no keys and checks `job:backup`, `job:restore_drill`, `backup_age`
  and `restore_drill`.
