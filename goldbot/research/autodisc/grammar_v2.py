"""Discovery grammar v2 (ADR 0058 amendment 2026-11-02; `prereg-amendment-2026-10-30-kill-rules.md` A4 and its quant
conditions; pre-registration `docs/research/prereg-2026-11-02-discovery-v2.md`).

v2 keeps v1's language (`spec.StrategySpec`, the G2 search in `gen_gp`, the honesty set in `nightly`) and adds a closed,
enumerated set of primitives. Every one is a when-to-trade GATE or a side; none is a new entry signal:

    gate leaves (entry/filter predicates, kind "regime" / "time" / "macro"):
      har_lo / har_mid / har_hi     H-16's HAR range forecast band for the opportunity day (0 / 1 / 2), monthly
                                    walk-forward refits inside the window (`day_type.har_forecast`)
      nr4 / nr7                     the last completed stub-free opportunity day was the narrowest of 4 / 7
      event_day                     a tier-1 US release (NFP, CPI, PCE, FOMC) is scheduled on the opportunity day, as
                                    known from the calendar's available_utc
      ry_up / ry_down               20-observation change of the 10y real yield (DFII10 first releases) > 0 / < 0
      usd_up / usd_down             20-observation % change of the broad dollar > 0 / < 0
      gvz_lo / gvz_mid / gvz_hi     GVZ's tercile over its own previous 252 observations
    side primitive:
      r4h_sign                      sign of the 4-hour mid return ending at the decision bar's close (H-A); a spec
                                    trades `with` or `against` it (a pseudo-family: never an entry leaf)
    exits (`outcomes.v2_menu`, closed list of 5): stop 1.0 ATR with a time exit only, target 1R / 2R / 3R, or break-even
      at +1 R with a 1 R trail; every exit closes at the timeframe's MAX_BARS.

Point in time. Every value is read at the decision bar's close T (its `visible_at`), where the rule decides and the
tensor enters. Daily values (HAR band, NR4/NR7) are emitted at their opportunity day's close (22:00 UTC) and reach a
bar only through `store.asof_join` on that `available_utc`; macro values through `asof_join` on each series' own
`available_utc` (`data.macro.driver_frames`); the calendar by the latest row with available_utc <= T
(`day_type.tier1_columns`); r4h from 1m mid closes whose `visible_at` <= T. Tests perturb everything after a cut and
require every mask before it unchanged.

Window. The macro and calendar frames are cut at the explore cutoff on available_utc (and on value_date / event_utc), so
no 2018+ row is ever held; the 1m bars arrive as `windows.ExploreFrame` as in v1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from goldbot.data.macro import driver_frames
from goldbot.data.store import asof_join
from goldbot.data.timeutil import epoch_ns, utc_index
from goldbot.features import day_type as dt
from goldbot.research.arms import Arm
from goldbot.research.autodisc.contracts import BoolArr, SideArr
from goldbot.research.autodisc.primitives import PrimitiveProvider, UnsupportedLeaf
from goldbot.research.autodisc.spec import Group, Leaf, Predicate
from goldbot.research.autodisc.windows import EXPLORE_CUTOFF, ExploreFrame

R4H = "r4h_sign"                       # the side pseudo-family (structure/family_rule, params {"rule": "r4h_sign"})
R4H_HOURS = 4
HAR = {"har_lo": 0, "har_mid": 1, "har_hi": 2}
NR = ("nr4", "nr7")
GVZ = {"gvz_lo": 0, "gvz_mid": 1, "gvz_hi": 2}
GVZ_RANK_OBS = 252
MACRO_SIGN = {"ry_up": ("macro_real_yield_chg20", 1), "ry_down": ("macro_real_yield_chg20", -1),
              "usd_up": ("macro_dollar_chg20", 1), "usd_down": ("macro_dollar_chg20", -1)}
V2_REGIME = (*HAR, *NR)
V2_TIME = ("event_day",)
V2_MACRO = (*MACRO_SIGN, *GVZ)
# ordinal ladders added to v1's for the plateau neighbours (one rung = one step)
V2_LADDERS: tuple[tuple[str, ...], ...] = (tuple(HAR), NR, tuple(GVZ))
V2_TARGET_LADDER = ("1R", "2R", "3R")
V2_TARGETS = ("time", "1R", "2R", "3R", "be_trail")


def _cut_rows(frame: pd.DataFrame | None, *cols: str) -> pd.DataFrame | None:
    """The rows of `frame` whose every named time column is before the explore cutoff (None stays None)."""
    if frame is None or len(frame) == 0:
        return frame
    keep = np.ones(len(frame), dtype=bool)
    for c in cols:
        if c in frame.columns:
            keep &= np.asarray(pd.to_datetime(frame[c], utc=True) < EXPLORE_CUTOFF)
    return frame[keep].reset_index(drop=True)


def _sign_leaf(pred: Leaf) -> bool:
    n = pred.name
    return ((pred.kind == "regime" and n in V2_REGIME) or (pred.kind == "time" and n in V2_TIME)
            or (pred.kind == "macro" and n in V2_MACRO))


class PrimitiveProviderV2(PrimitiveProvider):
    """v1's provider plus the v2 gate leaves and the r4h side (module docstring). `macro`: the long macro frame
    (release macro-v1); `calendar`: the US calendar frame (release calendar-v1). Both are cut at the explore cutoff."""

    def __init__(self, b1: ExploreFrame, macro: pd.DataFrame | None = None, calendar: pd.DataFrame | None = None,
                 arms: list[Arm] | None = None, arm_ids: dict[tuple[str, str], str] | None = None, *,
                 min_fit_rows: int = dt.MIN_FIT_ROWS, gvz_rank_obs: int = GVZ_RANK_OBS) -> None:
        super().__init__(b1, arms, arm_ids)
        self._min_fit_rows, self._gvz_rank_obs = int(min_fit_rows), int(gvz_rank_obs)    # frozen defaults; tests shrink them
        self._macro = _cut_rows(macro, "available_utc", "value_date")
        self._cal = _cut_rows(calendar, "available_utc", "event_utc")
        self._v2: dict[str, pd.DataFrame] = {}

    # ------------------------------------------------------------------ vocabulary
    @staticmethod
    def check_supported_v2(pred: Predicate) -> None:
        """Raise `UnsupportedLeaf` unless every leaf is a v1 leaf or a v2 gate leaf."""
        if isinstance(pred, Group):
            for c in pred.children:
                PrimitiveProviderV2.check_supported_v2(c)
            return
        if pred.kind == "structure" and pred.name == "family_rule" and pred.params.get("rule") == R4H:
            raise UnsupportedLeaf("r4h_sign is a side primitive only, never an entry leaf")
        if not _sign_leaf(pred):
            PrimitiveProvider.check_supported(pred)

    def _check(self, pred: Predicate) -> None:
        self.check_supported_v2(pred)

    # ------------------------------------------------------------------ the decision instants
    def _close_ns(self, timeframe: str) -> np.ndarray:
        return np.asarray(epoch_ns(utc_index(self.bars(timeframe)["visible_at"])), dtype=np.int64)

    def _probe(self, timeframe: str) -> pd.DataFrame:
        vis = utc_index(self.bars(timeframe)["visible_at"]).as_unit("ns")
        return pd.DataFrame({"ts_utc": vis, "_row": np.arange(len(vis))})

    def v2_columns(self, timeframe: str) -> pd.DataFrame:
        """Per decision bar, as known at its close: har_band, nr4, nr7, tier1_today, the macro drivers, gvz_tercile."""
        if timeframe in self._v2:
            return self._v2[timeframe]
        probe = self._probe(timeframe)
        n = len(probe)
        out = pd.DataFrame(index=np.arange(n))
        # daily inputs from the explore window's 1h mid bars (H-16's window range needs bars <= 60 minutes)
        inp = dt.daily_inputs(dt.daily_bars(self.mid_bars("1h")))
        for c in ("har_band", "nr4", "nr7"):
            out[c] = np.nan
        if len(inp):
            fc = dt.har_forecast(inp, min_rows=self._min_fit_rows)
            emit = pd.DataFrame({"available_utc": utc_index(inp["close_utc"]), "har_band": fc["band"].to_numpy(),
                                 "nr4": inp["nr4"].to_numpy(), "nr7": inp["nr7"].to_numpy()})
            j = asof_join(probe, emit).sort_values("_row")
            for c in ("har_band", "nr4", "nr7"):
                out[c] = j[c].to_numpy(dtype=float)
        tier = dt.tier1_columns(pd.DataFrame({"ts_utc": probe["ts_utc"]}), self._cal)
        out["tier1_today"] = tier["opp_tier1_today"].to_numpy(dtype=float)
        frames = driver_frames(self._macro) if self._macro is not None and len(self._macro) else {}
        for col in ("macro_real_yield_chg20", "macro_dollar_chg20"):
            out[col] = np.nan
            f = frames.get(col)
            if f is not None and len(f):
                out[col] = asof_join(probe, f[["available_utc", col]]).sort_values("_row")[col].to_numpy(dtype=float)
        out["gvz_tercile"] = np.nan
        g = frames.get("macro_gvz")
        if g is not None and len(g):
            g = g.reset_index(drop=True)
            rank = g["macro_gvz"].rolling(self._gvz_rank_obs, min_periods=self._gvz_rank_obs).rank(pct=True).to_numpy(dtype=float)
            terc = np.where(np.isfinite(rank), np.where(rank <= 1 / 3, 0.0, np.where(rank > 2 / 3, 2.0, 1.0)), np.nan)
            gf = pd.DataFrame({"available_utc": g["available_utc"], "gvz_tercile": terc})
            out["gvz_tercile"] = asof_join(probe, gf).sort_values("_row")["gvz_tercile"].to_numpy(dtype=float)
        self._v2[timeframe] = out
        return out

    def r4h(self, timeframe: str) -> SideArr:
        """Sign of the mid close at the decision bar's close minus the mid close R4H_HOURS earlier (1m bars whose
        visible_at <= each instant); 0 where either is missing or the return is zero."""
        b = self._b1
        vis = np.asarray(epoch_ns(utc_index(b["visible_at"])), dtype=np.int64)
        midc = (b["bid_close"].to_numpy(dtype=float) + b["ask_close"].to_numpy(dtype=float)) / 2
        t = self._close_ns(timeframe)

        def at(x: np.ndarray) -> np.ndarray:
            pos = np.searchsorted(vis, x, side="right") - 1
            return np.where(pos >= 0, midc[np.maximum(pos, 0)], np.nan)

        d = at(t) - at(t - R4H_HOURS * 3_600_000_000_000)
        return np.where(np.isfinite(d), np.sign(d), 0).astype(np.int8)

    # ------------------------------------------------------------------ masks
    def family_entries(self, family: str, timeframe: str) -> tuple[BoolArr, SideArr]:
        if family != R4H:
            return super().family_entries(family, timeframe)
        key = (family, timeframe)
        if key not in self._fam:
            side = self.r4h(timeframe)
            self._fam[key] = (side != 0, side)
        return self._fam[key]

    def _leaf(self, leaf: Leaf, timeframe: str) -> BoolArr:
        if not _sign_leaf(leaf):
            return super()._leaf(leaf, timeframe)
        c, n = self.v2_columns(timeframe), leaf.name
        if n in HAR:
            return np.asarray(c["har_band"].to_numpy() == HAR[n])
        if n in NR:
            return np.asarray(c[n].to_numpy() == 1.0)
        if n == "event_day":
            return np.asarray(c["tier1_today"].to_numpy() >= 1.0)
        if n in MACRO_SIGN:
            col, sgn = MACRO_SIGN[n]
            v = c[col].to_numpy(dtype=float)
            return np.asarray((v > 0) if sgn > 0 else (v < 0))
        return np.asarray(c["gvz_tercile"].to_numpy() == GVZ[n])
