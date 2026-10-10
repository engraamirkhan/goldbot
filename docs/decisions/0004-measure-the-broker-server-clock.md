# 0004: Measure the broker's server clock; support NY+7 alongside Europe/Athens

Status: accepted (SRE, 2026-10-10)

## Context
MT5 returns server-clock stamps. The design resolves them with `zoneinfo('Europe/Athens')` (EU DST dates). IC Markets
states that its MT4/MT5 server time "is based on '5 pm New York'" and announces the GMT+2 -> GMT+3 change on the US
DST start date (10 Mar 2024, 9 Mar 2025) and the move back on the US DST end (4 Nov 2018). That clock, New York + 7 h,
differs from Athens by one hour in the US-EU gap weeks (2-3 weeks in March, 1 week in Oct/Nov). A wrong zone shifts
the session table, the rollover gate, the Friday weekend rule and every timestamp. In the likely failure mode
(Athens configured, broker on NY+7) ticks look an hour in the future, which the stale-tick rule does not catch.

## Decision
- Add `NY+7` as a `server_tz` value (`goldbot/data/timeutil.py`), computed from America/New_York + 7 h so US DST law
  changes arrive with the tz database. Vectorised code never hands the custom tzinfo to pandas (pandas treats it as a
  fixed offset); `server_wall` / `server_to_utc` handle it.
- Never trust either zone: a read-only probe (`goldbot/ops/server_clock.py`) compares the broker's latest tick with
  host UTC while the market is open and reports a > 5 min mismatch in health (fail, alerted) and preflight (blocking).
  A mismatch is an entry-only block; exits are never gated.
- Keep `Europe/Athens` in accounts.yaml for now: the engine's `_server_clock` (runner.py, another lane) cannot resolve
  `NY+7` yet, and the probe has not yet measured our terminal in a gap week.

## Consequences
- The first measurement is possible on Mon 26 Oct 2026; the owner halts entries for the gap week if it fails, until
  the runner change lands and `server_tz: NY+7` is deployed (HANDOFF).
- What would change this: a gap-week measurement showing UTC+2 on ICMarketsSC (then Athens stays), or IC Markets
  changing its policy (the probe would catch it).
- The probe relies on host time sync (chrony/w32tm, preflight `time_sync`); a host clock off by minutes reads as a
  mismatch, which is also a reason not to trade.
