"""Purged, embargoed walk-forward splits over labelled candidates.

Windows per timeframe (design decision): 15m train 24 / test 3 / step 3 months, purge 2 d, embargo 1 d;
1h train 36 / test 6 / step 6 months, purge 5 d, embargo 2 d; 4h (proposal P4) train 48 / test 6 / step 6 months,
purge 10 d, embargo 4 d; 1d (horizon study) train 60 / test 12 / step 12 months on an expanding window, purge 20 d,
embargo 10 d (a daily rule fires a few dozen times a year, so a fold needs a year to reach the 60-candidate gate and the
training set grows with every fold). `expanding=True` trains on everything since the first label instead of the trailing
`train_months` (the first test fold still starts `train_months` in): a family with few candidates a year, such as
session_open (proposal P5), then has enough training rows, and with 6-month test folds every fold can reach the
60-candidate gate. A specialist declares such overrides in its `walkforward` attribute. Purge removes any training label whose
[entry, exit] interval overlaps the test window (extended by `purge`); embargo drops training labels
that start within `embargo` after the test window ends.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Iterator

import numpy as np
import pandas as pd

from goldbot.base import Record

if TYPE_CHECKING:
    from goldbot.config import Settings

WINDOWS = {
    "15m": dict(train_months=24, test_months=3, step_months=3, purge_days=2, embargo_days=1),
    "1h": dict(train_months=36, test_months=6, step_months=6, purge_days=5, embargo_days=2),
    "4h": dict(train_months=48, test_months=6, step_months=6, purge_days=10, embargo_days=4),
    "1d": dict(train_months=60, test_months=12, step_months=12, purge_days=20, embargo_days=10, expanding=True),
}


class Fold(Record):
    k: int
    train_idx: np.ndarray
    test_idx: np.ndarray
    test_start: pd.Timestamp
    test_end: pd.Timestamp
    complete: bool = True        # the data covers the whole test window (a truncated last fold is not)


def walk_forward_splits(labels: pd.DataFrame, *, train_months: int, test_months: int, step_months: int,
                        purge_days: int, embargo_days: int, min_train: int = 200,
                        expanding: bool = False) -> Iterator[Fold]:
    """labels needs ts_utc (signal time) and ts_exit (label end)."""
    ts = pd.DatetimeIndex(pd.to_datetime(labels["ts_utc"], utc=True))
    te = pd.DatetimeIndex(pd.to_datetime(labels["ts_exit"], utc=True))
    start, end = ts.min(), ts.max()
    purge, embargo = pd.Timedelta(days=purge_days), pd.Timedelta(days=embargo_days)
    first_test = start + pd.DateOffset(months=train_months)
    k = 0
    t0 = first_test
    while t0 < end:
        t1 = min(t0 + pd.DateOffset(months=test_months), end + pd.Timedelta(seconds=1))
        test_mask = (ts >= t0) & (ts < t1)
        train_lo = start if expanding else t0 - pd.DateOffset(months=train_months)
        in_window = (ts >= train_lo) & (ts < t0)
        # purge: label interval must end before the test window (minus purge) ...
        no_overlap = te < (t0 - purge)
        # ... and (for any training label after the test window, if we ever allowed it) embargo; here training
        # is strictly before the test window, so embargo guards the *next* fold via the step. Keep it explicit:
        after_embargo = ts >= (t1 + embargo)
        train_mask = (in_window & no_overlap) | (after_embargo & False)
        tr, tst = np.flatnonzero(train_mask), np.flatnonzero(test_mask)
        if len(tr) >= min_train and len(tst) > 0:
            full = t0 + pd.DateOffset(months=test_months) <= end + pd.Timedelta(seconds=1)
            yield Fold(k=k, train_idx=tr, test_idx=tst, test_start=t0, test_end=t1, complete=bool(full))
            k += 1
        t0 = t0 + pd.DateOffset(months=step_months)


def window_for(timeframe: str, settings: Settings | None = None, **overrides: Any) -> dict[str, Any]:
    """The walk-forward settings of a timeframe with a specialist's overrides (e.g. expanding, test_months).

    `WINDOWS` is the default; with `settings` its `walkforward` window (train/test/step months) and its `labels`
    purge/embargo days for the timeframe replace the constant's, and a timeframe settings do not carry (1d, which is
    research-only) keeps the constant. A specialist's overrides apply last."""
    window: dict[str, Any] = dict(WINDOWS[timeframe])
    if settings is not None:
        wf = settings.walkforward.get(timeframe)  # type: ignore[call-overload]
        if wf is not None:
            window.update(train_months=wf.train_months, test_months=wf.test_months, step_months=wf.step_months)
        for key in ("purge_days", "embargo_days"):
            days = getattr(settings.labels, key).get(timeframe)
            if days is not None:
                window[key] = days
    return {**window, **overrides}


def splits_for(labels: pd.DataFrame, timeframe: str, settings: Settings | None = None, **overrides: Any) -> list[Fold]:
    """Walk-forward folds of a timeframe; windows from `settings` when given, else the `WINDOWS` constant."""
    return list(walk_forward_splits(labels, **window_for(timeframe, settings, **overrides)))
