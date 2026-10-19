# 0036: Demo accounts enter automatically; real money keeps owner confirmation

Status: accepted (owner directive, 2026-10-18)

## Context
The design's standing mode is propose-and-approve: every entry waits up to 90 s for the owner's click; `/mode auto`
(TOTP + evidence, traceability row A10) is global. On 2026-10-18 the owner directed, verbatim: "for the demo
account, the trades will be opened and closed by the system, no confirmatio is needed, for real account with real
money , stick to original plan of confirming from me". Demo trading with clicks loses most signals (owner offline) and
so produces little evidence; real money keeps the human veto.

## Decision
- `config/accounts.yaml` gains `approval_mode: propose | auto` per account (default propose); `icm-demo` is `auto`.
  `goldbot/ops/services/engine.py` passes it to `EngineConfig.approval_mode`.
- Hard lock, one rule (`goldbot/engine/auto_entry.refusal`) at four layers: (1) `accounts.Account` refuses `auto` on
  any account that is not `mode: demo` at load; (2) `Engine.__init__` refuses `auto` on any engine whose mode is not
  demo/paper (`check_start`), so a live account configured auto fails startup loudly; (3) every account refresh applies
  the lock after the owner's `/mode` (`locked`): auto needs mode demo AND the terminal's own `trade_mode == "demo"`
  (MT5 `account_info`), else propose; (4) at the order itself a fresh broker read is checked again (`permitted`); a
  refusal proposes the entry to the owner and is journalled `auto_refused`. The paper broker (no terminal) may run auto.
- Unchanged: RiskGate is the only path to an order; every limit and cap; the 12% kill switch and the 30-day propose
  window after a re-arm force propose on demo too; the owner's `/mode propose` wins over the account's auto; exits
  are never gated; shadow-only agents still trade nothing. After the re-arm window ends a demo account returns to its
  configured mode (it no longer stays propose until a restart).
- A demo auto fill sends the owner an information notice "Opened automatically (demo): ..." (no buttons).

## Consequences
- Demo evidence accrues without the owner online; real-money behaviour is unchanged except that the global `/mode
  auto` no longer makes a live account automatic (the directive keeps confirmation for real money).
- A control.json left at `propose` by an earlier `/mode propose` keeps the demo engine proposing until the owner sends
  `/mode auto <TOTP>` (when eligible) or the value is cleared; this is deliberate (lowering authority always wins).
- Changing the live rule needs a new owner decision and ADR; the lock is code, not config.
