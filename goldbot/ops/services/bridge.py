"""The MT5 bridge service (`python -m goldbot.ops.run bridge <account_id>`, under the Wine or Windows Python next to the
terminal). Moved verbatim from goldbot/ops/run.py, which re-exports it."""
from __future__ import annotations


def run_bridge(account_id: str) -> None:  # pragma: no cover - needs the MetaTrader5 package and a terminal
    """Serve the MT5 terminal on this machine to the engine (goldbot/execution/bridge.py). Run under the Wine (or
    Windows) Python next to the terminal. The terminal's saved login is used; the listen address and token come from
    the keyring (`mt5-bridge-listen-<account>` as host:port, `mt5-bridge-token-<account>`)."""
    from goldbot.execution.bridge import GuardedBroker, serve
    from goldbot.execution.mt5_adapter import MT5Broker
    from goldbot.ops import accounts
    acc = accounts.load_accounts()[account_id]
    listen = accounts.get_secret(f"mt5-bridge-listen-{account_id}")
    token = accounts.get_secret(accounts.bridge_token_key(account_id))
    if not listen or not token:
        raise SystemExit(f"store mt5-bridge-listen-{account_id} and {accounts.bridge_token_key(account_id)} first "
                         "(python -m goldbot.ops.accounts bridge-serve " + account_id + ")")
    host, port = listen.rsplit(":", 1)
    if host not in ("127.0.0.1", "localhost"):     # the token must never cross a network in clear text
        raise SystemExit(f"bridge must listen on 127.0.0.1 (reached through the SSH tunnel), not {host}")
    broker = MT5Broker(terminal_path=acc.terminal_path, login=None, password=None, server=acc.server,
                       server_tz=acc.server_tz, symbol=acc.symbol, account_label=account_id)
    why = accounts.verify_terminal_account(acc, broker.account())
    if why:                                        # e.g. the terminal's saved login is a live account
        broker.shutdown()
        raise SystemExit(f"refusing to serve {account_id}: {why}")
    from goldbot.ops.bridge_compat import repo_commit
    serve(GuardedBroker(broker, magic_base=acc.magic_base,
                        verify=lambda info: accounts.verify_terminal_account(acc, info)), host, int(port), token,
          commit=repo_commit())           # read once: the code this process runs, reported on /health (BACKLOG 42)
