"""The real `contracts.MaskProvider` (ADR 0058 phase 1, backlog 140): the leaf masks that `spec.Leaf` names refer to.

Nothing here reimplements an indicator. A family is an existing rule of `arm_rules.RULE_FUNCS` run through
`arm_rules.candidates` on the arm of `config/exploration_arms.yaml` that carries it on that timeframe (the plainest
one: fewest params, then file order; `arm_ids` overrides), with no calendar (blackout windows empty: the explore
window precedes the release calendar). Filters are read from the existing code: `arm_rules_bands.hurst_tercile`,
`technical.adx` / `technical.atr`, `calendar.DEFAULT_SESSIONS` / `LOCAL_SESSIONS`. A leaf whose feature does not
exist in the code base (wavelet energy, day-type labels, opportunity score tiers, tier-1 release timing, macro) raises
`UnsupportedLeaf`: `supports()` lets a generator drop it before it is ledgered, so K is never inflated by a leaf that
cannot be evaluated.

Point in time. A mask at decision bar t reads bars <= t only: rule candidates are made on the close of bar `idx`
(`arm_rules` docstring), terciles are trailing ranks (`rolling(...).rank`), time leaves read the bar's own start.
Tests perturb every bar after a cut and require the masks before it unchanged.

Window. The 1m bars arrive as `windows.ExploreFrame`, whose construction refuses any bar at or after 2018-01-01;
decision bars are resampled from them and any bar closing at or after the cutoff is dropped (`bars()`).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from goldbot.data.calendar import DEFAULT_SESSIONS, LOCAL_SESSIONS
from goldbot.data.resample import mid, resample_bars
from goldbot.data.timeutil import utc_index
from goldbot.features.technical import adx
from goldbot.research import arm_rules as R
from goldbot.research.arm_rules_bands import hurst_tercile
from goldbot.research.arms import Arm, load_arms
from goldbot.research.autodisc.contracts import BoolArr, SideArr
from goldbot.research.autodisc.spec import Group, Leaf, Predicate, SessionWindow
from goldbot.research.autodisc.windows import EXPLORE_CUTOFF, ExploreFrame, guard_times

ARMS_YAML = Path(__file__).resolve().parents[3] / "config" / "exploration_arms.yaml"
RANK_BARS = 1000            # trailing window of the volatility and trend-strength terciles
RVOL_BARS = 20
NO_BLACKOUT = np.zeros(0, dtype=np.int64)
DAYS = ("mon", "tue", "wed", "thu", "fri")
HURST = {"hurst_mean_revert": 0, "hurst_mid": 1, "hurst_trend": 2}
TERCILE = {"lo": 0, "mid": 1, "hi": 2}


class UnsupportedLeaf(ValueError):
    """The leaf names a primitive the code base does not compute (never approximated)."""


class NoFamilyArm(ValueError):
    """No runnable arm carries this family on this timeframe."""


def _tercile(rank: np.ndarray) -> np.ndarray:
    """0 / 1 / 2 by trailing percentile rank (<= 1/3, <= 2/3, above); -1 where undefined."""
    out = np.where(rank <= 1 / 3, 0, np.where(rank > 2 / 3, 2, 1))
    return np.where(np.isfinite(rank), out, -1)


def _trailing_rank(x: pd.Series) -> np.ndarray:
    return x.rolling(RANK_BARS, min_periods=RANK_BARS).rank(pct=True).to_numpy(dtype=float)


class PrimitiveProvider:
    """`MaskProvider` over the explore-window 1m bars; one instance serves any timeframe (per-timeframe state cached).
    `arm_rules_spec.LiveProvider` is the one subclass that drops the explore window (it serves the live engine's bars);
    this class itself refuses any bar at or after the cutoff."""

    _explore_only = True

    def __init__(self, b1: ExploreFrame, arms: list[Arm] | None = None, arm_ids: dict[tuple[str, str], str] | None = None
                 ) -> None:
        frame = b1.frame
        guard_times(frame["ts_utc"], what="1m bars")
        guard_times(frame["visible_at"], what="1m visible_at")
        self._setup(frame, arms, arm_ids)

    def _setup(self, frame: pd.DataFrame, arms: list[Arm] | None, arm_ids: dict[tuple[str, str], str] | None) -> None:
        self._b1 = frame.reset_index(drop=True)
        self._arms = arms if arms is not None else load_arms(ARMS_YAML)
        self._arm_ids = arm_ids or {}
        self._bars: dict[str, pd.DataFrame] = {}
        self._ctx: dict[str, R.RuleContext] = {}
        self._fam: dict[tuple[str, str], tuple[BoolArr, SideArr]] = {}
        self._cache: dict[tuple[str, str], np.ndarray] = {}

    # ------------------------------------------------------------------ bars
    def bars(self, timeframe: str) -> pd.DataFrame:
        """Decision bars (store schema, positional index): complete bars closing before the explore cutoff only."""
        if timeframe not in self._bars:
            b = resample_bars(self._b1, timeframe).reset_index(drop=True)
            if self._explore_only:
                b = b[utc_index(b["visible_at"]) < EXPLORE_CUTOFF].reset_index(drop=True)
                guard_times(b["visible_at"], what="decision bars")
            self._bars[timeframe] = b
        return self._bars[timeframe]

    def _context(self, timeframe: str) -> R.RuleContext:
        if timeframe not in self._ctx:
            self._ctx[timeframe] = R.RuleContext(bars=self.bars(timeframe), b1=self._b1, blackout_lo=NO_BLACKOUT,
                                                 blackout_hi=NO_BLACKOUT)
        return self._ctx[timeframe]

    def mid_bars(self, timeframe: str) -> pd.DataFrame:
        return mid(self.bars(timeframe)).reset_index(drop=True)

    # ------------------------------------------------------------------ families
    def arm_for(self, family: str, timeframe: str) -> Arm:
        want = self._arm_ids.get((family, timeframe))
        pool = [a for a in self._arms if a.rule == family != "spec_rule" and a.timeframe == timeframe
                and a.status == "run" and a.twin_of is None and (want is None or a.id == want)]
        if not pool:
            raise NoFamilyArm(f"no runnable {timeframe} arm carries family {family!r}")
        return min(pool, key=lambda a: len(a.params))        # min is stable: the first in file order on a tie

    def families(self, timeframe: str) -> tuple[str, ...]:
        return tuple(sorted({a.rule for a in self._arms if a.timeframe == timeframe and a.status == "run"
                             and a.twin_of is None and a.rule in R.RULE_FUNCS
                             and a.rule != "spec_rule"}))

    def family_entries(self, family: str, timeframe: str) -> tuple[BoolArr, SideArr]:
        key = (family, timeframe)
        if key not in self._fam:
            n = len(self.bars(timeframe))
            cands = R.candidates(self.arm_for(family, timeframe), self._context(timeframe))
            cands = cands[cands["idx"] < n].drop_duplicates("idx")
            mask, side = np.zeros(n, dtype=bool), np.zeros(n, dtype=np.int8)
            idx = cands["idx"].to_numpy(dtype=int)
            mask[idx], side[idx] = True, cands["side"].to_numpy(dtype=np.int8)
            self._fam[key] = (mask, side)
        return self._fam[key]

    # ------------------------------------------------------------------ leaves
    def supports(self, pred: Predicate) -> bool:
        try:
            self._check(pred)
        except UnsupportedLeaf:
            return False
        return True

    @staticmethod
    def check_supported(pred: Predicate) -> None:
        """Raise `UnsupportedLeaf` unless every leaf of `pred` names a feature the code base computes (no state)."""
        if isinstance(pred, Group):
            for c in pred.children:
                PrimitiveProvider.check_supported(c)
            return
        n = pred.name
        ok = ((pred.kind == "structure" and n == "family_rule")
              or (pred.kind == "time" and (n in ("asia", "london", "ny", "london_ny_overlap", "month_turn") or n in DAYS))
              or (pred.kind == "regime" and (n in HURST or n.startswith(("rvol_q_", "trend_strength_"))
                                             and n.rsplit("_", 1)[1] in TERCILE)))
        if not ok:
            raise UnsupportedLeaf(f"{pred.kind}/{n}: no existing feature computes it")

    def _check(self, pred: Predicate) -> None:
        self.check_supported(pred)

    def predicate_mask(self, pred: Predicate, timeframe: str) -> BoolArr:
        if isinstance(pred, Group):
            masks = [self.predicate_mask(c, timeframe) for c in pred.children]
            return np.logical_and.reduce(masks) if pred.op == "and" else np.logical_or.reduce(masks)
        self._check(pred)
        key = (timeframe, pred.model_dump_json())
        if key not in self._cache:
            self._cache[key] = self._leaf(pred, timeframe)
        return self._cache[key].copy()

    def _leaf(self, leaf: Leaf, timeframe: str) -> BoolArr:
        n, bars = leaf.name, self.bars(timeframe)
        if leaf.kind == "structure":
            return self.family_entries(str(leaf.params["rule"]), timeframe)[0]
        if leaf.kind == "time":
            return self._time(n, utc_index(bars["ts_utc"]))
        m = self.mid_bars(timeframe)
        if n in HURST:
            return hurst_tercile(m) == HURST[n]
        level = TERCILE[n.rsplit("_", 1)[1]]
        if n.startswith("rvol_q_"):
            ret = pd.Series(np.log(m["close"].to_numpy(dtype=float))).diff()
            return _tercile(_trailing_rank(ret.rolling(RVOL_BARS, min_periods=RVOL_BARS).std())) == level
        return _tercile(_trailing_rank(adx(m, 14))) == level          # trend_strength_*

    @staticmethod
    def _time(name: str, start: pd.DatetimeIndex) -> BoolArr:
        if name in DAYS:
            return np.asarray(start.dayofweek == DAYS.index(name))
        if name == "month_turn":                                        # the last two and first two calendar days
            d = start.day
            return np.asarray((d <= 2) | (d >= start.days_in_month - 1))
        if name == "london_ny_overlap":
            return (~np.isnan(LOCAL_SESSIONS["london"].clock(start)[0])) & (~np.isnan(LOCAL_SESSIONS["newyork"].clock(start)[0]))
        label = DEFAULT_SESSIONS.session_label(start)
        return np.asarray(label == {"asia": "asia", "london": "london", "ny": "newyork"}[name])

    # ------------------------------------------------------------------ session window and side
    def session_mask(self, window: SessionWindow, timeframe: str) -> BoolArr:
        """The spec's session window on the decision bars' starts (`all`: every bar; `london_ny`: London or New York)."""
        n = len(self.bars(timeframe))
        if window == "all":
            return np.ones(n, dtype=bool)
        if window == "london_ny":
            return self._time_leaf("london", timeframe) | self._time_leaf("ny", timeframe)
        return self._time_leaf(window, timeframe)

    def _time_leaf(self, name: str, timeframe: str) -> BoolArr:
        return self._time(name, utc_index(self.bars(timeframe)["ts_utc"]))

    def side_of(self, leaf: Leaf, timeframe: str) -> SideArr:
        """Direction of a side primitive (+1 / -1 / 0): only a family's own rule has one in phase 1."""
        if leaf.kind == "structure" and leaf.name == "family_rule":
            return self.family_entries(str(leaf.params["rule"]), timeframe)[1]
        raise UnsupportedLeaf(f"{leaf.kind}/{leaf.name}: no side primitive")
