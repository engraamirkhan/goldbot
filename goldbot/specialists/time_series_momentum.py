"""Time-series momentum specialist `tsmom` (proposal P4; Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and Pedersen
2017): trade in the direction of the trailing return.

Signal: the mean of the volatility-scaled trailing log returns over 24, 120 and 480 hours (1, 5 and 20 trading days;
each return divided by the 96-hour standard deviation of hourly returns x sqrt(horizon)), so a value of 1 is a
one-sigma move. Long when it is >= `min_score`, short when <= -`min_score`.

Firing: on a fixed schedule, at most once per `schedule_h` hours (bars that close on a multiple of 4 UTC hours; every
bar on 4h), not on sign changes. A sign change happens a few dozen times a year at these horizons, which would give
far fewer than the screen's 1,000 events in 2010-2025; a schedule samples the signal evenly, and one position at a
time (`labels.one_at_a_time`) keeps the trades from overlapping.

Barriers: target 3.0 x ATR(1h), stop 1.5 x ATR(1h), 48 bars (two days). A 1h ATR on gold is several dollars against a
round trip of well under one dollar, so the target is many times the cost. Holding two days crosses up to two
rollovers (four on a Wednesday triple); the net labels pay swap for each (settings `costs.swap_*`), the gross screen
does not.

Timeframes: 1h (default), 4h or 1d. Horizons are in hours (24 per trading day), converted to bars of the decision
timeframe; on 4h set `max_bars` to 12 for the same two-day horizon (the population's timeframe mutation rescales it
automatically).

Daily option (horizon study, `timeframe: "1d"`): the decision bar is the feature-day (COMEX settlement to settlement,
13:30 New York), with no higher context timeframe (`features.mtf.context_tfs`). Its own defaults
(`timeframe_defaults`): vol-scaled returns over 20, 60 and 120 trading days with 60-day volatility, evaluated at every
settlement (a daily bar is its own schedule), target 3.0 x ATR(daily), stop 1.5 x ATR(daily), 10 bars (two weeks),
one position at a time; walk-forward train 60 / test 12 / step 12 months on an expanding window
(`research.walkforward.WINDOWS["1d"]`). Holding up to ten days pays up to ~14 nights of swap, charged in the net
labels.

Slow option (H-01, docs/research/preregistration-2027Q1.md; preset `slow`, `--variants '["slow"]'`): the signal is
computed on the feature-day bars (`signal_tf: "1d"`: 20/60/120-day vol-scaled returns, 60-day volatility, from the d1
context bars) and traded on 4h bars. A daily bar is visible at its settlement (`visible_at`), so the signal is read on
the first 4h bar whose close is at or after it and the trade enters at that bar's close, the open of the first 4h bar
after the signal is visible; each daily bar fires at most once. Barriers come from ATR(1d) (`atr_tf: "1d"`), read on
the same bar and frozen for the trade's life: target 3.0 x ATR(1d), stop 1.5 x ATR(1d), so R is in daily-ATR units.
Time barrier 20 trading days in 4h bars: 124 (a trading week has 31 four-hour bars, six a weekday plus the Sunday
open). Long and short, one position at a time, swap charged per rollover in the net labels. The walk-forward purge
is raised to cover the hold in calendar days (M14: purge >= the label horizon): 31 days instead of the 4h window's 10.
Without the context bars (the live engine calls `candidates`) the slow option proposes nothing: it is research-only
until the engine serves it the daily bars.

Friday stub: the feature-day after Friday's settlement covers only Friday 13:30-17:00 New York (3.5 trading hours)
before the weekend, a sixth "day" a week. The slow option's daily inputs (signal and ATR) drop every daily bar that
spans under MIN_DAILY_SPAN_H (12) trading hours, the weekend closure (Friday 17:00 to Sunday 18:00 New York) not
counted, so a lookback of 20 days is 20 five-a-week trading days and the ATR never averages a 3.5-hour range. The
stub's move is not lost: Monday's return and true range run from Friday's settlement close. The span is computed from
the bar's calendar times only (never its data), so a partial bar is treated the same as the finished one. `schedule_h`
does nothing under `signal_tf` (each daily bar fires once) and is left out of that configuration and its agent id.

Macro gate (H-06, docs/research/preregistration-2027Q1.md, BACKLOG 48; preset `slow_macro`, `--variants
'["slow_macro"]' --macro`): "H-01 with candidates kept only when the 20-day change of DFII10 and of the broad dollar
both agree with the trade side (falling real yield / dollar for longs)", the gate "on `macro-v1`, `asof_join` on
`available_utc`, as known at the signal bar's close; a missing value drops the candidate". The changes are
`macro_drivers`' own (`data.macro.driver_frames`): `macro_real_yield_chg20` (DFII10, difference over 20 observations
of the first releases) and `macro_dollar_chg20` (DTWEXBGS, the same as a ratio), joined at the close of the decision
bar the candidate fires on (`visible_at`, where the trade enters), so a value published at or before that instant is
seen. A long needs both changes < 0, a short both > 0; a zero or NaN change keeps neither. The gate reads the macro
table from `candidates_in_context(ctx=)` (research passes ctx["macro"]) and refuses to run without it, so an H-06
trial cannot be spent on an empty candidate set; the live call (`candidates`) proposes nothing, like the slow option.
Everything else (signal, barriers, hold, purge, declared model inputs) is H-01's.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import tf_seconds
from goldbot.data.macro import driver_frames
from goldbot.data.resample import mid
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features.momentum import tsmom_score
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.technical import atr
from goldbot.labels.triple_barrier import BarrierSpec
from goldbot.research.walkforward import WINDOWS
from goldbot.specialists.base import AgentIdentity, Specialist, register

# decision bars in one trading week (Sunday open to Friday close; 1d counts settlements only, the conservative end)
BARS_PER_WEEK = {"1h": 117, "4h": 31, "1d": 5}
HOLIDAY_SLACK_DAYS = 2        # closed weekdays a hold can span (Christmas and New Year fall in one four-week window)
MIN_DAILY_SPAN_H = 12.0       # a daily input bar spanning fewer trading hours is a stub (the post-settlement Friday)
WEEKEND_TZ = "America/New_York"   # gold closes Friday 17:00 and reopens Sunday 18:00 New York time
# H-06's macro gate inputs (macro_drivers' column -> the name the gate reports): DFII10 and the broad dollar index
MACRO_GATE = {"macro_real_yield_chg20": "real_yield_chg20", "macro_dollar_chg20": "dollar_chg20"}

# H-01 slow TSMOM, exactly as pre-registered (docs/research/preregistration-2027Q1.md)
SLOW: dict[str, Any] = {
    "timeframe": "4h", "signal_tf": "1d", "atr_tf": "1d",
    "lb_fast_h": 20 * 24, "lb_mid_h": 60 * 24, "lb_slow_h": 120 * 24, "vol_window_h": 60 * 24,
    "min_score": 0.5, "target_atr": 3.0, "stop_atr": 1.5,
    "max_bars": 20 * BARS_PER_WEEK["4h"] // 5,
}


def trading_hours(daily: pd.DataFrame) -> pd.Series:
    """Hours each daily bar spans from `ts_utc` to `visible_at`, less its overlap with the weekend closure (Friday
    17:00 to Sunday 18:00 New York). Calendar arithmetic only, so a bar still forming gets the same value."""
    s = utc_index(daily["ts_utc"]).tz_convert(WEEKEND_TZ).tz_localize(None)
    e = utc_index(daily["visible_at"]).tz_convert(WEEKEND_TZ).tz_localize(None)
    friday = s.normalize() - pd.to_timedelta((s.dayofweek - 4) % 7, unit="D")     # the last Friday on or before s
    shut, reopen = friday + pd.Timedelta(hours=17), friday + pd.Timedelta(days=2, hours=18)
    span = (e - s).total_seconds().to_numpy()
    inside = e.where(e < reopen, reopen) - s.where(s > shut, shut)          # time inside the weekend closure
    overlap = np.clip(inside.total_seconds().to_numpy(), 0, None)
    return pd.Series((span - overlap) / 3600, index=daily.index)


def drop_stub_days(daily: pd.DataFrame) -> pd.DataFrame:
    """The daily bars spanning at least MIN_DAILY_SPAN_H trading hours (module docstring: Friday stub)."""
    return daily[trading_hours(daily).to_numpy() >= MIN_DAILY_SPAN_H].reset_index(drop=True)


@register
class TimeSeriesMomentumSpecialist(Specialist):
    family = "tsmom"
    timeframe = "1h"
    timeframes = ("4h", "1d")
    default_config = {
        "lb_fast_h": 24,
        "lb_mid_h": 120,
        "lb_slow_h": 480,
        "vol_window_h": 96,
        "min_score": 0.5,
        "schedule_h": 4,
        "target_atr": 3.0,
        "stop_atr": 1.5,
        "max_bars": 48,
    }
    timeframe_defaults = {
        "1d": {"lb_fast_h": 20 * 24, "lb_mid_h": 60 * 24, "lb_slow_h": 120 * 24, "vol_window_h": 60 * 24,
               "schedule_h": 24, "target_atr": 3.0, "stop_atr": 1.5, "max_bars": 10},
    }
    # off unless a variant sets them: the timeframe of the signal bars and of the barrier ATR (a context timeframe), and
    # H-06's macro gate (module docstring)
    optional_config = {"signal_tf": None, "atr_tf": None, "macro_gate": None}
    presets = {
        "slow": SLOW,                                   # H-01
        "slow_macro": {**SLOW, "macro_gate": True},     # H-06: H-01 with the macro gate, nothing else
    }
    # declared meta-model inputs, chosen by rationale (the momentum signal at each horizon and how much they agree,
    # the volatility regime, trend quality on the bar and on the 4h/daily bars, stretch and nearby levels); the model
    # adds `side`, <= 40 in all
    model_features = (
        "tsmom_z_24", "tsmom_z_120", "tsmom_z_480", "tsmom_score", "tsmom_agree", "ret_1", "ret_4", "ret_16", "ret_96",
        "atr14_pct", "atr_ratio_14_100", "rv_ratio", "vol_tercile", "parkinson_20", "dist_ema20_atr", "dist_ema50_atr",
        "dist_ema200_atr", "slope_ema50", "slope_ema200", "ribbon_state", "adx14", "donchian_pos_20", "bb_z_20",
        "rsi14", "range_width_96_atr", "structure_state", "dist_res_atr", "dist_sup_atr", "tick_vol_ratio_20",
        "session_id", "dow", "h4_adx14", "h4_slope_ema50", "h4_dist_ema50_atr", "h4_ribbon_state",
        "d1_dist_ema50_atr", "d1_slope_ema50", "d1_adx14", "d1_atr_ratio_14_100",
    )

    def __init__(self, identity: AgentIdentity | None = None, **overrides: Any) -> None:
        super().__init__(identity, **overrides)
        for key in ("signal_tf", "atr_tf"):
            tf = self.config.get(key)
            if tf is not None and tf not in context_tfs(self.timeframe):
                raise ValueError(f"tsmom {key}={tf!r} must be a context timeframe of {self.timeframe}: "
                                 f"{context_tfs(self.timeframe)}")
        # M14: purge >= the label horizon. Raised only for a hold longer than the timeframe's purge (the slow option);
        # every default configuration fits its window
        purge = math.ceil(self.hold_calendar_days())
        if purge > WINDOWS[self.timeframe]["purge_days"]:
            self.walkforward = {**type(self).walkforward, "purge_days": purge}

    @classmethod
    def unused_config(cls, config: dict[str, Any]) -> set[str]:
        """`schedule_h` under `signal_tf`: each higher bar fires once, the schedule is never read. `macro_gate` when
        off (None/False): it gates nothing, so it cannot make a second id for the same rule."""
        unused = {"schedule_h"} if config.get("signal_tf") is not None else set()
        return unused | ({"macro_gate"} if not config.get("macro_gate") else set())

    def hold_calendar_days(self) -> float:
        """The longest label life (signal bar to time-out, max_bars + 1 decision bars) in calendar days, weekends and
        HOLIDAY_SLACK_DAYS included."""
        return (self.config["max_bars"] + 1) / BARS_PER_WEEK[self.timeframe] * 7 + HOLIDAY_SLACK_DAYS

    @property
    def label_spec(self) -> BarrierSpec:
        c = self.config
        return BarrierSpec(target_atr=c["target_atr"], stop_atr=c["stop_atr"], max_bars=c["max_bars"], name="tsmom")

    def exit_policy(self) -> dict[str, Any]:
        return {"type": "barrier"}

    def _bars(self, hours: float, tf: str | None = None) -> int:
        return max(1, int(round(hours * 3600 / tf_seconds(tf or self.timeframe))))

    def _context_mid(self, context: dict[str, pd.DataFrame] | None, key: str) -> pd.DataFrame:
        tf = self.config[key]
        bars = (context or {}).get(TF_LABEL[tf])
        if bars is None:
            raise ValueError(f"tsmom {key}={tf!r} needs the {TF_LABEL[tf]} context bars")
        bars = bars.reset_index(drop=True)
        return mid(drop_stub_days(bars) if tf == "1d" else bars)

    def _decided_at(self, mid_bars: pd.DataFrame) -> pd.DatetimeIndex:
        """Each decision bar's close: `visible_at`, or its open + the timeframe when the frame declares none."""
        if "visible_at" in mid_bars:
            return utc_index(mid_bars["visible_at"])
        return utc_index(mid_bars["ts_utc"]) + pd.Timedelta(seconds=tf_seconds(self.timeframe))

    def _visible_on(self, mid_bars: pd.DataFrame, higher: pd.DataFrame) -> np.ndarray:
        """For each decision bar, the position of the last higher bar visible at its close (visible_at <= close), or
        -1 before the first one: the visibility rule of features.mtf, applied at the decision bar's close."""
        vis = epoch_ns(utc_index(higher["visible_at"]))
        return np.searchsorted(vis, epoch_ns(self._decided_at(mid_bars)), side="right") - 1

    def macro_agreement(self, mid_bars: pd.DataFrame, macro: pd.DataFrame) -> pd.DataFrame:
        """H-06's gate inputs per decision bar: the 20-observation change of DFII10 (`real_yield_chg20`) and of the
        broad dollar (`dollar_chg20`, a ratio - 1), `macro_drivers`' values joined by `asof_join` on `available_utc` at
        the bar's close (module docstring: macro gate). NaN before 21 observations are public or without the series.
        Reads bar times and macro rows only."""
        n = len(mid_bars)
        probe = pd.DataFrame({"ts_utc": self._decided_at(mid_bars).as_unit("ns"), "_row": np.arange(n)})
        frames = driver_frames(macro)
        out = pd.DataFrame({name: np.full(n, np.nan) for name in MACRO_GATE.values()}, index=mid_bars.index)
        for col, name in MACRO_GATE.items():
            f = frames[col]
            if len(f):
                joined = asof_join(probe, f[["available_utc", col]]).sort_values("_row")
                out[name] = joined[col].to_numpy(dtype=float)
        return out

    @staticmethod
    def _macro_gate(side: np.ndarray, real_yield_chg: np.ndarray, dollar_chg: np.ndarray) -> np.ndarray:
        """True where both changes agree with the side: falling for a long, rising for a short (zero or NaN: False)."""
        longs = (side == 1) & (real_yield_chg < 0) & (dollar_chg < 0)
        shorts = (side == -1) & (real_yield_chg > 0) & (dollar_chg > 0)
        return np.asarray(longs | shorts)

    def barrier_atr(self, mid_bars: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> pd.Series | None:
        """With `atr_tf`: ATR(14) of the higher bars last visible at each decision bar's close (NaN before the first),
        so the barriers of a trade use the daily ATR known at its signal; otherwise the decision bars' own ATR."""
        if self.config.get("atr_tf") is None:
            return None
        h = self._context_mid(context, "atr_tf")
        pos = self._visible_on(mid_bars, h)
        a = np.append(atr(h, 14).to_numpy(dtype=float), np.nan)      # position -1 reads the NaN
        return pd.Series(a[pos], index=mid_bars.index)

    def candidates_in_context(self, mid_bars: pd.DataFrame, features: pd.DataFrame,
                              context: dict[str, pd.DataFrame] | None,
                              ctx: dict[str, Any] | None = None) -> pd.DataFrame:
        """With `signal_tf`: the score on the higher bars, read on the first decision bar whose close sees each new
        higher bar (once per higher bar); otherwise the scheduled score. With `macro_gate` (H-06): those candidates
        whose DFII10 and dollar changes agree with the side (`macro_agreement`, `_macro_gate`), from ctx["macro"];
        refused without it."""
        cands = self._signal_candidates(mid_bars, context)
        if not self.config.get("macro_gate"):
            return cands
        macro = (ctx or {}).get("macro")
        if macro is None or len(macro) == 0:
            raise ValueError("tsmom macro_gate (H-06) needs the macro table in ctx['macro'] "
                             "(release macro-v1; research_pass --macro)")
        agree = self.macro_agreement(mid_bars, macro)
        idx = cands["idx"].to_numpy(dtype=int)
        keep = self._macro_gate(cands["side"].to_numpy(dtype=int), agree["real_yield_chg20"].to_numpy()[idx],
                                agree["dollar_chg20"].to_numpy()[idx])
        return cands[keep].reset_index(drop=True)

    def _signal_candidates(self, mid_bars: pd.DataFrame, context: dict[str, pd.DataFrame] | None) -> pd.DataFrame:
        """The momentum signal's candidates, before any macro gate."""
        c = self.config
        if c.get("signal_tf") is None:
            return self._scheduled_candidates(mid_bars)
        tf = c["signal_tf"]
        h = self._context_mid(context, "signal_tf")
        horizons = tuple(self._bars(c[k], tf) for k in ("lb_fast_h", "lb_mid_h", "lb_slow_h"))
        score = tsmom_score(h["close"].reset_index(drop=True), horizons, max(2, self._bars(c["vol_window_h"], tf)))
        pos = self._visible_on(mid_bars, h)
        s = np.append(score.to_numpy(dtype=float), np.nan)[pos]
        first = (pos >= 0) & (pos != np.concatenate(([-1], pos[:-1])))
        side = np.where(s >= c["min_score"], 1, np.where(s <= -c["min_score"], -1, 0))
        hit = first & (side != 0) & np.isfinite(s)
        return pd.DataFrame({"idx": np.flatnonzero(hit), "side": side[hit].astype(int)})

    def candidates(self, mid_bars: pd.DataFrame, features: pd.DataFrame) -> pd.DataFrame:
        if self.config.get("signal_tf") is not None or self.config.get("macro_gate"):
            # the signal needs the higher bars and the gate the macro table (candidates_in_context); without them
            # propose nothing (fail closed)
            return pd.DataFrame({"idx": np.zeros(0, dtype=int), "side": np.zeros(0, dtype=int)})
        return self._scheduled_candidates(mid_bars)

    def _scheduled_candidates(self, mid_bars: pd.DataFrame) -> pd.DataFrame:
        """The decision bars' own score on the fixed schedule (module docstring: firing)."""
        c = self.config
        horizons = tuple(self._bars(c[k]) for k in ("lb_fast_h", "lb_mid_h", "lb_slow_h"))
        score = tsmom_score(mid_bars["close"].reset_index(drop=True), horizons, max(2, self._bars(c["vol_window_h"])))
        # schedule: the bar's close (open + timeframe) falls on a whole multiple of schedule_h UTC hours; a bar as long as
        # the schedule (4h with schedule_h 4, a daily bar closing at settlement) is on it every time
        close_ts = pd.DatetimeIndex(pd.to_datetime(mid_bars["ts_utc"], utc=True)) + pd.Timedelta(seconds=tf_seconds(self.timeframe))
        step = int(c["schedule_h"])
        if tf_seconds(self.timeframe) >= max(step, 1) * 3600:
            on_schedule = np.ones(len(close_ts), dtype=bool)
        else:
            on_schedule = np.asarray((close_ts.minute == 0) & (close_ts.hour % max(step, 1) == 0))
        s = score.to_numpy(dtype=float)
        side = np.where(s >= c["min_score"], 1, np.where(s <= -c["min_score"], -1, 0))
        hit = on_schedule & (side != 0) & np.isfinite(s)
        return pd.DataFrame({"idx": np.flatnonzero(hit), "side": side[hit].astype(int)})
