- Progress tab API (ROADMAP W2-3, implementer, 2026-10-17; spec docs/ux/progress-screens.md, ADR 0022, TRACEABILITY
  U21): three new read-only routes for viewer and above. `GET /api/gates` evaluates the gates and the stop rule on
  request, writes nothing and caches the result for 60 s. `GET /api/attribution` serves the daily results without the
  trade rows or calibration. Each row keeps its own bar and se (quant F1), and exit target/stop rows read "by
  construction" (quant F2). `GET /api/trades/summary` gives each account's last 30 opportunity days.
  - New setting `gates.signed_off: []` records which PROPOSED gate thresholds the owner has signed off. It changes no
    value, and an unknown or non-proposed name fails at load.
  - The `gate-evidence --detail` note now prints only in the CLI (`GateItem.detail`) and is no longer part of the
    evidence text.
  - Reviews needed: quant (verdict exposure, "by construction", the gates' iid 95% interval), security (read access,
    identifier cutting) and code. The web screens are the next lane.
