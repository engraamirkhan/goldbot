"""The health checks, one module per area; `goldbot.ops.health` runs them and re-exports every name it always had.

  engine.py      engine and loop: engine state, loop heartbeat, tick errors, failed orders
  bridge.py      MT5 bridge: /health, compatibility, broker server clock
  feeds.py       feeds and data: costs, P&L basis, data quality, drift, model watch, feature versions, news, reconciliation
  risk.py        risk and limits: supervisor, owner halt, risk state, phase and gates, loss caps, throttle, open
                 risk, entry windows
  services.py    services and heartbeats: scheduler and jobs, heartbeats, schema skew, approvals, alerts, dead man
  auth.py        auth and mail: keyring secrets (presence only), the email sign-in link
  operations.py  ops and backups: settings, accounts, disk, deploys, backups, retention, restore drill, agent spend

Adding a check: write it in its area module with `@register_check(scope, block=..., pos=...)` (registry.py explains
the order) and put a threshold it alone uses next to it; the shared table in _base.py is for thresholds several checks
quote. run_checks picks the check up from the registry. Also re-export it from goldbot/ops/health.py (import and
`__all__`; tests/test_health_registry.py enforces it), and import any new area module here or its checks never register.
"""
from goldbot.ops.checks import (  # noqa: F401 - registers the checks
    auth,
    bridge,
    engine,
    feeds,
    operations,
    risk,
    services,
)
