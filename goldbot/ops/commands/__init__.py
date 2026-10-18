"""The commands of `python -m goldbot.ops.run <command>`, one module per area; goldbot/ops/run.py dispatches through
the registry and re-exports every name it always had.

  services.py  the long-running services (goldbot/ops/services): supervisor, engine, bridge, api, webhook, scheduler,
               telegram, news
  drift.py     drift-review
  gates.py     record-gate, gates, gate-evidence
  costs.py     publish-costs (and the release uploader the scheduler shares)
  ops.py       backup / restore / restore-drill, setup, preflight, health, monthly-report, perf-report,
               bridge-smoke
  auth.py      revoke-sessions, mail-test
  research.py  owner-history, ruin, sizing-feasibility, tv-import, tv-repaint-check, tv-pull

Adding a command: write its handler in its area module with `@register_command("<name>", pos=...)` (registry.py
explains the order; lazy imports only), add its line to the usage text in goldbot/ops/run.py, re-export any function
callers use from run.py (import and `__all__`), and import any new area module here or its commands never register.
tests/test_ops_run_registry.py pins the names and their order.
"""
from goldbot.ops.commands import (  # noqa: F401 - registers the commands
    auth,
    costs,
    drift,
    gates,
    ops,
    research,
    services,
)
