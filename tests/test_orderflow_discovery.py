"""BACKLOG 211 (order-flow part): `research_pass.py --discover --orderflow` puts the order-flow proxy family (R139) into
the feature-discovery screen, with its pre-registration note.

Design rule (BACKLOG 211): "`--orderflow` (the run's own 1m bars) reaches the discovery path through `feature_ctx`, and
its pre-registration requires a survivor to beat the plain return and momentum features in the same screen
(`orderflow.SCREEN_NOTE`), at 15m with the H-14 expected move >= 3x round trip ... Adding the columns costs 0 trials; a
survivor costs 1." The news part stays collect-only (`news_llm.COLLECT_ONLY_MONTHS`): no `--news` flag exists.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.features import orderflow as of_feat
from goldbot.features import registry
from goldbot.research import discovery as disc
from goldbot.research import pipeline
from goldbot.research.discovery import DiscoveryConfig, discovery_pass, orderflow_screen_rule
from goldbot.research.pipeline import research_feature_names
from goldbot.research.registry import TrialRegistry
from goldbot.specialists import SPECIALISTS
from tests.test_count_owed_events import _bars_1m

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def clean_registry():
    feats, signed = dict(registry.FEATURES), dict(registry.SIGNED)
    yield
    registry.FEATURES.clear()
    registry.FEATURES.update(feats)
    registry.SIGNED.clear()
    registry.SIGNED.update(signed)


def _rp() -> ModuleType:
    spec = importlib.util.spec_from_file_location("research_pass_211", ROOT / "scripts" / "research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _args(rp: ModuleType, tmp_path: Path, *extra: str, specialist: str = "session_open") -> argparse.Namespace:
    ap = argparse.ArgumentParser()
    rp._discovery_args(ap)
    args = ap.parse_args(["--discover", *extra])
    args.specialist, args.pooled, args.score_holdout = specialist, "", False
    args.rationale, args.rule_ref, args.report = "211", None, str(tmp_path / "r.md")
    args.bars, args.from_year, args.to_year = str(tmp_path), 2010, 2100
    args.cpcv, args.h16 = None, False
    return args


class _Stop(Exception):
    """Raised by a stand-in, so a test stops before any registry row or look at the data."""


def _capture_discovery_pass(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def fake(spec: Any, b1: pd.DataFrame, reg: Any, **kw: Any) -> Any:
        seen.update(kw, spec=spec, b1=b1)
        raise _Stop
    monkeypatch.setattr(disc, "discovery_pass", fake)
    return seen


# ---------------------------------------------------------------------------------------------- the flag
def test_orderflow_is_refused_outside_the_discovery_screen():
    rp = _rp()
    ns = argparse.Namespace
    for args, flag in ((ns(orderflow=True, discover=False, cpcv=None, h16=False), "--discover"),
                       (ns(orderflow=True, discover=True, cpcv=[3], h16=False), "--cpcv"),
                       (ns(orderflow=True, discover=False, cpcv=None, h16=True), "--h16")):
        why = rp.orderflow_refusal(args)
        assert why is not None and "--orderflow" in why and flag in why
    assert rp.orderflow_refusal(ns(orderflow=True, discover=True, cpcv=None, h16=False)) is None
    assert rp.orderflow_refusal(ns(orderflow=False, discover=False, cpcv=None, h16=False)) is None


def test_there_is_no_news_flag_while_news_llm_is_collect_only():
    rp = _rp()
    ap = argparse.ArgumentParser()
    rp._discovery_args(ap)
    with pytest.raises(SystemExit):
        ap.parse_args(["--discover", "--news", "x.parquet"])


# ---------------------------------------------------------------------------------------------- research_pass wiring
def test_discover_with_orderflow_passes_the_runs_own_1m_bars_the_orderflow_family_and_its_screen_note(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_registry: None):
    rp = _rp()
    seen = _capture_discovery_pass(monkeypatch)
    b1 = _bars_1m("2023-01-02", "2023-01-04")
    settings = load_settings()
    variants = rp.parse_variants("session_open", "[{}]")
    with pytest.raises(_Stop):
        rp._discover(_args(rp, tmp_path, "--orderflow"), variants, b1, TrialRegistry(tmp_path / "r.jsonl"), settings,
                     0.37, None, settings.research.holdout_window(), "priors", None)
    assert (seen["ctx"]["bars_1m"]["ts_utc"].to_numpy() == b1["ts_utc"].to_numpy()).all()
    assert (seen["ctx"]["bars_1m"]["available_utc"] == b1["visible_at"]).all()
    assert seen["feature_names"] == [*research_feature_names(seen["ctx"]), of_feat.NAME]
    assert of_feat.SCREEN_NOTE in seen["notes"]
    assert of_feat.H14_NOTE in seen["notes"]                      # session_open decides on 15m bars


def test_the_h14_note_is_preregistered_only_for_a_15m_discovery():
    assert of_feat.H14_NOTE in of_feat.prereg_notes("15m")
    assert of_feat.prereg_notes("1h") == (of_feat.SCREEN_NOTE,)


def test_discover_without_orderflow_keeps_the_default_features_so_existing_hashes_are_unchanged(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rp = _rp()
    seen = _capture_discovery_pass(monkeypatch)
    b1 = _bars_1m("2023-01-02", "2023-01-04")
    settings = load_settings()
    with pytest.raises(_Stop):
        rp._discover(_args(rp, tmp_path), rp.parse_variants("session_open", "[{}]"), b1,
                     TrialRegistry(tmp_path / "r.jsonl"), settings, 0.37, None, settings.research.holdout_window(),
                     "priors", None)
    assert seen["ctx"] is None and seen["feature_names"] is None and seen["notes"] == ()


# ---------------------------------------------------------------------------------------------- discovery_pass
def test_an_orderflow_discovery_preregisters_the_orderflow_feature_and_its_notes_and_cuts_the_1m_input_at_the_holdout(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clean_registry: None):
    from goldbot.data.orderflow import ctx_frame
    b1 = _bars_1m("2023-01-02", "2023-02-06")
    holdout = (pd.Timestamp("2023-01-30", tz="UTC"), pd.Timestamp("2023-12-31", tz="UTC"))
    names = [*research_feature_names(), of_feat.enable()]
    notes = of_feat.prereg_notes("15m")
    seen: dict[str, Any] = {}

    def stop(bars_dec: pd.DataFrame, context: Any, **kw: Any) -> Any:
        seen.update(kw)
        raise _Stop
    monkeypatch.setattr(pipeline, "lookahead_check", stop)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    with pytest.raises(_Stop):
        discovery_pass(SPECIALISTS["session_open"](), b1, reg, budget_cap=20, reserved_setting=0, cfg=DiscoveryConfig(),
                       holdout=holdout, ctx={"bars_1m": ctx_frame(b1)}, feature_names=names, notes=notes)
    prereg = [r for r in reg._rows() if r.get("status") == "preregistered"]
    assert len(prereg) == 1
    assert prereg[0]["plan"]["features"] == names and prereg[0]["config"]["features"] == names
    assert prereg[0]["plan"]["notes"] == list(notes)
    assert seen["feature_names"] == names
    assert pd.to_datetime(seen["ctx"]["bars_1m"]["available_utc"], utc=True).max() <= holdout[0]


# ---------------------------------------------------------------------------------------------- the reading rule
def _table(rows: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"stability": list(rows.values())}, index=list(rows))


OWNERS = {"of_vw_retz_15m": "orderflow", "of_vw_run": "orderflow", "ret_4": "returns", "h1_ret_4": "returns",
          "tsmom_20": "tsmom", "atr_14": "atr"}


def test_an_orderflow_survivor_that_beats_every_return_and_momentum_column_passes_the_screen_note(
        clean_registry: None):
    table = _table({"of_vw_run": 0.9, "ret_4": 0.6, "h1_ret_4": 0.7, "tsmom_20": 0.8, "atr_14": 1.0})
    rule = orderflow_screen_rule(table, OWNERS, ["of_vw_run", "atr_14"])
    assert rule["passed"] is True
    assert rule["best_orderflow"] == ["of_vw_run", 0.9] and rule["best_plain"] == ["tsmom_20", 0.8]


def test_an_orderflow_survivor_tied_with_or_below_a_return_or_momentum_column_fails_as_a_restated_return(
        clean_registry: None):
    for plain in (0.9, 0.95):
        table = _table({"of_vw_run": 0.9, "ret_4": 0.2, "tsmom_20": plain})
        rule = orderflow_screen_rule(table, OWNERS, ["of_vw_run"])
        assert rule["passed"] is False and "restated return" in rule["detail"]


def test_no_orderflow_survivor_reads_as_not_passed():
    table = _table({"of_vw_run": 0.9, "ret_4": 0.2})
    rule = orderflow_screen_rule(table, OWNERS, ["ret_4"])
    assert rule["passed"] is False and "no order-flow survivor" in rule["detail"]


def test_the_report_prints_the_preregistered_notes_and_the_orderflow_rule():
    lines = disc.screen_rule_lines(list(of_feat.prereg_notes("15m")),
                                   {"passed": False, "detail": "no order-flow survivor"})
    text = "\n".join(lines)
    assert of_feat.SCREEN_NOTE in text and of_feat.H14_NOTE in text
    assert "no order-flow survivor" in text and "FAIL" in text
    assert disc.screen_rule_lines([], None) == []
    assert json.dumps(lines)                                                       # plain strings
