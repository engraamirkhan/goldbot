# Incidents

One blameless postmortem per incident: `YYYY-MM-DD-<slug>.md`, copied from `_template.md`. An incident is anything
that stopped, or could have stopped, an exit, an alert or a service (engine, bridge, supervisor, backups), or made a
deploy or bootstrap fail on a real host. The SRE writes it within 24 h. A postmortem is closed only when its
prevention items are shipped (a check, a test or a runbook step, linked from the "Prevention" table) or handed to the
owner with a date.

Blameless: describe what the system and the process allowed, not who did it.

| Date | Incident | Severity | Status |
| --- | --- | --- | --- |
| 2026-10-16 | [MT5 refuses to run under wine-stable ("A debugger has been found")](2026-10-16-mt5-debugger-found-under-wine.md) | S3 | open (ADR 0019 with the owner) |
| 2026-10-16 | [MT5 login to ICMarketsSC-Demo silently never connects (generic installer)](2026-10-16-mt5-login-generic-installer.md) | S3 | closed (ADR 0020) |
| 2026-10-16 | [First restore drill fails on a restic lock the no-delete key cannot remove](2026-10-16-restic-lock-no-delete-key.md) | S3 | open (drill on the brain after deploy) |
| 2026-10-20 | [A failed deploy's rollback left api, scheduler, telegram, news and tv-pull stopped](2026-10-20-deploy-rollback-left-services-stopped.md) | S1 | open (install the fixed goldbot-deploy on the brain) |
| 2026-11-01 | [A broker-side "Service is not available" latched vantage-demo's login off for 6 h](2026-11-01-vantage-demo-transient-auth-latched.md) | S2 | closed |
| 2026-11-02 | [The multi-instrument release left most books closed and the icm-probe bridge starved](2026-11-02-multi-instrument.md) | S2 | open (probe box upgrade with the owner) |
| 2026-11-09 | [vantage-demo entered nothing for 9.5 h of open market and nothing alerted](2026-11-09-vantage-demo-entry-silence.md) | S2 | open (vantage supply with PD; detection shipped) |
| 2026-11-10 | [The Telegram deploy check failed on every pass (git fetch under ProtectSystem=strict)](2026-11-10-deploy-check-exit-255.md) | S3 | open (deploy the fix on the brain) |
| 2026-11-13 | [Trial-registry sync failed for 13 days with no alert (HTTP 403, then an unpublished outbox)](2026-11-13-registry-sync-silent.md) | S3 | open (detection R140; deploy and publish: BACKLOG 215, 217) |
