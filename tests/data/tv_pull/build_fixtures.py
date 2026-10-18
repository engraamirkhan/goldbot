"""Builds tests/data/tv_pull/*.json: chart-websocket conversations in TradingView's wire format (synthetic values)."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(sys.argv[1])
NOTE = ("Synthetic scenario (values made up) in the wire shapes verified against TradingView on 2026-10-18 (series "
        "sds_1, studies st<k>, chart-page initData.content with a Script$PUB;<id> metaInfo string, 1e100 = na). "
        "Captured frames: tests/fixtures/tv/.")


def frame(obj):
    p = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"))
    return f"~m~{len(p)}~m~{p}"


def ts(h, m):
    return float(datetime(2026, 10, 16, h, m, tzinfo=timezone.utc).timestamp())


CS = "cs_fixture"


def bars(start, n, base):
    return [{"i": i, "v": [start + 900 * i, base + i, base + i + 2.0, base + i - 1.5, base + i + 0.5, 1000.0 + (start + 900 * i - ts(9, 30)) / 900]}
            for i in range(n)]


def study(times):
    # plot_0 a level near price, plot_1 an oscillator, plot_2 an event (na except every third bar), plot_3 a colourer
    return [{"i": i, "v": [t, 2395.0 + (t - ts(9, 30)) / 900, 50.0 + (t - ts(9, 30)) / 900,
                           1.0 if int((t - ts(9, 30)) / 900) % 3 == 0 else 1e100, 0.0]} for i, t in enumerate(times)]


META = {
    "description": "Structure Pro", "shortDescription": "smc",
    "pine": {"version": "7.0"},
    "inputs": [{"id": "text", "defval": "x", "type": "text"},
               {"id": "pineId", "defval": "PUB;fixture123", "type": "text"},
               {"id": "pineVersion", "defval": "7.0", "type": "text"},
               {"id": "in_0", "defval": 50, "type": "integer", "name": "Length"},
               {"id": "in_1", "defval": True, "type": "bool", "name": "Show"}],
    "plots": [{"id": "plot_0", "type": "line"}, {"id": "plot_1", "type": "line"},
              {"id": "plot_2", "type": "shapes"}, {"id": "plot_3", "type": "colorer", "target": "plot_0"}],
    "styles": {"plot_0": {"title": "EMA"}, "plot_1": {"title": "RSI"}, "plot_2": {"title": "Buy"}},
}
LAYOUT = {"charts": [{"panes": [{"sources": [
    {"type": "MainSeries", "state": {"symbol": "OANDA:XAUUSD", "interval": "15"}},
    {"type": "Study", "metaInfo": "Script$PUB;fixture123@tv-scripting-101[v.7.0]",
     "state": {"inputs": {"in_0": 21, "in_1": True, "pineId": "", "pineVersion": "", "__fast_calc": False}}},
    {"type": "LineToolTrendLine", "state": {"points": []}}]}]}]}
HTTP = {
    "https://www.tradingview.com/": '<html><script>window.user = {"username":"owner","id":4242,'
                                    '"auth_token":"AUTHTOKEN-fixture-abcdef123456"};</script></html>',
    "https://www.tradingview.com/my-charts/": json.dumps([
        {"image_url": "LayOut01", "name": "Gold 15m", "symbol": "OANDA:XAUUSD", "interval": "15"}]),
    "https://www.tradingview.com/chart/LayOut01/": "<html><script>window.initData = {};\ninitData.content = "
                                                   + json.dumps(LAYOUT) + ";\n</script></html>",
    "https://pine-facade.tradingview.com/pine-facade/translate/PUB%3Bfixture123/7.0": json.dumps(
        {"success": True, "result": {"ilTemplate": "bmI9Ks46_fixture_il", "metaInfo": META}}),
}
COMPLETED = frame({"m": "series_completed", "p": [CS, "sds_1", "streaming", "s1", {}]})


def series(rows):
    return frame({"m": "timescale_update", "p": [CS, {"sds_1": {"node": "n", "s": rows, "t": "s1", "lbs": {}}}, {}]})


def study_reply(times):
    return [frame({"m": "du", "p": [CS, {"st1": {"node": "n", "st": study(times), "t": "s1"}}]}),
            frame({"m": "study_completed", "p": [CS, "st1", "s1_st1", {}]})]


def times_of(rows):
    return [r["v"][0] for r in rows]


# backfill at 11:55: 10:30..11:45 loaded (11:45 still forming), one history page 09:30..10:15, then the start of history
first, older = bars(ts(10, 30), 6, 2400.0), bars(ts(9, 30), 4, 2396.0)
backfill = {
    "comment": NOTE, "http": HTTP,
    "hello": [frame({"session_id": "<0.1.2>_fixture", "timestamp": 1760600000, "release": "x"}), frame("~h~1")],
    "replies": {
        "create_series": [[series(first) + COMPLETED]],
        "request_more_data": [[series(older), frame("~h~2"), COMPLETED], [COMPLETED]],
        "create_study": [study_reply(times_of(older) + times_of(first))],
    },
}
# the next pass at 12:01: the same window one bar on (11:45 closed at 12:00, 12:00 forming); 11:30 repainted
live_rows = bars(ts(10, 45), 6, 2401.0)
live_rows[3]["v"][4] = 9999.0
live = {
    "comment": NOTE, "http": HTTP,
    "hello": [frame({"session_id": "<0.1.2>_fixture2", "timestamp": 1760601000, "release": "x"})],
    "replies": {
        "create_series": [[series(live_rows), COMPLETED]],
        "request_more_data": [],
        "create_study": [study_reply(times_of(live_rows))],
    },
}
OUT.mkdir(parents=True, exist_ok=True)
for name, script in (("backfill_15m.json", backfill), ("live_15m.json", live)):
    (OUT / name).write_text(json.dumps(script, indent=1) + "\n", encoding="utf-8")
