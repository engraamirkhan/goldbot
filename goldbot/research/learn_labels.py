"""The learn-only dl-v1 label route for the history models SEQ-1 and RL-X1 (ML plan 2026-11-25 items 12 and 17;
brief 2026-11-26: "labels only via load_labels on learn 2010-17 ... never confirm/test_old/holdout").

`load_learn` is a train read of `dense_labels.load_labels` with the window fixed to learn 2010-01..2017-12, phase 0
only and no booked read, so confirm and test_old can not be reached from here (load_labels drops any row touching
them on an unbooked read, and refuses the holdout). It never bypasses the store's seal: until ADR 0085 A4 records the
tiers file's sha256 (`dense_labels.RECORDED_TIERS_SHA256`) the read raises `SealedStore`.

Weights: one event per decision bar (both sides and every template of a bar share it), uniqueness over the event's
[entry_utc, horizon_end_utc] against the other events of the same instrument x tf (`objective.uniqueness_weights`,
López de Prado ch. 4), the horizon and not the realised exit (dense_labels: "a realised-exit interval would make a
stopped label's weight depend on its outcome"). Truncated labels (`label_ok` False) are dropped.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F
from goldbot.research import objective as O
from goldbot.research import windows as WN


def load_learn(cache: Path, *, instrument: str, tf: str, years: Iterable[int], tiers: F.Tiers | None,
               columns: Sequence[str], keys: Mapping[tuple[int, int], str], templates: Sequence[str] | None = None,
               proxy_source: str | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Phase-0 learn rows of (instrument, tf) with an event weight `w`; years outside learn are refused first."""
    lo, hi = WN.SPANS[WN.Window.LEARN]
    ys = sorted(int(y) for y in years)
    bad = [y for y in ys if not lo.year <= y < hi.year]
    if bad:
        raise ValueError(f"learn_labels reads learn {lo.year}..{hi.year - 1} only, not {bad}")
    lab, meta = D.load_labels(cache, instrument=instrument, tf=tf, years=ys, tiers=tiers, columns=columns, keys=keys,
                              window=(lo, hi), purpose="train", phases=(0,), templates=templates,
                              proxy_source=proxy_source, booked_read=None, ledger=None)
    lab = lab[lab["label_ok"].astype(bool)].reset_index(drop=True) if len(lab) else lab
    meta = {**meta, "learn_only": True}
    if not len(lab):
        return lab.assign(w=pd.Series(dtype=float)), meta
    ev = lab.drop_duplicates("decision_utc")[["decision_utc", "entry_utc", "horizon_end_utc"]]
    w = O.uniqueness_weights(epoch_ns(pd.DatetimeIndex(ev["entry_utc"])),
                             epoch_ns(pd.DatetimeIndex(ev["horizon_end_utc"])))
    lab = lab.merge(ev[["decision_utc"]].assign(w=w), on="decision_utc", how="left")
    return lab, meta
