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
