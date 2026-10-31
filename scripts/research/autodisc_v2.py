"""Discovery grammar v2: ONE explore-window run per timeframe (1h, 4h, 1d), frozen in
docs/research/prereg-2026-11-02-discovery-v2.md (ADR 0058 amendment 2026-11-02; kill-rules amendment A4).

    .venv/bin/python scripts/research/autodisc_v2.py --timeframe 1h --bars raw/ \
        --macro ~/tmp/macro-pit/macro/macro_fred.parquet --calendar ~/tmp/ts-data/cal/us_calendar.parquet \
        --state-dir ~/tmp/autodisc-v2

Reads 2010-2017 only: the 1m bars through `windows.ExploreFrame`, the macro and calendar rows cut at the explore cutoff
on available_utc (and value_date / event_utc) inside `PrimitiveProviderV2`. Every spec (search, null, neighbours) is
ledgered before it is evaluated in ONE ledger shared by the three timeframes (`<state-dir>/discovery_real.db`), and the
local registry sidecar is topped up after each run (`k_link.sync_discovery_k`). The report and a JSON summary go to
`<state-dir>/autodisc/`; nothing is committed (no prices: R units, counts and hashes only). One run per timeframe: a
second run of the same timeframe on the same ledger is refused.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import read_bar_files, read_macro_files, read_us_calendar_files  # noqa: E402
from goldbot.execution.costs import settings_swap  # noqa: E402
from goldbot.features.technical import atr  # noqa: E402
from goldbot.research.autodisc.gen_combine import MAX_BARS, existing_family_names  # noqa: E402
from goldbot.research.autodisc.gen_gp import GPConfig  # noqa: E402
from goldbot.research.autodisc.grammar_v2 import PrimitiveProviderV2  # noqa: E402
from goldbot.research.autodisc.k_link import sync_discovery_k  # noqa: E402
from goldbot.research.autodisc.ledger import SearchLedger  # noqa: E402
from goldbot.research.autodisc.nightly import render, search_and_judge  # noqa: E402
from goldbot.research.autodisc.outcomes import build_tensor, v2_menu  # noqa: E402
from goldbot.research.autodisc.windows import ExploreFrame, truncate_to_explore  # noqa: E402
from goldbot.research.registry import TrialRegistry  # noqa: E402

# ---- frozen by the pre-registration (section 3); changing any of these is a new pre-registration and a new K ----
TIMEFRAMES = ("1h", "4h", "1d")
POPULATION, GENERATIONS = 32, 8                 # as v1: at most 288 search specs a timeframe
NULL_REPS, MAX_CANDIDATES = 20, 20              # as v1
SEED = 20261102
NET_R_FLOOR = 0.05                              # absolute: own net mean R at the cost of record >= +0.05 R
# measured cells (scorecard-2026-10.md s.4, ICM all, 1,373 fills, week 2026-10-26..30): round trip $0.208, spread
# $0.085, stop overshoot $0.084 (618 stop exits). Charged per trade on top of the Dukascopy bid/ask in the fills:
# (0.208 - 0.085) slippage + commission, plus the overshoot on EVERY trade (not only stop exits) = $0.207/oz.
EXTRA_COST_USD = 0.207
MAX_NEIGHBOURS = 14                             # 6 ladder leaves x 2 rungs + target 2 (grammar v2 bounds)
K_V2_DECLARED = len(TIMEFRAMES) * (POPULATION * (GENERATIONS + 1) + MAX_CANDIDATES * MAX_NEIGHBOURS)   # 1,704
K_OFFSET = 1264                                 # program K before v2 (scorecard 2026-10-31: ~360 arm ids + v1 864 + 37 screen looks + 1 trial + 2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--timeframe", required=True, choices=TIMEFRAMES)
    ap.add_argument("--bars", required=True)
    ap.add_argument("--macro", required=True)
    ap.add_argument("--calendar", required=True)
    ap.add_argument("--state-dir", required=True)
    a = ap.parse_args()
    tf = a.timeframe
    state = Path(a.state_dir).expanduser()
    out_dir = state / "autodisc"
    out_dir.mkdir(parents=True, exist_ok=True)
    done = out_dir / f"v2-{tf}.json"
    if done.exists():
        raise SystemExit(f"grammar v2 already ran on {tf} ({done}): one run per timeframe (pre-registration s.3)")
    b1 = truncate_to_explore(read_bar_files(Path(a.bars).expanduser(), 2010, 2017), "ts_utc")
    prov = PrimitiveProviderV2(ExploreFrame.from_frame(b1, "ts_utc"),
                               macro=read_macro_files(Path(a.macro).expanduser()),
                               calendar=read_us_calendar_files(Path(a.calendar).expanduser()))
    bars = prov.bars(tf)
    settings = load_settings()
    t = build_tensor(ExploreFrame.from_frame(bars, "ts_utc"), atr(prov.mid_bars(tf), 14), v2_menu(MAX_BARS[tf]),
                     extra_cost_usd=EXTRA_COST_USD, swap=settings_swap(settings))
    fams = tuple(f for f in existing_family_names() if f in prov.families(tf))
    cost_line = (f"measured cells (ICM, 1,373 fills): Dukascopy bid/ask in the fills + {EXTRA_COST_USD:.3f} $/oz "
                 "(slippage + commission 0.123, stop overshoot 0.084 on every trade); swap prior (no measured swap)")
    ledger = SearchLedger(state / "discovery_real.db", mode="real")
    cfg = GPConfig(timeframe=tf, population=POPULATION, generations=GENERATIONS, seed=SEED, families=fams,
                   grammar="v2")
    res = search_and_judge(t, prov, ledger, cfg, reps=NULL_REPS, max_candidates=MAX_CANDIDATES, cost_line=cost_line,
                           night=f"v2-{tf}", k_offset=K_OFFSET, net_r_floor=NET_R_FLOOR, k_declared=K_V2_DECLARED)
    registry = TrialRegistry(str(state / "research_registry.jsonl"))
    batch = sync_discovery_k(registry, ledger, generator=res.generator + " v2", timeframe=tf)
    text = render(res)
    (out_dir / f"survivors-v2-{tf}.md").write_text(text)
    top = sorted(((c.spec_hash, c.fitness) for c in res.rejected), key=lambda x: -x[1])[:5]
    summary = {"timeframe": tf, "families": list(fams), "n_bars": t.n_bars, "k_search_cumulative": res.k_search,
               "k_search_tf": res.k_search_tonight, "k_null": res.k_null, "k_dsr": res.k_dsr,
               "k_v2_declared": K_V2_DECLARED, "evaluated": res.evaluated, "passed_floors": res.passed_floors,
               "null_bar": res.null_threshold, "real_best": res.real_best,
               "real_best_null_percentile": res.real_best_null_percentile,
               "spa_p": None if res.rc is None else res.rc.spa_p, "rc_p": None if res.rc is None else res.rc.rc_p,
               "pbo": None if res.pbo is None else res.pbo.pbo, "batch_ok": res.batch_ok,
               "candidates": res.candidates, "judged": res.judged, "survivors": len(res.survivors),
               "survivor_rows": [s.model_dump(mode="json") for s in res.survivors],
               "explore_failed": [e.model_dump(mode="json") for e in res.explore_failed],
               "rejected_top": top, "k_contrib": 0 if batch is None else int(batch["k_contrib"]),
               "head": list(res.head)}
    done.write_text(json.dumps(summary, indent=1, default=str))
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
