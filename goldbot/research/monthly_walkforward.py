"""Monthly walk-forward refit of record (ADR 0106 decision 7: "Retraining. Monthly at the least, on a fixed schedule,
walk-forward. The weekly and daily refits continue"; goal "Retraining on a fixed schedule (e.g. monthly), every change
reversible").

The scheduler job `monthly_walkforward` (goldbot/ops/jobs.py) runs on the 1st of every month at a fixed UTC time
(scheduler.monthly_walkforward). Its window ends at 00:00 UTC on the 1st: every trainer listed in
`model_quality.monthly_trainers` (config/settings.yaml, "package.module:function") is called as

    trainer(window_end=<the 1st, 00:00 UTC>, manifests_root=<research.models_dir>/manifests) -> list[RunManifest]

and refits on data strictly before `window_end`, recording one run manifest per artifact (run_manifest.record /
fit_and_record with schedule="monthly_walkforward"). The job checks each returned manifest (stored, on schedule, train
end, label end (quant Q4: "The run manifest records `label_end_max`, the latest horizon end among training rows.
`_problems` refuses a manifest whose `label_end_max >= window_end`.") and scaler fit end before the window end) and reports them. It never promotes, restores or changes any model's
status: a refit artifact is a challenger at most, and promotion stays with the promotion gates (ADR 0106 decision 6).
One trainer's failure never skips another; any failure or bad manifest fails the job after all have run, so health
shows `job:monthly_walkforward` FAIL.
"""
from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd

from goldbot.research import run_manifest as rm

log = logging.getLogger("goldbot.monthly_walkforward")

Trainer = Callable[..., Sequence[rm.RunManifest]]


class MonthlyRunFailed(RuntimeError):
    """At least one trainer failed or returned a manifest that breaks the monthly rule."""


def window_end(slot: pd.Timestamp) -> pd.Timestamp:
    """00:00 UTC on the first day of the slot's month: the refit reads only data before it."""
    t = pd.Timestamp(slot).tz_convert("UTC")
    return pd.Timestamp(year=t.year, month=t.month, day=1, tz="UTC")


def _trainer(ref: str) -> Trainer:
    mod, _, fn = ref.partition(":")
    f = getattr(importlib.import_module(mod), fn)
    if not callable(f):
        raise TypeError(f"{ref} is not callable")
    return f


def _problems(m: rm.RunManifest, end: pd.Timestamp, root: Path) -> list[str]:
    out: list[str] = []
    if m.schedule != "monthly_walkforward":
        out.append(f"{m.manifest_id[:12]} schedule is {m.schedule}, not monthly_walkforward")
    if m.train_end >= end:
        out.append(f"{m.manifest_id[:12]} train_end {m.train_end} is not before the window end {end}")
    if m.label_end_max >= end:      # quant Q4: purge by label end, not by decision time
        out.append(f"{m.manifest_id[:12]} label_end_max {m.label_end_max} is not before the window end {end}")
    if m.scaler_fit_end >= end:
        out.append(f"{m.manifest_id[:12]} scaler_fit_end {m.scaler_fit_end} is not before the window end {end}")
    try:
        rm.load(m.manifest_id, root)
    except rm.ManifestError as exc:
        out.append(f"{m.manifest_id[:12]} is not stored under {root}: {exc}")
    return out


def run(slot: pd.Timestamp, *, trainers: Sequence[str], manifests_root: Path | str) -> dict[str, Any]:
    """Call every trainer for the slot's window and check what it recorded. Raises MonthlyRunFailed after all ran
    when any failed; the return value is the job's record (stored by the scheduler)."""
    end = window_end(slot)
    root = Path(manifests_root)
    if not trainers:
        return {"window_end": end.isoformat(), "trained": 0, "manifests": {}, "errors": {},
                "note": "no trainer configured in model_quality.monthly_trainers: nothing was refit"}
    manifests: dict[str, list[str]] = {}
    errors: dict[str, str] = {}
    for ref in trainers:
        try:
            got = list(_trainer(ref)(window_end=end, manifests_root=root))
        except Exception as exc:   # noqa: BLE001 - one trainer's failure never skips another; the job fails below
            log.exception("monthly walk-forward trainer %s failed", ref)
            errors[ref] = f"{type(exc).__name__}: {exc}"
            continue
        manifests[ref] = [m.manifest_id for m in got]
        bad = [p for m in got for p in _problems(m, end, root)]
        if not got:
            bad.append("returned no manifest")
        if bad:
            errors[ref] = "; ".join(bad)
    out = {"window_end": end.isoformat(), "trained": sum(len(v) for v in manifests.values()), "manifests": manifests,
           "errors": errors}
    if errors:
        raise MonthlyRunFailed("; ".join(f"{k}: {v}" for k, v in errors.items()))
    return out
