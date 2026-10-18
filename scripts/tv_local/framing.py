"""TradingView chart-websocket wire format: `~m~<len>~m~<payload>`, several packets per websocket message.

* A heartbeat packet is `~h~<n>`: the client must echo the same packet back or the server drops the socket.
* Every other packet is JSON {"m": method, "p": [params...]}; the first server packet is a hello dict without "m".
* `<len>` is the payload length as the browser counts it (UTF-16 code units). Python `len()` counts code points, so
  a payload with astral characters (emoji in a study title) differs; `split_packets` counts UTF-16 units to match.
"""
from __future__ import annotations

import json
from typing import Any

SEP = "~m~"


def _u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def encode(payload: str) -> str:
    return f"{SEP}{_u16len(payload)}{SEP}{payload}"


def encode_msg(method: str, params: list[Any]) -> str:
    return encode(json.dumps({"m": method, "p": params}, separators=(",", ":")))


def _take_u16(s: str, start: int, n: int) -> int:
    """Index in `s` after `n` UTF-16 code units from `start`."""
    i, units = start, 0
    while units < n and i < len(s):
        units += 2 if ord(s[i]) > 0xFFFF else 1
        i += 1
    return i


def split_packets(message: str) -> list[str]:
    """One websocket message -> its packets (raw payload strings), by declared length."""
    out: list[str] = []
    i = 0
    while i < len(message):
        if not message.startswith(SEP, i):
            raise ValueError(f"bad frame at offset {i}")
        j = message.index(SEP, i + len(SEP))
        n = int(message[i + len(SEP):j])
        start = j + len(SEP)
        end = _take_u16(message, start, n)
        out.append(message[start:end])
        i = end
    return out


def is_heartbeat(packet: str) -> bool:
    return packet.startswith("~h~")


def parse(packet: str) -> dict[str, Any]:
    """JSON packet -> dict; the hello packet comes back as {"m": "__hello__", "p": [hello]}."""
    obj = json.loads(packet)
    if isinstance(obj, dict) and "m" in obj:
        return obj
    return {"m": "__hello__", "p": [obj]}
