"""Minimum-lot feasibility (playbook G-7 and section 4.4, BACKLOG item 22). Reporting only: it sizes nothing.

RiskGate sizes lots = equity x risk x m / (stop distance x 100 oz), rounded down to the lot step, at least the
minimum lot, and skips the trade (`min_lot_exceeds_risk`) when the minimum lot would risk more than 1.2x the target
(design "Sizing"). The arithmetic is shared with the gate through goldbot/risk/sizing.py (multiplier bounds, the hard
1% maximum, the 8% stage's quarter risk, the stops-level-plus-spread floor). On a slow horizon the stop is wide
(1.5 x ATR(1d) is $75-170/oz at 2025-26 prices), so 0.01 lot (1 oz) already risks $75-170 and a small account cannot
take the trade at all. For a price and a stop (given, or ATR(14) x the stop multiple from the store's bars, per
family, decision timeframe and registered preset, with each one's effective configuration: `timeframe_defaults`,
presets and `atr_tf`) this reports:
* min_equity_strict: the equity at which the minimum lot risks no more than the target risk;
* min_equity_gate:   the equity at which the gate accepts it (within its 1.2x tolerance);
* at a given equity: the raw and rounded lots, the risk the trade really takes and whether the gate would allow it
  (sizing rule only: caps, margin and the other gate checks are not evaluated here).
Price basis: the store's bars are Dukascopy bid/ask; price and ATR here are on the mid (as research and the labels
use), so the broker's own bid/ask ATR and stop can differ by about a spread.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.data.store import Store
from goldbot.data.timeutil import utc_index
from goldbot.execution.costs import CONTRACT_OZ
from goldbot.risk.sizing import MIN_LOT_TOLERANCE, effective_risk, size_lots

ATR_BARS = 14
MID_BASIS = "mid of the store's Dukascopy bid/ask bars (broker bid/ask ATR may differ by about a spread)"


class LotSpec(FrozenRecord):
    """The symbol's lot terms (MT5 SymbolInfo); defaults are XAUUSD at IC Markets: 100 oz, 0.01 min and step."""

    contract_oz: float = Field(CONTRACT_OZ, gt=0)
    volume_min: float = Field(0.01, gt=0)
    volume_step: float = Field(0.01, gt=0)
    volume_max: float = Field(50.0, gt=0)


class _Limits(FrozenRecord):
    """The sizing bounds (gate.RiskLimits fields that sizing reads)."""

    risk_per_trade: float = Field(gt=0)
    max_risk_per_trade: float = Field(gt=0)
    multiplier_bounds: tuple[float, float]


class Feasibility(FrozenRecord):
    family: str | None = None
    preset: str | None = None               # a registered preset (e.g. tsmom "slow", H-01)
    timeframe: str | None = None            # decision timeframe
    atr_timeframe: str | None = None        # the bars the ATR comes from (the preset's atr_tf, else the decision tf)
    as_of_utc: UtcTimestamp | None = None   # the last bar the price and ATR come from
    price_basis: str = "as given"
    price: float
    atr_usd: float | None = None
    stop_atr: float | None = None
    stop_distance: float                    # $/oz, after the stops-level-plus-spread floor
    risk_per_trade: float                   # the phase's rate before the multiplier and stage
    multiplier: float                       # after the bounds, the stage cap and the hard maximum
    size_down: bool = False                 # 8% stage: risk halved and multiplier capped at 0.5
    risk_fraction: float                    # the gate's target: risk per trade x multiplier after all of the above
    lot: LotSpec
    min_lot_risk_usd: float                 # what the minimum lot loses at the stop
    min_equity_strict: float                # minimum lot risk <= risk_fraction
    min_equity_gate: float                  # minimum lot risk <= 1.2 x risk_fraction (the gate's rule)
    equity: float | None = None
    lots_raw: float | None = None
    lots: float | None = None
    actual_risk_fraction: float | None = None
    allowed: bool | None = None
    reason: str | None = None


def min_lot_feasibility(*, price: float, risk_fraction: float, stop_distance: float | None = None,
                        atr_usd: float | None = None, stop_atr: float | None = None, equity: float | None = None,
                        lot: LotSpec | None = None, multiplier: float = 1.0, size_down: bool = False,
                        multiplier_bounds: tuple[float, float] = (0.25, 1.5), max_risk_per_trade: float = 0.01,
                        stops_level_points: float = 0.0, spread_points: float = 0.0, point: float = 0.01,
                        family: str | None = None, preset: str | None = None, timeframe: str | None = None,
                        atr_timeframe: str | None = None, as_of_utc: pd.Timestamp | None = None,
                        price_basis: str = "as given") -> Feasibility:
    """Feasibility of the minimum lot for one stop. `risk_fraction` is the phase's risk per trade; `stop_distance` in
    $/oz, or `atr_usd` x `stop_atr`, floored at the stops level plus the spread as the gate does."""
    from goldbot.risk import sizing
    lot = lot or LotSpec()
    if stop_distance is None:
        if atr_usd is None or stop_atr is None:
            raise ValueError("give stop_distance, or atr_usd and stop_atr")
        stop = sizing.stop_distance(stop_atr, atr_usd, stops_level_points=stops_level_points,
                                    spread_points=spread_points, point=point)
    else:
        stop = max(stop_distance, stops_level_points * point + spread_points * point)
    if not (math.isfinite(stop) and stop > 0 and risk_fraction > 0 and multiplier > 0):
        raise ValueError(f"stop distance {stop}, risk {risk_fraction} and multiplier {multiplier} must be > 0")
    limits = _Limits(risk_per_trade=risk_fraction, max_risk_per_trade=max_risk_per_trade,
                     multiplier_bounds=multiplier_bounds)
    eff = effective_risk(limits, size_down, multiplier)
    min_risk_usd = lot.volume_min * stop * lot.contract_oz
    out: dict[str, Any] = dict(
        family=family, preset=preset, timeframe=timeframe, atr_timeframe=atr_timeframe, as_of_utc=as_of_utc,
        price_basis=price_basis, price=price, atr_usd=atr_usd, stop_atr=stop_atr, stop_distance=stop,
        risk_per_trade=risk_fraction, multiplier=eff.mult, size_down=size_down, risk_fraction=eff.target, lot=lot,
        min_lot_risk_usd=min_risk_usd, min_equity_strict=min_risk_usd / eff.target,
        min_equity_gate=min_risk_usd / (MIN_LOT_TOLERANCE * eff.target))
    if equity is not None:
        sz = size_lots(equity=equity, risk=eff, stop_distance=stop, contract_oz=lot.contract_oz,
                       volume_min=lot.volume_min, volume_step=lot.volume_step, volume_max=lot.volume_max)
        out.update(equity=equity, lots_raw=sz.lots_raw, lots=round(sz.lots, 2), actual_risk_fraction=sz.realised_risk,
                   allowed=not sz.refused, reason="min_lot_exceeds_risk" if sz.refused else None)
    return Feasibility.model_validate(out)


def mid_ohlc(bars: pd.DataFrame) -> pd.DataFrame:
    """Mid OHLC of store bars (bid_*/ask_* columns, as goldbot.data.resample.mid derives it); bars that already
    carry open/high/low/close are returned as they are."""
    if "bid_close" not in bars.columns:
        return bars
    m = pd.DataFrame({"ts_utc": bars["ts_utc"]})
    for c in ("open", "high", "low", "close"):
        m[c] = (bars[f"bid_{c}"] + bars[f"ask_{c}"]) / 2.0
    if "spread_mean" in bars.columns:
        m["spread"] = bars["spread_mean"]
    return m


def feasibility_from_bars(bars: pd.DataFrame, *, stop_atr: float, risk_fraction: float,
                          price_bars: pd.DataFrame | None = None, **kw: Any) -> Feasibility:
    """Feasibility at the last bar: Wilder ATR(14) of `bars`, the price from the last close of `price_bars` (default
    the same bars). Store bars are read on the mid."""
    from goldbot.features.technical import atr
    if len(bars) < ATR_BARS + 1:
        raise ValueError(f"need at least {ATR_BARS + 1} bars for ATR({ATR_BARS}), got {len(bars)}")
    b = mid_ohlc(bars).sort_values("ts_utc").reset_index(drop=True)
    p = mid_ohlc(price_bars).sort_values("ts_utc").reset_index(drop=True) if price_bars is not None else b
    a = float(atr(b, ATR_BARS).iloc[-1])
    if not math.isfinite(a) or a <= 0:
        raise ValueError("ATR is not positive on the last bar")
    basis = MID_BASIS if "bid_close" in bars.columns else kw.pop("price_basis", "bars as given")
    return min_lot_feasibility(price=float(p["close"].iloc[-1]), atr_usd=a, stop_atr=stop_atr,
                               risk_fraction=risk_fraction, as_of_utc=utc_index(p["ts_utc"])[-1],
                               price_basis=basis, **kw)


def _last_bars(store: Store, tf: str, n: int = 400) -> pd.DataFrame:
    """The last `n` bars of bars_<tf> (empty when the table has none)."""
    try:
        last = store.sql(f"SELECT max(ts_utc) AS t FROM bars_{tf}")["t"].iloc[0]
    except Exception:          # no such view: the table has no files
        return pd.DataFrame()
    if pd.isna(last):
        return pd.DataFrame()
    end = pd.Timestamp(last)
    end = end.tz_localize("UTC") if end.tzinfo is None else end.tz_convert("UTC")
    # 3x the span of n bars covers weekends and the daily break
    start = end - pd.Timedelta(seconds=3 * n * tf_seconds(tf))
    df = store.read(f"bars_{tf}", start=start, end=end + pd.Timedelta(seconds=1))
    return df.tail(n).reset_index(drop=True)


def specialist_configs(timeframes: list[str] | None = None) -> list[tuple[str, str | None, str, float, str]]:
    """(family, preset, decision timeframe, stop_atr, ATR timeframe) for every registered family on each of its
    decision timeframes (with `timeframe_defaults`) and for every registered preset, from the specialist's own
    effective configuration."""
    from goldbot.specialists import SPECIALISTS
    from goldbot.specialists.base import TIMEFRAME_KEY
    out: list[tuple[str, str | None, str, float, str]] = []
    for fam, cls in sorted(SPECIALISTS.items()):
        variants: list[tuple[str | None, dict[str, Any]]] = [
            (None, {} if tf == cls.timeframe else {TIMEFRAME_KEY: tf}) for tf in (cls.timeframe, *cls.timeframes)]
        variants += [(name, dict(cfg)) for name, cfg in sorted(cls.presets.items())]
        for preset, overrides in variants:
            try:
                spec = cls(**overrides)
            except ValueError as exc:
                print(f"skipped {fam} {preset or overrides}: {exc}", file=sys.stderr)
                continue
            if timeframes is not None and spec.timeframe not in timeframes:
                continue
            out.append((fam, preset, spec.timeframe, float(spec.config.get("stop_atr", 1.5)),
                        spec.config.get("atr_tf") or spec.timeframe))
    return out


def _fmt_usd(x: float | None) -> str:
    return "-" if x is None else f"${x:,.0f}"


def format_rows(rows: list[Feasibility]) -> str:
    head = (f"{'family':<18} {'tf':<4} {'atr tf':<6} {'price':>9} {'ATR':>7} {'stop':>7} {'0.01 lot risk':>13} "
            f"{'min equity (<=risk)':>20} {'min equity (gate 1.2x)':>23}")
    eq = next((r.equity for r in rows if r.equity is not None), None)
    if eq is not None:
        head += f"   at {_fmt_usd(eq)}: lots  risk    gate"
    lines = [head]
    for r in rows:
        name = (r.family or "-") + (f":{r.preset}" if r.preset else "")
        line = (f"{name:<18} {(r.timeframe or '-'):<4} {(r.atr_timeframe or '-'):<6} {r.price:>9.2f} "
                f"{(f'{r.atr_usd:.2f}' if r.atr_usd is not None else '-'):>7} {r.stop_distance:>7.2f} "
                f"{_fmt_usd(r.min_lot_risk_usd):>13} {_fmt_usd(r.min_equity_strict):>20} {_fmt_usd(r.min_equity_gate):>23}")
        if r.equity is not None and r.actual_risk_fraction is not None:
            line += f"   {r.lots:>13.2f} {r.actual_risk_fraction:>5.2%}  {'ok' if r.allowed else 'refused'}"
        lines.append(line)
    if rows:
        r0 = rows[0]
        stage = " in the 8% stage (risk halved, multiplier capped at 0.5)" if r0.size_down else ""
        lines.append(f"risk {r0.risk_per_trade:.2%} per trade x multiplier {r0.multiplier:g} = {r0.risk_fraction:.3%}"
                     f"{stage}; price basis: {r0.price_basis}; sizing rule only (caps, margin and other gate checks "
                     "not evaluated)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py sizing-feasibility",
                                 description="Minimum equity at which the minimum lot fits the risk per trade (G-7).")
    ap.add_argument("--price", type=float, help="price; without it, price and ATR come from the store's bars")
    ap.add_argument("--stop-distance", type=float, help="stop distance in $/oz")
    ap.add_argument("--atr", type=float, help="ATR in $/oz (with --stop-atr)")
    ap.add_argument("--stop-atr", type=float, default=None,
                    help="stop in ATRs (with --atr; from the store: override every family's own)")
    ap.add_argument("--risk", type=float, default=None, help="risk per trade (default: settings risk.risk_per_trade)")
    ap.add_argument("--tiny-live", action="store_true", help="use risk.risk_per_trade_tiny_live")
    ap.add_argument("--multiplier", type=float, default=1.0, help="model multiplier (clamped like the gate)")
    ap.add_argument("--size-down", action="store_true", help="the 8% stage: risk halved, multiplier capped at 0.5")
    ap.add_argument("--equity", type=float, default=None, help="also report lots and real risk at this equity")
    ap.add_argument("--contract", type=float, default=CONTRACT_OZ)
    ap.add_argument("--min-lot", type=float, default=0.01)
    ap.add_argument("--lot-step", type=float, default=0.01)
    ap.add_argument("--stops-level-points", type=float, default=0.0, help="broker stops level (stop floor)")
    ap.add_argument("--spread-points", type=float, default=None,
                    help="spread for the stop floor (default: the last bar's mean spread in store mode, else 0)")
    ap.add_argument("--data-root", type=Path, default=None, help="store root (default: settings data_root)")
    ap.add_argument("--timeframes", nargs="*", default=None, help="only these decision timeframes (store mode)")
    ap.add_argument("--settings", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None, help="write the rows as JSON")
    args = ap.parse_args(argv)

    from goldbot.config import load_settings
    from goldbot.risk.gate import RiskLimits
    settings = load_settings(args.settings) if args.settings else load_settings()
    gl = RiskLimits.from_settings(settings.risk, tiny_live=args.tiny_live)
    risk = args.risk if args.risk is not None else gl.risk_per_trade
    lot = LotSpec(contract_oz=args.contract, volume_min=args.min_lot, volume_step=args.lot_step)
    common: dict[str, Any] = dict(equity=args.equity, lot=lot, multiplier=args.multiplier, size_down=args.size_down,
                                  multiplier_bounds=gl.multiplier_bounds, max_risk_per_trade=gl.max_risk_per_trade,
                                  stops_level_points=args.stops_level_points, point=gl.point)
    rows: list[Feasibility] = []
    if args.price is not None:
        rows.append(min_lot_feasibility(price=args.price, stop_distance=args.stop_distance, atr_usd=args.atr,
                                        stop_atr=args.stop_atr, risk_fraction=risk,
                                        spread_points=args.spread_points or 0.0, **common))
    else:
        store = Store(args.data_root if args.data_root is not None else Path(settings.data_root).expanduser())
        cache: dict[str, pd.DataFrame] = {}

        def bars(tf: str) -> pd.DataFrame:
            if tf not in cache:
                cache[tf] = _last_bars(store, tf)
            return cache[tf]

        for fam, preset, tf, stop_atr, atr_tf in specialist_configs(args.timeframes):
            atr_bars, price_bars = bars(atr_tf), bars(tf)
            if len(atr_bars) <= ATR_BARS or price_bars.empty:
                continue
            spread = args.spread_points
            if spread is None:
                last = price_bars.get("spread_mean")
                spread = float(last.iloc[-1]) / gl.point if last is not None and pd.notna(last.iloc[-1]) else 0.0
            rows.append(feasibility_from_bars(atr_bars, price_bars=price_bars, family=fam, preset=preset,
                                              timeframe=tf, atr_timeframe=atr_tf, risk_fraction=risk,
                                              stop_atr=args.stop_atr if args.stop_atr is not None else stop_atr,
                                              spread_points=spread, **common))
        if not rows:
            print("no bars in the store for those timeframes: run scripts/fetch_data_release.py, or give --price "
                  "with --stop-distance or --atr/--stop-atr", file=sys.stderr)
            return 2
    print(format_rows(rows))
    if args.out:
        args.out.write_text(json.dumps([r.model_dump(mode="json") for r in rows], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
