"""Local TradingView probe (spike, not system code). Owner-approved 2026-10-18, strictly read-only.

Module boundaries (so the system lane can lift them one by one):
  framing   wire format (~m~len~m~ packets, heartbeats)               no I/O
  auth      keyring cookies -> auth token / user id (never logged)    HTTP GET only
  client    one throttled websocket + frame recorder/sanitiser        websocket
  discovery saved layouts, studies on a layout, pine-facade metadata  HTTP GET only
  pull      bars + study plot values over one chart session          uses client
  storage   Parquet / JSON writers (epoch_ns timestamps)             local files
  run       CLI
"""
