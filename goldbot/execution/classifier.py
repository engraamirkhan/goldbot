"""Account classifier: Raw / Standard / Unknown. A declared class is the baseline, the measurement the cross-check,
and when the two disagree the stricter one (Standard) wins.

The owner may declare the class per account (`account_class` in config/accounts.yaml). Until a measured class is
stored, the declared class is the effective one, so a new account is not blocked as "unknown" for a week; the
measurement keeps running (`effective_class`, the ONE function every consumer uses). A measured class that disagrees
with the declared one never loosens the account: Standard wins and health warns (`class_mismatch`).

Measurement. Decisive test: commission on past deals => Raw. Otherwise session-filtered median spread:
< $0.20 floating with no deal history => Raw, >= $0.30 => Standard, between => Unknown. The last classification is
persisted and only changes after two consecutive disagreeing runs; a stored Unknown counts as never classified, so
the first known class is adopted on its first run. The nightly job re-runs it every night while the stored class is
Unknown (`PersistentClassifier.is_known`), on Fridays only once a class is known. On Standard, 15m specialists are
disabled; on Unknown, the engine trades paper and alerts.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.base import Record
from goldbot.data.calendar import DEFAULT_SESSIONS

MIN_TICKS = 1000        # London/NY ticks needed before the spread is read


class Classification(Record):
    account_class: str      # raw | standard | unknown
    median_spread: float | None
    p90_spread: float | None
    commission_seen: bool
    n_ticks: int
    reason: str


def classify(ticks: pd.DataFrame, deals: pd.DataFrame, *, raw_spread_max: float = 0.20, std_spread_min: float = 0.30,
             min_ticks: int = MIN_TICKS) -> Classification:
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


KNOWN_CLASSES = ("raw", "standard")


def read_stored_class(path: str | Path) -> str:
    """The class stored in state/classifier_<account>.json: raw | standard | unknown (missing, torn or odd is unknown)."""
    p = Path(path)
    try:
        c = str(json.loads(p.read_text()).get("class") or "unknown") if p.exists() else "unknown"
    except (OSError, ValueError, AttributeError):
        return "unknown"
    return c if c in ("raw", "standard", "unknown") else "unknown"


def effective_class(declared: str | None, measured: str | None) -> str:
    """The class the account trades by: the measured one when known and agreeing or alone; the declared one while
    nothing is measured; the stricter (standard) when both are known and disagree, never the looser one."""
    d = declared if declared in KNOWN_CLASSES else None
    m = measured if measured in KNOWN_CLASSES else None
    if d is None or m is None:
        return m or d or "unknown"
    return "standard" if "standard" in (d, m) else d


def class_mismatch(declared: str | None, measured: str | None) -> bool:
    """A declared and a measured class that are both known and differ (health warns; the stricter one is in force)."""
    return declared in KNOWN_CLASSES and measured in KNOWN_CLASSES and declared != measured


class PersistentClassifier:
    """Only changes the stored class after two consecutive runs disagree with it.

    "only changes when two consecutive runs disagree" guards a *known* class: Raw <-> Standard, and a known class
    falling back to Unknown, each need two agreeing runs. A stored Unknown adopts a known class on its first run
    when that cannot relax the account: the class is Standard (the restrictive one), or Raw was proven by
    commission, or the account has never been *stored* as anything but Raw. So one thin first Friday does not leave
    the account unclassified (and blocked by `account_class_unknown`) for a further week, while an account last
    stored as Standard needs two consecutive spread-only Raw runs before it is treated as Raw.

    `state["last_known"]` is the last raw/standard class actually stored as `state["class"]`. It is set only when
    the stored class is assigned, never from pending runs, and it never expires (unlike the 20 kept runs).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {"class": None, "pending": None, "runs": []}
        if "last_known" not in self.state:
            self.state["last_known"] = self._migrated_last_known()

    def _migrated_last_known(self) -> str | None:
        """Legacy files (no `last_known`): a known stored class is it; a stored unknown with any standard run in
        the kept runs is taken as last stored Standard (the restrictive reading); otherwise none."""
        cur = self.state["class"]
        if cur in ("raw", "standard"):
            return str(cur)
        if cur == "unknown" and any(r.get("account_class") == "standard" for r in self.state["runs"]):
            return "standard"
        return None

    def is_known(self) -> bool:
        """A raw or standard class is stored (a missing file or a stored unknown is not)."""
        return self.state["class"] in KNOWN_CLASSES

    def _store(self, account_class: str) -> None:
        self.state["class"] = account_class
        self.state["pending"] = None
        if account_class in ("raw", "standard"):
            self.state["last_known"] = account_class

    def _adopt_at_once(self, c: Classification) -> bool:
        cur = self.state["class"]
        if cur is None:
            return True
        if cur != "unknown" or c.account_class == "unknown":
            return False
        return c.account_class == "standard" or c.commission_seen or self.state.get("last_known") in (None, "raw")

    def update(self, c: Classification) -> str:
        cur = self.state["class"]
        if self._adopt_at_once(c):
            self._store(c.account_class)
        elif c.account_class != cur:
            if self.state["pending"] == c.account_class:
                self._store(c.account_class)
            else:
                self.state["pending"] = c.account_class
        else:
            self.state["pending"] = None
        self.state["runs"] = (self.state["runs"] + [c.model_dump()])[-20:]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.state, default=str))
        return self.state["class"]
