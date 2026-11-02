"""Run a batch of pre-registered position specs on one W2 window (goldbot/research/position, amendment W3 + addendum).

    .venv/bin/python scripts/position_screen.py --load-only                       (data coverage; no strategy runs)
    .venv/bin/python scripts/position_screen.py --spec pos1.specs:SPECS --window EXPLORE --out ~/tmp/pos1/explore.json
    .venv/bin/python scripts/position_screen.py --spec pos1.specs:SPECS --window CONFIRM \
        --frozen-prereg docs/research/pos1/prereg.md --out ~/tmp/pos1/confirm.json

`--spec module:attr` names a PositionSpec or a list of them (repeatable); the batch is every spec named. EXPLORE is
LEARN-OLD + LEARN read as one series (W3 addendum ruling 2). Per spec it reports, for each venue (A gates; S, B for
long/flat specs and C are report only), the book and per-market W3 statistics, Y_learn, and on venue A the explore
checks (POS-1 section 4). The JSON holds summary statistics only: no trade rows, no prices.

Fail closed:
* TEST-OLD and CONFIRM are read once: `--frozen-prereg` must be tracked at HEAD in a clean tree (its blob sha is
  recorded), and each (spec, window) is claimed in research.position.read_registry BEFORE the read; a second claim is
  refused for the whole batch;
* BH-FDR runs only at CONFIRM, over the confirm-eligible ids of the committed (re-stamped) table, at q; no other
  window carries a BH verdict;
* without DTB3 at research.position.short_rate_path, financing falls back to the constant only with
  `--allow-constant-rate`, and the output is marked `rate_source: constant` (it cannot gate); an FX pair or non-USD
  index without its currency's 3-month rate is refused;
* a market with no point cost is refused (`costs_for`); nothing at or after 2025-10-01 is read.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from goldbot.config import PositionSettings, load_settings  # noqa: E402
from goldbot.research.position import data, engine, financing, stats  # noqa: E402
from goldbot.research.position.costs import costs_for  # noqa: E402
from goldbot.research.position.signals import PositionSpec  # noqa: E402
from goldbot.research.position.windows import (  # noqa: E402
    COMPOSITES,
    EXPLORE_WINDOWS,
    ONCE_WINDOWS,
    WINDOWS,
    get_windows,
)

WINDOW_CHOICES = sorted({*WINDOWS, *COMPOSITES})


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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def frozen_prereg(root: Path, rel: str | None) -> str:
    """The blob sha of `rel` at HEAD; refused unless it is tracked at HEAD and the working tree is clean."""
    if not rel:
        raise SystemExit("this window is read once: pass --frozen-prereg with the committed pre-registration")
    try:
        _git(root, "ls-files", "--error-unmatch", rel)
        blob = _git(root, "rev-parse", f"HEAD:{rel}")
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit(f"{rel} is not tracked at HEAD: this window is read once, against a committed "
                         "pre-registration") from None
    if _git(root, "status", "--porcelain"):
        raise SystemExit("the working tree is not clean: a read-once window runs only on a committed tree")
    return blob


def eligible_ids(prereg_text: str) -> list[str]:
    """The ids whose row of the committed power table says confirm-eligible (POS-1 section 3, re-stamped)."""
    out = []
    for line in prereg_text.splitlines():
        cells = [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and "confirm-eligible" in cells[-1] and re.fullmatch(r"[\w-]+", cells[0]):
            out.append(cells[0])
    return out


def _matches(spec_id: str, ids: list[str]) -> bool:
    return spec_id in ids or any(spec_id.endswith(f"-{i}") for i in ids)


def claim_reads(registry: Path, specs: list[str], window: str, blob: str) -> None:
    """One marker per (spec, window), written BEFORE the read (a crashed read still counts); refuse any repeat."""
    done = set()
    if registry.exists():
        for line in registry.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                done.add((r["spec"], r["window"]))
    again = [s for s in specs if (s, window) in done]
    if again:
        raise SystemExit(f"{window} was already read for {again}: it is read once")
    registry.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    with registry.open("a") as fh:
        for s in specs:
            fh.write(json.dumps({"spec": s, "window": window, "prereg_blob": blob, "claimed_utc": now}) + "\n")


def load_rates(s: PositionSettings, root: Path, symbols: list[str], allow_constant: bool) -> financing.Rates:
    path = root / s.short_rate_path
    if not path.exists() and not allow_constant:
        raise SystemExit(f"no DTB3 at {path}: financing by date needs it (W3.3); --allow-constant-rate runs on "
                         f"{s.short_rate_constant_pct}% and marks the result ungated")
    usd = financing.load_fred(path) if path.exists() else None
    currency = {c: financing.load_fred(root / p) for c, p in s.currency_rate_paths.items()}
    return financing.build_rates(symbols, usd, currency, constant_pct=s.short_rate_constant_pct)


def _commit() -> str:
    try:
        return _git(ROOT, "rev-parse", "HEAD")
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    ap.add_argument("--raw-dir", default=os.environ.get("GOLDBOT_RAW_BARS") or str(root / "raw"))
    ap.add_argument("--settings", default=str(root / "config" / "settings.yaml"))
    ap.add_argument("--symbols", default="XAUUSD", help="comma separated, for --load-only")
    ap.add_argument("--spec", action="append", default=[], help="module:attr, a PositionSpec or a list (repeatable)")
    ap.add_argument("--window", default="EXPLORE", choices=WINDOW_CHOICES)
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
    blob = None
    if args.window in ONCE_WINDOWS:
        blob = frozen_prereg(root, args.frozen_prereg)
    elif args.window not in EXPLORE_WINDOWS:
        raise SystemExit(f"unknown window {args.window}")
    specs = load_specs(args.spec)
    symbols = sorted({x for sp in specs for x in sp.symbols})
    costs = {x: costs_for(x, s) for x in symbols}
    rates = load_rates(s, root, symbols, args.allow_constant_rate)
    eligible: list[str] | None = None
    if args.window == "CONFIRM":
        assert args.frozen_prereg is not None
        eligible = eligible_ids((root / args.frozen_prereg).read_text())
    if blob is not None:
        claim_reads(root / s.read_registry, [sp.id for sp in specs], args.window, blob)
    panel = data.load_panel(raw, symbols)
    cfg = engine.EngineConfig.from_settings(s)
    windows = get_windows(args.window)

    rows: list[dict[str, Any]] = []
    for sp in specs:
        res = engine.run(sp, panel, windows, cfg=cfg, costs={x: costs[x] for x in sp.symbols},
                         venues=financing.venues_from_settings(s, long_only=sp.long_only), rates=rates,
                         name=args.window)
        a = res.venues["A"].book
        rows.append({"id": sp.id, "rate_source": res.rate_source, "windows_read": list(res.windows_read),
                     "y_learn": res.y_learn, "p_boot": stats.p_one_sided(a.t_boot),
                     "explore_checks": stats.explore_checks(a),
                     "venues": {k: {"venue": v.venue, "gates": v.gates, "book": v.book.model_dump(),
                                    "per_year_net": {str(y): float(x) for y, x in v.daily["net"].groupby(
                                        pd.DatetimeIndex(v.daily.index).year).sum().items()},
                                    "assets": {m: st.model_dump() for m, st in v.assets.items()}}
                                for k, v in res.venues.items()}})
    if eligible is not None:                          # BH only at CONFIRM, over the committed eligible ids
        bh_rows = [r for r in rows if _matches(r["id"], eligible)]
        for r, rej in zip(bh_rows, stats.bh_fdr([r["p_boot"] for r in bh_rows], q=args.q), strict=True):
            r["bh_reject"] = bool(rej)
    out = {"window": args.window, "windows": [w.model_dump(mode="json") for w in windows], "engine_commit": _commit(),
           "q": args.q if eligible is not None else None, "frozen_prereg": args.frozen_prereg, "prereg_blob": blob,
           "bh_eligible": eligible, "costs_bps": {k: v.model_dump() for k, v in costs.items()}, "specs": rows}
    text = json.dumps(out, indent=1, default=str)
    if args.out:
        Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).expanduser().write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
