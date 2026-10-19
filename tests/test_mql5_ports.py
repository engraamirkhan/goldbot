"""The MQL5 ports of the owner's three indicators (goldbot/ops/linux/mt5/indicators) stay in step with the Python
references: every input the port declares carries the Python default, and each file has the shape MetaEditor needs
(indicator properties, OnInit, OnCalculate). No MetaEditor runs in CI, so this is a parity check of the declared
parameters and the file shape, not of computed values."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from goldbot.features import mfi_signals
from goldbot.features.smc_replica import DEFAULT_PARAMS as P

DIR = Path(__file__).resolve().parents[1] / "goldbot" / "ops" / "linux" / "mt5" / "indicators"

EXPECTED: dict[str, dict[str, object]] = {
    "goldbot_structure.mq5": {
        "SwingLen": P.swing_len, "InternalLen": P.internal_len, "InternalCandleFilter": P.internal_candle_filter,
        "EqualLen": P.equal_len, "EqualThreshold": P.equal_threshold, "EqualAtrLen": P.equal_atr_len,
        "FvgAutoThreshold": P.fvg_auto_threshold, "RangeLen": P.range_len, "RangeFrac1": P.range_fracs[0],
        "RangeFrac2": P.range_fracs[1],
    },
    "goldbot_money_flow.mq5": {
        "MfiLen": P.mfi_len, "Overbought": mfi_signals.OVERBOUGHT, "Oversold": mfi_signals.OVERSOLD,
        "BullLevel": mfi_signals.BULL, "BearLevel": mfi_signals.BEAR, "MidLevel": mfi_signals.MID, "PivotLen": 5,
    },
    "goldbot_sr_boxes.mq5": {
        "SrPivotLen": P.sr_pivot_len, "SrWidthPct": P.sr_width_pct, "SrMaxChannels": P.sr_max_channels,
        "SrLookback": P.sr_lookback, "SrRangeWindow": P.sr_range_window, "CandleVolLen": P.candle_vol_len,
        "CandleHighMult": P.candle_high_mult, "CandleLowMult": P.candle_low_mult,
    },
}
# Display-only inputs (colours) with no Python counterpart.
DISPLAY_ONLY = {"BoxColor"}
INPUT = re.compile(r"^\s*input\s+(\w+)\s+(\w+)\s*=\s*([^;]+);", re.M)


def _inputs(text: str) -> dict[str, tuple[str, str]]:
    return {name: (typ, value.strip()) for typ, name, value in INPUT.findall(text)}


def _value(typ: str, raw: str) -> object:
    if typ == "bool":
        return raw == "true"
    if typ == "int":
        return int(raw)
    if typ == "double":
        return float(raw)
    return raw


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_port_declares_the_python_defaults_and_nothing_else(name: str) -> None:
    got = _inputs((DIR / name).read_text())
    assert set(got) - DISPLAY_ONLY == set(EXPECTED[name])
    for key, want in EXPECTED[name].items():
        typ, raw = got[key]
        assert _value(typ, raw) == want, f"{name}: {key} = {raw}, Python default {want}"


def test_the_mfi_signal_pivot_length_default_is_five() -> None:
    import inspect
    assert inspect.signature(mfi_signals.mfi_signals).parameters["pivot_len"].default == 5


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_port_has_the_indicator_shape_and_names_its_python_source(name: str) -> None:
    text = (DIR / name).read_text()
    assert re.search(r"#property\s+indicator_(chart|separate)_window", text)
    buffers = int(re.search(r"#property\s+indicator_buffers\s+(\d+)", text).group(1))  # type: ignore[union-attr]
    plots = int(re.search(r"#property\s+indicator_plots\s+(\d+)", text).group(1))  # type: ignore[union-attr]
    assert plots <= buffers
    assert len(re.findall(r"\bBind\(\d+,", text)) == buffers                # every buffer bound exactly once
    assert re.search(r"\bint\s+OnInit\s*\(\s*\)", text)
    assert re.search(r"\bint\s+OnCalculate\s*\(\s*const\s+int\s+rates_total", text)
    assert "goldbot/features/" in text.split("#property")[0]          # the header names the Python source
    assert text.count("{") == text.count("}") and text.count("(") == text.count(")")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_port_never_evaluates_the_forming_bar(name: str) -> None:
    """No repaint: the bar loop stops before rates_total - 1 and that bar's buffers are blanked."""
    text = (DIR / name).read_text()
    assert "int closed = rates_total - 1;" in text
    assert re.search(r"for\(int i = g_next; i < closed; i\+\+\)", text)
    assert "Blank(rates_total - 1);" in text
