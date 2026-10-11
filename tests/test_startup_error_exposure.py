"""Trading-safety re-verification LOW-D (of e737380): a refused start (`ops.run.refuse_start`) and a start whose
positions read fails (`Engine._load_orders`) replaced engine_<account>.json with a small error payload, so the
supervisor lost the account's last-known exposure and equity: the combined exposure cap then summed 0 lots for an
account that may still hold positions. Now the error is merged into the previous file (open lots and notional, equity,
day / week start and the closed-balance high-water mark are kept), and the supervisor treats a file carrying
`startup_error` like an unreadable one for entries (halt, `engine_startup_refused`) while still summing its exposure."""
import json
import time
from pathlib import Path
from typing import Any

import pytest

from goldbot.engine import Engine, EngineConfig
from goldbot.ops import run as run_mod
from goldbot.risk.supervisor import Supervisor, SupervisorLimits, write_startup_error
from tests.test_currency_sizing import GbpPaper

KEPT = {"open_lots": 0.3, "open_notional": 124_500.0, "equity": 990.0, "day_start_equity": 1_000.0,
        "week_start_equity": 1_010.0, "balance_closed_hwm": 1_020.0}


def _running_file(d: Path, account: str = "icm-demo", **kw: Any) -> None:
    (d / f"engine_{account}.json").write_text(json.dumps({"account": account, "ts": time.time(), "currency": "GBP",
                                                          "stage": "normal", **KEPT, **kw}))


def _read(d: Path, account: str = "icm-demo") -> dict:
    return json.loads((d / f"engine_{account}.json").read_text())


def test_a_startup_error_is_merged_into_the_last_engine_file(tmp_path):
    _running_file(tmp_path)
    write_startup_error(tmp_path, "icm-demo", "refusing to trade icm-demo: x", dq_checks=["startup_refused"])
    e = _read(tmp_path)
    assert {k: e[k] for k in KEPT} == KEPT and e["currency"] == "GBP"
    assert e["startup_error"] == "refusing to trade icm-demo: x" and e["dq_error"] is True and e["stage"] == "unknown"


def test_a_startup_error_without_a_previous_file_or_with_junk_writes_the_error_alone(tmp_path):
    write_startup_error(tmp_path, "icm-demo", "boom", dq_checks=["positions_unreadable"], currency="GBP")
    assert _read(tmp_path)["startup_error"] == "boom" and "open_lots" not in _read(tmp_path)
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps({"ts": 1.0, "open_lots": float("nan"), "equity": "x"}))
    write_startup_error(tmp_path, "icm-demo", "boom", dq_checks=["positions_unreadable"])
    e = _read(tmp_path)
    assert "open_lots" not in e and "equity" not in e          # a non-finite or non-number value is never carried


def test_a_refused_start_keeps_the_last_known_exposure(tmp_path):
    _running_file(tmp_path)
    with pytest.raises(SystemExit):
        run_mod.refuse_start("icm-demo", "refusing to trade icm-demo: margin", None, state_dir=tmp_path)
    e = _read(tmp_path)
    assert {k: e[k] for k in KEPT} == KEPT and e["currency"] == "GBP" and "margin" in e["startup_error"]


def test_an_unreadable_positions_read_at_start_keeps_the_last_known_exposure(tmp_path):
    class Dead(GbpPaper):
        def positions(self, magic_prefix: int | None = None) -> list:
            raise RuntimeError("terminal down")

    _running_file(tmp_path)
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps({"sent": {}, "open": {}}))
    with pytest.raises(RuntimeError):
        Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)), Dead(), [], {})
    e = _read(tmp_path)
    assert {k: e[k] for k in KEPT} == KEPT and e["startup_error"] and e["dq_checks"] == ["positions_unreadable"]


def test_the_supervisor_halts_on_a_refused_engine_and_still_counts_its_exposure(tmp_path):
    _running_file(tmp_path, "vantage-demo", open_lots=0.2, open_notional=83_000.0)
    _running_file(tmp_path, "icm-demo", startup_error="refusing to trade icm-demo: margin mode unknown")
    s = Supervisor(tmp_path, SupervisorLimits()).evaluate()
    assert s["halt"] and "engine_startup_refused" in s["reasons"], s["reasons"]
    assert s["combined_open_lots"] == pytest.approx(0.5) and s["exposure"]["icm-demo"]["lots"] == pytest.approx(0.3)
    assert s["startup_refused_engines"] == ["engine_icm-demo.json"]
    _running_file(tmp_path, "icm-demo")                                           # the engine started: cleared
    assert "engine_startup_refused" not in Supervisor(tmp_path, SupervisorLimits()).evaluate()["reasons"]
