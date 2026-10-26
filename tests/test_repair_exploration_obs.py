"""PD decision (B), live 2026-10-26: the exploration_obs row a clock fault misfiled is quarantined, never deleted, by a
one-off idempotent script (scripts/repair_exploration_obs_2026_10_26.py); training excludes quarantined rows."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.db import Database
from goldbot.research import exploration_data as ed

SCRIPT = Path(__file__).parents[1] / "scripts" / "repair_exploration_obs_2026_10_26.py"
CID = "exa4a6f-1792998000-f5d1e8f3"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("repair_obs", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["repair_obs"] = mod
    spec.loader.exec_module(mod)
    return mod


def _row(db: Database, cid: str, **kw: Any) -> None:
    ed.record_candidate(db, candidate_id=cid, account_id="icm-probe", arm_id="I0", family="random_bracket",
                        style="intraday", tf="15m", side=1, decision_utc=pd.Timestamp("2026-10-26 07:00", tz="UTC"),
                        stop_dist=10.0, target_dist=20.0, max_hold_s=86400, **kw)


def test_the_misfiled_row_is_quarantined_once_and_kept_and_no_other_row_is_touched(tmp_path):
    db = Database.open(tmp_path, "aux")
    _row(db, CID, taken=False, blocked_reason="data_quality_error", branch="blocked", mu=0.0)
    _row(db, "exother-1792998000-00000000", taken=False, blocked_reason="arm_budget", branch="blocked", mu=0.0)
    mod = _load()
    assert mod.repair(db) == 1
    assert mod.repair(db) == 0                                    # idempotent
    with db.connection() as conn:
        rows = dict(conn.execute("SELECT candidate_id, quarantine FROM exploration_obs").fetchall())
    assert rows == {CID: mod.REASON, "exother-1792998000-00000000": None}
    assert mod.REASON == "clock_gap_week_misfiled_2026-10-26"
    assert CID not in set(ed.read_obs(db)["candidate_id"])        # training never reads it


def test_a_missing_row_changes_nothing(tmp_path):
    db = Database.open(tmp_path, "aux")
    assert _load().repair(db) == 0
