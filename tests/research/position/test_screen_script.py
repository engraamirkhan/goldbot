"""scripts/position_screen.py fails closed: once-only windows need a frozen pre-registration, financing needs DTB3."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research.position.signals import PositionSpec

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("position_screen", ROOT / "scripts" / "position_screen.py")
assert _spec is not None and _spec.loader is not None
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


def test_a_confirm_read_without_a_frozen_preregistration_is_refused() -> None:
    with pytest.raises(SystemExit, match="read once"):
        ps.main(["--spec", "nowhere:NOTHING", "--window", "CONFIRM"])
    with pytest.raises(SystemExit, match="read once"):
        ps.main(["--spec", "nowhere:NOTHING", "--window", "TEST-OLD", "--frozen-prereg", "docs/no-such-file.md"])


def test_financing_without_dtb3_needs_the_constant_rate_flag(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="DTB3"):
        ps.load_rates(tmp_path / "dtb3.parquet", False, 4.02)
    assert ps.load_rates(tmp_path / "dtb3.parquet", True, 4.02).uses_constant
    pd.DataFrame({"date": pd.to_datetime(["2012-01-03"]), "rate_pct": [0.05]}).to_parquet(tmp_path / "dtb3.parquet")
    assert not ps.load_rates(tmp_path / "dtb3.parquet", False, 4.02).uses_constant


def _sig(symbol: str, panel) -> pd.Series:
    c = panel[symbol]["close"]
    return np.sign(c / c.shift(20) - 1.0)


SPECS = [PositionSpec(id="SYN-A", symbols=("XAUUSD",), signal=_sig),
         PositionSpec(id="SYN-B", symbols=("XAUUSD",), signal=_sig, long_only=False, band=0.3)]


def test_a_synthetic_learn_batch_writes_summary_statistics_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rng = np.random.default_rng(9)
    dates = pd.bdate_range("2009-01-01", "2011-06-30")
    close = 1000 * np.cumprod(1 + rng.normal(0, 0.01, len(dates)))
    (tmp_path / "daily").mkdir()
    pd.DataFrame({"date": dates, "open": np.r_[1000, close[:-1]], "high": close * 1.004, "low": close * 0.996,
                  "close": close}).to_parquet(tmp_path / "daily" / "XAUUSD.parquet")
    monkeypatch.setattr(ps.data, "load_xau_daily", lambda raw: ps.data.load_daily_file(Path(raw) / "daily" / "XAUUSD.parquet"))
    monkeypatch.setitem(sys.modules, "syn_specs", type(sys)("syn_specs"))
    sys.modules["syn_specs"].SPECS = SPECS                          # type: ignore[attr-defined]
    out = tmp_path / "learn.json"
    assert ps.main(["--raw-dir", str(tmp_path), "--spec", "syn_specs:SPECS", "--window", "LEARN",
                    "--allow-constant-rate", "--out", str(out)]) == 0
    got = json.loads(out.read_text())
    assert [r["id"] for r in got["specs"]] == ["SYN-A", "SYN-B"]
    first = got["specs"][0]
    assert first["rate_source"] == "constant" and set(first["venues"]) == {"A", "C"}
    assert first["venues"]["A"]["gates"] and not first["venues"]["C"]["gates"]
    assert "bh_reject" in first and set(first["explore_checks"]) >= {"t_boot>=2", "round_trips>=30"}
    assert "close" not in out.read_text() and "units" not in out.read_text()      # no prices, no trade rows
