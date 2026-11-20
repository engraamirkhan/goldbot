# 0094: MT5 bridges restart only for bridge changes, outside market hours, and a deploy's own alerts are held

Status: proposed (sre, 2026-11-20)

## Context
The 2026-11-20 SRE audit (window 11-06..11-20) found that our own operations cost trading minutes and alert trust:
- 42 deploys in 14 days, and every one stopped and restarted all three MT5 bridges, most of them in market hours,
  although only 6 of the 72 commits on main in that window touched a file the bridge runs. Each bridge restart costs
  the engine its broker for the restart and then blocks entries until a decision bar closes clean (up to 15 min on
  15m, 60 min on 1h).
- About 63% of new Telegram alerts were caused by our own deploys: `tick_errors:<account>` (52 episodes, nearly all
  the 15-minute memory of the BridgeUnavailable burst a bridge restart causes) and `bridge_compat:<account>` (23,
  commit and confirmation gaps while the boxes roll out). The one real risk of the window (the vantage stale quote)
  sat among warnings like these for 10 days.
- The bridge's bind had no wait: a restart whose old process, or Wine's server holding that process's socket, had not
  let go of the port crashed at once (the 11-04 icm-demo outage mode).

## Decision
1. **A bridge restarts only when a bridge file changed.** `goldbot/ops/bridge_files.py` `BRIDGE_FILES_RE` names the
   files whose code runs in the bridge or its order lane, the config it reads at start and its package pins.
   goldbot-deploy carries the same string (root never runs repo code); a test pins the two equal and walks every
   goldbot import of the set, failing on one that is neither in it nor named in `NOT_FOLLOWED` with its reason. An
   MT5 forward deploy without a bridge change moves the checkout and restarts nothing, after the target's start
   check (`python -m goldbot.ops.bridge_files check <accounts>` as the service user: imports, settings, accounts,
   symbols and magic blocks resolve) says ok. A problem or a timeout there puts the old checkout back and refuses.
   A check that cannot run falls back to the full deploy. If the old checkout cannot be put back, the record is
   `failed` ("checkout NOT restored"), never "kept".
   The diff is taken against the checkout, not the bridge's running commit. That is exact: every checkout move
   without a bridge restart changed no bridge file, so a running bridge's files always equal the checkout's. Reading
   the running commit would need the root-run probe to pass on more of the /health body than one token, which its
   security conditions forbid. A hand-made checkout on the box is the only exception: it is out of process.
2. **Bridge restarts are held in market hours.** On an MT5 box a deploy that changes a bridge file is refused Mon-Fri
   07:00-21:00 UTC (exit 75, nothing changed) unless `--now`, which Claude's goldbot-deploy-all passes for safety
   fixes only. Rollbacks are recovery and are never held. The approval path rejects `--now`, so an owner-approved
   safety fix that changes a bridge file waits until 21:00 UTC unless Claude deploys it with `--now`.
3. **A deploy's own alerts are held for its own window.** goldbot-deploy on the brain writes
   `state/deploy_window.json` from its switch to 20 min after its record. goldbot-deploy-all opens
   `state/deploy_window_manual.json` (`python -m goldbot.ops.deploy_window open --minutes N`, at most 30, re-run to
   extend; an extension past 2 h starts a new window) on the brain before it deploys the MT5 boxes. Inside a window new `tick_errors:` and `bridge_compat:` alerts wait in the hysteresis
   state. One still bad when the window ends is told on the next poll, and one that clears inside it is not a flap.
   Nothing else is held: `bridge:` (5 min, immediate), `engine_loop:` and `heartbeat:` still alert a real outage.
   A window file that cannot be read, or that spans more than 2 h, holds nothing.
4. `bridge_compat:<account>` is ok, not a standing warning, when git says the bridge's commit and the brain's differ
   only outside the bridge's files (the expected state after decision 1).
5. **The bridge waits for its port.** `bind_server` retries an address-in-use bind with a doubling backoff
   (0.5 s .. 5 s) for at most 30 s, then exits 77 with an ERROR naming the port. `StartLimitIntervalSec=0` stays:
   a start limit would leave the account without exits until someone acts. The loop is detected by `bridge:`.

Options weighed: (a) a curated bridge file set, chosen; (b) the full static import closure, rejected because the
bridge imports `goldbot.ops.run`, which imports every service: the closure is about 300 files, so almost every
commit would restart the bridges; (c) restarting always and only deferring to the daily break, rejected because a
doc-only fix would still wait a day. Brain engine restarts are left out of the market-hours rule for now: they cost
about 21 s plus the entry block, and holding them would hold every brain fix too. That needs a PD/CTO decision.

## Consequences
- Measured on main's 72 first-parent commits of 11-06..11-20: 41 (57%) change no bridge file and would have
  restarted nothing. The 31 that do are mostly `goldbot/config.py` (19) and `config/settings.yaml` (21). The bridge
  reads only `mt5_login` from them, but both stay in the set because a narrower textual rule would be fragile. The
  follow-up is to move `Mt5LoginSettings` and the bridge's three `account_*` helpers (now in
  `goldbot/ops/services/engine.py`) into bridge-side modules. That would bring it to about 10 of 72. A bridge change
  in market hours waits until 21:00 UTC unless it is a safety fix.
- A file wrongly left out of the set leaves a bridge on older code until its next restart. The import-walk test and
  the start check bound this: the bridge loads its modules and config at start only, and its lane starts once.
- After a checkout-only deploy the bridge reports its older commit on /health. bridge_compat reads that as ok
  through git on the brain.
- goldbot-deploy-all (Claude's, outside the repo) holds the brain whenever any MT5 box is not on the target
  (refused for the window, failed, or rolled back; trading-safety review M1). A brain on code that needs a new
  bridge method would refuse entries (`bridge_incompatible`) until the boxes catch up.
- What would change this decision: a bridge-path regression that the set missed (widen the set or follow the cut),
  or a measured cost of the 21:00 deferral that is higher than one restart's lost minutes.
