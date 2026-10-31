# 0063: Observe-only live account for XAUUSD247

Status: proposed (principal engineer, 2026-10-31; trading-safety and security review pending)

## Context
ADR 0062 built a weekend lane for XAUUSD247, Vantage's 24/7 gold CFD, on a Vantage Standard STP demo (`vantage-247`).
On 2026-10-31 the owner found that XAUUSD247 is listed on no Vantage demo server. It is listed on the owner's **live**
Vantage account. The owner's directive: "use it via the LIVE Vantage account, but NO live trading".

What the system needs from the symbol is evidence, not orders:
- its weekend bars and spreads;
- its contract terms, sessions, swap and stops level;
- the same for the account's weekday XAUUSD, to compare the two on weekdays.

That evidence comes from reading a terminal. Reading a terminal that is logged in to a real-money account puts one
risk on the table that no other account has: any path that can send an order would reach real money, outside the
phase gate.

## Decision
1. **A new kind of account.** `kind: observe_only` in config/accounts.yaml (`vantage-live-obs`, `mode: live`, its own
   broker key `vantage_obs`, `observe_symbols: [XAUUSD247, XAUUSD]`).
   - Its server and login are `null` in the public yaml. Both are kept in the box keyring (`mt5-server-`,
     `mt5-login-`), as the password is.
   - An observe_only block that is enabled, or that has anything that could place, size or approve an order, is
     invalid: auto approval, cost probes, exploration, probe_only, shadow host, pilot, order lane, risk overrides,
     dynamic limits, max_positions, always-open sessions or weekend_only. `load_accounts` drops that one account with
     an ERROR (review of PR 236), so one bad block never stops every service. A dropped account is never returned at
     all, `unlock_live` still refuses it, and health's `observe` line WARNs "refused at load".
   - `enabled_accounts` never returns it, even after every gate.
   - `unlock_live` refuses it.
   - **Shared login (review of PR 236).** `vantage-live` may be the same real account. A trading account whose
     keyring login and server equal an observe_only account's is refused by `unlock_live` and by its box bridge's
     start, before any attach or login (`accounts.observe_login_clash`). Neither value appears in the reason.
   - The engine refuses to start for it before any broker connection (exit 78, nothing written).
2. **The investor password.** `accounts add` asks for the "investor (read-only) password, not the master password".
   With it, MT5 logs in with `ACCOUNT_TRADE_ALLOWED` false, so the broker rejects every order. Bridge protocol v9
   reports that flag (`AccountInfo.trade_allowed`).
   - `verify_terminal_account` refuses an observe_only account whose terminal says trading is allowed (a master
     password) or does not say (an older bridge).
   - The bridge's `verify_first` then serves nothing at all (`wrong_account`).
   - The hedging rule is skipped for these accounts only, because they never open a position.
   - A wrong server is reported as "server mismatch" without either name, for every account.
3. **Defence in depth, before the terminal.**
   - **Bridge (box).** It is observe-only when the account's `kind` says so, OR its unit sets
     `GOLDBOT_BRIDGE_OBSERVE_ONLY=1` (a drop-in the installer writes). Either one is enough, so the stricter wins.
     - It serves the `OBSERVE_METHODS` allowlist only: bars (`get_bars`, `get_bars_range`), ticks (`copy_ticks`,
       `last_tick`), `symbol_info`, `symbols_get` and `account`. Everything else gets 403 `observe_only` before the
       queue: orders, stop changes and closes, and also positions, deal history, broker terms and sizing reads
       (security review of PR 236).
     - It has no order lane.
     - Its `GuardedBroker` refuses the three calls again.
     - `/health` lists only the methods it serves, with `observe_only: true`.
   - **Brain.** `RemoteBroker(observe_only=True)` raises `ObserveOnlyRefused` before anything is sent. The collector
     builds its client that way, and the engine's client follows the account's kind.
   - **Closes too.** Closes are refused as well: this is the owner's own account, and the system holds no position
     there. "Exits are never gated" applies to the system's own positions, and there are none.
4. **A collector, not an engine.** `goldbot-observe@<account>` on the brain (`python -m goldbot.ops.run observe
   follow`, settings `observe:`) records:
   - closed M1 bars, with `available_utc` set to the time they were read, into store table `observe_bars_1m`;
   - quote and spread samples into `observe_ticks`, every 15 s and every 5 s from Friday 21:00 to Sunday 21:00 UTC;
   - the symbol terms into `state/observe/<account>/specs.jsonl`, a line whenever they change.

   Every table uses source = the account, so the data never pools with any trading account's tables. It is never
   published (data-local-only). It keeps running all week.
5. **Health.** An `observe` line shows "observe only, never trades", whether it is connected, the last bar's age and
   the spread. It only ever WARNs. The account is never in the trading accounts' checks.
6. **vantage-247 is retired.** It stays configured with `enabled: false`, so ADR 0062's weekend code and the WKS0 and
   WKI0 controls stay tested for the day a demo lists the symbol. Its units on the Vantage box are stopped and
   disabled.

Options weighed:
- (a) **Trade XAUUSD247 on the live account at the minimum lot.** Rejected: it is against the owner's directive, it
  passes no gate, and no family is qualified on the symbol.
- (b) **The master password, with code-level refusals only.** Rejected: one bug in any order path would reach real
  money. The investor login makes the broker the last line, by construction.
- (c) **Wait for a demo to list XAUUSD247.** Rejected as the only path: the weekends until then teach nothing. The
  retired demo lane stays ready for that day.
- (d) **An observe-only account with the investor password plus code-level refusals.** Chosen.

## Consequences
- **Trading safety.**
  - Nothing in the system can place, change or close an order on this account. There are five independent layers:
    1. the broker (the investor login);
    2. the bridge's terminal check (trading must be reported disallowed);
    3. the bridge's method filter;
    4. the GuardedBroker;
    5. the brain's client.
  - The engine, RiskGate, supervisor, phase gate and `unlock_live` paths are unchanged for every other account.
  - The trading-safety reviewer reads the bridge and the AccountInfo change (AGENT_STANDARDS S12).
- **Reversal.** An order on this account needs all of these, and editing config/accounts.yaml alone can never place
  one:
  - a qualified family (ADR 0061);
  - a reviewed change of `kind` (trading-safety and security review);
  - the master-password login, which is the owner's;
  - the owner's `unlock-live` and typed phrase;
  - the phase gate.
- **Credentials.** The owner types the server, login and investor password once, at
  `goldbot-mt5 accounts add vantage-live-obs` on the Vantage box. Nothing goes to git, chat or a log.
  - The investor password cannot trade. If it leaks, it exposes the account's history and positions, not its money.
  - The terminal's `servers.dat` is not readable as text, so the server name is typed rather than picked from a list.
- **Operations.**
  - The Vantage box runs a third Wine install, `C:\MT5\VantageObs`, with its bridge on 8767. The brain's tunnel uses
    8770.
  - On 2026-10-31 the box had 1089 MB of its 1959 MB available with two terminals. Retiring vantage-247's terminal
    frees about 140 MB, and the new terminal and bridge take about 320 MB.
  - The shared-Wine risks of ADR 0062 apply: one wineserver and one Xvfb for every terminal.
  - Bridge protocol v9 keeps MIN_COMPATIBLE 5. A v8 bridge reports `trade_allowed` as None, which only an
    observe_only account refuses, so either box can be deployed first.
- **Research integrity.** The collected data is evidence only.
  - It is not a qualification, label or cost source: its own tables, keyed by account, are never read by the learner,
    the matrix or the cost tables.
  - Any research use goes through a pre-registration, like every other source.
  - If the observe data shows a weekend edge, that is a hypothesis to test, not a qualification.
- **What would change this.** If a Vantage demo lists XAUUSD247, the weekend lane of ADR 0062 can trade it there.
  This account stays observe-only either way.
