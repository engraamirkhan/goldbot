---
section: next
---
- **BACKLOG 59 SRE re-verify: APPROVE (sre, 2026-10-16, 6287d10 + 412fd0d, merged at a13c8a3).** Evidence:
  `tests/test_weekend_reopen_alerting.py` (700c116) runs the engine, the bridge and the minute-by-minute health
  poll with `alert_text` on the production zone (Europe/Athens; the table opens 22:02 UTC Sun 18 Oct). No page
  through the close, the weekend or a first quote up to 5 min after the open; a bridge that dies on Saturday pages
  `bridge:` within 2 min and the engine's verdict goes `unchecked` (it is not refreshed by the heartbeat); a dead
  engine pages; an open market with no quote pages `bridge_compat:` at the 5-min verdict age and recovers. The
  supervisor's 60 s freshness rule only gains (weekend files now read fresh; no new halt reason). Two notes, neither
  blocking: (1) pre-existing, an engine STARTED in an open session before its first quote pages `engine:` "stale
  feed" once and recovers; start the engine on Sunday while the market is still closed (about 21:50 UTC) or only
  after the terminal shows a fresh XAUUSD quote. (2) LOW: the frozen-feed page reads "engine has not confirmed the
  bridge (its check may be stuck)", not "no quotes in an open market"; a clearer reason is a follow-up.
- **MT5 login on a generic-installer terminal (sre, 2026-10-16).** Postmortem
  `docs/incidents/2026-10-16-mt5-login-generic-installer.md`; ADR 0020 keeps MetaQuotes' installer and RUNBOOK 0.3
  logs in through File -> Open an Account -> Raw Trading Ltd; `mt5.initialize` gets `timeout=60000` beside `path=`
  (`MT5_INIT_TIMEOUT_MS`); the bridge requirements header no longer shows a bare `wine python`.
