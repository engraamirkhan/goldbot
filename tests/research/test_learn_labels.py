"""The learn-only label route for SEQ-1 and RL-X1 (goldbot/research/learn_labels.py): every read goes through
dense_labels.load_labels on learn 2010-01..2017-12, purpose train, phase 0, never a booked confirm/test_old read; a
sealed store stays sealed; every read names its procedure and writes its consumption row to the ledger (quant L1)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.research import dense_labels as D
from goldbot.research import learn_labels as LL
from goldbot.research import windows as WN
from tests.research.test_dense_labels import CORE6, _minutes, _store, _tiers


def test_a_year_outside_learn_is_refused_before_any_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls: list[Any] = []
    monkeypatch.setattr(D, "load_labels", lambda *a, **k: calls.append(k))
    for y in (2009, 2018, 2025):
        with pytest.raises(ValueError, match="learn"):
            LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="XAUUSD", tf="1h", years=[y], tiers=None, columns=CORE6, keys={})
    assert calls == []


def test_the_read_is_a_train_read_of_the_learn_window_without_a_booked_read_under_its_procedure(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    seen: dict[str, Any] = {}

    def spy(cache: Path, **k: Any) -> tuple[pd.DataFrame, dict[str, Any]]:
        seen.update(k)
        return pd.DataFrame(columns=list(D.COLUMNS)), {}

    monkeypatch.setattr(D, "load_labels", spy)
    LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="XAUUSD", tf="1h", years=[2012], tiers=None, columns=CORE6, keys={})
    assert seen["window"] == WN.SPANS[WN.Window.LEARN] and seen["purpose"] == "train"
    assert seen["phases"] == (0,) and seen.get("booked_read") is None
    assert seen["procedure"] == "seq-1" and seen["ledger"] == tmp_path / "reads.jsonl"


def test_a_sealed_store_stays_sealed(tmp_path: Path):
    m = _minutes("2015-03-02", 6 * 1440)
    _, k = _store(tmp_path, m, "1h", 0, "dukascopy")
    tiers = _tiers(tmp_path, "XAUUSD", ["2015-02", "2015-03"])      # sha not recorded in ADR 0085 A4
    with pytest.raises(D.SealedStore):
        LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="XAUUSD", tf="1h", years=[2015], tiers=tiers, columns=CORE6,
                      keys={(2015, 0): k}, proxy_source="dukascopy:XAUUSD")


def test_rows_of_a_readable_store_carry_a_uniqueness_weight_per_event(tmp_path: Path,
                                                                      monkeypatch: pytest.MonkeyPatch):
    m = _minutes("2015-03-02", 6 * 1440).assign(source="histdata:EURUSD")
    _, k = _store(tmp_path, m, "1h", 0, "histdata:EURUSD")
    tiers = _tiers(tmp_path, "EURUSD", ["2014-09", "2014-10", "2014-11", "2014-12", "2015-01",
                                         "2015-02", "2015-03"])
    monkeypatch.setattr(D, "RECORDED_TIERS_SHA256", frozenset({tiers.sha256}))   # fixture store only
    lab, meta = LL.load_learn(tmp_path, procedure="seq-1", ledger=tmp_path / "reads.jsonl", instrument="EURUSD", tf="1h", years=[2015], tiers=tiers, columns=CORE6,
                              keys={(2015, 0): k}, templates=("T2",))
    assert len(lab) and set(lab["template"]) == {"T2"} and lab["label_ok"].all()
    assert ((lab["w"] > 0) & (lab["w"] <= 1)).all()
    per_event = lab.groupby("decision_utc")["w"].nunique()
    assert (per_event == 1).all()                      # both sides of one bar share one event weight
    assert lab.drop_duplicates("decision_utc")["w"].median() < 0.2   # hourly bars, 1-day horizons: ~24 overlap
    assert meta["learn_only"] is True and meta["procedure"] == "seq-1"
    rows = WN.read_ledger(tmp_path / "reads.jsonl")
    assert [r["id"] for r in rows if r.get("type") == "consumption"] == ["seq-1/EURUSD/1h/train"]
