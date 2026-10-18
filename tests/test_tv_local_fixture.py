"""The recorded TradingView frames (tests/fixtures/tv) replay through the framing code and carry no secrets."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("tv_framing", REPO / "scripts" / "tv_local" / "framing.py")
assert _spec is not None and _spec.loader is not None
framing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(framing)

FIX = REPO / "tests" / "fixtures" / "tv" / "ws_unauth_oanda_xauusd_60_rsi.jsonl"


def _frames() -> list[dict[str, object]]:
    return [json.loads(line) for line in FIX.read_text().splitlines() if line.strip()]


def test_frames_split_and_reencode_exactly() -> None:
    methods: set[str] = set()
    for f in _frames():
        raw = str(f["frame"])
        pkts = framing.split_packets(raw)
        assert "".join(framing.encode(p) for p in pkts) == raw
        for p in pkts:
            if not framing.is_heartbeat(p):
                methods.add(framing.parse(p)["m"])
    assert {"__hello__", "symbol_resolved", "timescale_update", "series_completed", "study_error",
            "symbol_error"} <= methods


def test_bars_are_open_time_seconds_and_ordered() -> None:
    bars: dict[float, list[float]] = {}
    for f in _frames():
        if f["dir"] != "in":
            continue
        for p in framing.split_packets(str(f["frame"])):
            if framing.is_heartbeat(p):
                continue
            msg = framing.parse(p)
            if msg["m"] == "timescale_update":
                for row in msg["p"][1].get("sds_1", {}).get("s", []):
                    bars[float(row["v"][0])] = row["v"][1:]
    times = sorted(bars)
    assert len(times) >= 300
    assert 1.5e9 < times[0] < 2.5e9                                  # seconds, not ms
    assert all(b - a >= 3600 for a, b in zip(times, times[1:]))       # 60 m bars, gaps only at closes


def test_no_secrets_or_ips() -> None:
    text = FIX.read_text()
    assert re.search(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])", text) is None
    assert "sessionid" not in text
    assert re.search(r'set_auth_token\\?",\\?"p\\?":\[\\?"(unauthorized_user_token|<TOKEN>)', text)
