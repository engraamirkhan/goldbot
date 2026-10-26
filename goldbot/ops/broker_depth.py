"""How far back a broker's MT5 server serves bars, per timeframe: `python -m goldbot.ops.run broker-history-depth`.

Read-only. It calls only the bridge's existing `get_bars` (MT5 `copy_rates_from_pos(symbol, tf, 0, n)`), once per
timeframe, and prints the first and last bar received. Nothing is written anywhere.

Reading the result: `copy_rates_from_pos` returns at most `n` bars and at most what the terminal holds, which is
bounded by the terminal's "Max bars in chart" setting as well as by the broker's history. So
* bars < requested: the first bar is where this terminal's history starts for that timeframe (the broker's start, or
  the terminal's bar limit, whichever is earlier in bars: compare with the setting before calling it the broker's);
* bars == requested ("capped"): history goes back at least this far; ask again with a larger --n.

Load: every request is capped at MAX_BARS (a 1m frame of 500,000 bars is ~40 MB of JSON through the bridge, which
also serves the live engine). Run it in a quiet window (weekend, or with the engine flat), one account at a time.

  python -m goldbot.ops.run broker-history-depth <account_id> [--tf 1m,1h,1d] [--n 1m=100000] [--n 1d=20000]
"""
from __future__ import annotations

import argparse
from typing import Any, Protocol

import pandas as pd

from goldbot.base import FrozenRecord

MAX_BARS = 500_000
DEFAULT_N = {"1m": 100_000, "1h": 200_000, "1d": 20_000}


class _Bars(Protocol):
    def get_bars(self, symbol: str, tf: str, n: int) -> pd.DataFrame: ...


class DepthRow(FrozenRecord):
    tf: str
    requested: int
    bars: int
    first_utc: pd.Timestamp | None = None
    last_utc: pd.Timestamp | None = None
    capped: bool = False
    error: str = ""


def history_depth(broker: _Bars, symbol: str, requests: dict[str, int]) -> list[DepthRow]:
    """One get_bars call per timeframe (in `requests` order). A failing timeframe is reported in its row; the others
    still run. ValueError when a request exceeds MAX_BARS (checked before any call)."""
    too_big = {tf: n for tf, n in requests.items() if not 0 < n <= MAX_BARS}
    if too_big:
        raise ValueError(f"each request must be 1 .. at most {MAX_BARS:,} bars: {too_big}")
    rows = []
    for tf, n in requests.items():
        try:
            df = broker.get_bars(symbol, tf, n)
        except Exception as exc:    # report and go on: a timeout on 1m must not hide the daily answer
            rows.append(DepthRow(tf=tf, requested=n, bars=0, error=f"{type(exc).__name__}: {exc}"))
            continue
        ts = pd.DatetimeIndex(pd.to_datetime(df["ts_utc"], utc=True)) if len(df) else pd.DatetimeIndex([], tz="UTC")
        rows.append(DepthRow(tf=tf, requested=n, bars=len(ts), first_utc=ts.min() if len(ts) else None,
                             last_utc=ts.max() if len(ts) else None, capped=len(ts) >= n))
    return rows


def render(account_id: str, symbol: str, rows: list[DepthRow]) -> str:
    def t(x: pd.Timestamp | None) -> str:
        return "-" if x is None else f"{x:%Y-%m-%d %H:%M}"

    lines = ["| account | symbol | tf | bars | requested | first bar (UTC) | last bar (UTC) | reading |",
             "|---|---|---|---:|---:|---|---|---|"]
    for r in rows:
        reading = (f"error: {r.error}" if r.error else "at least this far (capped: ask more)" if r.capped
                   else "start of this terminal's history")
        lines.append(f"| {account_id} | {symbol} | {r.tf} | {r.bars} | {r.requested} | {t(r.first_utc)} | "
                     f"{t(r.last_utc)} | {reading} |")
    return "\n".join(lines)


# seams for tests (the keyring and the bridge are not reachable there)
def _load_account(account_id: str) -> Any:
    from goldbot.ops import accounts
    return accounts.load_accounts().get(account_id)


def _endpoint(account_id: str) -> tuple[str, str] | None:
    from goldbot.ops import accounts
    return accounts.bridge_endpoint(account_id)


def _remote_broker(url: str, token: str, account_id: str) -> _Bars:
    from goldbot.execution.bridge import RemoteBroker
    b = RemoteBroker(url, token, name=f"mt5-remote-{account_id}-depth", timeout_s=120.0)
    b.read_attempts = 1          # one paced request per timeframe; no retry storm against the live bridge
    return b


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run broker-history-depth",
                                 description="Earliest bar the broker's MT5 server serves per timeframe (read-only).")
    ap.add_argument("account")
    ap.add_argument("--symbol", default=None, help="default: the account's symbol (config/accounts.yaml)")
    ap.add_argument("--tf", default="1m,1h,1d", help="comma-separated timeframes (1m,5m,15m,1h,4h,1d,1w)")
    ap.add_argument("--n", action="append", default=[], metavar="TF=N", help=f"bars to request per timeframe "
                    f"(defaults {DEFAULT_N}, others 100000; at most {MAX_BARS:,})")
    a = ap.parse_args(argv)
    acc = _load_account(a.account)
    if acc is None:
        print(f"unknown account {a.account!r} (config/accounts.yaml)")
        return 2
    endpoint = _endpoint(acc.account_id)
    if endpoint is None:
        print(f"refused: no bridge endpoint for {acc.account_id} in the keyring (mt5-bridge-url/-token)")
        return 2
    overrides = {}
    for item in a.n:
        tf, _, n = item.partition("=")
        overrides[tf.strip()] = int(n)
    tfs = [t.strip() for t in a.tf.split(",") if t.strip()]
    requests = {tf: overrides.get(tf, DEFAULT_N.get(tf, 100_000)) for tf in tfs}
    symbol = a.symbol or acc.symbol
    rows = history_depth(_remote_broker(endpoint[0], endpoint[1], acc.account_id), symbol, requests)
    print(render(acc.account_id, symbol, rows))
    return 1 if any(r.error for r in rows) else 0
