- Progress tab API (ROADMAP W2-3, implementer, 2026-10-17; spec docs/ux/progress-screens.md, ADR 0022, TRACEABILITY
  U21): three new read-only routes for viewer and above. `GET /api/gates` evaluates the gates and the stop rule on
  request, writes nothing and caches the result for 60 s. `GET /api/attribution` serves the daily results without the
  trade rows or calibration. Each row keeps its own bar and se (quant F1), and exit target/stop rows read "by
  construction" (quant F2). `GET /api/trades/summary` gives each account's last 30 opportunity days. That is 30
  calendar days, about 22 trading days, and `days_traded` counts only Monday to Friday.
  - Review round 1 (quant, security and code all APPROVE on b8c33a5), fixed in one commit:
    - "by construction" rows also drop `ci95_net`, `t_stat` and `se`.
    - Gate items carry `ci95_basis` ("per-trade iid, return"), and gates carry `met_on_proposed`.
    - The `account_id` inside each cost table is cut too.
    - The gates cache builds outside its lock and serves the old value during a rebuild.
    - The route tests scan both bodies for emails and bare 5+ digit runs.
  - New setting `gates.signed_off: []` records which PROPOSED gate thresholds the owner has signed off. It changes no
    value, and an unknown or non-proposed name fails at load.
  - The `gate-evidence --detail` note now prints only in the CLI (`GateItem.detail`) and is no longer part of the
    evidence text.
  - Reviews needed: quant (verdict exposure, "by construction", the gates' iid 95% interval), security (read access,
    identifier cutting) and code. The web screens are the next lane.
