# Post-deploy audit 2: 2026-10-17 (both servers on `main` dbd5fa2, PR #85)

Finance-auditor, 2026-10-17. Written on the integration branch at `90e242d`; the code audited is `dbd5fa2`, the merge of
PR #85, read with `git show dbd5fa2:<path>`. File:line references below are at `dbd5fa2`. This closes F1 of the
morning audit (`audit-2026-10-17-post-deploy.md`): PR #85 merged and shipped, and this is its audit within 48 h.

**Deployed.** MT5 box 12:44 UTC, brain 12:46 UTC, Saturday 17 Oct (market closed, so no London/NY session was
interrupted). Deploy note on #34 at 12:49 UTC: preflight READY, restore drill OK (snapshot 12:41 UTC), bridge v5 OK,
dashboard reached from outside the servers, entries still blocked (`account_class_unknown` until the Fri 23 Oct
classifier read; first smoke order Mon 26 Oct at the earliest).

**Sample.** n = 0 trades, fills, approvals or swaps on the demo account. Every trading figure is nil. **No broker or
server access from here**: nothing is tied out to the broker (section 6).

## 1. Bad news first

1. **The release record is wrong about what shipped.** PR #85's description lists "BACKLOG 62 build" and "SRE
   reconcile/kill-switch health checks" as **out of scope (next release)**. Both are in `dbd5fa2`: the all-symbols kill
   switch (`goldbot/engine/runner.py:1440-1464`, `_read_kill_book` on `account_positions()`) and the `kill_switch`
   health check (`goldbot/ops/health.py:160`, `:525`; `goldbot/ops/checks/risk.py:178`). The code was reviewed
   (`docs/process/reviews/2026-10-17.md`: trading-safety E1 APPROVE on 90b2603/f211e0a/e35f566; SRE `kill_switch`
   check APPROVE on 0e514d2), and the owner notice C8 was posted on #34 at 06:02 UTC, before the deploy. So the
   change is not unreviewed, but a reader of the PR would believe the kill switch is still gold-only. (G1)
2. **The all-symbols close is not proven on a real terminal.** The BACKLOG 62 handoff says the fake MT5 in the tests
   cannot prove closing another symbol's position (fill mode, `symbol_select`). Until that is checked on the demo
   terminal, the 12% switch's new scope is tested only against the fake. (G2)
3. **The day-0 RC counts (morning F2) are still owed** (sre, Mon 19 Oct 22:00 UTC). Nothing on #34 since the deploy
   carries them.

## 2. Rule compliance (code and config as deployed; behaviour not observable here)

| Check | Result | Evidence (at dbd5fa2) | Severity | Owner |
|---|---|---|---|---|
| Demo only, no live | **Pass (config)** | `config/accounts.yaml:18` `icm-demo` `mode: demo`, `:25` `enabled: true`. Both live accounts `enabled: false` (`:50`, `:60`). The phase file lives on the server and was not read | Info | sre (phase state on #34) |
| No risk limit loosened | **Pass** | `config/settings.yaml:44-52` unchanged: `risk_per_trade` 0.005, `daily_cap` 0.02, `weekly_cap` 0.05, supervisor 0.015/0.04, `drawdown_stage2` 0.12. The only settings change in #85 is `gates.signed_off: []` (`:292`), which records owner sign-offs and changes no value. `accounts.yaml:28` `daily_loss_limit_abs: 50` (ADR 0004) unchanged. Per-trade cap: `goldbot/config.py:147` (`le=0.01`) and `goldbot/risk/gate.py:47` (`max_risk_per_trade` 0.01) unchanged | Info | - |
| `risk/gate.py` change (+182/-60) is a refactor plus a tighter new path | **Pass** | `git diff 01e70a7 dbd5fa2 -- goldbot/risk/gate.py`. `check()` (`:584`) now starts from `_account_gate` (`:508`) and ends in `_sized_limits` (`:531`). Both are moved code with the same order and constants: margin floor, combined exposure, absolute daily loss limit (`:569`, strict `> limit + 1e-9` as before) and heat. `risk_per_lot` and `cost_per_oz` are passed through unchanged. The new `check_operational` (`:652`) serves only the bridge smoke. It refuses outside demo or once any live gate is recorded (`LIVE_GATES`, `:229`), an unreadable phase file refuses, the size is fixed at the minimum lot, and it applies the per-trade cap (`:691`) and every sized limit. It skips the model checks only. `tests/test_risk_gate_split.py` pins `check()`'s decisions on a pre-split grid | Info | - |
| RiskGate the only order path, owner confirms | **Pass (code)** | The smoke (`goldbot/ops/smoke.py:539`) sizes through `check_operational` and needs one owner click within the 90 s window (`:627-649`); no click means EXPIRED_UNAPPROVED. At the bridge, the smoke's magic is refused unless the terminal reports a demo account (`goldbot/execution/bridge.py`, ADR 0021 F4) | Info | - |
| Smoke's daily projection omits cost | **Note** | `gate.py:693` passes `cost_per_oz=0.0`. At 0.01 lot and a $1.00 stop the smoke risks about $1 (about £0.75). The omitted round trip is about $0.20-0.40, against a £50 limit. Immaterial | Info | - |
| Lot ceiling (BACKLOG 66) | **Pass, unchanged** | `bridge.py:115` `MAX_NOTIONAL_EQUITY_MULT = 6.0`, check at `:377`. Entries only | Info | - |
| Kill switch closes every symbol (BACKLOG 62, ADR 0016 amendment) | **Pass, a tightening** | `runner.py:1449-1464`: while `_kill_everything` is set the switch reads `account_positions()`. A failed read falls back to the gold positions and never clears the flag. 12% threshold unchanged (`settings.yaml:52`). Bridge v5 on the box (deploy note), so the v4 fallback (flag stuck, C1) does not apply. Owner notice C8 posted on #34 before the deploy | Info | - |
| ...on a real terminal (G2) | **Not verified** | Handoff `2026-10-17-kill-switch-all-symbols.md`: closing a manual non-gold demo trade is untested against real MT5 | Medium | sre |
| Halt and kill flags survive a restart (ADR 0023 step 1) | **Pass (code)** | `goldbot/engine/store.py:104-116` `RiskSnapshot` carries `halted_at`, `rearm_seen`, `rearm_refused`, `kill_everything`, `stage2_pending_since`. Saved at `:176-187` (atomic write), loaded at `:189-214`. Move-only: 192-case golden grid unchanged. One stricter change: a value the engine never writes refuses the start instead of loading it (trading-safety ruling). That fails closed for entries. While the engine is down, protection is the broker-side stops only | Low | sre: the engine-down alert covers this; confirm it pages |
| `kill_switch` health check (C9) | **Pass (code)** | `checks/risk.py:178-203`: WARN to Telegram when the flag is set with closes still due, FAIL if the market stays open past the limit. Reporting only, never gates an exit | Info | - |
| NOT PLACED archiving (BACKLOG 71) | **Pass** | `runner.py:1227-1242` `_note_not_placed`, and `goldbot/execution/not_placed.py` holds the wording. A definite broker or bridge refusal is archived as `bridge_stale_order` or `broker_refused:<code>` with one owner notice. Fills, `ok` and ambiguous results stay APPROVED. No limit, exit or retry logic is touched. Trading-safety E1 APPROVE d067a19 | Info | - |
| Release record (G1) | **Finding** | PR #85 body vs `dbd5fa2` (section 1.1) | Low | release-manager |

## 3. Costs to date (1-17 Oct 2026, GBP)

Unchanged from the morning audit, section 3. **Total cash recorded month to date: £0.** No free trial or credit is in
use, so the 60-day rule raises nothing.

| Line | Amount | Note |
|---|---|---|
| Oracle Always Free (brain, MT5 box, volumes, Object Storage) | £0 | Pay-As-You-Go status and Object Storage size still not read (morning F4, OD-3) |
| GitHub Actions | £0 | Public repo on standard runners: not billed (CFO, `budget-2026-10.md` s.2). The planned targeted refetch (worst case about 1,100 minutes) is also £0 on that basis |
| Cloudflare quick tunnel (temporary dashboard address) | £0 | Free. A fixed address is an owner choice (section 8) |
| Staff agents (Anthropic API) | £0.00 reported | Not read from `agent_spend` here |
| Claude sessions running the program | not recorded | Morning F6, owner ruling pending |
| Trading costs (spread, commission, swap, slippage) | £0, n = 0 | No fills |

Break-even is unchanged: scenario A is £0 a month. BACKLOG 77 (email sign-in code) adds an email-sending dependency.
It must use a free tier or come to the owner as a spending decision before it is built (G5).

## 4. Owner-access incident: cost in owner time

Source: `docs/incidents/2026-10-17-owner-access.md`. Cash cost **£0**. The times below are from that record; the
owner's own time was not separately logged, so the figure is an estimate.

| Item | Time |
|---|---|
| Owner asks for the URL (~06:30 UTC) until signed in (~07:55 UTC) | **about 1 h 25 min of owner time**, during which the owner was actively involved |
| Dashboard down during the failed reset (API stopped) | "several minutes" (not timed). No trading effect: entries were already blocked, and exits do not depend on the API |
| Rework created | BACKLOG 77 (email-code sign-in), the CTO's deploy definition of done, ui-ux copy, and a fixed address. Agent time, cash £0 |

The owner's time is the scarcest input in this operating model, because each entry needs one click. An hour and a
half lost on access is about the owner time of 90 days of entry approvals at one approval a day of about a minute.
Fix: the incident's own rule ("done" means the owner can open it and complete the first action), now applied in the
12:49 deploy note.

## 5. Learning record (decisions this cycle; no trades, so LE-1 to LE-4 not computable)

| Decision | Evidence | Finance reading |
|---|---|---|
| **Q-H02U: H-02 survivor unit feature to group; gate-1 bar about t 4.6 to about t 4.0** (quant reviewer, 2026-10-17, before any H-02 outcome) | `preregistration-2027Q1.md:184-197`, table `:555-560`; #34 note 06:17 UTC | **A loosening of a promotion bar, made for a sound reason.** K_eff should count the units that actually go forward, and the slot rule forwards whole clusters. The required net mean at 1,000 trades falls from 0.20 R to 0.18 R. It was dated before data, and the owner was told, not asked. It stays honest only if the survivors really are whole groups (G4) |
| **O-5/O-6 day-summed DSR >= 0.95 (BACKLOG 76), stricter amendments** (200-day floor, real entry day, gate DSR on `ret`) | handoff `2026-10-17-day-summed-dsr.md` | A tightening: it counts clustered same-day trades as one observation. Coded on the integration branch (b7abc77, 6b9bda4); **not in `dbd5fa2`** (research-only, no money path) |
| **E-2 top-tercile cut (BACKLOG 75)**: `tercile_inner` needs at least 1,000 taken trades to "continue", q is in the config hash, overlapping folds are refused | handoff `2026-10-17-tercile-cut.md`; quant re-verify APPROVE 48226f5 | A tightening, and another q counts as another trial (K honest). The default rule is byte-identical (golden). Not in `dbd5fa2` |
| Q1 2027 round: 5 queued + 4 contingent survivor slots of 20 | `preregistration-2027Q1.md` "Order and budget" | Cash £0. The hit rate is still 0 of 20 Q4 rows (95% upper bound about 15%). No number here says the system is improving yet |

## 6. What could not be reconciled

- **Broker balance (RC-9)**, position ids (RC-8), engine state, the `decisions`/`fills` store and phase state: no
  broker or server access from this sandbox. n = 0, so the expected difference is £0 plus any demo interest or
  adjustment. Not seen.
- **The kill switch on the real terminal (G2)**: not observable from here.
- **Agent spend, Object Storage size, Oracle billing mode**: server or owner consoles; not read.
- The deploy note's "preflight READY / restore drill OK" is taken as reported.

## 7. Findings

| ID | Finding | Severity | Owner | Due |
|---|---|---|---|---|
| G1 | PR #85 says BACKLOG 62 and the kill-switch health check were out of scope; both shipped in `dbd5fa2` | Low | release-manager: correct the PR body (or add a #34 note) and the 62 handoff's "reviews owed" | Tue 20 Oct |
| G2 | The all-symbols kill-switch close is unproven on a real MT5 terminal | Medium | sre | before the first entry (Mon 26 Oct) |
| G3 | A risk file the store refuses stops the engine; while it is down only broker-side stops protect | Low | sre: confirm the engine-down check pages the owner | Fri 23 Oct |
| G4 | The gate-1 bar fell (t 4.6 to 4.0) on a reviewer ruling. It is only valid if H-02's survivors go forward as whole groups | Low (governance) | research-director | at H-02's reading |
| G5 | BACKLOG 77 brings in an email sender, a possible new running cost | Low | program-director | at its plan review |
| (F2-F6) | Morning findings still open: RC counts (Mon 19 Oct), F3 stale 66 text, F4 storage, F5 DST-week clock, F6 Claude spend | as filed | as filed | as filed |
| F1 | PR #85 needs its own post-deploy audit | **Closed** by this audit | - | - |

## 8. Recommendations (ranked)

### Program director

| # | Recommendation | Expected £ effect or risk avoided | Measurement | Due |
|---|---|---|---|---|
| P1 | **Demo-terminal check of the cross-symbol close (G2).** The owner places a 0.01-lot manual demo trade on a non-gold symbol; sre closes it through the bridge's `close` path (the one the kill switch uses). Do not trip the switch | £0 cash on demo. Avoids a manual position left open after a 12% trip (by then £120 of the £1,000 is already lost) | Close result and retcode posted on #34 (figures only) | before Mon 26 Oct |
| P2 | **Day-0 RC counts (morning F2)**, now on `dbd5fa2`: proposals, orders, fills, `closed_records_lost`, `entry_blocks`, daily allowance, `kill_everything` false | Finds a broken money path before the first order | Posted on #34 | Mon 19 Oct 22:00 UTC |
| P3 | **Release notes list what ships** (G1): generate the "in this release" list from the merged handoff fragments, not from the plan | Audit and owner trust; £0 | The next release PR's scope matches `git log` | next release |
| P4 | **Keep the Fri 23 Oct classifier read clear**: no deploy or restart 07:00-21:00 UTC. Today's Saturday deploy is the model to follow | Avoids another 7-day slip | Known class stored with >= 1,000 ticks | Fri 23 Oct |
| P5 | **BACKLOG 77 stays £0 (G5)**: name the email sender's free limit in its plan, or bring it to the owner as a spend | Keeps scenario A at £0 | Plan states the provider tier and cost | at plan review |
| P6 | **Confirm the engine-down alert pages (G3)** | Shortens unprotected-by-engine time after a refused start | One drill result on #34 | Fri 23 Oct |

### Research director

| # | Recommendation | Expected £ effect | Measurement | Due |
|---|---|---|---|---|
| R1 | **Check the group-unit premise at H-02's reading (G4).** If any survivor goes forward as a subset of a cluster, K_eff reverts to the feature count (about t 4.6) for every later trial | Prevents promoting a false edge on a 0.02 R lower bar. A false promotion costs its whole demo sample (at least 150 trades) | `k_eff` on the discovery row equals the groups screened, and every survivor's members equal a whole group | H-02 reading (Q1) |
| R2 | **Separate NOT PLACED from approved in the learning metrics** (performance-analyst): LE-4 (approval rate) and opportunity capture should count an approved but refused entry as not traded, and report it | Measurement accuracy; it would otherwise overstate traded approvals | The LE-4 table has an "approved, not placed" column | November audit |
| R3 | **Land BACKLOG 75/76 on `main` before any Q1 run uses them** | £0; avoids a run on uncoded rules | Commits b7abc77 and 93a7891 are ancestors of `main` before O-4/O-5 run | before 2027-01-01 freeze |

## Owner decisions (options, not decided here)

- **Fixed dashboard address** (incident follow-up): (a) a free subdomain, £0; (b) the owner's own domain, which
  costs a registrar fee per year (amount not verified here). **Recommendation: (a)**, which keeps running costs at £0;
  (b) only if the owner wants the name.
- **Claude spend in the books** (morning F6): still pending; the recommendation stays option (b), count spend above
  the base subscription.
- No spending, live or limit decision arises from this deploy.

## Not verified / left out

The broker balance, server state, store tables, phase file, agent spend and Oracle billing were not read, and nothing
in this audit is reconciled to the broker. The incident timings are taken from its record. No code, settings, limits or
trials were changed.
