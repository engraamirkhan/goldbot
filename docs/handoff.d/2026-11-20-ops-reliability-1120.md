- 2026-11-20 ops reliability (SRE audit 11-20: engineering debt 1, SRE findings 2, 3, 5 and 7; ADR 0094). Goal: no
  trading minutes lost to our own operations. Changes:
  - The bridge waits for its port: `execution/bridge.py bind_server` retries an address-in-use bind (errno 98/48,
    WSAEADDRINUSE 10048 under Wine) with a doubling wait of 0.5-5 s for at most 30 s. It then exits 77 with an ERROR
    naming the port. `bridge:<account>` FAILs and alerts if it does not recover. RUNBOOK "Bridge port held".
    `StartLimitIntervalSec=0` stays: a start limit would leave exits without a bridge.
  - MT5 deploys restart the bridges only when a bridge file changed (`goldbot/ops/bridge_files.py`
    `BRIDGE_FILES_RE`, the same string in goldbot-deploy, pinned and import-walked by tests/test_bridge_files.py).
    Without one: checkout only, after the target's start check (`python -m goldbot.ops.bridge_files check`) as the
    service user. A problem restores the old checkout and refuses; a check that cannot run falls back to the full
    deploy.
  - On an MT5 box, a deploy with a bridge change is refused Mon-Fri 07:00-21:00 UTC (exit 75, nothing changed)
    unless `--now` (safety fixes). A rollback is never held.
  - A deploy's own `tick_errors:` and `bridge_compat:` alerts are held while `state/deploy_window.json`
    (goldbot-deploy on the brain: from the switch to the record + 20 min) or `state/deploy_window_manual.json`
    (`python -m goldbot.ops.deploy_window open --minutes N`, for goldbot-deploy-all before the MT5 boxes) is open.
    Held means deferred: one still bad after the window is told at once, and one that clears inside it is no flap.
  - `bridge_compat:<account>` is ok when the bridge's and the brain's commits differ only outside the bridge files
    (git on the brain).
  Hosts (Claude-run, 2026-11-20): see the SRE hand-back for the brain swapfile and the one backup prune.
  To install: goldbot-deploy.sh changed, so install the root copy on all four hosts (RUNBOOK 0.5 "Installing a new
  goldbot-deploy"). The first bridge-file deploy runs in the daily break.
