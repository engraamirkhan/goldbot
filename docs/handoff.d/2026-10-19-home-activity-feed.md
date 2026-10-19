- Home live activity feed (owner request 2026-10-19: "no useful information on what is happening"):
  `GET /api/activity?limit=50` (viewer and above, read-only, built at most every 10 s) merges shadow trades opened and
  closed (taken only; state/shadow_book.json, stamped at their bars' close), account positions opened and closed
  (orders_<account>.json, closed-trade record), cost-probe orders opened and closed (aux.db `cost_probes`) and entry
  refusals from the decision journal (last 2 days; `gate:*`, `cost_probe_refused:*`, `auto_rejected:*`, ...; scores
  below the threshold left out), newest first, with agent, timeframe, side, prices, reason, R and P&L. Accounts by
  config name with digits cut; a source that cannot be read is named in `unreadable`. The Home page shows it full width
  (web/src/screens/Activity.tsx), refreshed every 15 s, coloured by kind. Code: goldbot/api/activity.py.
