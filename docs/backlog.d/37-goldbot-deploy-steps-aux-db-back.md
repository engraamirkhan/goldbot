---
number: 37
rank: 36
title: '`goldbot-deploy` steps aux.db back on rollback (Linux `Sync-AuxSchema`)'
stage: Operational resilience · RUN
size: S
status: done (SRE, 2026-10-11; `goldbot-deploy.sh` `aux_plan`/`aux_step_back`; security review pending)
---

- **37–42. Deploy path gaps found while planning the October batch rollout (SRE, 2026-10-11;
  `docs/deploy/2026-10-batch.md` section "Gaps").** Each item is owner-approved, CI-gated and reversible. A
  root-run step never writes or follows a path the service user can write: state is touched only through
  `sudo -u <service user>`. Every item needs a security review. Tests: shellcheck at warning level in CI (a
  pre-commit hook for `goldbot/ops/linux/*.sh`; there is none today), plus a pytest that runs the script against a
  temporary repository and state directory with `systemctl` stubbed.
  - **Done 2026-10-11 (37-40):** `goldbot/ops/linux/goldbot-deploy.sh`, tests in
    `tests/test_goldbot_deploy_sh.py` (temporary git repository and aux.db, stubbed systemctl/sudo/curl), shellcheck
    pre-commit hook at warning level. The root-owned copy on each VM must be re-installed after the security review
    (docs/deploy/2026-10-batch.md 1.4). Deviations from the text below: the settings check validates only
    `settings.local.yaml` merged over the target's tracked `settings.yaml` (the tracked files passed CI with the target
    code; `accounts.yaml` is tracked and has no local overlay), and it runs on an extracted copy of the target's code
    before the checkout, so a refusal changes nothing. A failed manual rollback restores the version that ran before
    and logs `rollback_reverted`. Security review fixes (same day): root installs the script only from GitHub with
    an off-box sha256 (RUNBOOK 0.5), the bootstraps install units and the script from root's own clone, an optional
    root-owned `/etc/goldbot/rollback-floor`, `ran_here` counts only real runs, output to root's terminal is
    filtered, and `tests/test_root_tooling_rules.py` enforces the root-run tooling rule (docs/AGENT_STANDARDS.md).
  - **37 (HIGH).** In `switch_to`, after the checkout and before any restart:
    - when the target's `goldbot/db/migrations.py` lacks `compat_back_to` and `state/aux.db` is at schema 5, stop
      every `goldbot-*` service, run the step-back as the service user, then restart (same logic as
      `goldbot/ops/vps_update.ps1` `Sync-AuxSchema`);
    - refuse any other version and say why.

    Acceptance:
    - a deploy from a schema-5 commit back to a schema-4 commit ends `rolled_back`, not `failed`;
    - the dashboard opens after it;
    - test with a fixture DB at 4 and 5.
  - **38 (HIGH).** `sudo goldbot-deploy rollback <sha>`:
    - accepts only an ancestor of the running commit that is on `main` and passed CI;
    - takes the lock;
    - waits for any pending approval;
    - runs the same restart order, stability check and aux.db step-back;
    - logs `rolled_back_manual` to deploys.jsonl, which Telegram reports.

    A Telegram **[Roll back]** button stays out of scope until the owner asks for it.
  - **39 (HIGH).** After the checkout, validate settings (`Settings` over `settings.yaml` plus
    `settings.local.yaml`) and `accounts.yaml` with the target's own interpreter, as the service user.
    Acceptance:
    - on a validation error, refuse before any restart and name the key, never the value;
    - test: a local `risk.min_lot_risk_cap` with a pre-batch target is refused and nothing restarts.
  - **40.**
    - Refuse while a scheduler job runs, using `python -m goldbot.db scheduler-status`, with the fallback to
      `scheduler.json`. Stop the scheduler first and check again, as `vps_update.ps1` steps 3 and 4 do.
    - Restart only units that are `enabled`.
  - **41.** Done 2026-10-11 with one deliberate change, per the program director: a worse check is reported as
    `deployed_degraded` (names in deploys.jsonl and on Telegram), never rolled back automatically, because exits and
    the owner's controls decide. "Worse" is ok < warn < fail per check (`goldbot/ops/health.py` `worsened`; the
    deploy script embeds the same rule for older releases). Original text:
    - Save `goldbot run health --out` before the switch and run `--baseline` after the 90 s wait.
    - Roll back on a new FAIL, except checks that need the market open while it is closed.
    - Log the failing check names.
  - **42.** *(done 2026-10-11; trading-safety and security review fixes M1, M2, L1-L3 applied)*
    - Done: the bridge reports its commit, protocol `api_version` and `min_compatible` (pinned with
      `protocol_digest()`: method table, RemoteBroker and MT5Broker signatures, wire record fields) and served
      `methods` on `/health`, beside `server_tz` (goldbot/execution/bridge.py).
    - Done: the engine checks the bridge at start and every 60 s off the tick loop (goldbot/ops/bridge_compat.py,
      goldbot/ops/run.py) and refuses entries with `bridge_incompatible` (fail closed, exits never gated) on a
      missing method (e.g. `loss_per_lot`), versions below each other's `min_compatible`, another or invalid
      `server_tz` or no report. The engine starts `unchecked` (blocking); an unreachable `/health` never confirms a
      bridge, and an ok over 5 min old counts as unchecked. `/health` reads are bounded (64 KiB, 5 s total). Health
      fails `bridge_compat:<account>` (one check for zone and protocol, instead of `bridge_server_tz:<account>`) and
      alerts on Telegram; preflight `bridge_compat` blocks. A commit mismatch alone warns.
    - Done: the Telegram deploy offer says when the MT5 box runs an older commit than the one offered
      (goldbot/ops/deploy.py `mt5_notes`).
    - Trading-safety review.
