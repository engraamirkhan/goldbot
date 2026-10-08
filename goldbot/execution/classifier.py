"""Account classifier: Raw / Standard / Unknown, measured not assumed.

Decisive test: commission on past deals => Raw. Otherwise session-filtered median spread:
< $0.20 floating with no deal history => Raw, >= $0.30 => Standard, between => Unknown. The last classification is
persisted and only changes after two consecutive disagreeing runs. On Standard, 15m specialists are
disabled; on Unknown, the engine trades paper and alerts.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.base import Record
from goldbot.data.calendar import DEFAULT_SESSIONS


class Classification(Record):
    account_class: str      # raw | standard | unknown
    median_spread: float | None
    p90_spread: float | None
    commission_seen: bool
    n_ticks: int
    reason: str


def classify(ticks: pd.DataFrame, deals: pd.DataFrame, *, raw_spread_max: float = 0.20, std_spread_min: float = 0.30,
             min_ticks: int = 1000) -> Classification:
    commission_seen = bool(not deals.empty and "commission" in deals.columns and (deals["commission"].abs() > 0).any())
    if commission_seen:
        med = float(np.median(ticks["ask"] - ticks["bid"])) if not ticks.empty else None
        return Classification(account_class="raw", median_spread=med, p90_spread=None, commission_seen=True, n_ticks=len(ticks), reason="commission on past deals")
    if ticks.empty:
        return Classification(account_class="unknown", median_spread=None, p90_spread=None, commission_seen=False, n_ticks=0, reason="no ticks")
    idx = pd.DatetimeIndex(pd.to_datetime(ticks["ts_utc"], utc=True))
    sess = DEFAULT_SESSIONS.session_label(idx)
    t = ticks[np.isin(sess, ["london", "newyork"])]
    if len(t) < min_ticks:
        return Classification(account_class="unknown", median_spread=None, p90_spread=None, commission_seen=False, n_ticks=len(t), reason=f"only {len(t)} London/NY ticks (< {min_ticks})")
    spread = (t["ask"] - t["bid"]).to_numpy()
    med, p90 = float(np.median(spread)), float(np.percentile(spread, 90))
    floating = float(np.std(spread)) > 1e-6
    if med < raw_spread_max and floating and deals.empty:
        return Classification(account_class="raw", median_spread=med, p90_spread=p90, commission_seen=False, n_ticks=len(t), reason="tight floating spread")
    if med >= std_spread_min:
        return Classification(account_class="standard", median_spread=med, p90_spread=p90, commission_seen=False, n_ticks=len(t), reason="wide spread, no commission")
    reason = "tight spread but zero-commission deal history" if med < raw_spread_max else "spread in dead band"
    return Classification(account_class="unknown", median_spread=med, p90_spread=p90, commission_seen=False, n_ticks=len(t), reason=reason)


class PersistentClassifier:
    """Only changes the stored class after two consecutive runs disagree with it."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"class": None, "pending": None, "runs": []}

    def update(self, c: Classification) -> str:
        cur = self.state["class"]
        if cur is None:
            self.state["class"] = c.account_class
        elif c.account_class != cur:
            if self.state["pending"] == c.account_class:
                self.state["class"] = c.account_class
                self.state["pending"] = None
            else:
                self.state["pending"] = c.account_class
        else:
            self.state["pending"] = None
        self.state["runs"] = (self.state["runs"] + [c.model_dump()])[-20:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, default=str))
        return self.state["class"]
