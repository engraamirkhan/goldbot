# Bridge protocol v7 rollout: close priority and the order lane (BACKLOG 135, ADR 0056)

SRE, 2026-10-27. Reviews commit `49335812` (branch `worktree-agent-a3a9dede5d5b3bbf7`) and its RUNBOOK section "Order
lane and close priority". This note is the plan. The procedure stays in the RUNBOOK, and this note asks for the RUNBOOK
changes listed in section 9. Claude runs every step, and nothing needs the owner. The one exception is the live box's
spend (section 7), which is the owner's decision. No box was changed while this was written. The facts in section 1
were read with read-only commands on 27 Oct at 13:43 UTC.

## 0. Verdict

- **Protocol v7 (close priority, lane off): go, after the gates in section 3.** Every pair of brain and box versions
  the rollout can produce is compatible (section 2), so the order of steps is chosen for risk, not for compatibility.
- **The order lane: on vantage-demo only, as a drill.** It must not run on the two 1 GB boxes (section 1). It stays
  off for the real-money pilot (section 8).
- **The live box:** an E4.Flex with 2 GB, not 1 GB. The real cost is about **£5.07 a month, not £3.6**, because the
  Always Free block storage is used up (section 7). The owner decides that spend.

## 1. Where things stand (measured 27 Oct 2026, 13:43 UTC, read-only)

| Box | Shape | Runs | `/health` | RAM avail / swap used | CPU steal (vmstat) | CPU PSI some avg10 / avg300 | Terminal / bridge RSS | `goldbot-deploy` installed |
|---|---|---|---|---|---|---|---|---|
| `goldbot-mt5` (icm-demo) | E2.1.Micro, 1 GB | `40c35b9b` | **v5**, terminal ok | 521 MB / 422 MB | **43-55 %** | 38 % / 14 % | 108 / 81 MB | an older copy (sha256 `4e9764…`), **not current** |
| `goldbot-mt5-probe` (icm-probe) | E2.1.Micro, 1 GB | `40c35b9b` | **v5**, terminal ok | 397 MB / 247 MB | **53-58 %** | 37 % / 15 % | 158 / 155 MB | the `c8349c16` copy (19 Oct), **predates the 20 Oct rollback fix** |
| `goldbot-mt5-vantage` (vantage-demo) | E5.Flex 1 OCPU (1/8 baseline), 2 GB | `63bb93d4` | **v5**, terminal ok | 1238 MB / 352 MB | 1-2 % | 1 % / 1.5 % | 134 / 101 MB | current (`ec4e3fa2`) |
| `goldbot-brain` | A1, 12 GB | `47f1f622` (v6) | n/a | 9.2 GB | n/a | n/a | n/a | current |

The steal figures were taken at a quiet moment, so the 40-49 % seen at peaks is the floor on the micros, not the
worst case. On icm-probe a third of the time something is already waiting for CPU.

Three findings change the plan:
1. **The boxes are still on protocol v5, not v6.** They run 210-260 commits behind main. A v7 deploy brings every
   bridge-side change since 26 Oct as well: v6 `get_bars_range`, the `5d661e94` order worker on the brain side,
   `bridge_queue`, `bridge_lane`, and 237 changed lines of `config/accounts.yaml`. The canary therefore tests that
   whole delta, not just the queue. No root-installed file and no bridge dependency changed in that range
   (`goldbot/ops/linux` only touches `goldbot-engine@.service`, which is brain-only, and
   `mt5-bridge-requirements.txt` is unchanged). So `goldbot-deploy <SHA>` is enough on each box, and
   `mt5-box-upgrade.sh` is not needed.
2. **Two boxes run an out-of-date `goldbot-deploy`.** The icm-probe copy predates the incident 2026-10-20 fix (a
   rollback could leave units stopped). Its automatic rollback is the safety net for this deploy, so the reviewed
   copy goes in first (gate G3).
3. **Rollback floor:** `422e8eab` on icm and icm-probe, and none on vantage. Both previous commits (`40c35b9b`,
   `63bb93d4`) descend from it, so `--rollback` to them is allowed.

## 2. Compatibility and the order of steps

`MIN_COMPATIBLE` is 5 on v5, v6 and v7 (checked in the code):
- **A v7 brain with a v5 or v6 box:** the v5 server reads `method`, `sent_at` and `args` only (`bridge.py` at
  `40c35b9b`, lines 353-361), so the new `timeout_s` key is ignored. `get_bars_range` is an `OPTIONAL_METHODS` read
  with a fallback.
- **A v6 brain with a v7 box:** the v7 box falls back to `READ_TTL_S` when no `timeout_s` arrives, and the brain drops
  the unknown `order_lane` field on `/health`.
- A live account refuses entries from a bridge below v7. No live account is enabled, so this does not apply to the
  rollout.

So demo trading never sees an incompatible pair, whatever the order. **The order chosen is: brain first, then the
icm-probe canary, then icm-demo, then vantage-demo.** This differs from the RUNBOOK's "boxes first, brain last".
- `scripts/release.sh` deploys the brain as soon as the PR merges. Holding that back would mean a second, unusual
  release path, which is the kind of variation that causes incidents.
- The brain-side change is small: it sends `timeout_s`, parses `order_lane`, and adds a live-only v7 check.
- The risky change is the box: a threaded HTTP server under Wine on a CPU-starved micro. That is where the canary
  belongs.

The RUNBOOK sentence should be changed to "either order; the release deploys the brain first" (section 9).

## 3. Gates before any box is touched

| Gate | What | Evidence |
|---|---|---|
| G1 | ADR 0056 reviews signed off: trading-safety, security (root-run paths unchanged, child process in the bridge cgroup), SRE (this note) | review notes on the PR |
| G2 | PR merged to main, CI green. `goldbot-deploy` itself refuses a commit that is not on main or has no green CI | `gh run list` on the merge commit |
| G3 | The current `goldbot-deploy` (`ec4e3fa2`, sha256 `74e84f8f…c5294`) installed on `goldbot-mt5` and `goldbot-mt5-probe` with the hash-checked steps in RUNBOOK "Installing a new goldbot-deploy" (from GitHub, never from `/opt/goldbot`) | `sha256sum /usr/local/sbin/goldbot-deploy` equals the hash at that commit, computed in the Mac clone |
| G4 | A baseline on each box, taken the day before its deploy during the London/NY overlap: `python scripts/bridge_latency_probe.py <account> --minutes 60 --every 1.0` on the brain (1.0 s on the micros, so the probe does not add load to the box it measures), plus `free -m`, `vmstat 5 12`, `/proc/pressure/cpu`, bridge `ps -o rss,nlwp`, and 24 h counts of `tick_error`, `order_unanswered`, `bridge_stale_order` and fills from the engine journal | saved under `~/tmp/v7/<box>-before.txt`; the numbers go into the record (section 10) |
| G5 | Market calendar: no box deploy within 60 min of a high-impact release (goldbot's news calendar; FOMC is due Wed 28 Oct), never on Friday after 20:00 UK, never at the weekend reopen | the calendar check written in the record |

## 4. Deploying one box (the same for each)

Claude runs these from the Mac. Each is one line, and `<SHA>` is the full 40-hex merge commit.
1. Check the book: `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain goldbot run positions --account <account>`.
   Deploy when the account is flat. Failing that, deploy only when every open position has its broker-side stop
   (the bridge is down for about 15 s).
2. Deploy, using a systemd unit so a dropped SSH connection cannot stop it halfway:
   `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes <box> 'sudo systemd-run --wait --unit goldbot-deploy-manual-$(date +%s) /usr/local/sbin/goldbot-deploy <SHA>; sudo tail -1 /var/lib/goldbot-deploy/deploys.jsonl'`.
   The result must be `deployed`. A crash loop inside the 90 s window rolls back by itself and shows as `rolled_back`.
3. Check the box: `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes <box> curl -fsS http://127.0.0.1:8765/health` shows
   `"api_version": 7`, `"order_lane": "off"`, `"terminal": "ok"`, and the commit equals `<SHA>`.
4. Check from outside the box, on the brain: `goldbot run health | grep -e 'bridge_compat:<account>' -e 'bridge:<account>' -e 'terminal:<account>'`
   prints ok on all three, and the dashboard shows the account's entry status as trading. Health alone is not
   enough: confirm the engine's latest decisions and its positions too.
5. If the auto mode classifier blocks step 2: tell the owner in one line, with the exact command, the first time it
   happens (incident 2026-10-17).

## 5. Canary: icm-probe first, then icm-demo, then vantage-demo

**Why icm-probe.** It is a demo exploration account with the most fills per day. A queue fault therefore shows up
in hours, not days. Its box is also the most CPU-starved, so it is the hardest test for a threaded server under Wine.

**Proposed dates** (the release manager confirms them after G1-G2; if a date slips, the program-director hears
within 24 h):

| When (UTC) | Step |
|---|---|
| Wed 28 Oct | G1-G3. Merge, and the brain deploys via `release.sh`. Baselines (G4) for all three boxes in the 12:00-16:00 overlap. No box deploy within 60 min of FOMC |
| Thu 29 Oct 03:00 | **icm-probe deploy** (Asian session, quiet). Soak until Fri 03:00 through the London open, NY open and US data |
| Fri 30 Oct 03:00 | Go or no-go on icm-probe (below). On go: **icm-demo deploy**, then a 4 h soak through the London open |
| Fri 30 Oct 08:00 | On go: **vantage-demo deploy**, then soak through the Friday NY session. Nothing after 20:00 UK |
| Mon 2 Nov | Read the weekend reopen (US DST ended on 1 Nov, so the server clock moves; keep that apart from v7 effects). The v7 rollout is done if all three pass |
| from Tue 3 Nov | Order-lane drill on vantage-demo, once gate L0 has shipped (section 6) |

**Go / no-go after the soak** (per box, against its own G4 baseline; a box fails if any one test fails):

| # | Check | Pass |
|---|---|---|
| C1 | `systemctl show -p NRestarts goldbot-bridge@<account>` over the soak; the watchdog journal | no increase; no watchdog restart |
| C2 | Bridge journal `queue worker error` | 0 |
| C3 | Bridge journal: 503 (queue full, 64) | 0. Anything above 0 means the brain opens more concurrent requests than budgeted |
| C4 | Engine journal `tick_error` | 0 (as now) |
| C5 | `order_unanswered` and `bridge_stale_order` (409) per 100 entries | no higher than the baseline. A rise means the new queue holds entries past `ORDER_MAX_AGE_S` (10 s) behind reads. On the micros this is the likeliest regression, given CPU steal |
| C6 | Fills per hour of market time | at least 80 % of the baseline for the same session (v7 must not reduce trading) |
| C7 | `bridge_latency_probe` 60 min in the overlap | p99 within baseline + 20 %; the slowest reads during entries shorter than the baseline's |
| C8 | Bridge `python.exe` RSS and threads (`ps -o rss,nlwp`) 1 h and 24 h after the start | RSS growth under 20 MB/24 h; threads under 80. A thread per request under Wine is new, so a leak would show here |
| C9 | `free -m` / PSI on the micros | available memory no more than 30 MB below the baseline; swap-in (`vmstat si`) 0 at steady state; memory PSI avg60 under 5 % |
| C10 | `bridge_compat:<account>` | ok throughout (commit equal after the brain deploy) |

**On no-go:** roll that box back (section 6) and keep the others where they are. Write a postmortem in
`docs/incidents/` if anything traded wrong or late. Re-run the canary on icm-probe after the fix.

## 6. The order-lane drill (vantage-demo only)

**Why not the micros.** The lane is a second Wine Python of 150-250 MB (RUNBOOK) on a box where a second CPU-bound
process also competes for a CPU of which 40-58 % is stolen.
- icm-probe has 397 MB available with 247 MB already in swap. Adding the lane leaves 150-250 MB, so the terminal
  pages during a fast market. That is exactly the failure that delays exits.
- icm-demo has 521 MB available but 422 MB swapped.

Rule (RUNBOOK change, section 9): **the order lane needs a box with at least 2 GB and measured steal under 20 %.**
Today only `goldbot-mt5-vantage` qualifies, and so will the live box (section 7).

**Gate L0, before the lane is on anywhere:** the lane has no alert today. `/health` reports
`order_lane: down`, the brain parses it (`BridgeInfo.order_lane`), but no health check reads it. So a lane that died
leaves the account at the slower bound without anyone being told. Owed (implementer, small):
- a `bridge_lane:<account>` check: when the account has `order_lane: true` and `/health` is not `separate` for more
  than 2 min, WARN on demo and FAIL on a live account (Telegram);
- counters on `/health` for dropped reads, 409 stale and 503 full, with a WARN when 503 is above 0 or stale entries
  rise. Until then, C2-C5 are journal greps only.

Every new failure mode needs a detection before it ships.

**Steps** (the RUNBOOK's six, with capacity gates added):
1. Pre-check: `free -m` available at least 600 MB (not 400: lane plus a fast market's working set). Steal (`vmstat 5 12`, `st`)
   averages under 20 %. Memory PSI avg60 under 5 %.
2. Baseline: `bridge_latency_probe.py vantage-demo --minutes 60` during entries (already in G4).
3. Make the lane change **its own commit** (`order_lane: true` under vantage-demo only), through CI. Deploy it to
   the vantage box only (section 4). The commit before it is then a CI-green rollback target that has run on that box
   (section 6, rollback).
4. Within 2 min: `/health` `order_lane: separate`, `terminal: ok`. Exactly one `terminal64.exe` (`pgrep -c -u mt5 -f '[t]erminal64[.]exe'` = 1).
   Two `python.exe` in the bridge unit's cgroup (`systemctl status goldbot-bridge@vantage-demo`).
5. Resources at 15 min, 1 h and 24 h: RSS of both `python.exe` and `wineserver`, `free -m`, steal, PSI. Pass:
   lane RSS under 250 MB; available memory never under 400 MB; no swap-in at steady state. Record the measured lane
   RSS in the RUNBOOK (it replaces the 150-250 MB estimate).
6. Effect: `bridge_latency_probe.py vantage-demo --minutes 240` across the London/NY overlap. PASS: no read over
   1 s at any entry's time (`order_late` or a fill in the engine journal) and `/health` `ok` throughout. If reads still
   stall during entries, the Wine terminal serialises its IPC clients. The lane then gives nothing; turn it off and
   record that in ADR 0056.
7. Fault rehearsals (demo, Claude-run):
   - kill the lane child with no order in flight (`sudo pkill -u mt5 -f 'bridge-order-lane'`): `/health` shows
     `down` and the L0 alert fires. Entries continue on the shared connection, and the bridge does not restart.
   - kill it with an entry in flight: no reply, the brain books it ambiguous, the BACKLOG 72 look-up settles it
     within 120 s, and nothing is re-sent;
   - restart the terminal unit: the bridge and lane come back with one terminal and `separate`.
   Each result goes into the RUNBOOK's recovery table.
8. The kill-switch drill (live-readiness L10): flatten a demo position while an entry is in flight. The close must
   land within 5 s (`gate-evidence`).

If the drill fails, the lane stays off for every account and the bound is the v7 no-lane one (the hold plus one
pass). That is the bound the pilot plans for anyway (section 8).

## 6a. Rollback

| What | How | Time |
|---|---|---|
| A box deploy that crash-loops | automatic: `goldbot-deploy` restores the previous commit within the 90 s window | about 2 min |
| A box that fails go/no-go later | `ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes <box> 'sudo systemd-run --wait --unit goldbot-deploy-manual-$(date +%s) /usr/local/sbin/goldbot-deploy --rollback <PREV>'`, with `<PREV>` = `40c35b9ba0df2d98fa6e5bc4ec57342a1b3939be` (icm, icm-probe) or `63bb93d4a89da13edff389bb97671923a0bac7d2` (vantage). Both are above the `422e8eab` floor and have run on those boxes | about 3 min, with about 15 s without ticks |
| The lane, bridge still healthy | nothing urgent: a lane that is `down` already sends entries on the shared connection. Merge `order_lane: false`, then deploy | the next release |
| The lane, bridge flapping or `not_running` | `--rollback` the vantage box to the commit before the lane commit (step 3 above). Do **not** follow the RUNBOOK's "stop the bridge, then merge the config": that keeps the account without ticks or engine-side exits for a whole CI cycle (about 15 min) | about 3 min |
| The brain | only for a brain-side fault, never for compatibility: `goldbot-deploy --rollback 47f1f6224ccc1e8ec90b3597c644a40b20e702d5` | the brain's usual 90 s |

## 7. The live box (it does not exist yet)

For the real-money pilot, one MT5 box for the live account, built as RUNBOOK 0.3b B4-B11 (as vantage-demo was):
- **Shape:** `VM.Standard.E4.Flex`, 1 OCPU at the 1/8 baseline, **2 GB**, a 47 GB boot volume. Not a 1 GB micro:
  the measured swap and the 40-58 % steal above are not acceptable for real money. The measured vantage box (2 GB,
  1-2 % steal, 1.2 GB available) is the model.
- **Cost:** compute £3.57 a month as approved, **plus £1.49 a month of storage**: boot volumes already take 197 of
  the 200 GB Always Free (docs/deploy/icm-depth-host.md). That makes **£5.07 a month**. If icm-depth is also built,
  storage goes further over. The owner decides the spend, and the finance-auditor records it.
- **Provisioning:** `scripts/deploy/oci_provision.sh` refuses paid shapes. Build the "prov-paid" follow-up
  (icm-depth-host.md section 2) first, instead of a second hand-run `oci compute instance launch`.
- **Software:** at or after the v7 merge commit (a live account refuses entries below v7). Lane off. The current
  `goldbot-deploy`. A rollback floor at the v7 commit, so no rollback can take it below v7.
- **Configuration owed before `enabled: true`** (trading-safety and implementer; found in this review):
  `config/accounts.yaml` `icm-live` has `terminal_path: C:\MT5\ICMarkets-Live\terminal64.exe`. The bootstrap and the
  terminal unit install and start `C:\MT5\ICMarkets` only, so the bridge would attach to a path no terminal runs
  from. Use `C:\MT5\ICMarkets` on the live account's own box, as vantage-demo does. Also:
  - `server_tz: Europe/Athens` contradicts the NY+7 measured for both ICM demo servers on 26 Oct. Measure it on the
    live server (ADR 0005).
  - no `order_timeout_s` (default 15 s) and no `daily_loss_limit_abs` ≤ 50 (ADR 0042 I4).
- **Operations before the first live order:** the box in the health and backup rota. The watchdog timer and the
  terminal unit enabled. The cold-restart test passed (RUNBOOK 0.5 post-reboot). The kill-switch drill (L10) done on
  this box. `bridge_compat:icm-live` ok with `live=True` (proves v7).

## 8. Is the order lane needed for the £50 pilot?

**No.** The pilot holds one position at a time and the owner confirms each entry. So an entry is never in flight
while a position the engine manages is open, and the stop-close case the lane exists for cannot happen. The windows
that remain:
- **The kill switch during an entry:** on v7 without the lane, the flatten runs right after the entry. The bound is
  the order's hold plus one pass (about 16 s at ICM's 15 s timeout; typical ICM fills take well under 2 s). Before v7
  it was about 45-55 s.
- **Broker-side stop and target** are sent with every order, so they cover the position whatever the bridge does.

What the pilot does need is **v7** (enforced: entries refused below it) and `max_open_total: 1` on the live account,
which the trading-safety reviewer confirms in config. The lane stays off for the pilot: it adds an unproven second
IPC client under Wine and a new ambiguous-order path, for no gain at one position. Revisit it only for a second
concurrent position, pyramiding, or a Vantage live account (fills up to 31 s), and only after the drill in section 6
passed.

## 9. Changes owed elsewhere (owners)

| Item | Owner | When |
|---|---|---|
| RUNBOOK "Order lane": deploy order "either order; release deploys the brain first"; lane rollback by `--rollback` to the pre-lane commit, not stop-and-merge; lane only on at least 2 GB and steal under 20 %; available at least 600 MB, not 400 | implementer (on the BACKLOG 135 branch, before merge) | G1 |
| `bridge_lane:<account>` health check plus queue counters on `/health` (L0) | implementer, via product-owner | before the lane drill |
| `icm-live` `terminal_path`, `server_tz`, `order_timeout_s`, `daily_loss_limit_abs` | implementer and trading-safety | before the live box |
| "prov-paid" in `oci_provision.sh` | SRE | before the live box |
| A check that every MT5 box runs the current `goldbot-deploy` (hash on the box against the hash at its deployed commit, a health WARN) | SRE, via product-owner | this week: two of three boxes drifted unnoticed |

## 10. Record (filled in during the rollout)

| Box | Baseline taken | Deployed (UTC, SHA) | Result | C1-C10 | Notes |
|---|---|---|---|---|---|
| brain | | | | n/a | |
| icm-probe | | | | | |
| icm-demo | | | | | |
| vantage-demo | | | | | |
| lane drill (vantage) | | | | steps 1-8 | |
