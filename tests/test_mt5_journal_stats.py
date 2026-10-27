"""scripts/mt5_journal_stats.py: the MT5 terminal journal -> requests, execution time, bursts, 10024, reconnects."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
L = "'1234567'"                       # a made-up login: the script never prints it

JOURNAL = [
    "#FILE 20261026.log",
    f"AA\t0\t13:00:01.100\tTrades\t{L}: market buy 0.01 XAUUSD sl: 4000.00 tp: 4010.00",
    f"AB\t0\t13:00:01.110\tTrades\t{L}: accepted market buy 0.01 XAUUSD sl: 4000.00 tp: 4010.00",
    f"AC\t0\t13:00:01.430\tTrades\t{L}: deal #11 buy 0.01 XAUUSD at 4005.00 done (based on order #101)",
    f"AD\t0\t13:00:01.431\tTrades\t{L}: order #101 buy 0.01 / 0.01 XAUUSD at 4005.00 done in 330.500 ms",
    f"AE\t0\t13:00:02.000\tTrades\t{L}: market sell 0.01 XAUUSD sl: 4012.00 tp: 4001.00",
    f"AF\t0\t13:00:02.400\tTrades\t{L}: order #102 sell 0.01 / 0.01 XAUUSD at 4006.00 done in 400.000 ms",
    f"AG\t0\t13:00:02.401\tTrades\t{L}: deal #12 sell 0.01 XAUUSD at 4006.00 done (based on order #102)",
    f"AH\t0\t13:07:30.000\tTrades\t{L}: market sell 0.01 XAUUSD, close #9 buy 0.01 XAUUSD 4007.00",
    f"AI\t0\t13:07:30.330\tTrades\t{L}: order #103 sell 0.01 / 0.01 XAUUSD at 4007.00 done in 330.000 ms",
    f"AJ\t0\t13:07:30.331\tTrades\t{L}: deal #13 sell 0.01 XAUUSD at 4007.00 done (based on order #103)",
    f"AK\t0\t13:09:00.000\tTrades\t{L}: deal #14 sell 0.01 XAUUSD at 4010.00 done (based on order #555)",
    f"AL\t0\t13:10:00.000\tTrades\t{L}: failed market buy 0.01 XAUUSD [Too many requests]",
    f"AM\t0\t13:11:00.000\tTrades\t{L}: failed market sell XAUUSD [Position doesn't exist]",
    f"AN\t0\t14:00:00.000\tNetwork\t{L}: authorized on Demo through Access Server - LD (ping: 72.20 ms, build 6182)",
    f"AO\t0\t14:01:00.000\tNetwork\t{L}: authorization on Demo failed (Invalid account)",
    "#FILE 20261027.log",
    f"BA\t0\t00:15:01.500\tTrades\t{L}: market sell 0.01 XAUUSD sl: 4019.54 tp: 4013.54",
    f"BB\t0\t00:15:32.840\tTrades\t{L}: order #201 sell 0.01 / 0.01 XAUUSD at 4016.28 done in 31318.196 ms",
]


@pytest.fixture(scope="module")
def js():
    spec = importlib.util.spec_from_file_location("mt5_journal_stats", ROOT / "scripts" / "mt5_journal_stats.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mt5_journal_stats"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_day_summary(js) -> None:
    days = js.parse(JOURNAL)
    s = days["20261026"].summary()
    assert (s["opens"], s["closes"], s["results"], s["deals"]) == (2, 1, 3, 4)
    assert s["server_exits"] == 1                                # deal #14: no request of ours (a server stop/target)
    assert s["done_ms_p50"] == 330.5 and s["done_ms_max"] == 400.0
    assert s["peak_per_min"] == 2 and s["quarter_first_min_max"] == 2
    assert s["quarter_first_min_share"] == pytest.approx(2 / 3, abs=1e-3)
    assert s["too_many_requests_10024"] == 1 and s["refusals"]["Position doesn't exist"] == 1
    assert s["reconnects"] == 1 and s["ping_ms_median"] == 72.2 and s["auth_failures"] == {"Invalid account": 1}
    late = days["20261027"].summary()
    assert late["requests"] == 1 and late["done_ms_max"] == 31318.2


def test_empty_day_has_no_times(js) -> None:
    s = js.parse(["#FILE 20261024.log"])["20261024"].summary()
    assert s["requests"] == 0 and s["done_ms_p95"] is None and s["quarter_first_min_share"] is None


def test_utf16_file_and_no_login_in_output(js, tmp_path, capsys) -> None:
    f = tmp_path / "20261026.log"
    f.write_bytes("﻿".encode("utf-16-le") + "\r\n".join(JOURNAL[1:16]).encode("utf-16-le"))
    assert js.main([str(f)]) == 0
    out = capsys.readouterr().out
    assert '"day": "20261026"' in out and '"opens": 2' in out and "1234567" not in out
