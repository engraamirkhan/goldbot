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
