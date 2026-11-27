"""Train/serve parity for the sr-v3 learners (card 3.5, test 13; ADR 0101; trading-safety M1 "train/serve parity holds
on the last 500 decision vectors").

The engine's vector is `self_retrain.design_row(features, side)`: the 40 sr-v2 INPUTS, SIGNED columns centred and
multiplied by side, then side. Every learner scores that vector:
- **L-cw** exports, per instrument, a `self_retrain.LogitModel` on the frozen INPUTS: the trained impute, centre and
  scale on its served columns, coefficient 0 elsewhere, intercept = unit intercept + the instrument's level. It loads
  in `engine/shadow_scoring` unchanged (`load_record_json(LogitModel, ...)`) and `LogitModel.score_row` returns
  σ(ĝ). The keep rule is NOT the sr-v2 tau: sr-v3 keeps iff ĝ + δ − cost(x) > 0 (card 3.4), so the export carries
  tau = 0.0, under which the sr-v2 rule keeps every candidate (the pre-model rule, never a refusal: H3), and
  `gross_row` gives ĝ with score_row's own operations, without the sigmoid.
- **G-cw, HB, D-40, AVG** score `p30_row(design_row(...))` (plus the range-to-cost and dense columns for G-cw) with
  their artifacts' `value_row`. These are NOT wired into the engine: the hook change (what it needs is listed in
  `ENGINE_HOOK_NEEDS`) goes through its own trading-safety review (card M2), and sr-v3 is not in GATE_SPECS.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from goldbot.research import learners as L
from goldbot.research import sr_v3 as V
from goldbot.research.self_retrain import INPUTS, LogitModel, design_row

P30_INDEX: tuple[int, ...] = tuple(INPUTS.index(c) for c in V.P30)
ENGINE_HOOK_NEEDS: tuple[str, ...] = (
    "sr-v3 SPEC_VERSION in shadow_scoring.GATE_SPECS and the keep rule ĝ + δ − cost(x) > 0 (L-cw, HB) or p_w >= 0.5 "
    "(G-cw, AVG) in place of score >= tau, reading the served sha once per decision (card M2)",
    "cost(x) at decision: decision_cost.cost_x on (instrument:tf:T2, side, max_hold_s, entry, risk_usd) with the "
    "served version's CostPriors and the cost of record's ub95 round trip",
    "range_to_cost = 1 / cost(x) and the D-40 columns from the served DenseArtifact for G-cw",
    "the template nearest the arm's exit geometry (card 3.4); the trainer serves the T2 head only",
    "a NaN value (no cell, no vector, stale) fails open to the pre-model rule and is counted in gate_fail_open (L1, H3)",
    "AVG needs every base's value of the same decision; a missing base fails open",
    "per-instrument L-cw rows in self_retrain_models (or sr_v3_versions) with model_sha256 = SHA-256 of the stored "
    "JSON, spec 'sr-v3' (shadow_scoring refuses a spec other than sr-v2 today, so nothing serves until the hook PR)",
)


def to_logit_model(a: L.LcwArtifact, instrument: str) -> LogitModel:
    """The L-cw body for one instrument as a LogitModel on INPUTS (see the module docstring for tau = 0.0)."""
    pos = {c: i for i, c in enumerate(a.columns)}
    med, mu, sd, coef = [], [], [], []
    for c in INPUTS:
        j = pos.get(c)
        med.append(float(a.impute[j]) if j is not None else 0.0)
        mu.append(float(a.center[j]) if j is not None else 0.0)
        sd.append(float(a.scale[j]) if j is not None else 1.0)
        coef.append(float(a.coef[j]) if j is not None else 0.0)
    return LogitModel(inputs=INPUTS, medians=tuple(med), means=tuple(mu), scales=tuple(sd), coef=tuple(coef),
                      intercept=float(a.intercept + a.levels.get(instrument, 0.0)), tau=0.0)


def gross_row(m: LogitModel, x: np.ndarray) -> float:
    """ĝ of one design_row vector with LogitModel.score_row's operations (score_row = σ(gross_row) bit for bit)."""
    v = x.reshape(1, -1)
    v = np.where(np.isfinite(v), v, np.asarray(m.medians))
    z = (v - np.asarray(m.means)) / np.asarray(m.scales)
    return float((z @ np.asarray(m.coef) + m.intercept)[0])


def p30_row(x: np.ndarray) -> np.ndarray:
    """The P30 inputs of one design_row vector, in P30 order (non-finite values are NaN: imputed by the artifact)."""
    v = np.asarray(x, dtype=float)[list(P30_INDEX)]
    return np.where(np.isfinite(v), v, np.nan)


def serve_vector(features: Mapping[str, Any], side: int) -> np.ndarray:
    return design_row(features, side)


__all__ = ["ENGINE_HOOK_NEEDS", "P30_INDEX", "gross_row", "p30_row", "serve_vector", "to_logit_model"]
