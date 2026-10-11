"""BACKLOG 50 (quant review F2 of BACKLOG 47): a queued row's run must not depend on how far data-v1 reaches.

* `--discover` cuts the 1m bars at research.holdout_from before anything is built, as `--h16` does, so the hashed
  `bars_to` is before the holdout whatever the release holds;
* the pre-registration writer records the data-v1 yearly parquet sha256 digests of the research window in every queued
  row's plan, and the runners (research_pass.py, `--discover`) verify them before any look;
* a run whose family and agent have a pending queued row of the quarter but whose config hash differs is refused
  instead of silently falling back to the open budget."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data.release import bar_digest_refusal, bar_file_digests
from goldbot.research import director
from goldbot.research import registry as registry_mod
from goldbot.research.discovery import H02_CLUSTERS, DiscoveryConfig, discovery_pass
from goldbot.research.preregistration import QuarterSpec, plan_quarter, write_quarter
from goldbot.research.registry import QueuedConfigMismatch, TrialRegistry
from goldbot.specialists import SPECIALISTS
from tests.test_preregister_quarter import BEFORE, DOC_TEXT, ROOT, Q, _clock, _complete_spec, _research

IN_QUARTER = datetime(2027, 1, 5, tzinfo=timezone.utc)
_HOLDOUT = load_settings().research.holdout_from
assert _HOLDOUT is not None
HOLDOUT_FROM = _HOLDOUT
FAMILY_D = "discovery_tsmom"


def _release(folder: Path, years: range = range(2010, 2027)) -> Path:
    """Stand-in data-v1 yearly files (only their bytes matter to a digest)."""
    folder.mkdir(parents=True, exist_ok=True)
    for y in years:
        (folder / f"xauusd_1m_dukascopy_{y}.parquet").write_bytes(f"bars of {y}".encode())
    return folder


LATER_FROM = "2015-01-04 23:00:00+00:00"               # a discovery row whose bars start after data-v1's first year


def _later_spec() -> dict[str, Any]:
    """The test spec with discovery D's bars starting in 2015."""
    spec = _complete_spec(reservation=4)
    spec["rows"][0] = {**spec["rows"][0], "bars_from": LATER_FROM}
    return spec


def _written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bars: Path,
             spec: dict[str, Any] | None = None) -> TrialRegistry:
    """The test spec's queued rows (discovery D, H-01) written with the research window's digests of `bars`."""
    _clock(monkeypatch, BEFORE)
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    plan = plan_quarter(QuarterSpec.model_validate(spec or _complete_spec(reservation=4)), research=_research(4),
                        doc_text=DOC_TEXT, quarter=Q,
                        data_digests=bar_file_digests(bars, to_year=HOLDOUT_FROM.year))
    write_quarter(reg, plan)
    return reg


def _research_pass(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    spec = importlib.util.spec_from_file_location("research_pass_b50", ROOT / "scripts/research_pass.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    real_quarter_of = registry_mod.quarter_of
    monkeypatch.setattr(registry_mod, "quarter_of", lambda ts=None: real_quarter_of(ts or IN_QUARTER))
    monkeypatch.setattr(mod, "quarter_of", lambda ts=None: real_quarter_of(ts or IN_QUARTER))
    return mod


class _Linked(Exception):
    """Raised right after the budget check, so a test stops before any data is read."""


def _stop_at_link(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    def stop(prereg: dict[str, Any] | None, rule_ref: str | None, *, capped: bool) -> str | None:
        seen["prereg"] = prereg
        raise _Linked
    monkeypatch.setattr(director, "cited_rule", stop)
    return seen


def _discover(rp: ModuleType, tmp_path: Path, reg: TrialRegistry, bars: Path, b1: pd.DataFrame,
              discovery: dict[str, Any] | None = None, from_year: int = 2010) -> None:
    ap = argparse.ArgumentParser()
    rp._discovery_args(ap)
    cfg = discovery or {"survivor_unit": "feature", "group_top_k": 3, "clusters": H02_CLUSTERS}
    args = ap.parse_args(["--discover", "--families", "cluster", "--discover-config", json.dumps(cfg)])
    args.specialist, args.pooled, args.score_holdout = "tsmom", "", False
    args.rationale, args.rule_ref, args.report = "D", None, str(tmp_path / "r.md")
    args.bars, args.from_year, args.to_year = str(bars), from_year, 2100
    variants = rp.parse_variants("tsmom", '[{"timeframe": "4h", "max_bars": 12}]')
    settings = load_settings()
    rp._discover(args, variants, b1, reg, settings, 0.37, None, settings.research.holdout_window(), "priors",
                 pd.DataFrame({"present": [1.0]}))


def _b1_past_the_holdout() -> pd.DataFrame:
    """The spec's first and last research bar, then bars in and after the holdout (data-v1 has grown since)."""
    return pd.DataFrame({"ts_utc": pd.to_datetime(["2010-01-03 23:00:00", "2017-06-01 00:00:00", "2025-09-30 20:59:00",
                                                   "2025-10-01 00:00:00", "2026-10-05 12:00:00"], utc=True)})


# ---------------------------------------------------------------------------------------------- the holdout cut
def test_discover_cuts_the_bars_at_holdout_from_so_bars_to_is_before_it_whatever_the_data_reaches(tmp_path: Path):
    holdout = load_settings().research.holdout_window()
    assert holdout is not None
    reg = TrialRegistry(tmp_path / "r.jsonl")
    seen: dict[str, Any] = {}

    def capture(jobs: Any, *a: Any, **k: Any) -> Any:
        seen["config"] = jobs[0][1]
        raise _Linked
    reg.check_budget_reserved = capture                                     # type: ignore[method-assign]
    for last in ("2025-10-01 00:00:00", "2026-03-01 00:00:00", "2026-10-05 12:00:00"):
        b1 = pd.DataFrame({"ts_utc": pd.to_datetime(["2010-01-03 23:00:00", "2025-09-30 20:59:00", last], utc=True)})
        with pytest.raises(_Linked):
            discovery_pass(SPECIALISTS["tsmom"](timeframe="4h", max_bars=12), b1, reg, budget_cap=20,
                           reserved_setting=0, cfg=DiscoveryConfig(group_top_k=3), holdout=holdout)
        assert pd.Timestamp(seen["config"]["bars_to"]) < holdout[0], last
        assert seen["config"]["bars_to"] == "2025-09-30 20:59:00+00:00"


def test_a_written_discovery_row_still_links_after_data_v1_grows_past_the_holdout(tmp_path: Path,
                                                                                 monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars)
    written = reg._rows()[0]
    (bars / "xauusd_1m_dukascopy_2026.parquet").write_bytes(b"2026 with October bars")   # the weekly refresh
    rp = _research_pass(monkeypatch)
    seen = _stop_at_link(monkeypatch)
    with pytest.raises(_Linked):
        _discover(rp, tmp_path, reg, bars, _b1_past_the_holdout())
    assert seen["prereg"] is not None and seen["prereg"]["ts"] == written["ts"]
    assert seen["prereg"]["config_hash"] == written["config_hash"]


# ---------------------------------------------------------------------------------------------- digests
def test_the_writer_pins_the_research_windows_data_v1_digests_in_every_queued_rows_plan(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    digests = bar_file_digests(bars, to_year=HOLDOUT_FROM.year)
    assert sorted(digests) == [f"xauusd_1m_dukascopy_{y}.parquet" for y in range(2010, HOLDOUT_FROM.year + 1)]
    assert digests["xauusd_1m_dukascopy_2015.parquet"] == hashlib.sha256(b"bars of 2015").hexdigest()
    reg = _written(tmp_path, monkeypatch, bars)
    rows = [r for r in reg._rows() if not r.get("slot")]
    assert rows and all(r["plan"]["data_digests"] == digests for r in rows)
    # without digests the spec is not writable
    plan = plan_quarter(QuarterSpec.model_validate(_complete_spec(reservation=4)), research=_research(4),
                        doc_text=DOC_TEXT, quarter=Q)
    assert any("data_digests" in item for item in plan.open_items) and not plan.writable


def test_each_row_pins_the_files_from_the_year_of_its_bars_from_and_records_the_pinned_years(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars, _later_spec())
    d, h01 = [r for r in reg._rows() if not r.get("slot")]
    last = HOLDOUT_FROM.year
    assert sorted(d["plan"]["data_digests"]) == [f"xauusd_1m_dukascopy_{y}.parquet" for y in range(2015, last + 1)]
    assert d["plan"]["data_years"] == [2015, last]
    # a specialist row has no bars_from: it pins every research-window file, from data-v1's first year
    assert len(h01["plan"]["data_digests"]) == last - 2010 + 1 and h01["plan"]["data_years"] == [2010, last]


def test_a_discovery_row_with_a_later_bars_from_runs_from_that_year(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars, _later_spec())
    written = reg._rows()[0]
    rp = _research_pass(monkeypatch)
    seen = _stop_at_link(monkeypatch)
    b1 = pd.DataFrame({"ts_utc": pd.to_datetime(["2015-01-04 23:00:00", "2017-06-01 00:00:00", "2025-09-30 20:59:00",
                                                 "2026-10-05 12:00:00"], utc=True)})
    with pytest.raises(_Linked):
        _discover(rp, tmp_path, reg, bars, b1, from_year=2015)
    assert seen["prereg"] is not None and seen["prereg"]["ts"] == written["ts"]
    # reading other years than the row's pinned ones is still refused, naming the row's pinned range
    before = reg.path.read_text()
    with pytest.raises(SystemExit, match=r"read by this run but not pinned.*pins years 2015-"):
        _discover(rp, tmp_path, reg, bars, b1, from_year=2014)
    assert reg.path.read_text() == before


def test_a_bars_from_after_every_file_is_refused_at_planning(tmp_path: Path):
    from goldbot.research.preregistration import SpecError
    spec = _complete_spec(reservation=4)
    spec["rows"][0] = {**spec["rows"][0], "bars_from": "2025-09-30 20:00:00+00:00"}
    with pytest.raises(SpecError, match="bars_from"):
        plan_quarter(QuarterSpec.model_validate(spec), research=_research(4), doc_text=DOC_TEXT, quarter=Q,
                     data_digests=bar_file_digests(_release(tmp_path / "raw", range(2010, 2025)),
                                                   to_year=HOLDOUT_FROM.year))


def test_the_writer_script_computes_the_digests_from_bars_and_refuses_a_write_without_them(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]):
    from tests.test_preregister_quarter import SPEC, _script
    _clock(monkeypatch, BEFORE)
    bars = _release(tmp_path / "raw")
    reg_path = tmp_path / "registry.jsonl"
    script = _script()
    assert script.main(["--quarter", Q, "--from", str(SPEC), "--registry", str(reg_path), "--dry-run",
                        "--bars", str(bars)]) == 0
    out = capsys.readouterr().out
    assert f"data-v1 digests: {HOLDOUT_FROM.year - 2010 + 1} research-window files" in out
    assert "xauusd_1m_dukascopy_2026.parquet" not in out                    # after the holdout: never pinned
    with pytest.raises(SystemExit, match="data_digests"):
        script.main(["--quarter", Q, "--from", str(SPEC), "--registry", str(reg_path)])


def test_a_data_v1_refresh_after_the_write_is_refused_with_the_digest_named(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars)
    before = reg.path.read_text()
    old = hashlib.sha256(b"bars of 2024").hexdigest()
    (bars / "xauusd_1m_dukascopy_2024.parquet").write_bytes(b"2024 re-pulled")
    new = hashlib.sha256(b"2024 re-pulled").hexdigest()
    rp = _research_pass(monkeypatch)
    _stop_at_link(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        _discover(rp, tmp_path, reg, bars, _b1_past_the_holdout())
    msg = str(exc.value)
    assert "xauusd_1m_dukascopy_2024.parquet" in msg and old in msg and new in msg
    assert reg.path.read_text() == before                                  # nothing recorded, no fallback


def test_a_specialist_run_verifies_its_queued_rows_digests_before_any_look(tmp_path: Path,
                                                                           monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars)
    before = reg.path.read_text()
    (bars / "xauusd_1m_dukascopy_2012.parquet").write_bytes(b"2012 re-pulled")
    rp = _research_pass(monkeypatch)
    args = argparse.Namespace(specialist="tsmom", pooled="", score_holdout=False, day_type=False,
                              us_calendar_features=False, rule_ref=None, rationale="H-01", skip_screen=False,
                              bars=str(bars), from_year=2010, to_year=2100)
    jobs = rp.make_jobs(args, rp.parse_variants("tsmom", '["slow"]'))
    settings = load_settings()
    with pytest.raises(SystemExit, match="xauusd_1m_dukascopy_2012.parquet"):
        rp._run(args, jobs, 0.37, settings.research.holdout_window(), pd.DataFrame(), reg, 0.0, settings,
                swap=None, cost_source="priors")
    assert reg.path.read_text() == before


def test_a_run_reading_other_years_than_the_pinned_ones_is_refused(tmp_path: Path):
    bars = _release(tmp_path / "raw")
    pinned = bar_file_digests(bars, to_year=2025)
    assert bar_digest_refusal(pinned, bars, 2010, 2025) is None
    why = bar_digest_refusal(pinned, bars, 2012, 2025)
    assert why is not None and "xauusd_1m_dukascopy_2010.parquet" in why and "not read" in why
    (bars / "xauusd_1m_dukascopy_2019.parquet").unlink()
    why = bar_digest_refusal(pinned, bars, 2010, 2025)
    assert why is not None and "xauusd_1m_dukascopy_2019.parquet" in why and "missing" in why


def test_the_preregister_workflow_downloads_data_v1_and_passes_bars_in_both_jobs():
    import yaml
    wf = yaml.safe_load((ROOT / ".github/workflows/preregister-quarter.yml").read_text())
    for job in ("dry-run", "write"):
        run = "\n".join(s.get("run", "") for s in wf["jobs"][job]["steps"])
        assert "gh release download data-v1 -p 'xauusd_1m_dukascopy_*.parquet' -D raw" in run, job
        assert "--bars raw" in run, job


# ---------------------------------------------------------------------------------------------- no silent fallback
def test_a_config_hash_mismatch_against_a_pending_queued_row_refuses_instead_of_falling_back(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars)
    before = reg.path.read_text()
    rp = _research_pass(monkeypatch)
    _stop_at_link(monkeypatch)
    other = {"survivor_unit": "feature", "group_top_k": 3, "clusters": H02_CLUSTERS, "top_k": 20}
    with pytest.raises(SystemExit) as exc:
        _discover(rp, tmp_path, reg, bars, _b1_past_the_holdout(), discovery=other)
    msg = str(exc.value)
    assert "pending queued row" in msg and "(D)" in msg and "discovery.top_k" in msg and "open budget" in msg
    assert reg.path.read_text() == before                                  # no pre-registration, no trial


def test_the_mismatch_refusal_keys_on_the_family_and_agent_of_the_pending_row(tmp_path: Path,
                                                                              monkeypatch: pytest.MonkeyPatch):
    bars = _release(tmp_path / "raw")
    reg = _written(tmp_path, monkeypatch, bars)
    rows = reg._rows()
    d = rows[0]
    changed = {**d["config"], "bars_to": "2025-09-29 20:59:00+00:00"}
    with pytest.raises(QueuedConfigMismatch, match="bars_to"):
        reg.check_budget_reserved([(FAMILY_D, changed)], 20, 4, now=IN_QUARTER, retired=set(), agents=[d["agent_id"]])
    # another agent of the family (another specialist config) is not this row's run: the open budget, as before
    _, (p,) = reg.check_budget_reserved([(FAMILY_D, changed)], 20, 4, now=IN_QUARTER, retired=set(),
                                        agents=["tsmom-g0-0000000000"])
    assert p is None
    # the exact config still links to its row
    _, (p,) = reg.check_budget_reserved([(FAMILY_D, d["config"])], 20, 4, now=IN_QUARTER, retired=set(),
                                        agents=[d["agent_id"]])
    assert p is not None and p["ts"] == d["ts"]
