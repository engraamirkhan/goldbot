"""Run a batch of pre-registered position specs on one W2 window (goldbot/research/position, amendment W3).

    .venv/bin/python scripts/position_screen.py --load-only                       (data coverage; no strategy runs)
    .venv/bin/python scripts/position_screen.py --spec pos1.specs:SPECS --window LEARN --out ~/tmp/pos1/learn.json
    .venv/bin/python scripts/position_screen.py --spec pos1.specs:SPECS --window CONFIRM \
        --frozen-prereg docs/research/pos1/prereg.md --out ~/tmp/pos1/confirm.json

`--spec module:attr` names a PositionSpec or a list of them (repeatable); the batch is every spec named. Per spec it
reports, for each venue (A gates; B for long/flat specs and C are report only), the book and per-market W3 statistics,
and on venue A the explore checks (POS-1 section 4). BH-FDR at q runs across the batch on venue A's one-sided
block-bootstrap p. The JSON holds summary statistics only: no trade rows, no prices.

Refusals (fail closed):
* TEST-OLD and CONFIRM are read once, so they need `--frozen-prereg` naming a committed pre-registration;
* without DTB3 at research.position.short_rate_path, financing falls back to the constant only with
  `--allow-constant-rate`, and the output is marked `rate_source: constant` (it cannot gate);
* a market with no point cost is refused (`costs_for`); nothing at or after 2025-10-01 is read (the loaders cut and
  assert, the engine re-asserts).
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from goldbot.config import load_settings  # noqa: E402
from goldbot.research.position import data, engine, financing, stats  # noqa: E402
from goldbot.research.position.costs import costs_for  # noqa: E402
from goldbot.research.position.signals import PositionSpec  # noqa: E402
from goldbot.research.position.windows import EXPLORE_WINDOWS, WINDOWS, get_window  # noqa: E402


def load_specs(names: list[str]) -> list[PositionSpec]:
    out: list[PositionSpec] = []
    for name in names:
        mod, _, attr = name.partition(":")
        obj = getattr(importlib.import_module(mod), attr)
        items = obj if isinstance(obj, list | tuple) else [obj]
        for it in items:
            if not isinstance(it, PositionSpec):
                raise TypeError(f"{name} holds {type(it).__name__}, not a PositionSpec")
            out.append(it)
    ids = [s.id for s in out]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate spec ids in the batch: {ids}")
    return out


def load_rates(path: Path, allow_constant: bool, constant_pct: float) -> financing.Rates:
    if path.exists():
        obs = pd.read_parquet(path, columns=["date", "rate_pct"])
        return financing.Rates(series=financing.rate_frame(obs), constant_pct=constant_pct)
    if not allow_constant:
        raise SystemExit(f"no DTB3 at {path}: financing by date needs it (W3.3); --allow-constant-rate runs on "
                         f"{constant_pct}% and marks the result ungated")
    return financing.Rates(constant_pct=constant_pct)


def _commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--raw-dir", default=os.environ.get("GOLDBOT_RAW_BARS") or str(ROOT / "raw"))
    ap.add_argument("--settings", default=str(ROOT / "config" / "settings.yaml"))
    ap.add_argument("--symbols", default="XAUUSD", help="comma separated, for --load-only")
    ap.add_argument("--spec", action="append", default=[], help="module:attr, a PositionSpec or a list (repeatable)")
    ap.add_argument("--window", default="LEARN", choices=sorted(WINDOWS))
    ap.add_argument("--frozen-prereg", help="the committed pre-registration (required for TEST-OLD and CONFIRM)")
    ap.add_argument("--allow-constant-rate", action="store_true")
    ap.add_argument("--q", type=float, default=0.10)
    ap.add_argument("--load-only", action="store_true", help="load the panel and print its coverage; run nothing")
    ap.add_argument("--out")
    args = ap.parse_args(argv)

    s = load_settings(args.settings).research.position
    raw = Path(args.raw_dir)
    if args.load_only:
        panel = data.load_panel(raw, [x.strip() for x in args.symbols.split(",") if x.strip()])
        for sym, df in panel.items():
            print(f"{sym}: {len(df)} days {df.index.min().date()} .. {df.index.max().date()}")
        return 0
    if not args.spec:
        ap.error("--spec is required unless --load-only")
    if args.window not in EXPLORE_WINDOWS:
        if not args.frozen_prereg or not (ROOT / args.frozen_prereg).is_file():
            raise SystemExit(f"{args.window} is read once: pass --frozen-prereg with the committed pre-registration")
    window = get_window(args.window)
    specs = load_specs(args.spec)
    symbols = sorted({x for sp in specs for x in sp.symbols})
    panel = data.load_panel(raw, symbols)
    costs = {x: costs_for(x, s) for x in symbols}
    rates = load_rates(ROOT / s.short_rate_path, args.allow_constant_rate, s.short_rate_constant_pct)
    cfg = engine.EngineConfig.from_settings(s)

    rows: list[dict[str, Any]] = []
    for sp in specs:
        res = engine.run(sp, panel, window, cfg=cfg, costs={x: costs[x] for x in sp.symbols},
                         venues=financing.venues_from_settings(s, long_only=sp.long_only), rates=rates)
        a = res.venues["A"].book
        rows.append({"id": sp.id, "rate_source": res.rate_source,
                     "p_boot": stats.p_one_sided(a.t_boot), "explore_checks": stats.explore_checks(a),
                     "venues": {k: {"venue": v.venue, "gates": v.gates, "book": v.book.model_dump(),
                                    "per_year_net": {str(y): float(x) for y, x in
                                                     v.daily["net"].groupby(pd.DatetimeIndex(v.daily.index).year).sum().items()},
                                    "assets": {m: st.model_dump() for m, st in v.assets.items()}}
                                for k, v in res.venues.items()}})
    rejected = stats.bh_fdr([r["p_boot"] for r in rows], q=args.q)
    for r, rej in zip(rows, rejected, strict=True):
        r["bh_reject"] = bool(rej)
    out = {"window": window.model_dump(mode="json"), "engine_commit": _commit(), "q": args.q,
           "frozen_prereg": args.frozen_prereg, "costs_bps": {k: v.model_dump() for k, v in costs.items()},
           "specs": rows}
    text = json.dumps(out, indent=1, default=str)
    if args.out:
        Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).expanduser().write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
