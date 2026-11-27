"""A refit cost cell (aux.db exploration_cost_cells) older than CELL_MAX_AGE at the label's slot is stale: it no longer
raises the replay cost, the label's cost_model_version records `+cells-stale:<day>`, and a stale-flagged label is left
out of the one cross-broker read (autodisc discovery priors). As-of semantics unchanged: a cell fitted after the slot
is never read."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.ops import jobs
from goldbot.research import daily_review as dr
from goldbot.research import exploration_data as ex
from goldbot.research.autodisc import discovery

FIT = pd.Timestamp("2026-11-07 06:15", tz="UTC")
BASE = ex.ReplayCosts(broker="vantage", version="settings-priors:vantage",
                      effective_utc=pd.Timestamp("2026-01-01", tz="UTC"), slippage_usd=0.15)


def _seed(sd: Path, *, broker: str = "vantage", fitted: pd.Timestamp = FIT, slip: float = 0.60,
          day: str = "2026-11-06", session: str = "london") -> None:
    db = Database.open(sd, "aux")
    dr.write_cell(db, broker=broker, session=session, day=day, slippage_usd=slip, fills=200, through=fitted,
                  now=fitted)


def test_a_fresh_cell_still_raises_the_replay_cost(tmp_path):
    _seed(tmp_path)
    c = jobs._with_refit_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=3), BASE)
    assert c.slippage_usd == pytest.approx(0.60)
    assert c.version == "settings-priors:vantage+cells:2026-11-06" and "stale" not in c.version


def test_a_stale_cell_is_flagged_and_the_prior_is_used(tmp_path):
    _seed(tmp_path)
    c = jobs._with_refit_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=20), BASE)
    assert c.slippage_usd == pytest.approx(0.15)                        # the prior, not the 11-06 cell
    assert c.version == "settings-priors:vantage+cells-stale:2026-11-06"
    assert c.effective_utc == BASE.effective_utc
    assert ex.cost_cells_stale(c.version)


def test_the_boundary_is_14_days_inclusive_fresh(tmp_path):
    _seed(tmp_path)
    assert dr.CELL_MAX_AGE == pd.Timedelta(days=14)
    at = jobs._with_refit_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=14), BASE)
    past = jobs._with_refit_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=14, microseconds=1), BASE)
    assert at.slippage_usd == pytest.approx(0.60) and "+cells:" in at.version
    assert past.slippage_usd == pytest.approx(0.15) and "+cells-stale:" in past.version


def test_as_of_unchanged_a_cell_fitted_after_the_slot_is_never_read(tmp_path):
    _seed(tmp_path)
    c = jobs._with_refit_cells(tmp_path, "vantage", FIT - pd.Timedelta(minutes=1), BASE)
    assert c == BASE


def test_a_fresh_session_cell_applies_while_a_stale_one_is_set_aside_and_flagged(tmp_path):
    _seed(tmp_path, slip=0.90, session="london")                               # dear, but stale at the slot
    _seed(tmp_path, slip=0.40, session="newyork", day="2026-11-20", fitted=FIT + pd.Timedelta(days=14))
    c = jobs._with_refit_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=20), BASE)
    assert c.slippage_usd == pytest.approx(0.40)                               # never the stale 0.90
    assert "+cells:2026-11-20" in c.version and "+cells-stale:2026-11-06" in c.version


def test_a_stale_cell_cheaper_than_the_table_still_flags_nothing_is_raised(tmp_path):
    _seed(tmp_path, slip=0.10)
    c = jobs._with_refit_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=20), BASE)
    assert c.slippage_usd == pytest.approx(0.15) and ex.cost_cells_stale(c.version)


def test_replay_cells_reports_stale_cells_separately(tmp_path):
    _seed(tmp_path)
    r = dr.replay_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=20))
    assert r is not None and r.slippage_usd is None and r.stale_day == "2026-11-06"
    fresh = dr.replay_cells(tmp_path, "vantage", FIT + pd.Timedelta(days=1))
    assert fresh is not None and fresh.slippage_usd == pytest.approx(0.60) and fresh.stale_day is None


def test_stale_flagged_labels_are_left_out_of_the_cross_broker_discovery_read(tmp_path, monkeypatch):
    rows = pd.DataFrame({"candidate_id": ["a", "b", "c", "d"], "broker": ["icm", "vantage", "vantage", "icm"],
                         "cost_model_version": ["costs:icm-demo:20261126T0600+cells:2026-11-25",
                                                "settings-priors:vantage+cells-stale:2026-11-06",
                                                "settings-priors:vantage", None]})
    seen: dict[str, Any] = {}

    def fake_read_obs(db: Any, **kw: Any) -> pd.DataFrame:
        seen.update(kw)
        return rows
    monkeypatch.setattr(ex, "read_obs", fake_read_obs)
    Database.open(tmp_path, "aux")
    out = discovery.read_demo_obs(None, tmp_path, FIT, write=False)  # type: ignore[arg-type]
    assert list(out["candidate_id"]) == ["a", "c", "d"]
    assert seen.get("broker") is None                                   # the read is cross-broker
