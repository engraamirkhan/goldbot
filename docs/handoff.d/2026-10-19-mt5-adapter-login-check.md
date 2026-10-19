- Trading-safety (MEDIUM, money path): the direct MT5 adapter now re-checks the terminal's account before every entry,
  as the bridge does. `MT5Broker(verify=...)` is a required keyword; the engine passes
  `accounts.verify_terminal_account` for its account, so a terminal switched to another login, server or a real account
  after start refuses the entry (retcode 10006, "terminal_account_changed: <reason>", logged; an unreadable account or a
  raising check is "unverifiable" and refused too). Closes and stop changes are never checked (closing only reduces
  risk; the kill switch closes everything), exactly as GuardedBroker. The bridge (GuardedBroker verifies above it) and
  the scheduler's read-only broker pass `verify=None`. Tests: tests/test_mt5_order_account_check.py.
