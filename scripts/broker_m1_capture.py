"""Broker MT5 M1 capture for the D-1 fallback (ii) forward window (ADR 0105): the traded broker's own M1 from 2026-10-01
copied into the git-ignored raw/ before the terminal's history rolls off. Read-only end to end.

No scheduled job of its own (PD ruling 2026-11-26): it runs as the first step of each monthly grade. The capture
record is the brain store: the broker's M1 is captured daily on the brain by the scheduler's `feed_reconcile` (MT5 copy_rates_range
through the read-only bridge into the store table `bars_1m_broker`; holes back-filled with `m1-backfill`). This script
copies that table, per account, into raw/broker_m1/<account>/<account>_m1_<YYYY-MM>.parquet with a sha256 per file,
and logs the gaps (missing minutes inside a trading session). No order, no engine or bridge restart, no trading config:
the remote side only reads the store.

Hosts and logins never enter the repo: the remote command comes from the environment variable GOLDBOT_BRAIN_SSH (for
example the ssh invocation of the operator's config), and accounts are the config ids of config/accounts.yaml.

    GOLDBOT_BRAIN_SSH="ssh -F <config> <alias>" uv run python scripts/broker_m1_capture.py icm-demo vantage-demo
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.data import holdout_guard  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
START = "2026-10-01"
REMOTE = """
import sys, pandas as pd
from goldbot.config import load_settings
from goldbot.data.store import Store
st = Store(load_settings().data_root)
b = st.read("bars_1m_broker", source=sys.argv[1], symbol=None, start=pd.Timestamp(sys.argv[2], tz="UTC"))
import io
buf = io.BytesIO()
b.to_parquet(buf, index=False)
sys.stdout.buffer.write(buf.getvalue())
sys.stdout.buffer.flush()
print(f"rows {len(b)}", file=sys.stderr)
"""
REMOTE_WRAP = "sudo -u goldbot -H bash -c 'cd /opt/goldbot && .venv/bin/python - {acc} {start}'"


def pull(ssh: list[str], account: str, start: str) -> pd.DataFrame:
    cmd = [*ssh, REMOTE_WRAP.format(acc=shlex.quote(account), start=shlex.quote(start))]
    r = subprocess.run(cmd, input=REMOTE.encode(), capture_output=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(f"{account}: remote read failed: {r.stderr.decode()[-300:]}")
    b = pd.read_parquet(io.BytesIO(r.stdout))
    holdout_guard.refuse(b, what=f"broker M1 capture {account}")    # forward minutes only: a holdout row is refused
    return b


def gaps(ts: pd.Series) -> list[dict[str, object]]:
    """Holes longer than 1 min between consecutive minutes, except the daily break and the weekend: a hole is logged
    when both ends lie on a weekday and it is shorter than 20 h (a weekend or holiday closure is longer)."""
    t = pd.DatetimeIndex(pd.to_datetime(ts, utc=True)).sort_values()
    d = t[1:] - t[:-1]
    out = []
    for a, b, g in zip(t[:-1], t[1:], d, strict=True):
        if g > pd.Timedelta(minutes=1) and g < pd.Timedelta(hours=20):
            out.append({"from": str(a), "to": str(b), "missing_min": int(g / pd.Timedelta(minutes=1)) - 1})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("accounts", nargs="+")
    ap.add_argument("--start", default=START)
    ap.add_argument("--raw", type=Path, default=ROOT / "raw")
    a = ap.parse_args()
    ssh_env = os.environ.get("GOLDBOT_BRAIN_SSH")
    if not ssh_env:
        print("refused: set GOLDBOT_BRAIN_SSH (the remote command lives outside the repo)")
        return 2
    ssh = shlex.split(ssh_env)
    base = a.raw / "broker_m1"
    report: dict[str, object] = {"captured_utc": dt.datetime.now(dt.UTC).isoformat(), "start": a.start, "accounts": {}}
    for acc in a.accounts:
        b = pull(ssh, acc, a.start)
        if b.empty:
            report["accounts"][acc] = {"rows": 0}          # type: ignore[index]
            print(f"{acc}: 0 rows", flush=True)
            continue
        b["ts_utc"] = pd.to_datetime(b["ts_utc"], utc=True)
        b = b.drop_duplicates("ts_utc").sort_values("ts_utc").reset_index(drop=True)
        month = b["ts_utc"].dt.strftime("%Y-%m")
        files = {}
        for m, g in b.groupby(month):
            p = base / acc / f"{acc}_m1_{m}.parquet"
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".part")
            g.to_parquet(tmp, index=False)
            os.replace(tmp, p)
            files[p.name] = {"rows": len(g), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
        gl = gaps(b["ts_utc"])
        (base / acc / "gaps.json").write_text(json.dumps(gl, indent=1))
        report["accounts"][acc] = {"rows": len(b), "first": str(b["ts_utc"].iloc[0]),  # type: ignore[index]
                                   "last": str(b["ts_utc"].iloc[-1]), "files": files, "gaps": len(gl),
                                   "missing_min": sum(int(str(x["missing_min"])) for x in gl)}
        print(f"{acc}: {len(b)} rows {b['ts_utc'].iloc[0]} .. {b['ts_utc'].iloc[-1]}, {len(gl)} gaps", flush=True)
    sums = sorted(base.rglob("*.parquet"))
    (base / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  "
                                             f"{p.relative_to(base).as_posix()}\n" for p in sums))
    with (base / "CAPTURES.jsonl").open("a") as fh:
        fh.write(json.dumps(report) + "\n")
    print(json.dumps(report)[:2000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
