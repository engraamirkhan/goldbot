"""sr-v3 D-4: the exit templates T0-T7 and the W2-2 h_ref are frozen (record xt-v1), shared with RL-1.

The gate (quant A4 on #482) refused every dl-v1 read with "h_ref = H: the exit templates' h_ref is not frozen (D-4)".
After D-4 it clears only for a label spec whose exit geometry hashes to the frozen record, and that hash is written
in the sr-v3 card, so the freeze changes only together with the card."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from goldbot.research import dense_labels as D

ROOT = Path(__file__).resolve().parents[2]
CARD = ROOT / "docs" / "research" / "prereg-2026-11-26-sr-v3.md"
XT_V1_SHA256 = "559739c72fc720a2d27035c14011dc7dc9a12e62da3121496bd16b270478979c"


def _lab(cost_version: str = "ecr-ub95", sha: str | None = None) -> pd.DataFrame:
    sha = D.EXIT_FREEZE_SHA256 if sha is None else sha
    return pd.DataFrame({"cost_version": [cost_version, cost_version], "exit_freeze_sha": [sha, sha]})


@pytest.fixture(autouse=True)
def _fresh_behaviour():
    """The behaviour sha is cached; a monkeypatched exit mechanic must be re-measured, then the cache restored."""
    D._behaviour_sha.cache_clear()
    yield
    D._behaviour_sha.cache_clear()


def test_xt_v1_is_the_card_table_literally() -> None:
    tpl = {t.name: (t.stop_k, t.target_k, t.trail_k, t.be_at_r) for t in D.EXIT_FREEZE.templates}
    assert tpl == {"T0": (None, None, None, None), "T1": (1.0, 1.0, None, None), "T2": (1.0, 2.0, None, None),
                   "T3": (1.0, 3.0, None, None), "T4": (1.5, 1.5, None, None), "T5": (1.0, None, 1.0, None),
                   "T6": (2.0, None, None, None), "T7": (1.0, 3.0, None, 1.0)}
    assert D.EXIT_FREEZE.freeze_id == "xt-v1"
    assert D.EXIT_FREEZE.h_by_tf == D.H_BY_TF and D.H_BY_TF["1m"] == 3_600 and D.H_BY_TF["1w"] == 20 * 86_400
    # h_ref = H per tf, in seconds: sqrt(H / h_ref) = 1, so k is the ATR14_tf multiple the card's table states
    assert D.EXIT_FREEZE.h_ref == D.H_BY_TF
    assert (D.EXIT_FREEZE.atr_n, D.EXIT_FREEZE.floor_tr_mult, D.EXIT_FREEZE.floor_window_min,
            D.EXIT_FREEZE.clip_r) == (14, 1.5, 1_440, 10.0)
    # quant 1 on #514: the mechanics are frozen too, not only the parameters
    assert D.EXIT_FREEZE.sides == (1, -1)
    assert D.EXIT_FREEZE.grid_time_exit_tfs == ("1d", "1w") and D.EXIT_FREEZE.close_to_close == ("T0",)
    assert len(D.EXIT_FREEZE.behaviour_sha) == 64
    # one definition repo-wide: the label spec carries exactly the frozen record
    assert D.exit_freeze(D.SPEC) == D.EXIT_FREEZE


def test_xt_v1_hash_is_pinned_and_recorded_in_the_card() -> None:
    assert D.exit_freeze_hash(D.SPEC) == D.EXIT_FREEZE_SHA256 == XT_V1_SHA256
    assert XT_V1_SHA256 in CARD.read_text(), "the D-4 freeze sha256 must be written in the sr-v3 card amendment"


def test_the_frozen_spec_clears_the_d4_block_and_d3_still_blocks() -> None:
    assert D.gate_status(_lab(), D.SPEC) == {"gate_eligible": True, "gate_block": []}
    w2 = D.gate_status(_lab("w2-point"), D.SPEC)
    assert w2["gate_eligible"] is False and not any("D-4" in b for b in w2["gate_block"])
    assert any("D-3" in b for b in w2["gate_block"])


@pytest.mark.parametrize("update", [
    {"h_ref": {**D.H_BY_TF, "15m": 15 * 60}},                                        # h_ref = one bar at 15m
    {"h_ref": "H"},                                                                    # the old placeholder
    {"templates": tuple(t.model_copy(update={"target_k": 2.5}) if t.name == "T2" else t for t in D.TEMPLATES)},
    {"floor_tr_mult": 1.0},
    {"atr_n": 20},
])
def test_any_other_exit_geometry_is_not_a_gate_read(update: dict) -> None:
    spec = D.SPEC.model_copy(update=update)
    g = D.gate_status(_lab(), spec)
    assert g["gate_eligible"] is False and any("D-4" in b and "xt-v1" in b for b in g["gate_block"])
    with pytest.raises(PermissionError, match="not a gate read"):
        D.require_gate_eligible(g)


def _both_touch_target_first(orig):
    """A mutated fill rule: a minute touching the initial stop and the target is a target (xt-v1: a stop)."""
    def fp(o, h, lo, c, e, z, d0, tpl, **kw):
        out = orig(o, h, lo, c, e, z, d0, tpl, **kw)
        if tpl.stop_k is None or tpl.target_k is None:
            return out
        x, ent = out["exit"], o[e]
        both = (out["hit"] == 1) & (h[x] >= ent + tpl.target_k * d0)
        out["hit"] = np.where(both, 2, out["hit"]).astype(np.int8)
        out["gross"] = np.where(both, tpl.target_k * d0 / out["risk"], out["gross"])
        return out
    return fp


def _gapped_stop_at_stop(orig):
    """A mutated fill rule: a gapped initial stop fills at the stop (xt-v1: at the minute's open)."""
    def fp(o, h, lo, c, e, z, d0, tpl, **kw):
        out = orig(o, h, lo, c, e, z, d0, tpl, **kw)
        x = out["exit"]
        gap = (out["hit"] == 1) & ~out["moved"] & (o[x] < o[e] - out["risk"])
        out["gross"] = np.where(gap, -1.0, out["gross"])
        return out
    return fp


@pytest.mark.parametrize("mutation", ["both_touch_target_first", "gapped_stop_at_stop", "grid_time_exit_tfs"])
def test_a_changed_exit_mechanic_is_a_d4_block(monkeypatch: pytest.MonkeyPatch, mutation: str) -> None:
    """Quant 1 on #514: the freeze hashes the behaviour of first_passage, entry_and_horizon and decision_bars/floor_at
    on a fixed synthetic set, so a changed fill rule or grid rule with the same parameters is not xt-v1."""
    before = D.exit_freeze_hash(D.SPEC)
    assert before == D.EXIT_FREEZE_SHA256
    if mutation == "both_touch_target_first":
        monkeypatch.setattr(D, "first_passage", _both_touch_target_first(D.first_passage))
    elif mutation == "gapped_stop_at_stop":
        monkeypatch.setattr(D, "first_passage", _gapped_stop_at_stop(D.first_passage))
    else:
        monkeypatch.setattr(D, "GRID_TIME_EXIT_TFS", frozenset({"1w"}))
    D._behaviour_sha.cache_clear()
    assert D.exit_freeze_hash(D.SPEC) != before
    g = D.gate_status(_lab(), D.SPEC)
    assert g["gate_eligible"] is False and any("D-4" in b for b in g["gate_block"])


def test_label_provenance_not_the_spec_argument_decides() -> None:
    """Quant 2 on #514: every row carries the exit freeze sha it was built under; rows built under another geometry
    are blocked even when the caller passes the frozen spec."""
    quarter = D.SPEC.model_copy(update={"h_ref": {**D.H_BY_TF, "15m": D.H_BY_TF["15m"] // 4}})
    lab = D.label_rows(D.Minutes(_minutes()), _rows15(), "15m", spec=quarter).assign(cost_version="ecr-ub95")
    assert "exit_freeze_sha" in D.SKELETON and (lab["exit_freeze_sha"] == D.exit_freeze_hash(quarter)).all()
    g = D.gate_status(lab)
    assert g["gate_eligible"] is False and any("D-4" in b for b in g["gate_block"])
    ok = D.label_rows(D.Minutes(_minutes()), _rows15(), "15m").assign(cost_version="ecr-ub95")
    assert (ok["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256).all()
    assert D.gate_status(ok) == {"gate_eligible": True, "gate_block": []}
    assert not D.gate_status(ok.drop(columns="exit_freeze_sha"))["gate_eligible"]     # no stamp, no gate read


def test_the_partition_manifest_records_the_exit_freeze_sha(tmp_path: Path) -> None:
    import json
    lab = D.label_rows(D.Minutes(_minutes()), _rows15(), "15m")
    parts = {"fs_key": "f", "minutes_sha256": "m", "minutes_to_utc": "2015-03-06"}
    key = D.write_partition(lab, cache=tmp_path, instrument="XAUUSD", tf="15m", year=2015, phase=0, parts=parts)
    man = json.loads(next((tmp_path / "dl-v1" / "XAUUSD" / "15m" / "p0" / "2015").glob(f"{key[:24]}.json")).read_text())
    assert man["exit_freeze_sha"] == D.EXIT_FREEZE_SHA256


def _minutes() -> pd.DataFrame:
    rng = np.random.default_rng(3)
    n = 4 * 1440
    ts = pd.date_range("2015-03-02", periods=n, freq="1min", tz="UTC")
    mid = 1500 + np.cumsum(rng.normal(0, 0.3, n))
    hi, lo = mid + np.abs(rng.normal(0, 0.2, n)), mid - np.abs(rng.normal(0, 0.2, n))
    op = np.clip(mid + rng.normal(0, 0.05, n), lo, hi)
    m = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1),
                      "bid_open": op - .1, "bid_high": hi - .1, "bid_low": lo - .1, "bid_close": mid - .1,
                      "ask_open": op + .1, "ask_high": hi + .1, "ask_low": lo + .1, "ask_close": mid + .1,
                      "tick_count": 10.0, "spread_mean": .2, "spread_max": .2, "source": "dukascopy",
                      "bid_only": False})
    return m


def _rows15() -> pd.DataFrame:
    bars = D.decision_bars(D.Minutes(_minutes()), "15m", 0)
    dec = D._to_utc(bars["close_ns"].to_numpy())
    return pd.DataFrame({"instrument": "XAUUSD", "tf": "15m", "phase": 0, "group_id": 0, "decision_utc": dec,
                         "available_utc": dec, "window_from_utc": dec, "source": "dukascopy", "bid_only": False})


def test_d0_implements_sqrt_h_over_h_ref_before_the_floor() -> None:
    """Card 2.1: stop = k x ATR14_tf x sqrt(hold / h_ref), floored at 1.5 x the session-median TR. Under xt-v1 the
    factor is exactly 1; a spec with h_ref = H / 4 doubles the ATR leg (and is not a gate read)."""
    mm = D.Minutes(_minutes())
    bars = D.decision_bars(mm, "15m", 0)
    dec = D._to_utc(bars["close_ns"].to_numpy())
    rows = _rows15()
    atr = pd.Series(bars["atr"].to_numpy(), index=dec)
    floor = pd.Series(mm.floor_at(D._ns(pd.Series(dec))), index=dec)
    base = D.label_rows(mm, rows, "15m").query("template == 'T0' and side == 1").set_index("decision_utc")
    quarter = D.SPEC.model_copy(update={"h_ref": {**D.H_BY_TF, "15m": D.H_BY_TF["15m"] // 4}})
    scaled = D.label_rows(mm, rows, "15m", spec=quarter).query("template == 'T0' and side == 1") \
        .set_index("decision_utc")
    ok = base["label_ok"] & floor.reindex(base.index).notna()
    assert ok.sum() > 100
    a, f = atr.reindex(base.index)[ok], floor.reindex(base.index)[ok]
    np.testing.assert_allclose(base.loc[ok, "base_dist"], np.fmax(a, f))
    np.testing.assert_allclose(scaled.loc[ok, "base_dist"], np.fmax(2 * a, f))
    assert (scaled.loc[ok, "base_dist"] > base.loc[ok, "base_dist"]).any()
