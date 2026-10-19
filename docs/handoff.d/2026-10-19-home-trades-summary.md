- Home trades summary (owner request 2026-10-19: "a summary of all types of trades"): `GET /api/trades/overview`
  (viewer and above, read-only, built at most every 15 s) counts shadow trades (taken, and all signals with the
  counterfactual candidates), demo and live positions and cost probes for today, the last 7 days and all time
  (opportunity days, D-1 22:00 UTC): open now, closed, wins, losses, win rate, total and average R, net P&L by currency
  (net-of-cost rows only, never summed across currencies), best and worst trade, exit reasons (target/stop/time/other),
  and the same by strategy family and by timeframe. It reuses the activity feed's readers (goldbot/api/activity.py now
  returns whole `Trade`s, one parse for both); a closed-trade record without `pnl_currency` now shows in its account's
  currency on both. An open position whose engine state is missing counts as demo. The Home page shows it under the
  activity feed (web/src/screens/TradesOverview.tsx): a tile per type, Today / 7 days / All, and a table by strategy or
  timeframe, refreshed every 30 s. Code: goldbot/api/trades_overview.py.
