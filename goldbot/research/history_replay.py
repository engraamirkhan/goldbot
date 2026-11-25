"""History replay of a pre-named exploration arm on Dukascopy XAUUSD 1m, one (arm, year) at a time (factored from
scripts/research/rb_history.py for H-101, docs/research/prereg-2026-11-25-model-zoo-fusion.md section 6 item 2).

- `candidates`: the arm's rule (`research.arm_rules`, as the history matrix replays it) on the year's bars, every
  candidate taken or not (no slot limit). Outcome-blind: no barrier is searched and no label is computed, so a count
  (Stage 0) never sees an outcome.
- `label`: the established replay (`scripts/run_arm_backtest.py` `run_arm`: entry at the open of the first 1m bar
  after the decision close, barriers on mid 1m bars) with the W2 bps cost of record (`bps_net`: RT_BP of the entry,
  OVERSHOOT_BP more on a stop exit; card 2.1 "net R per candidate under W2 bps costs ... the stop's overshoot
  included") and the gross diagnostic `r_mid` ("the same trade at mid, no cost": price move only, no swap).
- `features_at`: `feature_snapshots.decision_features` per decision, "every minute joined with `asof_join` on
  `available_utc` (history minutes: `available_utc` = nominal close, the repo's stored-bar convention)"; a snapshot
  whose latest minute is later than its decision raises (look-ahead).

Never reads the holdout: every bar frame is cut at HOLDOUT_FROM and asserted (`arms.cut_at_holdout` /
`assert_no_holdout_rows`). Warm-up and exit tails may include adjacent-window bars for state only. Market data and the
parquet cache stay local (`~/tmp` or the VM's data dir): never committed, never published.
"""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.resample import resample_bars
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.research import arm_rules as R
from goldbot.research import arms as A
from goldbot.research.feature_snapshots import ENGINE_MINUTES, decision_features

ROOT = Path(__file__).resolve().parents[2]
RT_BP = 0.575            # W2 bps round trip ($0.23 at $4,000), cost of record scale
OVERSHOOT_BP = 1.75      # stop-exit overshoot in bp ($0.70 at $4,000)
WARMUP_DAYS = 120        # rule state (the red-box channel needs 313 4h bars, about 75 calendar days)
TAIL_DAYS = 14           # bars after the span so a late entry can exit (still cut at the holdout)
MAX_OPEN_EVERY = 1_000_000
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14_400}
CACHE = Path.home() / "tmp" / "history-replay"


CODE_FILES = ("goldbot/research/history_replay.py", "goldbot/research/feature_snapshots.py", "scripts/run_arm_backtest.py")


def _blob_sha(path: Path) -> str:
    """The git blob SHA of a file (what `git hash-object` prints), computed without git."""
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data, usedforsecurity=False).hexdigest()


def code_hash() -> str:
    """sha256 over the git blob SHAs of the replay, feature and label code: part of every cache key, so a changed
    rule, feature or barrier search never reads a stale cached label or feature."""
    return hashlib.sha256("|".join(f"{f}:{_blob_sha(ROOT / f)}" for f in CODE_FILES).encode()).hexdigest()


def load_arm(arm_id: str, *, replay_as: str | None = None) -> A.Arm:
    """The arm row (config/exploration_arms.yaml) whose rule replays `arm_id`, with no slot limit (every candidate,
    taken or not). `replay_as`: a successor recorded under a new id replays its predecessor's row (RBR15 -> RB15)."""
    arms = A.load_arms(ROOT / "config" / "exploration_arms.yaml")
    want = replay_as or arm_id
    a = next((x for x in arms if x.id == want), None)
    if a is None:
        raise KeyError(f"{want!r} is not an arm of config/exploration_arms.yaml")
    return a.model_copy(update={"max_open": MAX_OPEN_EVERY})


def year_span(lo: pd.Timestamp, hi: pd.Timestamp, year: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    """[lo, hi) cut to the calendar year."""
    return max(lo, pd.Timestamp(f"{year}-01-01", tz="UTC")), min(hi, pd.Timestamp(f"{year + 1}-01-01", tz="UTC"))


def year_bars(bars_dir: Path, lo: pd.Timestamp, hi: pd.Timestamp) -> pd.DataFrame:
    """1m bars for entries in [lo, hi) with WARMUP_DAYS before and TAIL_DAYS after; nothing at or after the holdout."""
    from goldbot.data.release import read_bar_files
    first = (lo - pd.Timedelta(days=WARMUP_DAYS)).year
    last = min((hi + pd.Timedelta(days=TAIL_DAYS)).year, A.HOLDOUT_FROM.year)
    b = A.cut_at_holdout(read_bar_files(bars_dir, first, last))
    ts = utc_index(b["ts_utc"])
    b = b.loc[np.asarray((ts >= lo - pd.Timedelta(days=WARMUP_DAYS)) & (ts < hi + pd.Timedelta(days=TAIL_DAYS)))]
    b = b.reset_index(drop=True)
    A.assert_no_holdout_rows(b)
    return b


def load_blackout(calendar_dir: Path) -> tuple[np.ndarray, np.ndarray]:
    """Tier-1 blackout windows from the calendar-v1 files (as `run_arm_backtest.load_blackout`)."""
    from goldbot.data.release import read_us_calendar_files
    cal = read_us_calendar_files(calendar_dir)
    if cal.empty:
        raise FileNotFoundError(f"no calendar rows in {calendar_dir}: the tier-1 blackout cannot be applied")
    return R.blackout_windows(cal)


def horizon_seconds(arm: A.Arm, bars: pd.DataFrame, idx: np.ndarray, max_bars: np.ndarray) -> np.ndarray:
    """Each candidate's max_hold_s: its true maximum wall-clock exposure, from its decision close to the close of the
    decision bar its time barrier ends on. The label's time barrier counts decision bars ("the close of decision bar
    idx + 1 + max_bars", `triple_barrier_next_open_1m`, with the larger of the arm's hold_bars and the candidate's
    max_bars here so it is never short) and the bar frame has no rows while the market is shut, so a hold across a
    weekend, the daily break, a holiday or a data gap lasts longer on the clock than (bars + 1) x the timeframe. Never
    less than that nominal span (a barrier clipped at the end of the frame). The label stage asserts every exit
    inside it."""
    idx = np.asarray(idx, dtype=int)
    held = np.maximum(np.asarray(max_bars, dtype=float), float(arm.params["hold_bars"])).astype(int)
    nominal = (held + 1) * float(TF_SECONDS[arm.timeframe])
    if not len(idx):
        return nominal
    close = epoch_ns(utc_index(bars["visible_at"]))
    last = np.minimum(idx + 1 + held, len(bars) - 1)
    return np.maximum((close[last] - close[idx]) / 1e9, nominal)


def decision_bars(b1: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    return b1 if timeframe == "1m" else resample_bars(b1, timeframe).reset_index(drop=True)


def session_day(ts: pd.Series | pd.DatetimeIndex) -> np.ndarray:
    """The session day of a time: the New York date after the 17:00 roll (the broker's server day)."""
    t = utc_index(pd.Series(ts)).tz_convert("America/New_York") + pd.Timedelta(hours=7)
    return np.asarray(t.normalize().tz_localize(None).to_numpy().astype("datetime64[D]"))


def candidates(arm_id: str, arm: A.Arm, b1: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp,
               blackout: tuple[np.ndarray, np.ndarray]) -> pd.DataFrame:
    """Outcome-blind: the arm's candidates decided in [lo, hi) (decision = the decision bar's close), with side,
    max_hold_s (`horizon_seconds`: the true
    wall-clock exposure, market-shut gaps included) and session day. No barrier, no label."""
    c = _horizons(arm, b1, blackout)
    cols = ["arm", "decision_utc", "side", "max_hold_s", "session_day"]
    if c.empty:
        return pd.DataFrame(columns=cols)
    dec = utc_index(c["decision_utc"])
    c = c[np.asarray((dec >= lo) & (dec < hi))].reset_index(drop=True)
    dec = utc_index(c["decision_utc"])
    return pd.DataFrame({"arm": arm_id, "decision_utc": dec, "side": c["side"].to_numpy(dtype=float),
                         "max_hold_s": c["max_hold_s"].to_numpy(dtype=float), "session_day": session_day(dec).astype(str)})


def _horizons(arm: A.Arm, b1: pd.DataFrame, blackout: tuple[np.ndarray, np.ndarray]) -> pd.DataFrame:
    """Every candidate of the arm's rule on `b1`: decision_utc, side and max_hold_s (`horizon_seconds`). The one
    source of the horizon for `candidates` and `label`, so the count's purge and the label's are the same."""
    bars = decision_bars(b1, arm.timeframe)
    c = R.candidates(arm, R.RuleContext(bars=bars, b1=b1, blackout_lo=blackout[0], blackout_hi=blackout[1]))
    if c.empty:
        return pd.DataFrame({"decision_utc": pd.DatetimeIndex([], tz="UTC"), "side": np.zeros(0),
                             "max_hold_s": np.zeros(0)})
    idx = c["idx"].to_numpy(int)
    return pd.DataFrame({"decision_utc": utc_index(bars["visible_at"])[idx], "side": c["side"].to_numpy(dtype=float),
                         "max_hold_s": horizon_seconds(arm, bars, idx, c["max_bars"].to_numpy(dtype=float))})


def bps_net(trades: pd.DataFrame) -> np.ndarray:
    """W2 cost of record (rb-history card section 4): move (swap included) less RT_BP of the entry, less
    OVERSHOOT_BP on a stop exit, in R of the candidate's own stop."""
    entry = trades["entry"].to_numpy(float)
    stop = (trades["barrier_hit"] == "stop").to_numpy(float)
    pnl = trades["move_usd"].to_numpy(float) - RT_BP * 1e-4 * entry - OVERSHOOT_BP * 1e-4 * entry * stop
    return pnl / trades["risk_usd"].to_numpy(float)


def _run_arm_module() -> ModuleType:
    """scripts/run_arm_backtest.py, the established history replay (one implementation for live and history rules)."""
    spec = importlib.util.spec_from_file_location("run_arm_backtest", ROOT / "scripts" / "run_arm_backtest.py")
    if spec is None or spec.loader is None:
        raise ImportError("scripts/run_arm_backtest.py not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _trade_hold_s(arm: A.Arm, b1: pd.DataFrame, blackout: tuple[np.ndarray, np.ndarray], dec: pd.DatetimeIndex,
                  side: np.ndarray) -> np.ndarray:
    """Each trade's max_hold_s from its own candidate (`_horizons`, as `candidates`), matched on (decision, side); a
    trade without its candidate raises."""
    c = _horizons(arm, b1, blackout)
    table = {(int(t), float(sd)): float(h) for t, sd, h in zip(epoch_ns(utc_index(c["decision_utc"])),
                                                                c["side"].to_numpy(dtype=float),
                                                                c["max_hold_s"].to_numpy(dtype=float), strict=True)}
    out = np.array([table.get((int(t), float(sd)), np.nan) for t, sd in zip(epoch_ns(dec), side, strict=True)])
    if np.isnan(out).any():
        raise ValueError(f"{arm.id}: a replayed trade without its candidate: its horizon is unknown")
    return out


def label(arm_id: str, arm: A.Arm, b1: pd.DataFrame, lo: pd.Timestamp, hi: pd.Timestamp,
          blackout: tuple[np.ndarray, np.ndarray]) -> pd.DataFrame:
    """The labelled candidates decided in [lo, hi): candidate_id, decision, side, label exit, max_hold_s, session day,
    net R `r` (W2 bps) and mid R `r_mid`. Called only by a booked stage (Stage L / C / T), never by a count. A label exit
    after the candidate's horizon raises (the purge would be wrong)."""
    run = _run_arm_module()
    trades, _ = run.run_arm(b1, arm, lo, hi - pd.Timedelta(days=1), A.load_costs(ROOT / "config" / "arm_costs.yaml"),
                            blackout)
    cols = ["candidate_id", "arm", "decision_utc", "side", "label_exit_utc", "max_hold_s", "session_day", "r", "r_mid"]
    if trades.empty:
        return pd.DataFrame(columns=cols)
    dec = utc_index(trades["ts_utc"])
    trades = trades[np.asarray((dec >= lo) & (dec < hi))].reset_index(drop=True)
    dec = utc_index(trades["ts_utc"])
    side = trades["side"].to_numpy(dtype=float)
    hold_s = _trade_hold_s(arm, b1, blackout, dec, side)
    exit_ts = utc_index(trades["ts_exit"])
    from goldbot.research.self_retrain import MAX_SEND_DELAY
    if (exit_ts > dec + MAX_SEND_DELAY + pd.to_timedelta(hold_s, unit="s")).any():
        raise ValueError(f"{arm_id}: a label exit after its horizon (decision + delay + max_hold_s)")
    mid_r = side * (trades["exit_px"].to_numpy(float) - trades["entry"].to_numpy(float)) / trades["risk_usd"].to_numpy(float)
    ns = epoch_ns(dec)
    return pd.DataFrame({"candidate_id": [f"{arm_id}:{int(t)}:{int(s)}" for t, s in zip(ns, side, strict=True)],
                         "arm": arm_id, "decision_utc": dec, "side": side, "label_exit_utc": exit_ts,
                         "max_hold_s": hold_s, "session_day": session_day(dec).astype(str),
                         "r": bps_net(trades), "r_mid": mid_r})


def history_minutes(b1: pd.DataFrame) -> pd.DataFrame:
    """1m bars with `available_utc` = the nominal close (`visible_at`), the stored-bar convention for history."""
    out = b1.reset_index(drop=True).copy()
    out["available_utc"] = pd.to_datetime(out["visible_at"], utc=True)
    return out


def features_at(minutes: pd.DataFrame, timeframe: str, decisions: pd.Series | pd.DatetimeIndex) -> pd.DataFrame:
    """The engine's default feature row at each decision (`decision_features` of `timeframe`), reading only minutes
    joined with `asof_join` on `available_utc` at the decision. One row per distinct decision: `decision_utc`,
    `available_utc_max`, `memory_from_utc` (the first minute of its ENGINE_MINUTES memory) and the feature columns
    (NaN where the history is too short). Prices only: a learn decision in early 2010 reads late-2009 minutes (test_old
    prices as indicator state, never a test_old label); a test_old decision early in 2008 may read pre-2008-02 minutes
    (disclosed by Stage T as `memory_before_test_old`)."""
    dec = pd.DatetimeIndex(pd.to_datetime(pd.Series(decisions), utc=True)).unique().sort_values()
    if len(dec) == 0:
        return pd.DataFrame(columns=["decision_utc", "available_utc_max", "memory_from_utc"])
    right = pd.DataFrame({"available_utc": minutes["available_utc"].to_numpy(), "pos": np.arange(len(minutes))})
    j = asof_join(pd.DataFrame({"ts_utc": dec}), right)
    rows: list[dict[str, Any]] = []
    for at, pos in zip(dec, j["pos"].to_numpy(), strict=True):
        row: dict[str, Any] = {"decision_utc": at, "available_utc_max": pd.NaT, "memory_from_utc": pd.NaT}
        if np.isfinite(pos):
            p = int(pos)
            seen = minutes.iloc[max(0, p + 1 - ENGINE_MINUTES):p + 1]
            row["memory_from_utc"] = pd.Timestamp(seen["ts_utc"].iloc[0])
            snap = decision_features(seen, timeframe, at)
            if snap is not None:
                if snap.available_utc_max > at:
                    raise ValueError(f"look-ahead: a feature minute available at {snap.available_utc_max} > {at}")
                row.update(snap.features)
                row["available_utc_max"] = snap.available_utc_max
        rows.append(row)
    return pd.DataFrame(rows)


__all__ = ["CACHE", "code_hash", "OVERSHOOT_BP", "RT_BP", "bps_net", "candidates", "features_at", "history_minutes", "label",
           "load_arm", "load_blackout", "session_day", "year_bars", "year_span"]
