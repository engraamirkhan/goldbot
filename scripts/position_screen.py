"""Run a batch of pre-registered position specs on one W2 window (goldbot/research/position, amendment W3 + addendum).

    .venv/bin/python scripts/position_screen.py --load-only                       (data coverage; no strategy runs)
    .venv/bin/python scripts/position_screen.py --spec pos1.specs:SPECS --window EXPLORE --out ~/tmp/pos1/explore.json
    .venv/bin/python scripts/position_screen.py --write-confirm-set docs/research/pos1/confirm-set.json \
        --from-explore ~/tmp/pos1/explore.json --frozen-prereg docs/research/pos1/prereg.md [--ensemble ID ...]
    (commit the confirm set, then)
    .venv/bin/python scripts/position_screen.py --spec pos1.specs:CONFIRM_SPECS --window CONFIRM \
        --frozen-prereg docs/research/pos1/prereg.md --confirm-set docs/research/pos1/confirm-set.json \
        --out ~/tmp/pos1/confirm.json

`--spec module:attr` names a PositionSpec or a list of them (repeatable); the batch is every spec named. EXPLORE is
LEARN-OLD + LEARN read as one series (W3 addendum ruling 2). Per spec it reports, for each venue (A gates; S, B for
long/flat specs and C are report only), the book and per-market W3 statistics, Y_learn, and on venue A the explore
checks (POS-1 section 4). The JSON holds summary statistics only: no trade rows, no prices.

Fail closed:
* TEST-OLD and CONFIRM are read once: `--frozen-prereg` must be tracked at HEAD in a clean tree (its blob sha is
  recorded), and each (spec, window) is claimed in research.position.read_registry BEFORE the read; a second claim is
  refused for the whole batch;
* EXPLORE runs only with `--frozen-prereg` tracked at HEAD in a clean tree and records its blob, because it feeds the
  confirm set (LEARN and LEARN-OLD alone are development reads and cannot);
* the confirm set is fixed between EXPLORE and CONFIRM (POS-1 section 4: "m = the eligible explore passers plus any
  eligible C1 ensembles, fixed before the run"): `--write-confirm-set` takes the explore JSON and the committed
  prereg's confirm-eligible ids (status cell exactly "confirm-eligible"), keeps the eligible ids that passed EVERY
  explore check on a DTB3-financed run, adds the named ensembles (each needs its own committed "confirm-eligible"
  row), refuses if the prereg blob differs from the one EXPLORE recorded, records the explore JSON's sha256, and
  writes the set to commit;
* CONFIRM refuses unless `--confirm-set` is tracked at HEAD and the batch ids equal that set exactly (no subset, no
  extra); BH-FDR at q runs over exactly that set, and no other window carries a BH verdict;
* without DTB3 at research.position.short_rate_path, financing falls back to the constant only with
  `--allow-constant-rate`, and the output is marked `rate_source: constant` (it cannot gate); an FX pair or non-USD
  index without its currency's 3-month rate is refused;
* a market with no point cost is refused (`costs_for`); nothing at or after 2025-10-01 is read.
"""
from __future__ import annotations

import argparse
import hashlib
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
    return committed(root, rel, "pre-registration (this window is read once)")


def eligible_ids(prereg_text: str) -> list[str]:
    """The ids whose row of the committed power table says confirm-eligible (POS-1 section 3, re-stamped)."""
    out = []
    for line in prereg_text.splitlines():
        cells = [c.strip().strip("*").strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[-1] == "confirm-eligible" and re.fullmatch(r"[\w-]+", cells[0]):
            out.append(cells[0])
    return out


def _matches(spec_id: str, ids: list[str]) -> bool:
    return spec_id in ids or any(spec_id.endswith(f"-{i}") for i in ids)


def confirm_set(explore: dict[str, Any], eligible: list[str], ensembles: list[str]) -> dict[str, Any]:
    """The confirm set from an EXPLORE result: eligible ids that passed every explore check on a DTB3-financed run (a
    constant-rate run cannot gate), plus the named ensembles, each of which must have its own committed section 3 row
    with the exact status "confirm-eligible". Refused for any other window."""
    if explore.get("window") != "EXPLORE":
        raise SystemExit(f"the confirm set comes from the EXPLORE read, not {explore.get('window')!r}")
    unknown = [e for e in ensembles if not _matches(e, eligible)]
    if unknown:
        raise SystemExit(f"ensembles {unknown} have no committed section 3 row with the status confirm-eligible")
    passed = [r["id"] for r in explore["specs"] if _matches(r["id"], eligible) and r["rate_source"] == "DTB3"
              and all(r["explore_checks"].values())]
    ids = passed + [e for e in ensembles if e not in passed]
    return {"window_read": "EXPLORE", "engine_commit": explore.get("engine_commit"), "eligible": eligible,
            "explore_passers": passed, "ensembles": list(ensembles), "ids": ids, "m": len(ids)}


def build_confirm_set(root: Path, prereg: str, explore_path: Path, ensembles: list[str]) -> dict[str, Any]:
    """W3 addendum ruling 17 (R-1): the set binds to the EXPLORE read it came from. The pre-registration must be
    committed in a clean tree with the SAME blob the EXPLORE JSON recorded (it did not change in between); its
    eligible ids and the ensembles' rows are read from that blob at HEAD; the explore JSON's sha256 is recorded."""
    blob = committed(root, prereg, "pre-registration")
    raw = explore_path.read_bytes()
    explore = json.loads(raw)
    if explore.get("prereg_blob") != blob:
        raise SystemExit(f"the pre-registration changed between EXPLORE ({explore.get('prereg_blob')}) and the "
                         f"confirm set ({blob}): refused")
    cs = confirm_set(explore, eligible_ids(_git(root, "show", f"HEAD:{prereg}")), ensembles)
    cs["prereg_blob"] = blob
    cs["explore_sha256"] = hashlib.sha256(raw).hexdigest()
    return cs


def committed(root: Path, rel: str, what: str) -> str:
    """The blob sha of `rel` at HEAD, refused unless tracked at HEAD in a clean tree."""
    try:
        _git(root, "ls-files", "--error-unmatch", rel)
        blob = _git(root, "rev-parse", f"HEAD:{rel}")
    except (OSError, subprocess.CalledProcessError):
        raise SystemExit(f"{rel} is not tracked at HEAD: the {what} must be committed before the read") from None
    if _git(root, "status", "--porcelain"):
        raise SystemExit("the working tree is not clean: a read-once window runs only on a committed tree")
    return blob


def claim_reads(registry: Path, specs: list[str], window: str, blob: str) -> None:
    """One marker per (spec, window), written BEFORE the read (a crashed read still counts); refuse any repeat."""
    if os.environ.get("PYTEST_CURRENT_TEST") and registry.resolve().is_relative_to(ROOT / "state"):
        # 2026-11-06: a test fixture's claims leaked into the real registry and marked POS1-001 as read in CONFIRM
        raise SystemExit(f"refusing to claim reads in the real registry {registry} from a test run")
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
    needed = sorted(({c for x in symbols for c in financing.FX_PAIRS.get(x, ())}
                     | {financing.INDEX_HOME[x] for x in symbols if x in financing.INDEX_HOME}) - {"USD"})
    currency = {}
    for c in needed:
        f = root / s.currency_rate_paths[c] if c in s.currency_rate_paths else None
        if f is None or not f.exists():
            raise KeyError(f"no {c} 3-month rate file ({f}): refused, never financed at the USD rate")
        currency[c] = financing.load_fred(f)
    return financing.build_rates(symbols, usd, currency, constant_pct=s.short_rate_constant_pct)


def _extra(net: pd.Series) -> dict[str, float]:
    """Report-only: daily profit factor, max drawdown (equity units, no compounding) and the net without 2025 (W2)."""
    pos, neg = float(net[net > 0].sum()), float(-net[net < 0].sum())
    cum = net.cumsum()
    yr = pd.DatetimeIndex(net.index).year
    return {"pf_daily": pos / neg if neg > 0 else float("inf"), "max_dd": float((cum.cummax() - cum).max()),
            "net_ex2025": float(net[yr != 2025].sum())}


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
    ap.add_argument("--confirm-set", help="the committed confirm set (required for CONFIRM)")
    ap.add_argument("--write-confirm-set", help="write the confirm set from --from-explore and exit")
    ap.add_argument("--from-explore", help="the EXPLORE result JSON the confirm set is taken from")
    ap.add_argument("--ensemble", action="append", default=[], help="an eligible C1 ensemble id (repeatable)")
    ap.add_argument("--out")
    ap.add_argument("--daily-out", help="local dir (never committed) for each spec's venue-A book daily P&L parquet")
    args = ap.parse_args(argv)

    s = load_settings(args.settings).research.position
    raw = Path(args.raw_dir)
    if args.load_only:
        panel = data.load_panel(raw, [x.strip() for x in args.symbols.split(",") if x.strip()])
        for sym, df in panel.items():
            print(f"{sym}: {len(df)} days {df.index.min().date()} .. {df.index.max().date()}")
        return 0
    if args.write_confirm_set:
        if not args.from_explore or not args.frozen_prereg:
            ap.error("--write-confirm-set needs --from-explore and --frozen-prereg")
        cs = build_confirm_set(root, args.frozen_prereg, Path(args.from_explore).expanduser(), args.ensemble)
        Path(args.write_confirm_set).write_text(json.dumps(cs, indent=1) + "\n")
        print(f"confirm set m = {cs['m']}: {cs['ids']} -> {args.write_confirm_set} (commit it before CONFIRM)")
        return 0
    if not args.spec:
        ap.error("--spec is required unless --load-only")
    blob = None
    if args.window in ONCE_WINDOWS or args.window == "EXPLORE":   # EXPLORE feeds the confirm set: bound to HEAD
        blob = frozen_prereg(root, args.frozen_prereg)
    elif args.window not in EXPLORE_WINDOWS:
        raise SystemExit(f"unknown window {args.window}")
    specs = load_specs(args.spec)
    symbols = sorted({x for sp in specs for x in sp.symbols})
    costs = {x: costs_for(x, s) for x in symbols}
    rates = load_rates(s, root, symbols, args.allow_constant_rate)
    eligible: list[str] | None = None
    set_blob = None
    if args.window == "CONFIRM":
        if not args.confirm_set:
            raise SystemExit("CONFIRM needs --confirm-set: the set fixed after EXPLORE and committed")
        set_blob = committed(root, args.confirm_set, "confirm set")
        eligible = list(json.loads((root / args.confirm_set).read_text())["ids"])
        batch = [sp.id for sp in specs]
        if sorted(batch) != sorted(eligible):
            raise SystemExit(f"the CONFIRM batch {sorted(batch)} is not exactly the committed confirm set "
                             f"{sorted(eligible)}: BH runs over that set, no subset and no extra")
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
        if args.daily_out:
            Path(args.daily_out).expanduser().mkdir(parents=True, exist_ok=True)
            res.venues["A"].daily.to_parquet(Path(args.daily_out).expanduser() / f"{sp.id}_{args.window}.parquet")
        rows.append({"id": sp.id, "rate_source": res.rate_source, "windows_read": list(res.windows_read),
                     "y_learn": res.y_learn, "p_boot": stats.p_one_sided(a.t_boot),
                     "explore_checks": stats.explore_checks(a),
                     "venues": {k: {"venue": v.venue, "gates": v.gates, "book": v.book.model_dump(),
                                    **_extra(v.daily["net"]),
                                    "per_year_net": {str(y): float(x) for y, x in v.daily["net"].groupby(
                                        pd.DatetimeIndex(v.daily.index).year).sum().items()},
                                    "assets": {m: st.model_dump() for m, st in v.assets.items()}}
                                for k, v in res.venues.items()}})
    if eligible is not None:                          # BH only at CONFIRM, over exactly the committed confirm set
        for r, rej in zip(rows, stats.bh_fdr([r["p_boot"] for r in rows], q=args.q), strict=True):
            r["bh_reject"] = bool(rej)
    out = {"window": args.window, "windows": [w.model_dump(mode="json") for w in windows], "engine_commit": _commit(),
           "q": args.q if eligible is not None else None, "frozen_prereg": args.frozen_prereg, "prereg_blob": blob,
           "bh_set": eligible, "confirm_set": args.confirm_set, "confirm_set_blob": set_blob,
           "costs_bps": {k: v.model_dump() for k, v in costs.items()}, "specs": rows}
    text = json.dumps(out, indent=1, default=str)
    if args.out:
        Path(args.out).expanduser().parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).expanduser().write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
