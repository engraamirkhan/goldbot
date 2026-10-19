- **Dedicated probe account `icm-probe` (ADR 0037 amendment 4, R70).** config/accounts.yaml adds `icm-probe` (demo,
  GBP, `enabled: false`, `probe_only`, `shadow_host`, its own £20 day with `budget_share` 0.8) and gives `icm-demo`
  an explicit `shadow_host: true`; the shadow host is now that flag (on the canonical-cost broker), not the broker
  alone, and two enabled hosts fail at load. A probe_only engine trades no live agent and refuses any non-probe entry
  (`probe_only_refused`); the supervisor keeps engine files with literal `probe_only: true` out of every combined sum,
  cap, currency check and exposure (reported under `probe_engines`). Switch-over once the owner has stored icm-probe's
  credentials and the terminal exists: icm-probe `enabled: true`; icm-demo `shadow_host: false` and
  `cost_probe.enabled: false`; restart both engines. Reverse the three lines to go back.
