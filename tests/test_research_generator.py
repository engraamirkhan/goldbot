"""Continuous research loop, phase 1 (docs/plans/continuous-research-loop.md): the candidate generator and the queue
script. The generator never looks at returns; it only drops what has been tried, what cannot trade and what is retired."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.timeutil import epoch_ns
from goldbot.research.director import build_plan
from goldbot.research.generator import Candidate, batch, generate, read_registry, sizing_feasible
from goldbot.research.population import check_founder_config
from goldbot.research.registry import config_hash
from goldbot.risk.sizing import MIN_LOT_TOLERANCE
from goldbot.specialists import SPECIALISTS

NOW = pd.Timestamp("2026-10-18", tz="UTC")
FAMILIES = sorted(SPECIALISTS)
SMALL_ATR = {"15m": 0.5, "1h": 1.0, "4h": 2.0, "1d": 5.0}      # small enough that every stop is sizable at 0.01 lot


def _queue_script():
    path = Path(__file__).resolve().parents[1] / "scripts" / "research_queue.py"
    spec = importlib.util.spec_from_file_location("research_queue", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _plan(rows: list[dict] | None = None):
    return build_plan(NOW, FAMILIES, rows or [], [], [], quarter_budget=20, monthly_total=48,
                      trial_budget_per_month=12, floor=2)


def _gen(rows: list[dict] | None = None, atr: dict[str, float] | None = None, settings=None):
    settings = settings or load_settings()
    return generate(rows or [], settings, _plan(rows), atr or SMALL_ATR)


def _full_hash(c: Candidate) -> str:
    return config_hash(SPECIALISTS[c.family](**c.overrides).config)


def test_grid_is_deterministic_for_the_same_registry_and_plan():
    a, b = _gen(), _gen()
    assert a and [c.model_dump() for c in a] == [c.model_dump() for c in b]
    assert len({(c.family, c.config_hash) for c in a}) == len(a)       # no duplicate inside one grid


def test_every_candidate_passes_check_founder_config():
    cands = _gen()
    assert {c.timeframe for c in cands} >= {"1h", "4h"}                 # several timeframes, not only the default
    for c in cands:
        assert check_founder_config(c.family, c.overrides) == c.overrides


def test_already_tried_config_hashes_are_skipped():
    first = _gen()
    tried = first[0]
    rows = [{"trial": 1, "family": tried.family, "config_hash": _full_hash(tried), "status": "evaluated"}]
    after = _gen(rows)
    assert (tried.family, tried.config_hash) not in {(c.family, c.config_hash) for c in after}
    assert len(after) == len(first) - 1


def test_batch_is_empty_at_zero_remaining_and_exactly_full_at_remaining():
    cands = _gen()
    assert len(cands) > 5
    assert batch(cands, 0) == []
    assert len(batch(cands, 5)) == 5
    assert len(batch(cands, 10_000)) == len(cands)                      # never more than there are candidates
    for job in batch(cands, 5):                                         # research.yml's inputs, one trial each
        assert set(job) >= {"specialist", "variants", "rationale", "from_year", "to_year"}
        assert isinstance(json.loads(job["variants"]), list) and len(json.loads(job["variants"])) == 1


def test_screening_and_retired_families_are_excluded():
    settings = load_settings()
    retired = {r.family for r in settings.research.retired_families}
    screening = {f for f, cls in SPECIALISTS.items() if cls.screening}
    assert retired and screening
    fams = {c.family for c in _gen(settings=settings)}
    assert fams and not fams & (retired | screening)


@pytest.mark.parametrize("scale,kept", [(1.0, True), (1.0 + 1e-6, False)])
def test_min_lot_risk_at_exactly_1_2x_target_is_kept_and_just_above_is_dropped(scale, kept):
    equity, risk_fraction, stop_atr = 1300.0, 0.005, 1.5
    target = equity * risk_fraction
    # 0.01 lot (1 oz) risks the stop distance in dollars; pick the ATR whose stop risks exactly 1.2 x target
    atr = MIN_LOT_TOLERANCE * target / stop_atr * scale
    assert sizing_feasible(stop_atr=stop_atr, atr_usd=atr, equity_usd=equity, risk_fraction=risk_fraction) is kept


def test_no_candidate_window_reaches_the_holdout():
    window = load_settings().research.holdout_window()
    assert window is not None
    start = window[0]
    cands = _gen()
    assert cands
    cut = int(epoch_ns(pd.DatetimeIndex([start]))[0])
    for c in cands:
        assert int(epoch_ns(pd.DatetimeIndex([pd.Timestamp(c.window_to, tz="UTC")]))[0]) <= cut


def test_unreadable_registry_yields_no_batch(tmp_path: Path, capsys):
    bad = tmp_path / "registry.jsonl"
    bad.write_text('{"trial": 1}\nnot json\n')
    with pytest.raises(ValueError):
        read_registry(bad)
    rc = _queue_script().main(["--registry", str(bad), "--atr", json.dumps(SMALL_ATR)])
    assert rc != 0
    assert json.loads(capsys.readouterr().out) == []


def test_candidates_that_cannot_size_the_minimum_lot_or_have_no_atr_are_dropped():
    assert {c.timeframe for c in _gen(atr={"1h": 1.0})} == {"1h"}     # no ATR for a timeframe: dropped, fail closed
    assert _gen(atr={tf: 1000.0 for tf in SMALL_ATR}) == []
