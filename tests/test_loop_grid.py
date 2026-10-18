"""Loop grid v1 (docs/plans/continuous-research-loop.md, "Loop grid v1"; quant review of phase 2): the pre-declared
dimensions (timeframe, session filter, point-in-time regime gate, exits), the point-in-time filters themselves, grid
membership as the loop's refusal rule, `--lot-fit` on every loop trial, "grid exhausted", and the workflow's concurrency."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.config import load_settings
from goldbot.research import cells
from goldbot.research.director import build_plan
from goldbot.research.generator import (
    batch,
    generate,
    grid,
    grid_members,
    grid_sizes,
    loop_refusal,
)
from goldbot.research.registry import config_hash, n_trials_effective, quarter_trials
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import AgentIdentity, Specialist
from goldbot.specialists.loop_filters import REGIME_FILTER, REGIMES, SESSION_FILTER, SESSIONS, apply_filters

ROOT = Path(__file__).resolve().parents[1]
NOW = pd.Timestamp("2026-10-18", tz="UTC")
TFS = ("15m", "1h", "4h", "1d")


def _plan(rows: list[dict[str, Any]] | None = None):
    return build_plan(NOW, sorted(SPECIALISTS), rows or [], [], [], quarter_budget=20, monthly_total=48,
                      trial_budget_per_month=12, floor=2)


def _gen(rows: list[dict[str, Any]] | None = None):
    return generate(rows or [], load_settings(), _plan(rows))


def _cfg(c) -> dict[str, Any]:
    return SPECIALISTS[c.family](**c.overrides).config


# ------------------------------------------------------------------------------------------ the filters
def _features() -> pd.DataFrame:
    return pd.DataFrame({"session_id": [0, 1, 2, 1], "vol_tercile": [0.0, 1.0, 2.0, 0.0],
                         "adx14": [10.0, 30.0, 19.9, 25.0]})


def _all(n: int = 4) -> pd.DataFrame:
    return pd.DataFrame({"idx": np.arange(n), "side": np.ones(n, dtype=int)})


@pytest.mark.parametrize(("session", "kept"), [("asia", [0]), ("london", [1, 3]), ("newyork", [2])])
def test_session_filter_keeps_only_candidates_decided_in_that_session(session, kept):
    out = apply_filters({SESSION_FILTER: session}, _all(), _features())
    assert out["idx"].tolist() == kept


@pytest.mark.parametrize(("regime", "kept"), [("low_vol", [0, 3]), ("not_high_vol", [0, 1, 3]),
                                              ("trending", [1, 3]), ("ranging", [0, 2])])
def test_regime_gate_uses_only_the_decision_bars_own_trailing_features(regime, kept):
    out = apply_filters({REGIME_FILTER: regime}, _all(), _features())
    assert out["idx"].tolist() == kept


def test_both_filters_must_pass_and_no_filter_leaves_candidates_untouched():
    out = apply_filters({SESSION_FILTER: "london", REGIME_FILTER: "trending"}, _all(), _features())
    assert out["idx"].tolist() == [1, 3]
    assert apply_filters({}, _all(), pd.DataFrame(index=range(4))).equals(_all())


def test_a_filter_whose_feature_column_is_missing_keeps_nothing():
    out = apply_filters({REGIME_FILTER: "trending"}, _all(), pd.DataFrame({"session_id": [0, 1, 2, 1]}))
    assert out.empty


def test_unknown_filter_values_are_rejected_by_the_specialist():
    with pytest.raises(ValueError, match="session_filter"):
        SPECIALISTS["tsmom"](AgentIdentity(family="tsmom", config={SESSION_FILTER: "tokyo"}))
    with pytest.raises(ValueError, match="regime_filter"):
        SPECIALISTS["tsmom"](AgentIdentity(family="tsmom", config={REGIME_FILTER: "bull"}))


def test_every_specialists_candidates_pass_through_the_filters_in_every_entry_point():
    class _Everything(Specialist):
        family = "loop_filter_probe"
        timeframe = "1h"
        default_config = {"x": 1}

        def candidates(self, mid_bars, features):
            return _all(len(mid_bars))

        @property
        def label_spec(self):
            raise NotImplementedError

    m = pd.DataFrame(index=range(4))
    assert _Everything().candidates(m, _features())["idx"].tolist() == [0, 1, 2, 3]
    spec = _Everything(AgentIdentity(family="loop_filter_probe", config={SESSION_FILTER: "london"}))
    assert spec.candidates(m, _features())["idx"].tolist() == [1, 3]
    assert spec.candidates_in_context(m, _features(), None)["idx"].tolist() == [1, 3]


def test_a_config_without_filters_keeps_its_hash():
    assert "session_filter" not in SPECIALISTS["tsmom"]().config and "regime_filter" not in SPECIALISTS["tsmom"]().config


# --------------------------------------------------------------------------------------------- the grid
def test_grid_has_every_declared_dimension_for_tsmom_and_is_deterministic():
    g = grid(load_settings())
    assert g == grid(load_settings())
    mine = [c for c in g if c.family == "tsmom"]
    assert {c.timeframe for c in mine} == {"1h", "4h", "1d"}
    for tf in ("1h",):
        sessions = {c.overrides[SESSION_FILTER] for c in mine if c.timeframe == tf and SESSION_FILTER in c.overrides}
        assert sessions == set(SESSIONS)
    for tf in ("1h", "4h", "1d"):
        regimes = {c.overrides[REGIME_FILTER] for c in mine if c.timeframe == tf and REGIME_FILTER in c.overrides}
        assert regimes == set(REGIMES)
        assert any(k in c.overrides for c in mine if c.timeframe == tf for k in ("target_atr", "stop_atr", "max_bars"))
    assert not any(SESSION_FILTER in c.overrides for c in mine if c.timeframe in ("4h", "1d"))   # a session says nothing there


def test_grid_excludes_screening_and_retired_families_and_a_one_at_a_time_dimension_is_never_combined():
    g = grid(load_settings())
    retired = {r.family for r in load_settings().research.retired_families}
    assert not {c.family for c in g} & (retired | {f for f, c in SPECIALISTS.items() if c.screening})
    assert all(sum(k in c.overrides for k in (SESSION_FILTER, REGIME_FILTER, "target_atr", "stop_atr", "max_bars")) <= 1
               for c in g)


def test_grid_sizes_report_every_timeframe_with_zero_for_an_empty_one():
    sizes = grid_sizes(grid(load_settings()), TFS)
    assert set(sizes) == set(TFS) and sizes["1h"] > 0 and sizes["4h"] > 0 and sizes["1d"] > 0


def test_generate_drops_what_the_registry_holds_but_the_grid_membership_does_not_shrink():
    g = grid(load_settings())
    first = _gen()[0]
    rows = [{"trial": 1, "ts": "2026-10-18T10:00:00+00:00", "status": "evaluated", "family": first.family,
             "config_hash": first.config_hash}]
    assert first.config_hash not in {c.config_hash for c in _gen(rows)}
    assert len(_gen(rows)) == len(_gen()) - 1 and grid(load_settings()) == g
    assert (first.family, first.config_hash) in grid_members(load_settings())


def test_generation_does_not_use_atr_or_the_sizing_filter():
    import inspect
    assert "atr" not in inspect.signature(generate).parameters
    assert not hasattr(load_settings().research, "loop_equity_usd")



# --------------------------------------------------------------------------------- refusal rules (C1)
def _job():
    c = _gen()[0]
    return c.family, _cfg(c)


def test_loop_refuses_a_config_that_is_not_a_member_of_the_grid():
    family, cfg = _job()
    assert loop_refusal([], [(family, cfg)], load_settings(), lot_fit=True) is None
    odd = {**cfg, "stop_atr": 1.3}
    assert "grid" in (loop_refusal([], [(family, odd)], load_settings(), lot_fit=True) or "")


def test_loop_refuses_skip_screen_and_a_run_without_lot_fit():
    family, cfg = _job()
    assert "skip" in (loop_refusal([], [(family, cfg)], load_settings(), lot_fit=True, skip_screen=True) or "")
    assert "lot-fit" in (loop_refusal([], [(family, cfg)], load_settings(), lot_fit=False) or "")


def test_loop_cells_run_with_lot_fit():
    plan = cells.plan({"BATCH": json.dumps(batch(_gen(), 1))})
    assert "--lot-fit" in cells.argv(plan[0])
    hand = cells.plan({"SPECIALIST": "tsmom", "VARIANTS": "[{}]"})
    assert "--lot-fit" not in cells.argv(hand[0])


# ----------------------------------------------------------------------------------- grid exhausted
def _queue():
    spec = importlib.util.spec_from_file_location("research_queue", ROOT / "scripts" / "research_queue.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_queue_prints_the_grid_size_per_timeframe_and_a_batch(tmp_path, capsys):
    rc = _queue().main(["--registry", str(tmp_path / "none.jsonl")])
    cap = capsys.readouterr()
    assert rc == 0 and json.loads(cap.out)
    assert all(f"{tf}=" in cap.err for tf in TFS)


def test_queue_says_grid_exhausted_with_exit_code_3_instead_of_an_empty_batch_when_every_member_is_tried(tmp_path, capsys):
    reg = tmp_path / "registry.jsonl"
    reg.write_text("".join(json.dumps({"trial": i, "ts": "2026-10-18T10:00:00+00:00", "status": "evaluated",
                                       "family": c.family, "config_hash": c.config_hash}) + "\n"
                           for i, c in enumerate(grid(load_settings()), 1)))
    rc = _queue().main(["--registry", str(reg)])
    cap = capsys.readouterr()
    assert rc == 3 and json.loads(cap.out) == [] and "grid exhausted" in cap.err


# --------------------------------------------------------------------------------------- the workflow (C2)
def _workflow() -> dict[Any, Any]:
    return yaml.safe_load((ROOT / ".github/workflows/research-loop.yml").read_text())


def test_loop_workflow_does_not_join_the_registry_group_and_uses_its_own():
    wf = _workflow()
    assert wf["concurrency"] == {"group": "research-loop", "cancel-in-progress": False}
    assert all("research-registry" not in json.dumps(j.get("concurrency", "")) for j in wf["jobs"].values())


def test_loop_workflow_reports_an_exhausted_grid_with_issues_write_in_its_own_job_only():
    jobs = _workflow()["jobs"]
    assert jobs["exhausted"]["permissions"] == {"contents": "read", "issues": "write"}
    assert "issues" not in jobs["dispatch"]["permissions"] and "actions" not in jobs["exhausted"]["permissions"]


# ------------------------------------------------------- end to end: research_pass --rule-ref loop (C3)
@pytest.mark.integration
class TestResearchPassLoop:
    @pytest.fixture(scope="class")
    @classmethod
    def release_dir(cls, tmp_path_factory) -> Path:
        from goldbot.data.quality import check_bars
        from goldbot.data.resample import ticks_to_1m
        from goldbot.data.synthetic import synthetic_ticks
        d = tmp_path_factory.mktemp("release")
        b1, _ = check_bars(ticks_to_1m(synthetic_ticks("2022-10-01", "2025-10-01", ticks_per_minute=1, seed=11)))
        b1["source"] = "synthetic"
        years = pd.DatetimeIndex(b1["ts_utc"]).year
        for y in sorted(set(years)):
            b1[years == y].to_parquet(d / f"xauusd_1m_dukascopy_{y}.parquet", index=False)
        return d

    @staticmethod
    def _rp():
        spec = importlib.util.spec_from_file_location("research_pass", ROOT / "scripts" / "research_pass.py")
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def _argv(self, release_dir, tmp_path, *extra: str, variants: str | None = None) -> list[str]:
        member = next(c for c in grid(load_settings()) if c.family == "tsmom" and c.timeframe == "1h"
                      and REGIME_FILTER in c.overrides)
        v = variants if variants is not None else json.dumps([member.overrides])
        return ["research_pass.py", "--bars", str(release_dir), "--registry", str(tmp_path / "registry.jsonl"),
                "--report", str(tmp_path / "report.md"), "--specialist", "tsmom", "--variants", v,
                "--rule-ref", "loop", *extra]

    def test_refusals_then_a_loop_trial_is_recorded_charged_to_the_loop_and_counted_in_k(
            self, release_dir, tmp_path, monkeypatch):
        rp = self._rp()
        registry = tmp_path / "registry.jsonl"
        for extra, variants, why in [
                ((), None, "lot-fit"),                                         # every loop trial runs --lot-fit
                (("--lot-fit", "--skip-screen"), None, "skip"),
                (("--lot-fit",), '[{"stop_atr": 1.3}]', "grid"),               # not a member of the grid
                (("--lot-fit", "--score-holdout"), None, "holdout"),
                (("--lot-fit", "--discover"), None, "discovery|walk-forward")]:
            monkeypatch.setattr(sys, "argv", self._argv(release_dir, tmp_path, *extra, variants=variants))
            with pytest.raises(SystemExit, match=why):
                rp.main()
            assert not registry.exists() or not registry.read_text().strip()
        monkeypatch.setattr(sys, "argv", self._argv(release_dir, tmp_path, "--lot-fit"))
        assert rp.main() == 0
        rows = [json.loads(line) for line in registry.read_text().splitlines()]
        assert len(rows) == 1 and rows[0]["rule_ref"] == "loop" and rows[0]["config"][REGIME_FILTER] in REGIMES
        assert quarter_trials(rows, rows[0]["budget_quarter"]) == 0           # not charged to the quarter
        assert n_trials_effective(rows) == 1                                  # but in K
        if rows[0]["status"] != "screened":
            assert "lot_fit" in rows[0]["results"]                            # account fit reported
        monkeypatch.setattr(sys, "argv", self._argv(release_dir, tmp_path, "--lot-fit"))
        with pytest.raises(SystemExit, match="already"):                      # the same trial is never run twice
            rp.main()
        assert config_hash(rows[0]["config"]) == rows[0]["config_hash"]
