"""Bars + study plots over one chart session on one socket.

Sequence (one chart session per symbol x timeframe, deleted afterwards):
  chart_create_session [cs, ""]
  resolve_symbol       [cs, "sds_sym_1", '={"symbol":..,"adjustment":"splits","session":"regular"}']
  create_series        [cs, "sds_1", "s1", "sds_sym_1", tf, n, ""]       -> timescale_update ... series_completed
  request_more_data    [cs, "sds_1", n]   (repeat until no growth)      -> timescale_update ... series_completed
  create_study         [cs, "st<k>", "st1", "sds_1", "Script@tv-scripting-101!", inputs] -> study_loaded,
                       timescale_update/du {st<k>: {st: [{i, v: [time, plot_0, plot_1, ...]}]}} ... study_completed
  chart_delete_session [cs]
Bar times are the bar OPEN in epoch seconds (float). Values >= 1e100 are TradingView's NaN.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .client import TvError, TvSocket
from .discovery import StudyMeta, create_study_inputs

TF_SECONDS = {"1": 60, "5": 300, "15": 900, "30": 1800, "60": 3600, "240": 14400, "1D": 86400, "D": 86400,
              "1W": 604800, "W": 604800}
NAN_SENTINEL = 1e99
ERRORS = {"symbol_error", "series_error", "study_error", "critical_error", "protocol_error"}


@dataclass
class StudyResult:
    meta: StudyMeta
    study_id: str
    values: dict[float, list[float | None]] = field(default_factory=dict)   # bar open -> [plot_0, plot_1, ...]
    loaded: dict[str, Any] = field(default_factory=dict)                     # study_loaded payload (if any)
    error: str = ""


@dataclass
class SeriesResult:
    symbol: str
    tf: str
    resolved: dict[str, Any] = field(default_factory=dict)
    bars: dict[float, list[float]] = field(default_factory=dict)   # bar open -> [o, h, l, c, v]
    requests: int = 0
    data_end: bool = False
    error: str = ""
    studies: list[StudyResult] = field(default_factory=list)
    received_at: float = 0.0                                        # wall clock (s) when the pull finished


def _clean(v: Any) -> float | None:
    if v is None:
        return None
    f = float(v)
    return None if abs(f) >= NAN_SENTINEL else f


class ChartPull:
    def __init__(self, sock: TvSocket, cs: str) -> None:
        self.sock = sock
        self.cs = cs
        self.res: SeriesResult | None = None

    # -------------------------------------------------------------- dispatch
    def _absorb(self, p: dict[str, Any]) -> str:
        """Fold one packet into the result; return the event name relevant to waiting."""
        m = p["m"]
        params = p.get("p", [])
        assert self.res is not None
        if m in ERRORS:
            if not params or params[0] == self.cs or m in ("critical_error", "protocol_error"):
                return f"error:{m}:{params[1:]!s:.300}"
            return ""
        if not params or params[0] != self.cs:
            return ""
        if m == "symbol_resolved":
            self.res.resolved = params[2]
        elif m in ("timescale_update", "du"):
            for key, blob in (params[1] or {}).items():
                if not isinstance(blob, dict):
                    continue
                if key == "sds_1":
                    for row in blob.get("s", []):
                        v = row["v"]
                        self.res.bars[float(v[0])] = [float(x) for x in v[1:6]] + ([0.0] if len(v) < 6 else [])
                else:
                    st = next((s for s in self.res.studies if s.study_id == key), None)
                    if st is not None:
                        for row in blob.get("st", []):
                            v = row["v"]
                            st.values[float(v[0])] = [_clean(x) for x in v[1:]]
        elif m == "study_loaded":
            st = next((s for s in self.res.studies if s.study_id == params[1]), None)
            if st is not None:
                st.loaded = {"params": params[2:]}
        elif m == "series_completed":
            return "series_completed"
        elif m == "study_completed":
            return f"study_completed:{params[1]}"
        return ""

    def _wait(self, want: str, idle_s: float = 20.0) -> str:
        for p in self.sock.packets(idle_s):
            ev = self._absorb(p)
            if ev.startswith("error:"):
                return ev
            if ev == want:
                return ev
        return "timeout"

    # -------------------------------------------------------------- steps
    def series(self, symbol: str, tf: str, first: int = 5000, more: int = 5000, cap: int = 100_000) -> SeriesResult:
        self.res = SeriesResult(symbol=symbol, tf=tf)
        self.sock.send("chart_create_session", [self.cs, ""])
        self.sock.send("resolve_symbol", [self.cs, "sds_sym_1",
                                          '={"symbol":"%s","adjustment":"splits","session":"regular"}' % symbol])
        self.sock.send("create_series", [self.cs, "sds_1", "s1", "sds_sym_1", tf, first, ""])
        self.res.requests = 1
        ev = self._wait("series_completed")
        if ev != "series_completed":
            self.res.error = ev
            return self.res
        while len(self.res.bars) < cap:
            before = len(self.res.bars)
            self.sock.send("request_more_data", [self.cs, "sds_1", more])
            self.res.requests += 1
            ev = self._wait("series_completed", idle_s=15.0)
            if len(self.res.bars) <= before:          # no growth: the plan/feed limit or the start of data
                self.res.data_end = True
                break
            if ev.startswith("error:"):
                self.res.error = ev
                break
        self.res.received_at = time.time()
        return self.res

    def study(self, meta: StudyMeta, overrides: dict[str, Any] | None = None) -> StudyResult:
        assert self.res is not None
        sid = f"st{len(self.res.studies) + 1}"
        sr = StudyResult(meta=meta, study_id=sid)
        self.res.studies.append(sr)
        kind = "StrategyScript@tv-scripting-101!" if meta.is_strategy else "Script@tv-scripting-101!"
        self.sock.send("create_study", [self.cs, sid, "st1", "sds_1", kind, create_study_inputs(meta, overrides)])
        ev = self._wait(f"study_completed:{sid}", idle_s=30.0)
        if ev != f"study_completed:{sid}":
            sr.error = ev
        self.sock.send("remove_study", [self.cs, sid])
        return sr

    def close(self) -> None:
        self.sock.send("chart_delete_session", [self.cs])
        if self.res is not None:
            self.res.received_at = self.res.received_at or time.time()


def pull(sock: TvSocket, cs: str, symbol: str, tf: str, studies: list[tuple[StudyMeta, dict[str, Any] | None]],
         first: int = 5000, more: int = 5000, cap: int = 100_000) -> SeriesResult:
    cp = ChartPull(sock, cs)
    try:
        res = cp.series(symbol, tf, first=first, more=more, cap=cap)
        if not res.error:
            for meta, overrides in studies:
                cp.study(meta, overrides)
        return res
    finally:
        cp.close()


def resolve_only(sock: TvSocket, cs: str, symbol: str) -> tuple[bool, str]:
    """Does the symbol exist for this session? (resolve_symbol, no series.)"""
    sock.send("chart_create_session", [cs, ""])
    sock.send("resolve_symbol", [cs, "sds_sym_1", '={"symbol":"%s","adjustment":"splits"}' % symbol])
    out = (False, "timeout")
    for p in sock.packets(10):
        if p["m"] == "symbol_resolved" and p["p"][0] == cs:
            r = p["p"][2]
            out = (True, f"{r.get('full_name') or r.get('name')} | {r.get('description', '')} | tz={r.get('timezone')}")
            break
        if p["m"] == "symbol_error" and p["p"][0] == cs:
            out = (False, str(p["p"][2:])[:200])
            break
        if p["m"] in ("critical_error", "protocol_error"):
            raise TvError(str(p["p"])[:300])
    sock.send("chart_delete_session", [cs])
    return out
