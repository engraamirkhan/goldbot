# 0037: Demo cost probe: min-lot mirror of challenger signals for measured costs

Status: proposed (research director ruling, 2026-10-19; amends ADR 0036; pending trading-safety review)

## Context
The costs the research gates charge (spread, slippage, commission, swap) are priors or come from the few fills of
live agents. Challengers trade only in the shadow book, which fills at the bar close on the paying side and so never
sees slippage, order latency or the spread at the moment of the order. The demo account (ADR 0036: entries are
automatic there) can measure those costs at almost no risk. On 2026-10-19 the research director ruled that demo
learning trades are a cost probe, not a new population status: "mirror challengers' SHADOW entry signals as
minimum-lot (broker min, 0.01) demo orders, choosing which signal by a fixed round-robin rotation over agent_ids
(never by P&L/score). At most one probe position open at a time; real (live-agent) trades take priority".

## Decision
- `config/accounts.yaml` gains `cost_probe: { enabled: true|false }` per account (default off); `icm-demo` has it on.
  `accounts.Account` refuses it on any account that is not `mode: demo` at load; `Engine.__init__` refuses it on any
  engine whose mode is not demo (`cost_probe.check_start`), paper included.
- `goldbot/engine/cost_probe.py` (runner.py calls in at three points and stays under its line cap): at the end of
  each bar close, on the shadow host, it takes this close's new taken entries of challengers (shadow versions that are
  no agent's champion) of agents the broker may trade (TradingView and replica agents stay shadow only), one per agent,
  and picks the next agent id after the last one probed (sorted, wrapping). p, score and P&L never reach the choice.
- A probe is sent only on a flat account (no position at the broker, no engine trade, no pending proposal, no order in
  flight), so at most one probe position exists. The ADR 0036 lock applies unchanged at the order (`auto_entry
  .permitted`: account demo AND terminal `trade_mode == "demo"` AND the engine in auto), so a terminal that is not demo,
  the owner's `/mode propose`, the re-arm propose window and the kill switch stop probes.
- The order goes through `RiskGate.check` (model and account checks, the demo caps) on an Intent whose largest lot is
  the broker's minimum, so the gate sizes and checks exactly the lots sent; then `Engine._execute` (pending_orders row
  first). The trade is tagged `cost_probe:<agent_id>`, keeps the challenger's stop, target, time exit and exit policy
  and exits automatically like every trade (exits are never gated).
- Identity at the broker (trading-safety review round 1): a probe order carries its own magic, `magic_base + 97`
  (no strategy family's crc32 % 100, guarded by a test; not the bridge smoke's 99), and a `cp-` client order id, which
  is its MT5 comment. A probe found without its order row (a lost orders file) is adopted as `cost_probe:orphan` and
  recorded in the probe ledger, never as a live agent's trade; one recovered from an unconfirmed send keeps its
  challenger's exit policy.
- Budget (trading-safety review round 1): `cost_probe.budget_share` (default 0.2) per account. No new probe once
  today's realised probe loss reaches that share of the day's loss allowance (£10 of £50), nor while less than that
  share of the allowance remains; an unknown allowance or an unknown probe P&L today refuses (journal
  `cost_probe_budget:<why>`).
- Real trades take priority: a live agent's candidate closes an open probe before its decision, and any other position
  closes it at the bar close (exit `cost_probe_yield`). The probe never holds the position slot, the heat or the daily
  allowance a real trade needs. An ambiguous probe order still refuses every entry (`unconfirmed_order_pending`): an
  unknown order state outranks the priority rule.
- Records: each probe is a row of aux.db `cost_probes` (migration 14, additive), purpose `cost_probe`, with the agent
  id and version: signal, requested and fill price, spread, send latency, retcode, and at the close the exit, swap,
  commission and net P&L from the broker's deals. A probe's close never goes to `closed_trades.jsonl` and its fill
  never to the store's `fills`, so the phase gate, the throttle, attribution, the shadow scoring, the tournament and the
  nightly slippage table never see one. The broker ledger reconciles closed probes against `cost_probes` (named in its
  note) instead of reporting them missing; the balance tie-out still counts their P&L. Costs-v2 reads
  `cost_probe.probes()`; refitting costs on them is a later preregistered step.

## Consequences
- Measured slippage, spread at the order, latency and swap accrue on demo from the challengers' own signals, without
  the owner and without touching the evidence the gates and the tournament use.
- Probe losses are real demo money: they count toward the daily and weekly caps (the gate reads the broker's equity),
  but the budget stops new probes once they have lost their share of the day's allowance (or the allowance left is
  below it), so probes alone cannot use the part a real trade needs; one open probe can still lose up to its stop.
- A probe can be cut short when a real trade needs its slot, so its exit facts (and swap) cover fewer full trades than
  its entry facts; the entry-side costs, the purpose of the probe, are unaffected.
- The trades list on the dashboard does not show probes (they are not in the closed-trade record); an open probe
  appears among open positions under its `cost_probe:` tag. A labelled "cost probe (demo)" view is a follow-up.
- Changing the selection rule, the lot or enabling probes on a live account needs a new ADR; the demo-only lock is
  code, not config.

## Amendment 2026-10-19 (research director ruling, part C)
Owner: "the system needs to learn, so more trades need to be opened and closed". Every guard above stays (demo-only
lock, own magic, RiskGate, live trades first, 0.01 lot, round-robin never by P&L).
- Up to `cost_probe.max_open` (2, bounded at 2 in code) probe positions at once, on an account that holds probes only;
  at most one new probe per bar close and never two on one agent.
- Each probe's stop is clamped so its loss at the stop, from the broker's `loss_per_lot` at the minimum lot in the
  account currency, is at most `cost_probe.max_loss` (£4); the stop the RiskGate sets is checked again after the gate
  (a stops-level floor above the clamp refuses: `probe_loss_above_cap`), and a probe whose loss cannot be computed is
  refused (`probe_loss_unknown`). The target stays the challenger's.
- Budget: no new probe once today's realised probe loss (a profit adds nothing) + the open probes' loss at their
  current stops + the new probe's would exceed `budget_share` x the day's loss limit (0.2 x £50 = £10); an open probe
  whose risk is unknown refuses (`open_risk_unknown`). `max_loss` must fit inside that budget (checked at load).
- The bound is at the stop: a gap through the stop, commission and swap are outside it; they reach the realised P&L
  the budget reads. Revisit 3 probes after 4 weeks of clean data.

## Amendment 2026-10-19 (owner: "the demo account is for learning and testing strategies"; research and program directors' ruling)
- The clamped stop aims at 99% of `max_loss` (the terminal rounds `loss_per_lot` to the cent; aimed at exactly £4,
  every box_break_close probe measured a fraction of a penny above and was refused).
- Below-threshold (untaken) shadow entries are mirrored too, a taken one first for the same agent. They are capped:
  at most 6 a risk day (`UNTAKEN_PER_DAY`), at most half of `max_open` open, and only while more than half the probe
  budget is left (`cost_probe_untaken:daily_cap | slots_reserved | budget_reserved`); taken entries keep the rest.
  The ledger records `taken` per probe.
- Every probe goes to the RiskGate as a learning probe (`Intent.learning_probe`): the gate skips only the edge after
  costs (`negative_ev`, `target_below_cost_floor`, `standard_account_edge`, `standard_account_15m`). It checks the demo
  lock itself (registry mode demo and no live gate in the phase file, F1's rule: `learning_probe_not_demo`), the
  minimum lot (`learning_probe_not_min_lot`) and a positive edge before costs (`negative_gross_edge`);
  `time_exit_ev_unsupported`, every loss limit, heat, margin, exposure and throttle still apply.
- Evidence: probes stay out of closed_trades, the phase gate and shadow scoring (unchanged); no hypothesis is chosen
  or tuned from probe P&L.

## Amendment 3, 2026-10-19 (owner: learn and test faster on demo; research and program directors' ruling)
icm-demo: `max_open` 4 (code bound raised from 2 to 4), `budget_share` 0.4 (£20 of the £50 day), `max_loss` £4 per
probe unchanged; untaken probes at most 12 a risk day (still at most half the slots and half the probe budget).
- Up to 2 new probes per bar close (`PER_CLOSE`), from different agents, each checked afresh; never two on one agent.
- At most 2 open probes on one side (`MAX_SAME_SIDE`): one move must not hit four correlated probes.
- Real trades first: no new probe once the day's allowance left after the open probes' risk and the new probe's loss
  would fall below 0.6 x the day's loss limit (`REAL_TRADE_RESERVE`, £30: three real trades at the 1% cap); the
  existing allowance_low rule and `yield_to_live` stay. The account's £50 daily limit is unchanged and probe losses
  count toward it.
- Review after 10 risk days (2 weeks of clean data): if simultaneous probes' outcomes correlate above 0.7, return to
  `max_open` 2.
- In practice (trading-safety review of amendment 3): the reserve needs £30 + the new probe's £4 + the open probes'
  risk left of the £50, so realised probe losses stop near £16 a day; the full £20 and the fourth slot are reachable
  only on a day with few losses. Safe by construction; the review after 10 risk days reads the numbers as they are.

## Amendment 4, 2026-10-19 (owner approved a dedicated demo account `icm-probe`, GBP; directors' ruling: "probes must never consume the real trades' allowance")
Context: on icm-demo the probes share the real (champion) demo trades' equity, £50 day, weekly cap and drawdown
stages, and the supervisor sums every account into one set of combined limits, so a probe loss also tightened the
real account's combined caps (SRE finding). Decision:
- `icm-probe` (config/accounts.yaml): mode demo, its own magic base (260500), `approval_mode: auto`, the owner's demo
  ceilings (ADR 0032), `login: null` (keyring only). Its own day: `daily_loss_limit_abs` £20; `cost_probe`
  `budget_share` 0.8 (£16 = four probes at £4), `max_open` 4, `max_loss` £4. Proposed by the research and program
  directors within the owner's approval; the £20 and the 0.8 are revisited with the amendment 3 review.
- `probe_only: true`: the engine trades no live agent (no live shares) and `Engine._execute` refuses any entry that is
  not a cost probe (`probe_only_refused`, fail closed). Refused at load unless `cost_probe.enabled` and `shadow_host`
  are set on the same account. With no real trade to protect, its `REAL_TRADE_RESERVE` is 0
  (`cost_probe.real_trade_reserve`): its probe budget binds, inside its own daily limit, which the RiskGate enforces
  unchanged. The RiskGate and every limit's semantics are unchanged.
- Shadow host: an explicit per-account `shadow_host` flag (on the canonical-cost broker; another broker's flag is
  logged and not hosted). At most one enabled account may be the host: two fail at load. The shadow book is
  `state/shadow_book.json` in the shared state dir, so the new host continues the existing books.
- Supervisor: an engine file stating `probe_only: true` (literally; missing or malformed counts as real, fail closed)
  is out of every combined sum, cap, currency check and the exposure real engines read; reported under
  `probe_engines`. A probe engine counts the real accounts' lots and notional against its own combined caps but never
  their equity. An unreadable engine file or a clock fault still halts every account.
- Switch-over, config only and reversible: while icm-probe has no credentials or terminal it stays `enabled: false`
  and icm-demo is host and prober as before. To switch: store icm-probe's credentials
  (`python -m goldbot.ops.accounts add icm-probe`), then icm-probe `enabled: true`, icm-demo `shadow_host: false` and
  `cost_probe.enabled: false`, restart both engines. Reverse the same three lines to go back.

Consequences: probe losses no longer touch the real demo account's allowance or combined limits; icm-demo trades
champions only after the switch. A probe engine whose start fails rewrites its file without the flag and is then
counted as a real account (`engine_startup_refused` halts), the strict direction. Trading-safety review required
(order path: `_execute` refusal; limits: supervisor exclusion, reserve 0 on probe accounts).

## Amendment 5 (2026-10-20, owner-approved)
Context: the scheduled M1 cost probes (`cm-sched-`, ledger basis `scheduled_m1`, purpose `cost_measurement`;
docs/research/prereg-cost-measurement-2026-10.md) carry no signal, so the learning-probe fence (amendment 2026-10-19)
refused every one `time_exit_ev_unsupported,negative_gross_edge`, and the prereg (K8) kept them refused until the gate
had a cost-measurement purpose. Asked whether scheduled cost probes may get a fenced RiskGate exception, the owner
answered "Allow, demo only" on 2026-10-20. Goal: hundreds of demo fills for the measured-cost protocol.

Decision: `Intent.purpose = "cost_measurement"` (goldbot/risk/gate.py) skips ONLY the expected-value / gross-edge
requirement (`time_exit_ev_unsupported`, `negative_gross_edge`; a probe never had `negative_ev`,
`target_below_cost_floor` or the standard-account edge rules). It is allowed only when ALL of these hold, each a
named refusal:
- the order is a learning probe (`cost_measurement_not_probe`: any other order claiming the purpose is refused);
- the account is demo by the registry and no live gate is in the phase file (the learning probe's lock, F1's rule:
  `learning_probe_not_demo`), AND the terminal itself reports demo (`account_info().trade_mode`, read fresh at the
  order by goldbot/engine/cost_probe.py; unreadable is None and refuses: `cost_measurement_terminal_not_demo`);
- the lot is the symbol's minimum, asked and sized (`learning_probe_not_min_lot`);
- a protective stop is present (`invalid:stop_atr` otherwise) and at most $3/oz from entry, asked and as sized after
  the stops-level and spread floor (`cost_measurement_stop_above_cap`);
- the planned hold is in (0, 60] s (`cost_measurement_hold`);
- the loss at the stop is within the cost-probe budget headroom and the dynamic per-probe ceiling, both known and
  positive (`cost_measurement_budget`).
Every other check is unchanged and runs first: halts (owner, supervisor, drift, the 12% kill switch), the news and
entry windows, the daily and weekly caps, the open-risk projection, heat, margin, exposure, throttle and the
same-direction cap. Mirror probes keep the amendment-2026-10-19 fence unchanged. Exits are never gated.

Consequences: scheduled probes now fill on a demo terminal within the probe budget (`_send_scheduled` asks for the
purpose with its hold, headroom and dynamic ceiling; nothing else in the probe path changed). The gate checks the
planned hold, not the actual one: the 60 s bound at the close is `hold_exits` (never gated). The purpose is a
caller-declared field: the fence, not the caller, decides, so a wrong claim refuses rather than widens. Tests:
tests/test_cost_measurement_gate.py, tests/test_probe_scheduled.py. Trading-safety review required (order path).
