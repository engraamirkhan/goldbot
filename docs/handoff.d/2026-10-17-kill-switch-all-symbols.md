- **BACKLOG 62, the 12% kill switch closes every symbol (implementer, 2026-10-17; reviews owed: code-reviewer,
  trading-safety-reviewer E1, security before push).** Two commits on the plan
  `docs/proposals/2026-10-backlog62-kill-switch-all-symbols-plan.md` with the trading-safety conditions C1-C9.
  PR-A, broker layer: `MT5Broker.close` uses the position's own symbol (fill mode `_filling(symbol)`, `symbol_select`
  before the tick, no symbol info or tick is a refused close 10021, no partial close of another symbol); the paper
  broker keeps a quote per symbol (`set_quote`) and lists only its symbol in `positions()`, every symbol in
  `account_positions()`, as MT5. PR-B, engine: `_read_kill_book`; while `_kill_everything` is set the switch and
  `_retry_closes` read `account_positions()` and close every symbol and magic; an unreadable account book closes the
  `positions()` fallback and never clears the flag; the flag clears only on a flat `account_positions()` read (one
  more read after an all-ok run); `kill_switch_close` rows carry symbol, magic and lots. ADR 0016 amended (manual
  trades are closed; pending orders out of scope), RUNBOOK and OWNER_GUIDE warn the owner, TRACEABILITY R46.
  Before the deploy: the one-time owner notice on #34 (C8). Follow-ups: SRE health WARN when `kill_everything` stays
  set with closes due (C9); an older (v4) bridge without `account_positions` keeps the flag set and the switch
  running each tick until the bridge is upgraded (by design, C1). Verify on the demo terminal: closing a manual
  EURUSD trade (fill mode, `symbol_select`), which the fake MT5 in the tests cannot prove. Review fix round
  (trading-safety E1 APPROVE, LOW 1 and LOW 4): a 10021 close on another symbol backs off with the market-closed
  table (one journal row per step; the adapter logs it once per streak) while XAUUSD keeps the 1 s retry, and every
  `_close_due` entry records its symbol. Follow-ups: LOW 2 (a re-arm leaves `_kill_everything` set; the C9 health
  WARN in the SRE lane covers it before the deploy), LOW 3 (re-send another symbol's close without the read).
