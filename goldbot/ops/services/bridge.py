"""The MT5 bridge service (`python -m goldbot.ops.run bridge <account_id>`, under the Wine or Windows Python next to the
terminal). Moved verbatim from goldbot/ops/run.py, which re-exports it."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

# The bridge exits with this when initialize(path=...) launched a terminal of its own (review L3): systemd then stops
# the bridge unit's whole cgroup, that terminal included, and starts the bridge again behind the terminal unit's.
TERMINAL_SPAWNED_EXIT = 76


def _proc_root() -> Path:
    """Linux /proc as this Python sees it: through Wine's Z: drive under the Windows Python, else directly."""
    return Path("Z:/proc") if sys.platform == "win32" else Path("/proc")


def terminals_in_bridge_cgroup(proc_root: Path) -> int | None:
    """How many terminal64.exe processes run inside a goldbot-bridge@ unit's cgroup, i.e. launched by the bridge's own
    initialize(path=...) rather than by goldbot-mt5-terminal@ (the terminal died between the unit's wait and the
    attach). None when /proc cannot be listed (a Windows VPS, or Wine not mapping it): the caller cannot tell."""
    try:
        pids = [p for p in proc_root.iterdir() if p.name.isdigit()]
    except OSError:
        return None
    n = 0
    for p in pids:
        try:
            if b"terminal64.exe" not in (p / "cmdline").read_bytes().lower():
                continue
            cgroup = (p / "cgroup").read_text(errors="replace")
        except OSError:                        # gone mid-scan, or not ours to read
            continue
        if "/goldbot-bridge@" in cgroup:
            n += 1
    return n


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
    # Attaches to the terminal its own unit started (goldbot-mt5-terminal@.service; the bridge unit waits for the
    # process first): initialize(path=..., timeout=60 s). A failed attach raises, and Restart=always tries again.
    broker = MT5Broker(terminal_path=acc.terminal_path, login=None, password=None, server=acc.server,
                       server_tz=acc.server_tz, symbol=acc.symbol, account_label=account_id)
    # If the terminal died between the unit's wait and the attach, initialize(path=...) launched one of its own, in
    # this unit's cgroup: never serve it. Exiting lets systemd stop the cgroup (that terminal with it).
    spawned = terminals_in_bridge_cgroup(_proc_root())
    if spawned:
        log.error("initialize launched %d MT5 terminal(s) under the bridge unit: exiting so systemd stops them", spawned)
        broker.shutdown()
        raise SystemExit(TERMINAL_SPAWNED_EXIT)
    if spawned is None:
        log.warning("could not list processes: a terminal launched by initialize would go unnoticed here")
    guarded = GuardedBroker(broker, magic_base=acc.magic_base,
                            verify=lambda info: accounts.verify_terminal_account(acc, info))
    # The registered-account check (login, server, demo vs real, hedging) gates every RPC instead of the start:
    # until the terminal is once seen `ok`, /health answers with the token (not_logged_in, wrong_account, ...) and
    # every call is refused, so a lost login shows as such rather than as a bridge that keeps restarting; after that,
    # every call is refused while the terminal is on another account. Orders are checked again on every send
    # (GuardedBroker.verify). The token is logged; the reason (it names the account) is not.
    log.info("MT5 terminal state at start: %s", guarded.terminal_state())
    from goldbot.ops.bridge_compat import repo_commit
    serve(guarded, host, int(port), token, verify_first=True,
          commit=repo_commit())           # read once: the code this process runs, reported on /health (BACKLOG 42)
