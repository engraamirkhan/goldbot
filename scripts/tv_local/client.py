"""One TradingView chart websocket, throttled (>= 1 s between client requests), with an optional frame recorder.

The recorder keeps every frame in both directions after `sanitise` has replaced the auth token, user ids and
session cookies; nothing secret reaches disk. Heartbeats are answered here and never reach the caller.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Iterator

import websocket  # type: ignore[import-not-found,unused-ignore]  # websocket-client (uv run --with)

from . import framing

# `?type=chart` matters: without it every create_study fails "Study not allowed in this connection"
URL = "wss://data.tradingview.com/socket.io/websocket?from=chart%2F&type=chart"
PRO_URL = "wss://prodata.tradingview.com/socket.io/websocket?from=chart%2F&type=chart"  # paid plans; the free/unauth data host is URL
ORIGIN = "https://www.tradingview.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36"
MIN_GAP_S = 1.0


class TvError(RuntimeError):
    pass


class Recorder:
    """Collects sanitised frames: {"dir": "in"|"out", "t": seconds since start, "frame": str}."""

    def __init__(self, secrets: list[str] | None = None) -> None:
        self.frames: list[dict[str, Any]] = []
        self.t0 = time.monotonic()
        self.secrets = [s for s in (secrets or []) if s]

    def add(self, direction: str, frame: str) -> None:
        # sanitise packet by packet and re-encode, so the ~m~<len>~m~ prefixes stay correct for replay tests
        clean = "".join(framing.encode(self.sanitise(p)) for p in framing.split_packets(frame))
        self.frames.append({"dir": direction, "t": round(time.monotonic() - self.t0, 3), "frame": clean})

    def sanitise(self, frame: str) -> str:
        for s in self.secrets:
            frame = frame.replace(s, "<REDACTED>")
        frame = re.sub(r'"set_auth_token","p":\["[^"]*"\]', '"set_auth_token","p":["<TOKEN>"]', frame)
        frame = re.sub(r'"(user_id|userId|auth_token|sessionid|sessionid_sign|private_channel)"\s*:\s*("[^"]*"|\d+)',
                       r'"\1":"<REDACTED>"', frame)
        # hello packet carries a server-side session id; harmless but not needed
        frame = re.sub(r'"session_id"\s*:\s*"[^"]*"', '"session_id":"<SID>"', frame)
        # hello `via` is an IP:port (may be the client's egress); never keep IPs
        frame = re.sub(r'"via"\s*:\s*"[^"]*"', '"via":"<IP>"', frame)
        frame = re.sub(r"(?<![\d.])\d{1,3}(?:\.\d{1,3}){3}(?![\d.])", "<IP>", frame)
        return frame

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(f, separators=(",", ":")) for f in self.frames) + "\n")


class TvSocket:
    def __init__(self, url: str = URL, recorder: Recorder | None = None, timeout_s: float = 30.0) -> None:
        self.url = url
        self.recorder = recorder
        self.timeout_s = timeout_s
        self.ws: websocket.WebSocket | None = None
        self._last_send = 0.0
        self.hello: dict[str, Any] | None = None

    def __enter__(self) -> TvSocket:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        self.ws = websocket.create_connection(self.url, timeout=self.timeout_s,
                                              header=[f"Origin: {ORIGIN}", f"User-Agent: {UA}"])

    def close(self) -> None:
        if self.ws is not None:
            try:
                self.ws.close()
            finally:
                self.ws = None

    def send(self, method: str, params: list[Any]) -> None:
        assert self.ws is not None
        wait = MIN_GAP_S - (time.monotonic() - self._last_send)
        if wait > 0:
            time.sleep(wait)
        frame = framing.encode_msg(method, params)
        if self.recorder is not None:
            self.recorder.add("out", frame)
        self.ws.send(frame)
        self._last_send = time.monotonic()

    def packets(self, until_s: float) -> Iterator[dict[str, Any]]:
        """Yield parsed packets until `until_s` seconds pass with no packet (idle timeout) or the caller stops."""
        assert self.ws is not None
        self.ws.settimeout(until_s)
        while True:
            try:
                msg = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                return
            if isinstance(msg, bytes):
                msg = msg.decode("utf-8")
            if self.recorder is not None:
                self.recorder.add("in", msg)
            for pkt in framing.split_packets(msg):
                if framing.is_heartbeat(pkt):
                    hb = framing.encode(pkt)
                    if self.recorder is not None:
                        self.recorder.add("out", hb)
                    self.ws.send(hb)           # heartbeat echo is not a request: not throttled
                    continue
                p = framing.parse(pkt)
                if p["m"] == "__hello__":
                    self.hello = p["p"][0]
                yield p
