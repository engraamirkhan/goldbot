"""Superseded feature versions, kept so models trained on them keep scoring exactly the frame they were trained on.

Sessions v1 (before the DST fix, playbook D-1): fixed UTC session windows, asia 23:00-07:00, london 07:00-12:30,
newyork 12:30-21:00, and fixed UTC opens for `min_since_*_open`. The code below reproduces `session`@1,
`session_zones`@1 and `market_structure`@1 exactly (tests/test_sessions_v1_migration.py compares them with the output
of the pre-fix code, tests/data/sessions_v1_golden.parquet). Registered with `legacy=True`: never part of FEATURES,
so research, retrains and the default feature set build v2; the engine rebuilds v1 columns only for a model whose
recorded feature version needs them (`registry.versions_for`). Delete a legacy version once no champion, challenger,
shadow model or previous champion (the model_watch rollback target, which can become champion again) in any registry
carries a feature version that needs it: the health check `feature_versions` counts all of them.
"""
from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.features.columns import Columns
from goldbot.features.registry import FeatureCtx, feature
from goldbot.features.session import _dst_flag
from goldbot.features.trader import market_structure_frame, session_zones_frame

DAY_NS = 86_400 * 1_000_000_000
V1_SESSIONS_UTC: dict[str, tuple[time, time]] = {
    "asia": (time(23, 0), time(7, 0)),
    "london": (time(7, 0), time(12, 30)),
    "newyork": (time(12, 30), time(21, 0)),
}
V1_SESSION_OPENS_UTC = {"asia": 23, "london": 7, "newyork": 12.5}


def v1_session_label(utc_ts: pd.DatetimeIndex) -> np.ndarray:
    """The pre-fix `SessionTable.session_label`: fixed UTC windows, "asia" outside every window."""
    idx = pd.DatetimeIndex(utc_ts).tz_convert("UTC")
    secs = idx.hour * 3600 + idx.minute * 60
    out = np.full(len(idx), "asia", dtype=object)
    for name, (a, b) in V1_SESSIONS_UTC.items():
        sa, sb = a.hour * 3600 + a.minute * 60, b.hour * 3600 + b.minute * 60
        mask = (secs >= sa) & (secs < sb) if sa < sb else (secs >= sa) | (secs < sb)
        out[np.asarray(mask)] = name
    return out


def v1_session_instances(ts: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """The pre-fix `trader.session_instances`: fixed UTC windows (code -1 and start -1 outside every window)."""
    ns = epoch_ns(ts)
    code = np.full(len(ns), -1, dtype=np.int64)
    start = np.full(len(ns), -1, dtype=np.int64)
    tod = ns % DAY_NS
    for k, (a, b) in enumerate(V1_SESSIONS_UTC.values()):
        so = (a.hour * 3600 + a.minute * 60) * 1_000_000_000
        length = ((b.hour * 3600 + b.minute * 60) * 1_000_000_000 - so) % DAY_NS
        since = (tod - so) % DAY_NS
        inside = since < length
        code[inside] = k
        start[inside] = ns[inside] - since[inside]
    return code, start


@feature("session", "calendar", version="1", legacy=True)
def f_session_v1(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    idx = pd.DatetimeIndex(df["ts_utc"]).tz_convert("UTC")
    sess = v1_session_label(idx)
    out = Columns(df.index)
    out["session_id"] = pd.Categorical(sess, categories=["asia", "london", "newyork"]).codes
    hour = idx.hour + idx.minute / 60
    for name, h in V1_SESSION_OPENS_UTC.items():
        m = (hour - h) % 24
        out[f"min_since_{name}_open"] = m * 60
    out["dow"] = idx.dayofweek
    out["hour_utc"] = idx.hour
    out["us_dst"] = _dst_flag(idx, "America/New_York")
    out["eu_dst"] = _dst_flag(idx, "Europe/London")
    return out.frame()


@feature("session_zones", "session", version="1", lookback=1000, signed={"prev_sess_pos": 0.0}, legacy=True)
def f_session_zones_v1(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return session_zones_frame(df, v1_session_instances)


@feature("market_structure", "structure", version="1", lookback=1000,
         signed={"sweep_dir": 0.0, "bos_event": 0.0, "bos_dir": 0.0}, legacy=True)
def f_market_structure_v1(df: pd.DataFrame, ctx: FeatureCtx) -> pd.DataFrame:
    return market_structure_frame(df, ctx, v1_session_instances)
