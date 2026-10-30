# Deploy approval runbook (standing)

2026-10-30 · cto, with the sre · for the owner. Purpose: routine deploys of CI-green `main` must not stall on a
per-command approval, while everything that can hurt the account stays a per-command owner decision. The owner
decides which option below to apply; no agent edits permission settings. Procedure detail: `docs/RUNBOOK.md`
2.7 and "Updates"; flow: `scripts/release.sh`; on-box logic: `goldbot/ops/linux/goldbot-deploy.sh` (header).

## What `goldbot-deploy` already enforces on the box (why a forward deploy is low risk)
- Target must be a full 40-hex commit on `main`, a fast-forward of what runs, with green CI (backend + frontend).
- Before touching anything: aux.db schema the target can open, `settings.local.yaml` valid for the target's own
  Settings model, no scheduler job mid-run.
- After the restart (supervisor, engines, rest): every service active and un-restarted for 30 s with fresh
  heartbeats, else the previous commit is restored; a watcher rolls back on any restart up to 90 s.
- Rollback: only an older `main` commit that passed CI or ran on this box, never below `/etc/goldbot/rollback-floor`.
- Engines are down for seconds; broker-side SL/TP stay in place during the gap; exits are never gated.

## The commands (H = `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes`; SHA = full 40-hex from `git rev-parse origin/main`)

| # | Step | Exact command | Touches | Rollback |
| --- | --- | --- | --- | --- |
| D1 | Stage while CI runs | `H goldbot-brain "sudo /usr/local/sbin/goldbot-deploy --stage SHA"` | `/opt/goldbot-stage/SHA` only (uv cache, web build); no service | none needed (`rm` the stage dir) |
| D2 | Deploy the brain | `H goldbot-brain "sudo systemctl reset-failed 'goldbot-deploy-manual-*'; sudo systemd-run --wait --unit goldbot-deploy-manual-\$(date +%s) /usr/local/sbin/goldbot-deploy SHA"` (as `release.sh:117-119`) | `/opt/goldbot` checkout, venv, `web/dist`, aux.db schema step, every goldbot service restarted | automatic within 90 s on any failed check; else D5 |
| D3 | Read the result | `H goldbot-brain "sudo tail -1 /var/lib/goldbot-deploy/deploys.jsonl"` | read only | - |
| D4 | Post-deploy checks (what the owner sees) | `H goldbot-brain 'bash -lc "goldbot run health --json"'`; `... "goldbot run positions"`; `... "goldbot run shadow-report --since <deploy UTC>"`; engine decisions since the deploy (RUNBOOK; read-only) | read only | - |
| D5 | Manual rollback | `H goldbot-brain "sudo /usr/local/sbin/goldbot-deploy --rollback OLDSHA"` | as D2, to an older commit | deploy forward again |
| D6 | MT5 box (bridge changes only) | `H goldbot-mt5 "sudo goldbot-deploy SHA"` (likewise `goldbot-mt5-probe`, `goldbot-mt5-vantage`) | terminal and bridge restart under Wine; that account cannot trade until the terminal is back | `--rollback OLDSHA` on that box |

A deploy is done only when D3 says `deployed` and D4 shows, per enabled account: `entry_status` not blocked, engine
decisions written since the switch with no new refusal class (for example `history_gap`), and positions matching
the broker. Health alone is not enough (incident 2026-10-20).

## Never pre-approved (per-command owner approval, every time)
D6 (MT5 boxes); `--drop-unknown-local-keys`; `--interrupt-jobs`; installing or replacing root-run tooling
(`goldbot-deploy` itself, systemd units, bootstrap scripts); any edit of `settings.local.yaml`, the keyring or
`/etc/goldbot/rollback-floor`; any other `sudo`; anything touching `unlock_live` or `phase_state.json` (owner-only
for good).

## Options for the owner

| Option | What the owner does | Gain | Risk accepted |
| --- | --- | --- | --- |
| A. Status quo | Approves each exact command | Nothing runs unseen | Every deploy and rollback waits for the owner; an exit fix can sit for hours |
| **B. Standing approval of one wrapper (recommended)** | (1) Installs a reviewed, hash-checked copy of a wrapper outside the repo (`~/.local/bin/goldbot-release`), which accepts only D1-D5, only a 40-hex SHA equal to `origin/main` (D1-D2) or an older `main` commit (D5), checks CI is green via `gh`, and runs the fixed ssh forms above. (2) Adds one allow rule naming that absolute path to the project's `.claude/settings.local.json`. (3) Updates `autoMode.environment` in `~/.claude/settings.json`: the deploy targets are the Oracle Linux VMs `goldbot-brain` and the `goldbot-mt5*` boxes via `goldbot-deploy` (the text today says "Windows VPS ... NSSM", which is out of date), and "deploying a CI-green `main` commit to `goldbot-brain` with `goldbot-release`, and rolling it back, is pre-approved by the owner; MT5-box deploys and everything in 'Never pre-approved' are not" | Brain deploys and rollbacks run in minutes without the owner; the 10-18 standing permission ("you may deploy any CI-green main commit to the brain in auto mode") becomes a written, bounded rule | A wrong but CI-green commit reaches the demo brain without a human look; bounded by the on-box checks above, the 90 s auto-rollback, demo-only until the phase gate, and the exclusions. The wrapper lives outside the repo so an agent cannot widen it by editing a file it can write |
| C. Allow `ssh goldbot-*` broadly | One prefix rule | Fastest | Rejected: the remote command is a free string, so a prefix rule on `ssh` pre-approves any remote write, including the exclusions |

Recommendation: **B**, brain only. Revisit after 2 weeks: if there is no unintended deploy and no rollback caused by
the wrapper, keep it; any deploy outside the rules, and the owner removes the allow rule (one line) and we return to A.
The wrapper is built by the sre through a normal PR (security-reviewer GO required, root-run tooling rules) and its
sha256 is given to the owner with the install command.
