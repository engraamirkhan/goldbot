- Deploy offer works again under the telegram sandbox (R120, incident docs/incidents/2026-11-10-deploy-check-exit-255.md):
  `DeployWatch.check()` no longer runs `git fetch`. Under ProtectSystem=strict that could not write /opt/goldbot/.git
  and exited 255 on every pass, so no version was ever offered. The check now reads origin main with `git ls-remote`
  (30 s timeout, no credential prompt) and asks GitHub's compare API for the fast-forward, the commit subjects (newest
  15, each cut to 120 chars) and the MT5-box ancestry. GitHub answers are streamed and capped at 5 MB and 30 s; 5 MB
  rather than 2 MB because a compare carries the patches. If git or GitHub fails, nothing is offered and one warning
  is logged per outage, with no traceback. Each successful read goes to state/deploy/check.json. The new health check
  `deploy_read` warns and tells the owner after 2 h without one (RUNBOOK 0.5 "No new version offered"). Deploys
  themselves are unchanged (`sudo goldbot-deploy`).
  **Post-deploy host check (brain):**
  `sudo systemd-run --uid=goldbot -p ProtectSystem=strict -p ReadWritePaths=/opt/goldbot/state --wait --pipe -d /opt/goldbot /opt/goldbot/.venv/bin/python -m goldbot.ops.deploy check`
  must print JSON or `null` with no warning. An offer it prints is recorded as pending, so Telegram does not repeat it.
  The incident closes once this check passes.
