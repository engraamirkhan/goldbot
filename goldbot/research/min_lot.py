"""Minimum-lot feasibility (playbook G-7 and section 4.4, BACKLOG item 22). Reporting only: it sizes nothing.

RiskGate sizes lots = equity x risk x m / (stop distance x 100 oz), rounded down to the lot step, at least the
minimum lot, and skips the trade (`min_lot_exceeds_risk`) when the minimum lot would risk more than 1.2x the target
(goldbot/risk/gate.py `check`, design "Sizing"). On a slow horizon the stop is wide (1.5 x ATR(1d) is $75-170/oz at
2025-26 prices), so 0.01 lot (1 oz) already risks $75-170 and a small account cannot take the trade at all. This
module reports, for a price and a stop (given, or ATR(14) x the family's stop multiple from the store's bars):
* min_equity_strict: the equity at which the minimum lot risks no more than the risk fraction;
* min_equity_gate:   the equity at which the gate accepts it (within its 1.2x tolerance);
* at a given equity: the raw and rounded lots, the risk the trade really takes and whether the gate would allow it
  (sizing rule only: caps, margin and the other gate checks are not evaluated here).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.data.store import Store
from goldbot.data.timeutil import utc_index
from goldbot.execution.costs import CONTRACT_OZ

MIN_LOT_TOLERANCE = 1.2      # gate.py: the minimum lot is used only while it keeps risk within 1.2x the target
ATR_BARS = 14


class LotSpec(FrozenRecord):
    """The symbol's lot terms (MT5 SymbolInfo); defaults are XAUUSD at IC Markets: 100 oz, 0.01 min and step."""

    contract_oz: float = Field(CONTRACT_OZ, gt=0)
    volume_min: float = Field(0.01, gt=0)
    volume_step: float = Field(0.01, gt=0)
    volume_max: float = Field(50.0, gt=0)


class Feasibility(FrozenRecord):
    family: str | None = None
    timeframe: str | None = None
    as_of_utc: UtcTimestamp | None = None   # the last bar the price and ATR come from
    price: float
    atr_usd: float | None = None
    stop_atr: float | None = None
    stop_distance: float                    # $/oz
    risk_fraction: float                    # target risk per trade x multiplier
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
                        lot: LotSpec | None = None, multiplier: float = 1.0, family: str | None = None,
                        timeframe: str | None = None, as_of_utc: pd.Timestamp | None = None) -> Feasibility:
    """Feasibility of the minimum lot for one stop. `stop_distance` in $/oz, or `atr_usd` x `stop_atr`."""
    lot = lot or LotSpec()
    if stop_distance is None:
        if atr_usd is None or stop_atr is None:
            raise ValueError("give stop_distance, or atr_usd and stop_atr")
        stop_distance = atr_usd * stop_atr
    if not (math.isfinite(stop_distance) and stop_distance > 0 and risk_fraction > 0 and multiplier > 0):
        raise ValueError(f"stop distance {stop_distance}, risk {risk_fraction} and multiplier {multiplier} must be > 0")
    risk = risk_fraction * multiplier
    min_risk_usd = lot.volume_min * stop_distance * lot.contract_oz
    out = dict(family=family, timeframe=timeframe, as_of_utc=as_of_utc, price=price, atr_usd=atr_usd,
               stop_atr=stop_atr, stop_distance=stop_distance, risk_fraction=risk, lot=lot,
               min_lot_risk_usd=min_risk_usd, min_equity_strict=min_risk_usd / risk,
               min_equity_gate=min_risk_usd / (MIN_LOT_TOLERANCE * risk))
    if equity is not None:
        # the gate's arithmetic, line for line (goldbot/risk/gate.py `check`, sizing)
        lots_raw = equity * risk / (stop_distance * lot.contract_oz)
        steps = math.floor(lots_raw / lot.volume_step + 1e-9)
        lots = min(max(lot.volume_min, steps * lot.volume_step), lot.volume_max)
        realised = lots * stop_distance * lot.contract_oz / equity
        refused = realised > MIN_LOT_TOLERANCE * risk and lots_raw < lot.volume_min
        out.update(equity=equity, lots_raw=lots_raw, lots=round(lots, 2), actual_risk_fraction=realised,
                   allowed=not refused, reason="min_lot_exceeds_risk" if refused else None)
    return Feasibility.model_validate(out)


def feasibility_from_bars(bars: pd.DataFrame, *, stop_atr: float, risk_fraction: float, equity: float | None = None,
                          lot: LotSpec | None = None, multiplier: float = 1.0, family: str | None = None,
                          timeframe: str | None = None) -> Feasibility:
    """Feasibility at the last bar: its close as the price, Wilder ATR(14) of the bars as the ATR."""
    from goldbot.features.technical import atr
    if len(bars) < ATR_BARS + 1:
        raise ValueError(f"need at least {ATR_BARS + 1} bars for ATR({ATR_BARS}), got {len(bars)}")
    b = bars.sort_values("ts_utc").reset_index(drop=True)
    a = float(atr(b, ATR_BARS).iloc[-1])
    if not math.isfinite(a) or a <= 0:
        raise ValueError("ATR is not positive on the last bar")
    return min_lot_feasibility(price=float(b["close"].iloc[-1]), atr_usd=a, stop_atr=stop_atr,
                               risk_fraction=risk_fraction, as_of_utc=utc_index(b["ts_utc"])[-1], equity=equity,
                               lot=lot, multiplier=multiplier, family=family, timeframe=timeframe)


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


def _fmt_usd(x: float | None) -> str:
    return "-" if x is None else f"${x:,.0f}"


def format_rows(rows: list[Feasibility]) -> str:
    head = (f"{'family':<18} {'tf':<4} {'price':>9} {'ATR':>7} {'stop':>7} {'0.01 lot risk':>13} "
            f"{'min equity (<=risk)':>20} {'min equity (gate 1.2x)':>23}")
    eq = next((r.equity for r in rows if r.equity is not None), None)
    if eq is not None:
        head += f"   at {_fmt_usd(eq)}: lots  risk    gate"
    lines = [head]
    for r in rows:
        line = (f"{(r.family or '-'):<18} {(r.timeframe or '-'):<4} {r.price:>9.2f} "
                f"{(f'{r.atr_usd:.2f}' if r.atr_usd is not None else '-'):>7} {r.stop_distance:>7.2f} "
                f"{_fmt_usd(r.min_lot_risk_usd):>13} {_fmt_usd(r.min_equity_strict):>20} {_fmt_usd(r.min_equity_gate):>23}")
        if r.equity is not None and r.actual_risk_fraction is not None:
            line += f"   {r.lots:>13.2f} {r.actual_risk_fraction:>5.2%}  {'ok' if r.allowed else 'refused'}"
        lines.append(line)
    rf = rows[0].risk_fraction if rows else 0.0
    lines.append(f"risk fraction {rf:.2%} per trade; sizing rule only (caps, margin and other gate checks not evaluated)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="run.py sizing-feasibility",
                                 description="Minimum equity at which the minimum lot fits the risk per trade (G-7).")
    ap.add_argument("--price", type=float, help="price; without it, price and ATR come from the store's bars")
    ap.add_argument("--stop-distance", type=float, help="stop distance in $/oz")
    ap.add_argument("--atr", type=float, help="ATR in $/oz (with --stop-atr)")
    ap.add_argument("--stop-atr", type=float, default=None,
                    help="stop in ATRs (with --atr; from the store: override every family's default)")
    ap.add_argument("--risk", type=float, default=None, help="risk per trade (default: settings risk.risk_per_trade)")
    ap.add_argument("--tiny-live", action="store_true", help="use risk.risk_per_trade_tiny_live")
    ap.add_argument("--multiplier", type=float, default=1.0)
    ap.add_argument("--equity", type=float, default=None, help="also report lots and real risk at this equity")
    ap.add_argument("--contract", type=float, default=CONTRACT_OZ)
    ap.add_argument("--min-lot", type=float, default=0.01)
    ap.add_argument("--lot-step", type=float, default=0.01)
    ap.add_argument("--data-root", type=Path, default=None, help="store root (default: settings data_root)")
    ap.add_argument("--timeframes", nargs="*", default=None, help="only these decision timeframes (store mode)")
    ap.add_argument("--settings", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None, help="write the rows as JSON")
    args = ap.parse_args(argv)

    from goldbot.config import load_settings
    settings = load_settings(args.settings) if args.settings else load_settings()
    risk = args.risk if args.risk is not None else (
        settings.risk.risk_per_trade_tiny_live if args.tiny_live else settings.risk.risk_per_trade)
    lot = LotSpec(contract_oz=args.contract, volume_min=args.min_lot, volume_step=args.lot_step)
    rows: list[Feasibility] = []
    if args.price is not None:
        rows.append(min_lot_feasibility(price=args.price, stop_distance=args.stop_distance, atr_usd=args.atr,
                                        stop_atr=args.stop_atr, risk_fraction=risk, equity=args.equity, lot=lot,
                                        multiplier=args.multiplier))
    else:
        from goldbot.specialists import SPECIALISTS
        store = Store(args.data_root if args.data_root is not None else Path(settings.data_root).expanduser())
        cache: dict[str, pd.DataFrame] = {}
        for fam, cls in sorted(SPECIALISTS.items()):
            stop_atr = args.stop_atr if args.stop_atr is not None else float(cls.default_config.get("stop_atr", 1.5))
            for tf in (cls.timeframe, *cls.timeframes):
                if args.timeframes is not None and tf not in args.timeframes:
                    continue
                if tf not in cache:
                    cache[tf] = _last_bars(store, tf)
                if len(cache[tf]) <= ATR_BARS:
                    continue
                rows.append(feasibility_from_bars(cache[tf], stop_atr=stop_atr, risk_fraction=risk, equity=args.equity,
                                                  lot=lot, multiplier=args.multiplier, family=fam, timeframe=tf))
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
