"""A decision timeframe's frame at a bar close: its completed bars, mid bars, ATR and features (the same rule as
research, `pipeline.build_decision_frame`).

Design rules (live 2026-11-04, icm-probe: "pass budget 5.0 s spent after 8|16|19 of 20 books"; measured with
scripts/bench_account_pass.py, the features were about two thirds of a 15m close's pass):
- The features (`Frame.X`) are built on first read and kept for the frame. Only the agents' decisions, the shadow
  book and the blackout re-scores read them, so a book without agents (every non-gold book; probe hosts) never builds
  them. A frame whose features are read gets exactly what the eager build returned: same function, same inputs.
- The bars, mid bars and ATR are built at once, as before: exits, policies and the time barriers read them.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import pandas as pd
from pydantic import Field, PrivateAttr

from goldbot.base import Record
from goldbot.data.resample import mid
from goldbot.features import build_features
from goldbot.features.mtf import TF_LABEL, context_tfs, merge_higher_tf
from goldbot.features.technical import atr
from goldbot.research.pipeline import DEFAULT_FEATURE_NAMES

if TYPE_CHECKING:
    from goldbot.engine.runner import Engine

MIN_BARS = 120          # decision bars a frame needs (fewer: no frame, no decision)


class Frame(Record):
    """Completed bars of one decision timeframe; `X` (the features) built by `features` on first read."""
    dec: pd.DataFrame
    m: pd.DataFrame
    atr: pd.Series
    features: Callable[[], pd.DataFrame]
    by_version: dict[str, pd.DataFrame | None] = Field(default_factory=dict)   # X rebuilt for older model versions
    _x: pd.DataFrame | None = PrivateAttr(default=None)

    @property
    def X(self) -> pd.DataFrame:
        if self._x is None:
            self._x = self.features()
        return self._x


def frame(eng: Engine, complete: pd.DataFrame, tf: str, close_ts: pd.Timestamp) -> Frame | None:
    """`Engine._frame`: the frame of `tf` at `close_ts` from the completed 1m bars; None with fewer than MIN_BARS."""
    from goldbot.engine import books
    dec = eng._resample(complete, tf)
    dec = dec[dec["visible_at"] <= close_ts].tail(eng.cfg.feature_window).reset_index(drop=True)
    if len(dec) < MIN_BARS:
        return None
    m = mid(dec)
    ctx = books.feature_ctx(eng)              # the active book's, read now: X is read inside the same bar close

    def features() -> pd.DataFrame:
        X = build_features(m, DEFAULT_FEATURE_NAMES, ctx=ctx)   # non-gold: its symbol (L1)
        version = X.attrs["feature_version"]
        for ctf in context_tfs(tf):
            hit = eng._context(complete, ctf, close_ts)
            if hit is not None:
                X = merge_higher_tf(X, hit[1], hit[0], TF_LABEL[ctf])
        X.attrs["feature_version"] = version          # as research stamps it (pipeline.build_decision_frame)
        return X
    return Frame(dec=dec, m=m, atr=atr(m, 14), features=features)
