- Health checks split into modules with a registry (2026-10-12, speed-up review item 7; no behaviour change).
  goldbot/ops/health.py had grown to about 1,770 lines and was the place every lane appended a check, so merges kept
  colliding there. The checks now live in goldbot/ops/checks/, one module per area (engine, bridge, feeds, risk,
  services, auth, operations), and shared thresholds and helpers are in `_base.py`. Each check registers itself with
  `@register_check(scope, block=, pos=)`. `run_checks` walks the registry in an explicit (block, position) order, so
  the report order does not depend on import order (goldbot/ops/checks/registry.py explains the blocks).
  goldbot/ops/health.py keeps the context, report, heartbeats, alerts and CLI, and re-exports every check, threshold
  and helper it had. Checks are called through it, so patching `health.check_x` still works. To add a check, edit
  only its area module. Tests: tests/test_health_registry.py compares names, statuses, reasons and order against a
  golden file captured from the old hand-written `run_checks`.
