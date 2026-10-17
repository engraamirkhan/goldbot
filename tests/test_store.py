"""BACKLOG 74 (ADR 0023 addendum A1, ADR 0010 rollback = restart): the risk file a newer release wrote still loads on
this one, and the keys this release does not know are noted as schema skew, as the orders file's are."""
from __future__ import annotations

import json
from collections import Counter

import pytest

from goldbot import base
from goldbot.engine.store import JsonEngineStore, RiskSnapshot
from goldbot.risk.gate import Stage

ACCOUNT = "icm-demo"


def test_risk_file_unknown_key_notes_skew_and_still_loads(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """The unknown keys are noted once under the risk file's source and otherwise ignored: never raised, so the halt
    and the stage load exactly as they did before the note."""
    monkeypatch.setattr(base, "_skew", Counter())
    (tmp_path / f"risk_{ACCOUNT}.json").write_text(
        json.dumps({"stage": "halted", "day_start_equity": 1000.0, "zeta_cap": 0.5, "alpha_flag": True}))
    got = JsonEngineStore(tmp_path, ACCOUNT).load_risk()
    assert got is not None and got.stage == Stage.HALTED and got.day_start_equity == 1000.0
    assert base.schema_skew() == {("risk_<account>.json", "risk file", ("alpha_flag", "zeta_cap")): 1}


def test_a_risk_file_this_release_wrote_notes_no_skew(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Every key `save_risk` writes is one `load_risk` knows, so a file this release wrote is never skew."""
    monkeypatch.setattr(base, "_skew", Counter())
    s = JsonEngineStore(tmp_path, ACCOUNT)
    s.save_risk(RiskSnapshot(last_reset=None, day_start_equity=1.0, week_start_equity=1.0, balance_closed_hwm=1.0,
                             stage=Stage.NORMAL, rearm_seen=None, weekend_done=None, halted_at=None,
                             propose_only_until=None, rearm_refused=None, kill_everything=False,
                             stage2_pending_since=None))
    assert s.load_risk() is not None
    assert base.schema_skew() == {}
