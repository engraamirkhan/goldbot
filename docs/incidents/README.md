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
