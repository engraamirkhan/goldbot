"""One-off data repair, brain 2026-10-26: quarantine the icm feed rows stamped one hour ahead (wrong server clock).

Evidence (principal data engineer, 2026-10-26): from the Sunday reopen until the icm MT5 bridges were switched to
server_tz NY+7 at about 06:24 UTC, they converted MT5 times with Europe/Athens (UTC+2) while the broker ran UTC+3 (the
US-EU gap week, ADR 0005). Every tick part file the icm-demo and icm-probe engines wrote from 22:02 Sunday to 06:16
Monday holds ticks stamped 3,599-3,600 s after the file's own write time, which no live tick can be; files written
from 06:25 on are normal (stamps 0.1-0.4 s before the write). The engines' minutes (`bars_1m_live`, source icm) in the
span were built from those ticks, and from 06:24 to 07:16 two price streams $5-7 apart were interleaved.

What it does (dry run by default; --apply writes):
  1. backs up the affected partitions (ticks icm-demo/icm-probe, bars_1m_live icm, dq_events) once, before any write;
  2. moves every tick row stamped more than 30 minutes after its part file was written, between 2026-10-25 21:00 and
     2026-10-26 08:00, from `ticks` to `ticks_quarantine` (goldbot/data/quarantine.py: never edited, never deleted);
  3. moves the `bars_1m_live` (source icm) minutes of the span to `bars_quarantine`;
  4. stores the corrected minutes for the span from the broker's own M1 (one get_bars call through the icm-demo bridge,
     which must report server_tz NY+7), flagged `warning:corrected_broker_m1`, with available_utc = now (they were not
     knowable before; never backdated). Corrected ticks are NOT written: a complete broker tick history in the engine's
     sampled `ticks` table would bias the cost tables;
  5. records the span in dq_events (check quarantine_span) for icm-demo, icm-probe and icm, so D12 reports it;
  6. prints the exploration_trades and cost_probes whose entry fell in the span, and those after it until now (their
     decisions read windows containing the span), as ids, arm/agent and account only (no R, no P&L: blinded for the
     quant reviewer's ruling). Nothing is excluded from evidence here.
Idempotent: a re-run moves nothing more, writes no second span, and skips corrected minutes already present.
Afterwards restart goldbot-engine@icm-demo and @icm-probe so their warm start reads the corrected minutes.

    sudo -n -u goldbot env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring \\
        /opt/goldbot/.venv/bin/python scripts/quarantine_tz_ticks_2026_10_26.py [--apply]     (cwd /opt/goldbot)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data import quarantine as qr
from goldbot.data.crossfeed import broker_m1_bars
from goldbot.data.store import Store

REASON = "tz_mislabel_athens_vs_ny7_2026_10_26"
TICK_SOURCES = ("icm-demo", "icm-probe")
BARS_SOURCE = "icm"                      # bars_1m_live source = broker name (engine/history.py)
SYMBOL = "XAUUSD"
LO, HI = pd.Timestamp("2026-10-25 21:00", tz="UTC"), pd.Timestamp("2026-10-26 08:00", tz="UTC")
AHEAD = pd.Timedelta(minutes=30)
BRIDGE_ACCOUNT = "icm-demo"


def ahead(path: Path, rows: pd.DataFrame) -> pd.Series:
    """Rows in [LO, HI) stamped more than AHEAD after their part file was written."""
    ts = pd.to_datetime(rows["ts_utc"], utc=True)
    written = pd.Timestamp(os.path.getmtime(path), unit="s", tz="UTC")
    return (ts >= LO) & (ts < HI) & (ts > written + AHEAD)


def stamped_range(store: Store) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """[first, last] stamp of the ahead rows still in `ticks` or already in `ticks_quarantine`."""
    lo: list[pd.Timestamp] = []
    hi: list[pd.Timestamp] = []
    for src in TICK_SOURCES:
        root = store.table_dir("ticks") / f"source={src}" / f"symbol={SYMBOL}"
        for f in root.glob("year=2026/month=10/*.parquet"):
            t = pd.read_parquet(f, columns=["ts_utc"])
            sel = t[ahead(f, t).to_numpy()]
            if len(sel):
                lo.append(pd.Timestamp(sel["ts_utc"].min()))
                hi.append(pd.Timestamp(sel["ts_utc"].max()))
        q = store.read("ticks_quarantine", source=src, symbol=SYMBOL, start=LO, end=HI)
        if not q.empty and "quarantine_reason" in q.columns:
            q = q[q["quarantine_reason"] == REASON]
            if len(q):
                lo.append(pd.Timestamp(q["ts_utc"].min()))
                hi.append(pd.Timestamp(q["ts_utc"].max()))
    if not lo:
        return None
    return pd.Timestamp(min(lo)).tz_convert("UTC"), pd.Timestamp(max(hi)).tz_convert("UTC")


def span_of(first: pd.Timestamp, last: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The true-time span the bad rows describe (stamp - 1 h) joined to the stamps they occupy, whole minutes."""
    return (first - pd.Timedelta(hours=1)).floor("min"), last.floor("min") + pd.Timedelta(minutes=1)


def backup(store: Store, dest: Path) -> list[str]:
    if dest.exists():
        return [f"{dest} exists: backup already taken"]
    done = []
    for table, src in [("ticks", s) for s in TICK_SOURCES] + [("bars_1m_live", BARS_SOURCE)] + \
                      [("dq_events", s) for s in (*TICK_SOURCES, BARS_SOURCE)]:
        part = store.table_dir(table) / f"source={src}" / f"symbol={SYMBOL}" / "year=2026" / "month=10"
        if part.exists():
            shutil.copytree(part, dest / table / f"source={src}", ignore=shutil.ignore_patterns(".lock", "*.tmp"))
            done.append(f"{table}/source={src}")
    return done


def corrected_minutes(span: tuple[pd.Timestamp, pd.Timestamp], now: pd.Timestamp) -> pd.DataFrame:
    """The broker's own M1 for the span through the icm-demo bridge (which must convert with NY+7)."""
    from goldbot.execution.bridge import bridge_server_tz
    from goldbot.ops import accounts
    from goldbot.ops.services.scheduler import _job_broker
    acc = {a.account_id: a for a in accounts.enabled_accounts()}[BRIDGE_ACCOUNT]
    ep = accounts.bridge_endpoint(BRIDGE_ACCOUNT)
    tz = bridge_server_tz(ep[0]) if ep else None
    if tz != "NY+7" or acc.server_tz != "NY+7":
        raise SystemExit(f"refused: the {BRIDGE_ACCOUNT} bridge converts with {tz!r}, accounts.yaml says "
                         f"{acc.server_tz!r}; both must be NY+7")
    need = int((now - span[0]).total_seconds() // 60) + 60
    raw = _job_broker(acc).get_bars(SYMBOL, "1m", min(need, 5000))
    m1 = broker_m1_bars(raw)
    m1 = m1[(m1["ts_utc"] >= span[0]) & (m1["ts_utc"] < span[1])].reset_index(drop=True)
    return m1.assign(dq_flag="warning:corrected_broker_m1", available_utc=now, correction_of=REASON)


def evidence(state_dir: Path, span: tuple[pd.Timestamp, pd.Timestamp], now: pd.Timestamp) -> dict[str, Any]:
    """Ids, arm/agent and account of exploration trades and cost probes entered in the span and after it (blinded)."""
    con = sqlite3.connect(f"file:{state_dir / 'aux.db'}?mode=ro", uri=True)
    a, b, z = (int(t.value) for t in (span[0], span[1], now))
    out: dict[str, Any] = {"span": [span[0].isoformat(), span[1].isoformat()], "until": now.isoformat()}
    for table, who in (("exploration_trades", "arm_id"), ("cost_probes", "agent_id")):
        for label, lo, hi in (("in_span", a, b), ("after_span_lookback", b, z)):
            rows = con.execute(f"SELECT client_order_id, {who}, account_id, ts_ns FROM {table} "   # noqa: S608
                               "WHERE ts_ns >= ? AND ts_ns < ? AND account_id IN (?, ?) ORDER BY ts_ns",
                               (lo, hi, *TICK_SOURCES)).fetchall()
            out[f"{table}.{label}"] = [{"client_order_id": r[0], who: r[1], "account_id": r[2],
                                        "entry_utc": pd.Timestamp(r[3], unit="ns", tz="UTC").isoformat()} for r in rows]
    con.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write (default: dry run)")
    ap.add_argument("--data-root", default=None, help="default: settings.data_root")
    ap.add_argument("--state-dir", default="state")
    ap.add_argument("--backup-dir", default="backups/quarantine-tz-2026-10-26")
    a = ap.parse_args()
    from goldbot.config import load_settings
    store = Store(a.data_root or load_settings().data_root)
    now = pd.Timestamp.now("UTC")
    rng = stamped_range(store)
    if rng is None:
        print("no ahead-stamped tick rows found and none quarantined: nothing to do")
        return 0
    span = span_of(*rng)
    report: dict[str, Any] = {"mode": "apply" if a.apply else "dry-run", "stamped": [t.isoformat() for t in rng],
                              "span": [t.isoformat() for t in span]}
    if a.apply:
        report["backup"] = backup(store, Path(a.backup_dir))
    for src in TICK_SOURCES:
        report[f"ticks.{src}"] = qr.move_rows(store, "ticks", source=src, symbol=SYMBOL, select=ahead, reason=REASON,
                                              now=now, apply=a.apply)

    def in_span(_: Path, rows: pd.DataFrame) -> pd.Series:
        ts = pd.to_datetime(rows["ts_utc"], utc=True)
        fixed = rows["correction_of"].eq(REASON) if "correction_of" in rows.columns else False
        return (ts >= span[0]) & (ts < span[1]) & ~fixed
    report[f"bars_1m_live.{BARS_SOURCE}"] = qr.move_rows(store, "bars_1m_live", source=BARS_SOURCE, symbol=SYMBOL,
                                                         select=in_span, reason=REASON, now=now, apply=a.apply)
    have = store.read("bars_1m_live", source=BARS_SOURCE, symbol=SYMBOL, start=span[0], end=span[1])
    if have.empty or "correction_of" not in have.columns or not have["correction_of"].eq(REASON).any():
        fix = corrected_minutes(span, now)
        report["corrected_minutes"] = len(fix)
        if a.apply and not fix.empty:
            store.append("bars_1m_live", fix, source=BARS_SOURCE, symbol=SYMBOL)
    else:
        report["corrected_minutes"] = "already stored"
    detail = (f"stamps {rng[0].isoformat()}..{rng[1].isoformat()} were 1 h ahead (bridge Europe/Athens vs broker NY+7); "
              "rows in ticks_quarantine/bars_quarantine, corrected minutes from broker M1 in bars_1m_live")
    report["spans_recorded"] = [src for src in (*TICK_SOURCES, BARS_SOURCE) if a.apply and qr.record_span(
        store, source=src, symbol=SYMBOL, start=span[0], end=span[1], reason=REASON, detail=detail, now=now)]
    report["evidence_for_quant_review"] = evidence(Path(a.state_dir), span, now)
    print(json.dumps(report, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
