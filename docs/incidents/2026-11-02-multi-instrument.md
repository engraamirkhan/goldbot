# 2026-11-02: the multi-instrument release left most books closed and the icm-probe bridge starved

Status: open (prevention items 1-9 shipped in the follow-up PRs; the probe box upgrade, item 10, is with the owner)
Severity: S2 a service degraded on demo accounts; no real money; broker-side stops held throughout
Author: sre. Reviewed by: code-reviewer, trading-safety-reviewer, security-reviewer (on the PRs).

## Summary
The 2026-11-02 multi-instrument release (20 books on icm-probe, 8 on vantage-demo) went out in two deploys to the
brain and the MT5 boxes between 17:30 and 18:38 UTC. Three faults met. Every non-gold book on both accounts read
"market closed" while its market was open (a session read that took today's unfinished day as a whole day). The five
ICM index CFDs refused every entry as `swap_unknown`, shown as "terms not loaded". And the icm-probe MT5 box, an
E2.1.Micro, had no CPU left for 20 books: its bridge took 4 minutes to serve after each restart, reads timed out
every few minutes, and the deploy reported success 2.5 minutes before the bridge answered. On the brain, a health
pass slowed by those timeouts falsely showed vantage-demo BLOCKED.

## Impact
- **Entries.** 14 open ICM books (FX, energy, metals) and all 7 Vantage non-gold books refused entries as
  `market_closed` from each engine's start (17:3x and again 18:33 / 18:38) until the fix (PR #317) is deployed. The
  5 index CFDs refused entries as `swap_unknown` over the same window. Gold kept trading on every account.
- **icm-probe bridge.** Two unreachable windows of about 4 min (from 17:30:46 and from 18:31:46), both caused by
  deploy restarts. In the second, the engine crash-looped every ~13 s on `BridgeUnavailable` (18:33:32 to 18:35:40).
  Between restarts, `broker_terms: ReadTimeout` at 17:35:50, 18:27:16, 18:37:28 and 18:39:15. Loop passes took
  34.6-52.6 s against a 5 s pass budget ("pass budget 5.0 s spent after N of 20 books" on almost every pass from 17:38).
- **Exits.** No exit was lost. While the probe bridge was down the engine could not move a stop or close by itself.
  Every position kept its broker-side stop, and the engine reconciled with the broker on its restart. Demo accounts only.
- **Health.** False BLOCKED on vantage-demo ("supervisor's report is stamped ahead") while the probe bridge was slow.
  It never stopped a real entry: engines judge that rule on their own clock.
- **Money.** None at risk (demo). The cost is lost learning: most non-gold books took no trade on their first day.

## Timeline (UTC)
| Time | Event |
| --- | --- |
| 17:30:11 | MT5 box (icm-probe): `goldbot-deploy` of the first multi-instrument commit; bridge restarted 17:30:46 |
| 17:30:46-17:33:30 | probe bridge not serving (2 min 44 s; no symbols selected yet) |
| 17:34:52-17:40:55 | probe bridge logs "`<method>` not run, its client went while it waited"; first engine `ReadTimeout` 17:35:50 |
| 17:38 | engine passes run 35-52 s; "pass budget 5.0 s spent" on almost every pass |
| 18:25:01 | brain deploy stops the icm-probe engine; it restarts and reads `trading_sessions` for 19 books in turn (18:26:17 to 18:26:51) |
| 18:31:25 | MT5 box (icm-probe): `goldbot-deploy` of 27753bd6; bridge restarted 18:31:46, started 18:31:55 |
| 18:33:20 | brain deploy (27753bd6) stops the engine again; the engine crash-loops on `BridgeUnavailable` from 18:33:32 |
| 18:33:28 | MT5-box deploy passes its stability window and reports `deployed` (the bridge is active, not serving) |
| 18:35:54 | probe bridge "serving" (4 min after its start) |
| 18:33 / 18:38 | engines started: every non-gold book reads its hours from a day that ends at the read and closes a minute later |
| 18:46-18:52 | SRE diagnosis (read-only): PSI, steal, RSS, journals, `engine_icm-probe.json`, `instruments.db` |
| 18:57 | owner asked: upgrade the probe box (~£4.52/month) or cut it to 8 books |
| 19:07 | CI issue #318 (bridge protocol digest) on PR #317 |
| 19:37 | owner approves the upgrade |
| 19:37:45 | in-place resize to VM.Standard.E5.Flex refused by OCI: "belongs to a hardware series that is not compatible with the current shape: VM.Standard.E2.1.Micro" |
| 19:38:21 | the next resize attempt is blocked by the session's auto-mode classifier; the owner is given the command |

## Root cause
1. **Sessions from an unfinished day.** `MT5Broker.trading_sessions` builds each weekday's hours of a non-gold symbol
   from the latest day that has M1 bars. For today's weekday that day is today, whose bars stop at the read. Every
   engine start or session refresh rebuilt the table, and each book closed about a minute after it was read. Health
   counts `market_closed` as fine, so nothing flagged it. Nothing tested a read made in the middle of a trading day.
2. **Swap units not priced.** `instruments_db.swap_per_lot` priced only swap modes 0 and 1. The ICM index CFDs report
   modes 2 and 3 (amounts in the base or margin currency). Their swap was stored NULL and their entries were refused,
   which was correct (a cost the engine cannot price is never 0). But the state mapped to `terms_missing`, so the
   dashboard and health said "terms not loaded", which sent the reader to the wrong cause.
3. **A box too small for 20 books.** The probe box is an E2.1.Micro (2 vCPUs burstable from a 1/8 OCPU baseline,
   1 GB). At 18:46-18:52:
   `vmstat` steal 58-77 %, CPU pressure (PSI `some`) avg10/60/300 = **52 / 50 / 47 %**. For comparison, the icm-demo
   box (the same shape, 1 symbol) shows 13-17 % and the Vantage box (E5.Flex, 1 OCPU at a 1/8 baseline, 2 GB, 8 books)
   shows 5 %. Memory was not the limit: 431 MB available, python.exe RSS 202 MB, terminal 87 MB, swap used 550 MB with
   swap-in and swap-out near zero. Every call of 20 books queues on one terminal (the MetaTrader5 package is not
   thread-safe), and the VM got about a quarter to a third of its CPUs. The engine start made it worse: 19 session
   reads in a row, each 14 days of M1 bars, and the bridge kept working on each after the engine had given up at 5 s.
4. **Why the process let it through.**
   - The MT5-box deploy judged "active and not restarted for 30 s". A bridge is a Wine process that is active long
     before it serves.
   - The bridge had no slow-call or lock-wait signal, so the contention showed only indirectly (client timeouts).
   - The release was sized for memory (the Max bars and swapfile steps in the RUNBOOK) but not for CPU. No PSI budget
     was set for an MT5 box.
   - Two deploys went to the probe box during an active session, each one costing a 3-4 minute bridge outage.
   - Health's entry-status path judged "stamped ahead" against the start of its own pass, unlike its other checks.

## Detection
Found by the owner on the dashboard ("1 of 20" / "1 of 8 can enter", "terms not loaded") and by the SRE diagnosis
about 75 min after the first deploy. It should have been found by:
- the MT5-box deploy itself, which should not report success before the bridge answers (now item 1);
- the bridge, whose slow calls and lock waits should show on `bridge_compat:<account>` (now items 2-3);
- a test of a session read in the middle of a trading day (now item 5).

## Recovery
- PR #317: sessions from complete server days only; mode 2/3 swaps priced when their unit is the profit currency
  (or the base currency, times price); health's "stamped ahead" judged against the later of its pass start and now.
  Deploy it to the brain and to both multi-instrument MT5 boxes. The MT5 boxes are needed for the `currency_base` /
  `currency_margin` fields (bridge protocol v11) and for the session read, which runs on the bridge.
- Probe box: owner approved the upgrade (~£4.52/month). OCI refuses an in-place resize from E2.1.Micro to E5.Flex.
  The next step is an E4.Flex resize (same AMD family, untried), else a new E5.Flex box re-hosting icm-probe
  (RUNBOOK 0.3b). Until then: no deploy to the probe box during active trading, and the books it trades stay as they are.
- RUNBOOK: "Bridge contention on an MT5 box", the MT5-box bullet in 0.5 Daily use, and the instrument terms file.

## Prevention
| # | Item | Type | Where | Status |
| --- | --- | --- | --- | --- |
| 1 | An MT5-box deploy passes only once every bridge that ran before answers GET /health (any terminal token) within 8 min of the switch; otherwise it rolls back | check + test | `goldbot/ops/linux/goldbot-deploy.sh` `bridges_answer`; tests/test_goldbot_deploy_sh.py | shipped (fix/sre-mi-followups) |
| 2 | The bridge logs every call holding the terminal over 2 s (method, seconds, requests waiting) and reports `lock_wait_s` / `slow_calls` on /health | check + test | `goldbot/execution/bridge.py`; tests/test_bridge_slow_calls.py | shipped (fix/sre-mi-followups) |
| 3 | `bridge_compat:<account>` WARNs on a contended terminal (wait of 10 s or more, or 10 or more slow calls in 5 min), read from the GET it already makes | check + test | `goldbot/ops/bridge_compat.py` `contention` | shipped (fix/sre-mi-followups) |
| 4 | Engine start makes no blocking non-gold session read: tables cached 24 h in `instruments.db`, reads staggered one per pass after the loop starts; 8 observed days instead of 14 | test | `goldbot/engine/session_cache.py`, `goldbot/ops/services/engine.py`; tests/test_session_cache.py | shipped (fix/sre-mi-followups) |
| 5 | Session template from complete server days only | test | `goldbot/execution/mt5_adapter.py` (`observed_template(before=today)`) | shipped (PR #317) |
| 6 | Mode 2/3 swaps priced where the unit is known; bridge protocol v11 for `currency_base` / `currency_margin` | test | `goldbot/engine/instruments_db.py`, `goldbot/execution/bridge.py` | shipped (PR #317; v11 pin in fix/sre-mi-followups) |
| 7 | `swap_unknown` reads "swap not priced: entries refused, exits still close" (engine, API, dashboard, health) | test | `goldbot/engine/books.py`, `goldbot/ops/checks/engine.py`, `web/src/screens/EntrySymbols.tsx` | shipped (fix/sre-mi-followups) |
| 8 | Health's "stamped ahead" judged against the later of its pass start and now | test | `goldbot/risk/supervisor.py` | shipped (PR #317) |
| 9 | Rule: no deploy to the probe box during active trading (a deploy costs about 4 min of bridge on that box) | runbook | RUNBOOK 0.5 "Daily use", "Bridge contention on an MT5 box" | shipped (fix/sre-mi-followups) |
| 10 | Probe box upgraded to 1 OCPU / 2 GB (E4.Flex resize, else a new E5.Flex box); PSI `some` avg300 re-measured under 20 % with all books on | decision (owner-approved spend) | OCI; RUNBOOK 0.3b | open: owner approved 2026-11-02 19:37 UTC; in-place E5.Flex refused; E4.Flex resize or re-host by 2026-11-04 |
| 11 | A CPU budget for MT5 boxes: PSI `some` avg300 under 20 % with every book on, measured after any change to an account's book list | runbook | RUNBOOK "Bridge contention on an MT5 box" | shipped (fix/sre-mi-followups) |
| 12 | Review of #319: bridges stay un-restarted 30 s after their last answer (watcher 90 s from it); INT/TERM ignored from the switch to the record and the MT5 deploy run under `systemd-run --wait`; after a rollback the old bridges must answer or the record is `failed` | check + test | `goldbot-deploy.sh`, `mt5-box-upgrade.sh`; TRACEABILITY X24 | shipped (fix/sre-mi-followups) |
| 13 | Review of #319: session tables cached only from bridge protocol v11 (unmarked rows ignored); a hung session read no longer holds the others; "x price" only for fx and metal pairs; the probe with an empty environment; the largest /health body within the probe's 1024 bytes | test | X25-X27 | shipped (fix/sre-mi-followups) |

## Lessons
- **Went well.** Every exit had a broker-side stop, so the outages cost learning, not money. Entries on an unpriced
  swap were refused, not priced at 0. The read-only diagnosis found all three causes in about 6 minutes, using
  evidence already on the boxes (PSI, the journals, the engine's state file).
- **Went badly.**
  - "Active" was taken as "serving" for a Wine bridge.
  - The release planned memory for 20 symbols on a 1/8-OCPU box but not CPU.
  - Health hid a whole-account outage of non-gold entries as "market closed", which looks normal.
  - Two deploys went to a starved box in the middle of the session.
- **Lucky.** It happened on demo accounts on a quiet Monday evening. The same release on a live account would have
  queued the closes of 20 books behind one terminal on the smallest VM we run.
- **Standard raised.**
  - An MT5-box deploy is judged on the bridge answering, not on the unit being active.
  - Any change to an account's book list comes with a measured CPU budget (PSI), as memory already did.
