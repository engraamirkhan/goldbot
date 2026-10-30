"""Acceptance 1 (backlog 140): three existing families, re-expressed as (candidate mask, side, exit) on the outcome
tensor, match `research.screen` (via `pipeline.prepare`) on the explore window: events within 1%, net mean R within
0.01 R. Local 2010-2017 bars only (GOLDBOT_RAW_BARS or the repo's gitignored raw/); skipped where they are absent.
The bars are read through `windows.truncate_to_explore`: nothing from 2018 on is ever loaded into the frame."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.release import read_bar_files
from goldbot.data.resample import resample_bars
from goldbot.execution.costs import research_costs
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.research.autodisc import ExploreFrame, truncate_to_explore
from goldbot.research.autodisc import outcomes as oc
from goldbot.research.pipeline import build_decision_frame, prepare, rule_only
from goldbot.specialists import SPECIALISTS

RAW = Path(os.environ.get("GOLDBOT_RAW_BARS", str(Path(__file__).resolve().parents[3] / "raw")))
pytestmark = [pytest.mark.skipif(not (RAW / "xauusd_1m_dukascopy_2017.parquet").exists(),
                                 reason="local 2010-2017 data absent"), pytest.mark.integration]

CASES = [("tsmom", "4h", {"timeframe": "4h"}), ("box_break_close", "15m", {}), ("session_open", "15m", {})]


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return truncate_to_explore(read_bar_files(RAW, 2010, 2017), "ts_utc")


@pytest.mark.parametrize("family,tf,over", CASES)
def test_parity_with_screen(b1: pd.DataFrame, family: str, tf: str, over: dict) -> None:
    extra, swap, _ = research_costs(load_settings(), None)
    spec = SPECIALISTS[family](**over)
    assert spec.timeframe == tf
    b_dec = resample_bars(b1, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    frame = build_decision_frame(b_dec, context)
    prep = prepare(spec, b_dec, context, extra_cost_usd=extra, frame=frame, swap=swap,
                   bars_1m=b1 if spec.needs_bars_1m else None)
    ref = rule_only(prep.gross, prep.labels)["net"]

    m, X = frame
    cands = spec.complete_windows(b_dec, spec.candidates_in_context(m, X, context, None))
    own = spec.barrier_atr(m, context)
    a = atr(m, 14) if own is None else own
    n = len(b_dec)
    mask, side = np.zeros(n, bool), np.zeros(n, np.int8)
    c = cands.drop_duplicates("idx")
    mask[c["idx"].to_numpy()], side[c["idx"].to_numpy()] = True, c["side"].to_numpy()
    exit_ = oc.BarrierExit.from_barrier_spec(spec.label_spec)
    ten = oc.build_tensor(ExploreFrame.from_frame(b_dec, "ts_utc"), a, [exit_], extra_cost_usd=extra, swap=swap,
                          bars_1m_in=ExploreFrame.from_frame(b1, "ts_utc") if spec.needs_bars_1m else None,
                          mode="1m" if spec.barriers_on_1m else "bar")
    got = ten.select(mask, side, 0)
    got_r = float(got.r().mean())
    print(f"PARITY {family} {tf}: screen n={ref['n']} net mean R={ref['mean_r']:+.4f}; "
          f"tensor n={len(got)} net mean R={got_r:+.4f}")
    assert ref["n"] > 0
    assert abs(len(got) - ref["n"]) <= 0.01 * ref["n"]
    assert abs(got_r - ref["mean_r"]) <= 0.01
