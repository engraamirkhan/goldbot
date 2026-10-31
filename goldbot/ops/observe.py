"""The observe lane (ADR 0063; owner directive 2026-10-31: XAUUSD247 is listed on no Vantage demo, so it is read through
the owner's LIVE Vantage account, with "NO live trading").

An account of `kind: observe_only` (config/accounts.yaml) is read, never traded. Its terminal is logged in with the MT5
investor (read-only) password, so the broker rejects any order by construction; on top of that its bridge serves the
read-only methods only (`observe_bridge`: the account's kind or the installer's GOLDBOT_BRIDGE_OBSERVE_ONLY=1, the
stricter wins), its terminal-side broker refuses every order call, the brain's client below is built
`observe_only=True` (every order, stop change and close refused before the network), and no engine, arm, allocator or
report ever loads the account (`accounts.enabled_accounts`, `services.engine.run_engine`).

`python -m goldbot.ops.run observe follow --account <id>` (goldbot-observe@<id>.service on the brain) records, per store
symbol in `observe_symbols` (resolved on the terminal by `resolve_symbol`):
  * closed M1 bars since the last one stored -> store `observe_bars_1m` (source = the account, symbol = the store
    symbol), `available_utc` = when the collector read them (point in time);
  * a quote sample (bid, ask, spread) every `tick_every_s`, every `weekend_tick_every_s` inside the weekend window
    (Friday 21:00 .. Sunday 21:00 UTC, when XAUUSD247 quotes and weekday gold is closed) -> store `observe_ticks`;
  * the symbol terms (contract size, sessions, swap, stops level, volumes) every `spec_every_s` -> one JSON line in
    state/observe/<account>/specs.jsonl whenever they changed.
Bars and quotes are buffered and written every `flush_every_s` (and at the first step and on stop), so the store's
month partitions are not rewritten every few seconds. Nothing here is ever published (ADR data-local-only).
state/observe_<account>.json is the lane's status (connected, last bar, spread) for health's `observe` check, which
shows the lane as "observe only, never trades" and only ever WARNs: observation never blocks trading.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import signal
import sys
import time
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import pandas as pd
from pydantic import Field

from goldbot.base import Record, UtcTimestamp, write_atomic

if TYPE_CHECKING:
    from goldbot.config import ObserveSettings
    from goldbot.data.store import Store
    from goldbot.ops.accounts import Account
    from goldbot.ops.checks._base import Check
    from goldbot.ops.health import HealthContext

log = logging.getLogger(__name__)

OBSERVE_ENV = "GOLDBOT_BRIDGE_OBSERVE_ONLY"   # the installer's drop-in on the bridge unit (=1): reads only
BARS_TABLE, TICKS_TABLE = "observe_bars_1m", "observe_ticks"
CONFIG_EXIT = 78                              # EX_CONFIG: the unit's RestartPreventExitStatus (no bridge stored yet)


def observe_bridge(acc: Account, environ: Mapping[str, str]) -> bool:
    """True when this account's bridge must serve reads only: the account is observe_only, OR its unit sets
    GOLDBOT_BRIDGE_OBSERVE_ONLY=1. Either alone suffices (the stricter wins): an edited yaml cannot re-open orders on a
    terminal the installer marked, and a missing drop-in cannot re-open them on an observe_only account."""
    return acc.is_observe_only or environ.get(OBSERVE_ENV, "").strip() == "1"


def resolve_symbol(names: Iterable[str], want: str) -> str | None:
    """The terminal's name for the store symbol `want`: the exact name, else the first (by name) that is `want`
    followed by a broker suffix starting with a non-alphanumeric character (XAUUSD -> XAUUSD+ or XAUUSD.r), never a
    longer instrument name (XAUUSD -> XAUUSD247 is another instrument). None when the terminal lists none."""
    names = list(names)
    if want in names:
        return want
    suffixed = sorted(n for n in names if n.startswith(want) and len(n) > len(want) and not n[len(want)].isalnum())
    return suffixed[0] if suffixed else None


def select_symbols(api: Any, broker: Any, wants: Iterable[str]) -> list[str]:  # pragma: no cover - MT5 package
    """On the MT5 box, at the observe bridge's start: add each observed symbol to Market Watch (symbol_select), so its
    ticks and bars answer. Returns the names selected; a symbol the terminal lacks is logged and skipped."""
    selected = []
    for want in wants:
        name = resolve_symbol(broker.symbols_get(f"{want}*"), want)
        if name is None or not api.symbol_select(name, True):
            log.warning("observe: %s is not on this terminal; not collected", want)
            continue
        selected.append(name)
    return selected


class SymbolObservation(Record):
    """One observed symbol in the lane's status."""
    broker_symbol: str | None = None          # the terminal's name (None: not on the terminal, or not resolved yet)
    last_bar_utc: UtcTimestamp | None = None  # open time of the last closed M1 bar read
    last_tick_utc: UtcTimestamp | None = None
    spread: float | None = None               # ask - bid of the last quote, in price units
    bars_read: int = 0                        # since this process started
    ticks_read: int = 0
    spec_utc: UtcTimestamp | None = None      # when the terms were last read
    error: str | None = None                  # the last failure for this symbol (type only), None after a success


class ObserveStatus(Record):
    """state/observe_<account>.json: what health's `observe` check reads. Never a login, server or balance."""
    account_id: str
    ts: UtcTimestamp
    observe_only: Literal[True] = True
    connected: bool = False                   # the last step reached the bridge without an error
    error: str | None = None
    symbols: dict[str, SymbolObservation] = Field(default_factory=dict)


def status_path(state_dir: Path, account_id: str) -> Path:
    return Path(state_dir) / f"observe_{account_id}.json"


def spec_path(state_dir: Path, account_id: str) -> Path:
    return Path(state_dir) / "observe" / account_id / "specs.jsonl"


class Collector:
    """One observe_only account's reads (see the module docstring). `broker` is read-only: symbols_get,
    get_bars_range, last_tick and symbol_info are the only calls made. `step(now)` never raises for a bridge fault: the
    fault is recorded in the status and the next step tries again."""

    def __init__(self, account_id: str, symbols: Iterable[str], broker: Any, store: Store, state_dir: Path,
                 cfg: ObserveSettings) -> None:
        self.account_id, self.symbols, self.broker = account_id, tuple(symbols), broker
        self.store, self.state_dir, self.cfg = store, Path(state_dir), cfg
        self._window = cfg.weekend_window
        self._due: dict[tuple[str, str], pd.Timestamp] = {}
        self._resolved: dict[str, str] = {}
        self._last_bar: dict[str, pd.Timestamp] = {}
        self._spec_hash: dict[str, str] = {}
        self._bars: dict[str, list[pd.DataFrame]] = {}
        self._ticks: dict[str, list[dict[str, Any]]] = {}
        self._next_flush: pd.Timestamp | None = None
        self.status = ObserveStatus(account_id=account_id, ts=pd.Timestamp(0, tz="UTC"),
                                    symbols={s: SymbolObservation() for s in self.symbols})

    # ------------------------------------------------------------------------------------------------ cadence
    def _tick_every(self, now: pd.Timestamp) -> float:
        return self.cfg.weekend_tick_every_s if self._window.inside(now) else self.cfg.tick_every_s

    def _is_due(self, kind: str, sym: str, now: pd.Timestamp, every_s: float) -> bool:
        due = self._due.get((kind, sym))
        if due is not None and now < due:
            return False
        self._due[(kind, sym)] = now + pd.Timedelta(seconds=every_s)
        return True

    def seconds_to_next(self, now: pd.Timestamp) -> float:
        """Seconds until the next read is due (at least 1)."""
        nxt = [t for t in self._due.values()] + ([self._next_flush] if self._next_flush is not None else [])
        if not nxt:
            return 1.0
        return max(1.0, (min(nxt) - now).total_seconds())

    # ------------------------------------------------------------------------------------------------ reads
    def _resolve(self, want: str) -> str | None:
        if want not in self._resolved:
            name = resolve_symbol(self.broker.symbols_get(f"{want}*"), want)
            if name is None:
                return None
            self._resolved[want] = name
        return self._resolved[want]

    def _stored_last_bar(self, want: str, now: pd.Timestamp) -> pd.Timestamp | None:
        start = now - pd.Timedelta(hours=self.cfg.backfill_hours)
        try:
            df = self.store.read(BARS_TABLE, source=self.account_id, symbol=want, start=start, columns=["ts_utc"])
        except Exception as exc:                        # noqa: BLE001 - an unreadable store: backfill again (deduped)
            log.warning("observe %s: stored bars of %s unreadable (%s)", self.account_id, want, type(exc).__name__)
            return None
        return None if df.empty else pd.Timestamp(df["ts_utc"].max())

    def _read_bars(self, want: str, name: str, now: pd.Timestamp, obs: SymbolObservation) -> None:
        last = self._last_bar.get(want) or self._stored_last_bar(want, now)
        start = (last + pd.Timedelta(minutes=1)) if last is not None else now - pd.Timedelta(hours=self.cfg.backfill_hours)
        df = self.broker.get_bars_range(name, "1m", start, now)
        if df is None or df.empty:
            return
        df = df[pd.to_datetime(df["ts_utc"], utc=True) + pd.Timedelta(minutes=1) <= now].copy()   # closed bars only
        if last is not None:
            df = df[pd.to_datetime(df["ts_utc"], utc=True) > last]
        if df.empty:
            return
        df["available_utc"] = now                       # point in time: when this collector saw them
        df["broker_symbol"] = name
        self._bars.setdefault(want, []).append(df)
        self._last_bar[want] = pd.Timestamp(df["ts_utc"].max())
        obs.last_bar_utc, obs.bars_read = self._last_bar[want], obs.bars_read + len(df)

    def _read_tick(self, want: str, name: str, now: pd.Timestamp, obs: SymbolObservation) -> None:
        t = self.broker.last_tick(name)
        spread = round(float(t.ask) - float(t.bid), 6)
        self._ticks.setdefault(want, []).append({"ts_utc": t.ts_utc, "bid": float(t.bid), "ask": float(t.ask),
                                                 "spread": spread, "sampled_utc": now, "broker_symbol": name})
        obs.last_tick_utc, obs.spread, obs.ticks_read = t.ts_utc, spread, obs.ticks_read + 1

    def _read_spec(self, want: str, name: str, now: pd.Timestamp, obs: SymbolObservation) -> None:
        info = self.broker.symbol_info(name).model_dump(mode="json")
        digest = hashlib.sha256(json.dumps(info, sort_keys=True).encode()).hexdigest()
        obs.spec_utc = now
        if self._spec_hash.get(want) == digest:
            return
        line = {"sampled_utc": now.isoformat(), "account_id": self.account_id, "store_symbol": want,
                "broker_symbol": name, **info}
        f = spec_path(self.state_dir, self.account_id)
        f.parent.mkdir(parents=True, exist_ok=True)
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
        self._spec_hash[want] = digest

    # ------------------------------------------------------------------------------------------------ the step
    def step(self, now: pd.Timestamp) -> ObserveStatus:
        """Every read that is due, then a flush when due, then the status file. Never raises for a bridge fault."""
        errors: list[str] = []
        reached = False
        for want in self.symbols:
            obs = self.status.symbols.setdefault(want, SymbolObservation())
            try:
                name = self._resolve(want)
                reached = True
                if name is None:
                    obs.broker_symbol, obs.error = None, f"{want} is not on this terminal"
                    continue
                obs.broker_symbol = name
                if self._is_due("spec", want, now, self.cfg.spec_every_s):
                    self._read_spec(want, name, now, obs)
                if self._is_due("bars", want, now, self.cfg.bars_every_s):
                    self._read_bars(want, name, now, obs)
                if self._is_due("tick", want, now, self._tick_every(now)):
                    self._read_tick(want, name, now, obs)
                obs.error = None
            except Exception as exc:                    # noqa: BLE001 - the loop outlives any read; logged by type
                obs.error = type(exc).__name__
                errors.append(f"{want}: {type(exc).__name__}")
                log.warning("observe %s: %s read failed (%s)", self.account_id, want, type(exc).__name__)
        if self._next_flush is None or now >= self._next_flush:
            self.flush()
            self._next_flush = now + pd.Timedelta(seconds=self.cfg.flush_every_s)
        self.status.ts = now
        self.status.connected = reached and not errors
        self.status.error = "; ".join(errors) or None
        write_atomic(status_path(self.state_dir, self.account_id), self.status.model_dump_json(indent=1),
                     durable=False)
        return self.status

    def flush(self) -> None:
        """Write the buffered bars (deduped by minute) and quotes (append-only) to the local store."""
        for want, frames in list(self._bars.items()):
            if frames:
                self.store.append(BARS_TABLE, pd.concat(frames, ignore_index=True), source=self.account_id,
                                  symbol=want)
            self._bars[want] = []
        for want, rows in list(self._ticks.items()):
            if rows:
                df = pd.DataFrame(rows).drop_duplicates(["ts_utc", "bid", "ask"])
                self.store.append(TICKS_TABLE, df, source=self.account_id, symbol=want, dedupe=False)
            self._ticks[want] = []


# ------------------------------------------------------------------------------------------------ health
def check_observe(ctx: HealthContext) -> Check:
    """ADR 0063: the observe lane on health, "observe only, never trades". Per observe_only account: not installed
    (no bridge URL stored) is OK; else WARN on a missing or silent status (the collector is down), a bridge it cannot
    reach, or a 24/7 symbol without a new bar for `bar_stale_s`. Never FAIL: observation never blocks trading."""
    from goldbot.base import load_record_json
    from goldbot.config import ObserveSettings
    from goldbot.ops.checks._base import Check, Status, _age, _worst
    cfg = ctx.settings.observe if ctx.settings is not None else ObserveSettings()
    parts: list[str] = []
    statuses: list[Status] = []
    for lane in ctx.observe_lanes:
        aid = lane.account_id
        if not lane.installed:
            parts.append(f"{aid}: observe only, never trades: not installed (no bridge stored)")
            continue
        f = status_path(ctx.state_dir, aid)
        try:
            st = load_record_json(ObserveStatus, f.read_text(encoding="utf-8"), source=f.name) if f.exists() else None
        except (ValueError, OSError) as exc:
            st = None
            parts.append(f"{aid}: status unreadable ({type(exc).__name__})")
        if st is None:
            statuses.append("warn")
            parts.append(f"{aid}: observe only, never trades: no status (goldbot-observe@{aid} not running?)")
            continue
        age = (ctx.now - st.ts).total_seconds()
        bits = []
        for sym, o in st.symbols.items():
            bar = "no bar yet" if o.last_bar_utc is None else f"last bar {_age((ctx.now - o.last_bar_utc).total_seconds() - 60)} old"
            spread = "" if o.spread is None else f", spread {o.spread:.2f}"
            bits.append(f"{sym} {bar}{spread}" if o.broker_symbol else f"{sym} not on the terminal")
        main = st.symbols.get(lane.symbol)
        if age > cfg.status_stale_s:
            statuses.append("warn")
            head = f"collector silent for {_age(age)}"
        elif not st.connected:
            statuses.append("warn")
            head = f"not connected ({st.error or 'unknown'})"
        elif main is None or main.last_bar_utc is None or \
                (ctx.now - main.last_bar_utc).total_seconds() > cfg.bar_stale_s + 60:
            statuses.append("warn")
            head = f"connected, but no {lane.symbol} bar for {cfg.bar_stale_s / 60:.0f} min"
        else:
            head = "connected"
        parts.append(f"{aid}: observe only, never trades: {head}; " + "; ".join(bits))
    return Check(name="observe", status=_worst(statuses), reason="; ".join(parts) or "no observe lane")


# ------------------------------------------------------------------------------------------------ CLI
def _client(account_id: str) -> Any:
    """The account's bridge client, built observe_only (every order call refused before the network), or None."""
    from goldbot.execution.bridge import RemoteBroker
    from goldbot.ops import accounts
    ep = accounts.bridge_endpoint(account_id)
    if ep is None:
        return None
    rb = RemoteBroker(ep[0], ep[1], name=f"observe-{account_id}", timeout_s=20.0, observe_only=True)
    rb.read_attempts = 1                    # paced by the loop: a failed read is tried at the next step
    return rb


def main(argv: list[str]) -> int:
    """`observe once|follow --account ID [--state DIR] [--data-root DIR]`. Exit 2: not an observe_only account;
    CONFIG_EXIT: no bridge stored for it (the unit does not restart on it)."""
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.ops import accounts
    ap = argparse.ArgumentParser(prog="run.py observe", description="Observe-only account collector (ADR 0063).")
    ap.add_argument("action", choices=("once", "follow"))
    ap.add_argument("--account", required=True)
    ap.add_argument("--state", default="state")
    ap.add_argument("--data-root", default=None, help="the Parquet store (default: settings data_root)")
    a = ap.parse_args(argv)
    acc = accounts.load_accounts(logins=False).get(a.account)
    if acc is None or not acc.is_observe_only:
        print(f"refused: {a.account} is not an observe_only account in config/accounts.yaml (ADR 0063)")
        return 2
    settings = load_settings()
    broker = _client(acc.account_id)
    if broker is None:
        print(f"no bridge stored for {acc.account_id}: mt5-bridge-url-{acc.account_id} and its token (RUNBOOK 0.3d)")
        return CONFIG_EXIT
    c = Collector(acc.account_id, acc.observe_symbols, broker, Store(a.data_root or settings.data_root),
                  Path(a.state), settings.observe)
    if a.action == "once":
        st = c.step(pd.Timestamp.now("UTC"))
        c.flush()
        print(st.model_dump_json(indent=1))
        return 0
    stop = {"now": False}

    def _stop(*_: Any) -> None:
        stop["now"] = True
    signal.signal(signal.SIGTERM, _stop)
    log.info("observe %s: collecting %s (read only)", acc.account_id, ", ".join(acc.observe_symbols))
    try:
        while not stop["now"]:
            now = pd.Timestamp.now("UTC")
            c.step(now)
            end = time.monotonic() + c.seconds_to_next(pd.Timestamp.now("UTC"))
            while not stop["now"] and time.monotonic() < end:
                time.sleep(min(1.0, max(end - time.monotonic(), 0.0)))
    finally:
        c.flush()                            # buffered bars and quotes are kept on a stop
    return 0


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    sys.exit(main(sys.argv[1:]))
