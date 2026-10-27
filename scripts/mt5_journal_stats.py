"""Order-path capacity from an MT5 terminal's own journal (volume plan 2026-10-26 section 5, SRE gate), read-only.

The terminal writes one line per trade request and per result to `<terminal>/logs/YYYYMMDD.log` (UTF-16LE):
    `market buy 0.01 XAUUSD sl: .. tp: ..`            an open the bridge sent (the stop and target ride on it)
    `market sell 0.01 XAUUSD, close #N buy ..`        a close the bridge sent (time exit, Friday cut, manual)
    `order #N buy 0.01 / 0.01 XAUUSD at P done in X ms`  the result: the terminal-to-server execution time
    `failed market .. [reason]`                       a refusal; `[Too many requests]` is retcode 10024
    `deal #N .. done (based on order #N)`             every fill, the server's own stop/target exits included
    `authorized on <server> through <access point> (ping: X ms ..)`   a (re)connect
Per day it reports the requests (opens, closes), their execution time p50/p95/max, the busiest minute and 10 s,
the requests in the first minute of each quarter hour (the :00/:15/:30/:45 burst), the server-side exits (deals
with no request: no bridge load), refusals by reason with the 10024 count, and reconnects with their ping.

The time is the terminal's ("done in": order sent by the terminal to the result back). The engine's bridge round
trip adds the HTTP call and the Wine/Python bridge on top; it is in aux.db `exploration_trades.latency_ms`.
The account login in the journal is never printed.

Usage (from the Mac; the box converts, nothing is written there):
  ssh -F ~/.ssh/goldbot.conf -o BatchMode=yes goldbot-mt5-probe 'D=/home/mt5/.wine/drive_c/MT5/ICMarkets/logs;
    for d in 26 27; do echo "#FILE 202610$d"; sudo -n -u mt5 cat "$D/202610$d.log" | iconv -f UTF-16LE -t UTF-8;
    done' > ~/tmp/probe.log
  uv run scripts/mt5_journal_stats.py ~/tmp/probe.log [--json out.json]
A raw UTF-16 `YYYYMMDD.log` is read as is (its date from the file name).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

TOO_MANY = re.compile(r"too many requests|10024", re.I)
REQ_OPEN = re.compile(r"^'\d+': market (buy|sell) [\d.]+ \S+ sl: ")
REQ_CLOSE = re.compile(r"^'\d+': market (buy|sell) [\d.]+ \S+, close #\d+")
DONE = re.compile(r"^'\d+': order #\d+ .* done in ([\d.]+) ms")
FAILED = re.compile(r"^'\d+': failed .*?\[([^\]]+)\]")
DEAL = re.compile(r"^'\d+': deal #\d+ .* done \(based on order #(\d+)\)")
ORDER_NO = re.compile(r"^'\d+': order #(\d+) ")
AUTH = re.compile(r"authorized on .*\(ping: ([\d.]+) ms")
AUTH_FAIL = re.compile(r"authorization on .* failed \(([^)]+)\)")
TIME = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d{3})$")


@dataclass
class Day:
    day: str
    opens: int = 0
    closes: int = 0
    done_ms: list[float] = field(default_factory=list)
    done_s: list[float] = field(default_factory=list)       # seconds of the day of each result
    req_s: list[float] = field(default_factory=list)        # seconds of the day of each request
    requested: set[str] = field(default_factory=set)        # order numbers with a request result
    deal_orders: list[str] = field(default_factory=list)
    refusals: Counter[str] = field(default_factory=Counter)
    reconnects: int = 0
    pings: list[float] = field(default_factory=list)
    auth_failures: Counter[str] = field(default_factory=Counter)

    def summary(self) -> dict[str, Any]:
        ms = np.asarray(self.done_ms, dtype=float)
        p50, p95, mx = (np.percentile(ms, 50), np.percentile(ms, 95), ms.max()) if ms.size else (None, None, None)
        req = np.asarray(sorted(self.req_s))
        per_min = Counter((req // 60).astype(int)) if req.size else Counter()
        per_10s = Counter((req // 10).astype(int)) if req.size else Counter()
        quarter = Counter(int(t // 900) for t in req if t % 900 < 60)
        server_exits = sum(1 for o in self.deal_orders if o not in self.requested)
        too_many = sum(n for why, n in self.refusals.items() if TOO_MANY.search(why))
        return {
            "day": self.day, "requests": self.opens + self.closes, "opens": self.opens, "closes": self.closes,
            "results": len(self.done_ms), "deals": len(self.deal_orders), "server_exits": server_exits,
            "done_ms_p50": _r(p50), "done_ms_p95": _r(p95), "done_ms_max": _r(mx),
            "peak_per_min": max(per_min.values(), default=0), "peak_per_10s": max(per_10s.values(), default=0),
            "quarter_first_min_max": max(quarter.values(), default=0),
            "quarter_first_min_share": round(sum(quarter.values()) / req.size, 3) if req.size else None,
            "refusals": dict(self.refusals), "too_many_requests_10024": too_many,
            "reconnects": self.reconnects, "ping_ms_median": _r(np.median(self.pings)) if self.pings else None,
            "auth_failures": dict(self.auth_failures),
        }


def _r(x: Any) -> float | None:
    return None if x is None or not np.isfinite(x) else round(float(x), 1)


def _secs(stamp: str) -> float | None:
    m = TIME.match(stamp)
    if m is None:
        return None
    h, mi, s, ms = (int(g) for g in m.groups())
    return h * 3600 + mi * 60 + s + ms / 1000


def parse(lines: list[str], day: str = "?") -> dict[str, Day]:
    """Journal lines -> per-day counters. `#FILE <name>` lines switch the day (the name's YYYYMMDD)."""
    days: dict[str, Day] = {}
    cur = days.setdefault(day, Day(day))
    for raw in lines:
        line = raw.rstrip("\r\n").lstrip("﻿")
        if line.startswith("#FILE"):
            name = line.split(maxsplit=1)[1] if " " in line else "?"
            d = re.search(r"(\d{8})", name)
            key = d.group(1) if d else name
            cur = days.setdefault(key, Day(key))
            continue
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        t, src, msg = _secs(parts[2]), parts[3], "\t".join(parts[4:])
        if src == "Network":
            if (m := AUTH_FAIL.search(msg)) is not None:
                cur.auth_failures[m.group(1)] += 1
            elif "authorized on" in msg:
                cur.reconnects += 1
                if (m := AUTH.search(msg)) is not None and m.group(1):
                    cur.pings.append(float(m.group(1)))
            continue
        if src != "Trades":
            continue
        if REQ_OPEN.search(msg):
            cur.opens += 1
            if t is not None:
                cur.req_s.append(t)
        elif REQ_CLOSE.search(msg):
            cur.closes += 1
            if t is not None:
                cur.req_s.append(t)
        elif (m := DONE.search(msg)) is not None:
            cur.done_ms.append(float(m.group(1)))
            if t is not None:
                cur.done_s.append(t)
            if (o := ORDER_NO.search(msg)) is not None:
                cur.requested.add(o.group(1))
        elif (m := FAILED.search(msg)) is not None:
            cur.refusals[m.group(1)] += 1
        elif (m := DEAL.search(msg)) is not None:
            cur.deal_orders.append(m.group(1))
    return days


def read_lines(path: Path) -> list[str]:
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or (len(raw) > 1 and raw[1:2] == b"\x00"):
        text = raw.decode("utf-16")
        return [f"#FILE {path.name}", *text.splitlines()]
    return raw.decode("utf-8", errors="replace").splitlines()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("logs", type=Path, nargs="+")
    ap.add_argument("--json", type=Path)
    a = ap.parse_args(argv)
    rows: list[dict[str, Any]] = []
    for p in a.logs:                                    # one terminal per file: never merged across boxes
        days = parse(read_lines(p.expanduser()))
        rows += [{"source": p.stem, **days[k].summary()} for k in sorted(days) if days[k].opens or
                 days[k].closes or days[k].deal_orders or days[k].reconnects]
    for r in rows:
        print(json.dumps(r))
    if a.json:
        a.json.write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
