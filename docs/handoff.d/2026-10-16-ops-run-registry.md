- Service entry points split into modules with a command registry (2026-10-16, speed-up review item 7; no behaviour
  change). goldbot/ops/run.py was the file every lane edited to add an entry point (19 hand-resolved merges). The
  services now live in goldbot/ops/services/, one module each (engine, supervisor, bridge, api, webhook, scheduler,
  telegram, news). The commands live in goldbot/ops/commands/, one module per area, and each registers its handler
  with `@register_command(name, pos=)`. Positions are explicit and keep the old dispatcher's order, so the order does
  not depend on import order. run.py keeps the usage text and a small `main` that looks the command up, and it
  re-exports every name it had. Moved code calls other moved names through goldbot.ops.run (`resolve` / `patched` in
  services/_base.py), so `patch("goldbot.ops.run.x")` still takes effect. Importing run.py still loads no third-party
  package, which matters for the Wine bridge. Tests: tests/test_ops_run_cli_golden.py was captured before the split. It
  pins the usage text, every side-effect-free `--help`, which function each command reaches and with which arguments,
  the systemd, PowerShell and deploy invocations, and the import closure. tests/test_ops_run_registry.py pins the
  registry order, the re-exports and the call-through rule.
