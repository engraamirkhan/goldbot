"""Rule models (ADR 0038): how a deterministic rule agent (e.g. `box_break_close`) takes part in the registry and the
shadow book without a trained meta-label model and without a fake probability.

DESIGN: "no champion for an agent -> no model -> the engine never proposes for it (no placeholder probabilities)".
A `RuleModel` is therefore explicit about what it is: `predict` returns RULE_FIRED (1.0) for every candidate, which
means "the rule fired", not a probability; `calibrated_p` is False. Consequences enforced elsewhere:

* the shadow book records every rule candidate as taken (1.0 clears any threshold) and keeps no raw score, so the
  weekly recalibration never refits it (`engine.shadow.score` gives p_raw None);
* `Engine._decide` never sizes a live entry from an uncalibrated model (decision `uncalibrated_rule_model`);
* a rule challenger carries no backtest, so the weekly challenger gate can never promote it; live trading of a rule
  needs a passed research trial and a calibrated model, through the existing gates.
"""
from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import Record

RULE_FIRED = 1.0


class RuleModel(Record):
    rule: str                                                # the agent's family, e.g. "box_break_close"
    feature_names: list[str] = Field(default_factory=list)  # reads no feature column: the rule decided already
    feature_version: str = ""                                # any frame version: the rule reads the replica, not X
    calibrated_p: Literal[False] = False                     # ADR 0038: p is "the rule fired", never calibrated

    def predict(self, X: pd.DataFrame) -> np.ndarray:
        return np.full(len(X), RULE_FIRED)


def is_uncalibrated(model: object) -> bool:
    """True for a model whose p is not a calibrated probability (a RuleModel)."""
    return getattr(model, "calibrated_p", True) is False
