# 0023: Engine split: seams, move-only rule, ratchet

Status: accepted with conditions (cto, 2026-10-17). Number reserved by the CTO register (`docs/process/tech-debt.md`
TD-2) and claimed with `new-adr --number 23`. Seam map: `docs/proposals/2026-10-runner-seam-map.md` (architect,
`7dc12a3`). Sequencing against W2-1 (option C) was ruled on 2026-10-16 (register section 7, ruling 1).
Requirements: DESIGN Stack "SQLite for live state"; TRACEABILITY X7, A11, S4.

## Context
- `goldbot/engine/runner.py` is 3,492 lines at `7dc12a3` (`wc -l`), one `Engine` class (324-3492) with 146 methods
  (`grep -c "    def "`). It took 99 of the non-merge commits since 10-01, twice the next file (register section 1).
  W2-1 (orders, risk state and approvals into `core.db`) and Wave 3 (A2, D10, M9, M3) all edit it.
- Option C: first extract the persistence boundary, move-only (step 1). W2-1 then adds a `core.db` implementation
  behind it, and the other seams move after W2-1 (steps 2-7). Rules: move-only PRs, composition rather than
  mixins, a line-cap ratchet, and file ownership in the ROADMAP "Shared files" note.
- CTO measurements of the map at `7dc12a3`
  (a regex pass over the map's line ranges: `self.<attr>` names that are not methods, read or written
  inside the range and also outside it and `__init__`, leaving out the injected `cfg`, `broker`, `clock`, `center`,
  `gate`):

| Step | Seam | Lines moved | Engine attributes shared with other code (approx.) |
|---|---|---|---|
| 1 | store | 181 (part of it stays, see below) | 14, all carried as snapshot fields, none held by the store |
| - | account guard | 356 | 24 |
| - | exits | 343 | 10 (of which `agents`, `models`, `decisions`, `store` are injectable) |
| - | reconcile | 493 | 17 |
| - | clock | 219 | 15 |
| - | sizing | 334 | 10 |
| - | windows | 152 | 4 |
| | **all seams** | **2,078** | runner keeps **1,414** before facade delegates and glue |

  The count is a regex, not a data-flow trace. It over-counts reads that the facade will pass in, so it ranks the
  seams; it does not size their interfaces.

## Decision

### Step 1: `engine/store.py`. Interface accepted
The map's step 1 is accepted as written: `EngineStore` protocol, `JsonEngineStore(state_dir, account_id)`,
FrozenRecord `RiskSnapshot` and `OrdersSnapshot`, "None = confirmed absent, unreadable raises". This is the right
shape. The store holds no Engine state. The runner builds and applies snapshots, so the 14 shared attributes cross
the boundary as data rather than references. That makes the "reverse if" of ruling 1 (persistence too entangled
for a narrow interface) not met. Option C stands. Conditions on the move PR:

| # | Condition | Evidence | Proof |
|---|---|---|---|
| S1 | The snapshots are built from the values the existing parse produces. The raw file dict is never passed to `model_validate`. `FrozenRecord` is `extra="forbid"` (`goldbot/base.py:38`), while `_load_risk_state` silently ignores keys it does not know (`runner.py:2929-2953`). Validating the dict would turn a newer release's risk file into a refused start. | base.py:38; runner.py:2936-2953 | golden edge test: a risk file with an unknown key loads as today, with no skew note and no raise |
| S2 | Defaults for absent fields stay word for word in the store's parse: `kill_everything` False, `stage2_pending_since` None, `stage` NORMAL, equities 0.0. A corrupt file raises the same exception type as today. | runner.py:2941-2953, 2496 | edge tests in PR 0 assert the exception type, not just "raises" |
| S3 | `_write_state` keeps its write order (engine heartbeat, then risk, then orders), and the engine write keeps `durable=False`. | runner.py:3488-3490 | golden grid on the three files |
| S4 | `store.py` imports `write_atomic` as a module-level name. The one non-import test edit allowed is `tests/test_equity_confirmation.py:348` (`runner.write_atomic` becomes `store.write_atomic`). The AST ratchet then guarantees no durable write is left in the runner that the full-disk patch would miss. | test_equity_confirmation.py:340-348 | the ratchet below; the full-disk tests pass unchanged |
| S5 | The AST ratchet `test_runner_does_no_state_file_io` cannot ban `json.loads` / `read_text` outright at step 1. Three non-state reads stay: `runner.py:527` (approvals pending, `_retired_proposal`, moved by W2-1 F1), 3307 (`_drift`) and 3319 (`_account_class`), the last two moving with sizing. Rule: `write_atomic` is banned in `runner.py` from step 1. `json.loads` / `read_text` are allowed only in a named allow-list of those three functions, and each entry is removed when its code moves. | grep at `7dc12a3` | the ratchet test with its allow-list |
| S6 | W2-1 may extend the protocol additively and is expected to. Examples: an entry-path `commit_sending(OrdersSnapshot)` for F4/F6's 2000 ms versus 500 ms busy timeouts, and `written_ns` stamping inside the store for F3. `JsonEngineStore` implements each extension by delegating to the existing methods. Any non-additive change to the protocol needs an architect note and CTO sign-off. | W2-1 plan F3, F4, F6 | protocol conformance test runs against both implementations |
| S7 | PR 0 (the golden grid and the edge tests, on today's code) merges before the move PR opens. The move PR changes no golden file. | seam map "Proving move-only" | `git diff --stat tests/golden` empty in the move PR |

### `_orders_record` stays in `runner.py`. Accepted
It mutates `sent_ids` before the write (`runner.py:2468-2475`). That is the X7 ordering: the id is known before
`order_send`. W2-1 F7 requires `sent_ids.add` to stay before the commit. Moving it into the store would put engine
state behind the persistence boundary. This corrects the register's step-1 row of 2026-10-16, which listed it as
moving. `_save_risk_state_now` also stays, because it carries the engine's never-raise rule and `note_tick_error`
(`runner.py:2916-2927`).

### Order of steps 2-7. Changed: account guard is fourth, not second
The architect's reason for putting the account guard second is that it carries the kill switch. Isolating it is
the most valuable step for review. It is also the most coupled seam: about 24 shared attributes against the map's
own stop threshold of about 8. Second place would test the composition pattern for the first time on the
kill-switch code, which is the seam most likely to be stopped. The lowest-coupling seams go first so that the
pattern (collaborator, injected dependencies, facade delegates) is proven and reviewed before the kill switch
moves. The account guard still lands mid-sequence with a full review window, not in the last slot before the 12-14
freeze.

| Step | Module | Code that moves (lines at `7dc12a3`) | Depends on | Due | Accountable |
|---|---|---|---|---|---|
| 1 | `engine/store.py` | `OpenTrade`, `PendingClose`, `SentOrder` (258-323, re-exported); the path, dump, write and parse of `_orders_path` / `_save_orders` / `_load_orders` (2465-2525) and `_risk_path` / `_save_risk_state` / `_load_risk_state` (2900-2953); the engine-file write in `_write_state` (3488) | BACKLOG 62 merged; PR 0 (golden grid, by Tue 10-27) | Fri 10-30 (merge on the integration branch; no deploy in the DST-gap week). **Done 2026-10-17** (built on the integration branch at 57bdc02; golden grid unchanged; runner.py 3,552 -> 3,471, cap 3,471) | implementer |
| 2 | `engine/windows.py` | `_blackout` … `_windows_state` (2980-3131) | step 1 | Tue 11-24 | implementer |
| 3 | `engine/exits.py` | `_read_positions` … `_blackout_rescore` (1476-1818) | W2-1 merged (F4) | Thu 11-26 | implementer |
| 4 | `engine/account_guard.py` | `_refresh_account` … `_reset_owner_mode` (1963-2261); `_roll_risk_period` … `_rearm_conditions` (2407-2463) | BACKLOG 62, W2-1 (F5); state-ownership table (below) | Tue 12-01 | implementer; ownership table by the architect |
| 5 | `engine/sizing.py` | `_account_currency` … `_published_limits` (980-1142); `_refresh_broker_terms` … `_throttle_state` (3251-3421) | TD-16 merged | Thu 12-03 | implementer |
| 6 | `engine/reconcile.py` | `_own` … `_lot_terms` (1819-1937); `_resolve_sent` … `_closed_excursion` (2526-2899) | steps 1, 3; W2-1 | Tue 12-08 | implementer |
| 7 | `engine/clock.py` | `_host_now` … `_host_clock_exits` (711-784); `_server_clock` … `_check_new_bars` (2262-2406) | none | Fri 12-11 | implementer |

Step 1 note (trading-safety ruling, 2026-10-17): one deviation from S2 is kept. `RiskSnapshot`'s types refuse a risk
file value the engine never writes (a non-string `rearm_seen`, a "NaT" timestamp) with a ValueError at start; exact
parity would keep a fail-open re-arm check and a mid-tick `NaT` crash.

Line numbers are re-read at each PR's base. One split PR is open at a time, and each is reviewed by the
trading-safety-reviewer and the code-reviewer with `git diff --color-moved=dimmed-zebra` and full gates. A step
whose dependency is late may swap with a later step that has none, such as clock. The swap is recorded in the
register the same day, and the account guard never moves after Tue 12-08.

### Stop rule. Accepted, made measurable
"About 8 Engine attributes" becomes a number the PR states:

- **Metric.** Count the Engine attributes the collaborator reads or writes that it does not own. Constructor-injected
  dependencies do not count: `cfg`, `broker`, `clock`, `center`, `gate`, `store`, `agents`, `models`, `decisions`.
  The attributes the collaborator owns move into it, and the Engine reaches them only through the collaborator.
- **Threshold.** More than 8 means the seam is stopped and recorded in the register, with the list. It is never
  forced through a mixin, a back-reference to `Engine`, or a "context" object that wraps `self`.
- **Before the PR.** For steps 4, 6 and 7 (17-24 shared at a coarse count), the architect delivers a
  state-ownership table with the TD-14 plan on Fri 11-13. It says which attributes each seam owns and which it reads.
  If the account guard is still above 8, it is split in two: equity and high-water-mark confirmation
  (`_refresh_account` … `_set_stage2_pending`), and kill switch and re-arm (`_kill_switch`, `_reset_owner_mode`,
  `_roll_risk_period` … `_rearm_conditions`). Each half is then measured on its own. A stopped seam does not block
  the steps after it.

### Line-cap ratchet. Mechanism accepted, target changed
`tests/test_engine_structure.py::test_runner_py_stays_under_its_line_cap` lands with step 1. Each step lowers the cap
to that step's measured `wc -l` with no slack, and the cap is never raised; raising it needs a CTO ruling. **The
≤ 1,200 target after step 7 is not accepted as a commitment.** The map's own ranges move 2,078 lines, which leaves
1,414 before the facade delegates. The step-1 delegates alone serve about 50 test call sites (`grep` of
`tests/` at `7dc12a3`). The committed target is **≤ 1,500 after step 7** if every seam lands. ≤ 1,200 stays the
platform goal, to be reached by a **step 8** that the architect scopes at the step-7 review (Fri 12-11) from the
residual file. Candidates for step 8:

- re-point tests to the collaborators in tests-only PRs and delete the delegates;
- move the owned state's initialisation out of `__init__`, which is 188 lines (325-512);
- move proposal-lifecycle reads to the W2-1 bus (513-559).

Step 8 is not dated until it is scoped.

### What stays in `runner.py`
The map's list is accepted: state attributes (until their owning seam moves), the eight persistence method names as
thin delegates, `_orders_record`, `_save_risk_state_now`, `_flush_closed` and the 7-day prune, reconciliation,
`excursion_approx`, `write_startup_error`, the `kill_everything=False` reset and the first-start `rearm_seen`,
`_kill_pending` / `_mode_reset_due`, and how `_write_state` builds its payload. The constructor signature is
unchanged. Long-term residents are the tick loop, bar close, frame building, `_decide` / `_on_decision` /
`_execute` orchestration and shadow stepping. The public facade is unchanged: `on_tick`, `on_bar_close`,
`warm_start`, `no_quote_pass`, `set_*`, `flush_*`, `note_tick_error`, `clock_skew_s`.

### Rules for every step (from register TD-2, unchanged)
Move-only, one seam per PR, no test edits other than imports (plus S4's one patch site). Composition, not mixins.
The ratchet is lowered each step. The ROADMAP "Shared files" note names the new module and its owning lane. No
split step deploys during the DST-gap week (10-26 to 10-30). Non-negotiables untouched: RiskGate is the only path
to an order, exits are never gated, features are unchanged. No owner decision is involved.

## Consequences
- W2-1 builds `CoreDbEngineStore` in `engine/store.py` (or `db/core_state.py`, imported by it) behind `EngineStore`.
  It reuses `RiskSnapshot` instead of defining `RiskStateRecord`; the W2-1 plan's file table is amended. W2-1 still
  edits `runner.py` at `_execute`, `_orders_record` (F7 `state_unwritable`), `_archive_stale_proposals`,
  `_retired_proposal` and `_reset_owner_mode`. Those edits are small, and they are sequenced before steps 2-7.
- Step 1 slips one week if BACKLOG 62 is not merged by Mon 10-26. The program-director is told the same day, and
  W2-1 (Mon 11-02) then starts on top of the step-1 PR branch rather than in `runner.py`.
- Each seam costs one trading-safety review round (about 7 in total, plus PR 0), and steps 2-7 take the
  11-23 to 12-11 window as the register already budgets (TD-14, ~10 lane-days).
- **Reverse if** PR 0's golden grid cannot be reproduced by a move-only step 1 (the store needs logic changes to
  pass). Then W2-1 goes in place (option B) and the split follows it. Steps 2-7 are revisited if three seams in a
  row are stopped by the stop rule: then the class structure, not the file, is the problem, and the architect
  proposes a state-model change instead.

## Addendum A1 (2026-10-17, architect; S5 resolution and the ADR 0010 gap)

PR 0 (34bfdf3, trading-safety APPROVE) found that after step 1 removes the seven orders/risk/engine-file sites, three
functions still read files that are not engine state. The ratchet allow-list is per function:

| Function | Seam | Step | Kind |
|---|---|---|---|
| `_drift` (~3300) | `sizing.py` (inside the step-5 range 3251-3421) | 5 | move-only |
| `_account_class` (~3313) | `sizing.py` | 5 | move-only |
| `_archive_stale_proposals` (~513) | W2-1 F1 approvals path (behind the bus/state store), not a 1-7 seam | W2-1 F1 | interface: `approvals.archive_stale(now_ns) -> int` |

Rules: step 1 bans `write_atomic` from `runner.py`; `json.loads`/`read_text` are allowed only in those three functions;
each entry is deleted in the PR that moves its function, and a stale entry fails the ratchet (the allow-list must be a
subset of the functions still present); if W2-1 F1 has not merged by step 5, `_archive_stale_proposals` moves unchanged
into `engine/approvals_io.py` and W2-1 re-points it later. **After step 7 the allow-list is empty**: `runner.py` holds no
`json.loads`, `read_text`, `write_text`, `open(` or `write_atomic` (the step-7 PR's final-state assertion).

ADR 0010 gap: `_load_risk_state` ignores unknown keys without `note_schema_skew`. The fix **follows** the step-1 move
(S1/S7: the move changes no golden) as its own PR in `store.py` before step 2 — BACKLOG 74.

## Ruling 12 (CTO, 2026-10-17): runner.py line cap, one-time raise

`LINE_CAP` is raised once, 3,492 → **3,552**, the measured size on the integration tip. PR 0 (34bfdf3) was written in
parallel with two approved feature lanes that have since merged — BACKLOG 71 (d067a19) and BACKLOG 62 (e35f566), both
trading-safety E1 APPROVE — so the pin was stale when written. Trimming now (option b) was rejected: it would edit a
file the step-1 lane holds and put safety-reviewed code at risk to satisfy a counter. Exempting the ratchet (option c)
was rejected: it stays live. The step-1 move lowers the cap to its measured size in the same PR, with no slack.
**Future feature lanes touching runner.py:** while a move step is open, a feature lane lands after that step or rebases
onto it; it may raise the cap only by its net added lines, with a code-reviewer note in the PR, a trading-safety E1 for
any engine change and a CTO ruling line; a feature adding more than 40 net lines to runner.py puts the new code in the
extracted module instead. Owner: implementer; review at the step-1 merge.
