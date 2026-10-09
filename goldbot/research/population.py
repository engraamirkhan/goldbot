"""A competing population of trading agents (design: "A competing population of trading agents").

Every specialist variant is an agent with its own identity (family, config, parent, generation), shadow and live
record, and capital share. The tournament (run weekly by the scheduler) applies the design's rules:

* fitness comes only from out-of-sample shadow trades at live prices: per-trade Sharpe x calibration (1 - ECE) x a
  drawdown haircut x a diversity penalty (1 - correlation of its weekly returns with the funded agents');
* an agent is ranked only after MIN_RANK_TRADES (60) shadow trades;
* retirement: the lower 80% confidence bound on expectancy (in R) is below zero after RETIRE_MIN_TRADES (100) trades,
  or the agent sits in the bottom quartile of its generation for two consecutive months; retired agents keep
  shadow-trading for RETIRED_SHADOW_DAYS so the record has no survivorship bias, then leave the book;
* promotion shadow -> live needs a deflated Sharpe above DSR_PROMOTE with the population size as the trial count;
* winners (top quartile, live, positive fitness) are cloned into 2-3 mutated children that start in shadow with zero
  capital; a parent is never duplicated unchanged (AgentIdentity.mutate refuses);
* caps: LIVE_CAP live and SHADOW_CAP shadow agents; capital within a family is split by fitness share (the allocator
  multiplies by its family weight).

Mutations (mutate_agent): perturbed barriers or trigger thresholds, a different feature subset within the 40-feature
cap (`feature_seed`), or a different decision timeframe for families that allow one (holding horizon kept).
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path
from typing import Any, Callable, Literal

import numpy as np
import pandas as pd
from pydantic import Field
from scipy import stats

from goldbot.base import Record, UtcTimestamp
from goldbot.config import tf_seconds
from goldbot.engine.shadow import ShadowBook
from goldbot.research.metrics import deflated_sharpe, max_drawdown
from goldbot.specialists import SPECIALISTS
from goldbot.specialists.base import FEATURE_SEED_KEY, TIMEFRAME_KEY, AgentIdentity, Specialist

MIN_RANK_TRADES = 60
RETIRE_MIN_TRADES = 100
LOWER_80_Z = 1.2816                 # two-sided 80% interval -> lower bound
DSR_PROMOTE = 0.95
LIVE_CAP = 12
SHADOW_CAP = 24
RETIRED_SHADOW_DAYS = 182
CHILDREN_PER_WINNER = (2, 3)
MAX_LIVE_CHILDREN = 3               # a winner with this many children alive is not cloned again
MUTATION_FACTORS = (0.8, 0.9, 1.1, 1.25)
DD_HAIRCUT_AT = 0.30                # a 30% shadow drawdown halves fitness

AgentStatus = Literal["shadow", "live", "retired"]


class Member(Record):
    agent_id: str
    family: str
    config: dict[str, Any]
    parent_id: str | None = None
    generation: int = 0
    status: AgentStatus = "shadow"
    created_utc: UtcTimestamp
    status_since_utc: UtcTimestamp
    shadow_expired: bool = False                       # retired and past RETIRED_SHADOW_DAYS: out of the book
    monthly_quartile: dict[str, int] = Field(default_factory=dict)   # "YYYY-MM" -> 0 (best) .. 3 (worst)
    fitness: float = 0.0
    capital_weight: float = 0.0                        # share of its family's capital
    stats: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    def identity(self) -> AgentIdentity:
        return AgentIdentity(family=self.family, config=self.config, parent_id=self.parent_id, generation=self.generation)

    def specialist(self) -> Specialist:
        return SPECIALISTS[self.family](identity=self.identity())

    @property
    def in_shadow_book(self) -> bool:
        return not self.shadow_expired


# ---------------------------------------------------------------------------------------------- measurements
def agent_trades(book: ShadowBook) -> dict[str, pd.DataFrame]:
    """Closed shadow trades per agent across all of its model versions: ret, r (in units of risk), p, target_hit, exit_ts."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for vb in book.books.values():
        for t in vb.closed:
            if not t.taken or t.ret is None or t.exit_ts is None:
                continue          # counterfactual (not-taken) candidates are not trades
            risk = abs(t.entry - t.stop) / t.entry
            rows.setdefault(t.agent_id, []).append({"ret": t.ret, "r": t.ret / risk if risk > 0 else 0.0, "p": t.p,
                                                    "target_hit": int(t.barrier == "target"), "exit_ts": t.exit_ts})
    return {a: pd.DataFrame(r).sort_values("exit_ts").reset_index(drop=True) for a, r in rows.items()}


def calibration_ece(p: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    if len(p) == 0:
        return 1.0
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    ece = 0.0
    for b in range(bins):
        m = idx == b
        if m.any():
            ece += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(ece)


def weekly_returns(trades: pd.DataFrame) -> pd.Series:
    if trades.empty:
        return pd.Series(dtype=float)
    ts = pd.DatetimeIndex(pd.to_datetime(trades["exit_ts"], utc=True))
    week_start = ts.normalize() - pd.to_timedelta(ts.dayofweek, unit="D")     # Monday 00:00 UTC, tz kept
    return trades["ret"].groupby(week_start).sum()


def correlation_with(weekly: pd.Series, others: list[pd.Series]) -> float:
    if weekly.empty or not others:
        return 0.0
    funded = pd.concat(others, axis=1, sort=True).fillna(0.0).sum(axis=1)
    both = pd.concat([weekly, funded], axis=1, sort=True).fillna(0.0)
    if len(both) < 4 or both.iloc[:, 0].std() == 0 or both.iloc[:, 1].std() == 0:
        return 0.0
    return float(both.iloc[:, 0].corr(both.iloc[:, 1]))


class Score(Record):
    n: int
    expectancy_r: float
    expectancy_lower80_r: float | None   # undefined below two trades
    hit_rate: float
    ece: float
    max_dd: float
    sharpe_per_trade: float
    corr_funded: float
    score: float          # ranking value (may be negative)
    fitness: float        # capital value: max(score, 0)
    dsr: float


def score_agent(trades: pd.DataFrame, corr_funded: float, n_population: int) -> Score:
    n = len(trades)
    if n < 2:
        return Score(n=n, expectancy_r=float(trades["r"].mean()) if n else 0.0, expectancy_lower80_r=None, hit_rate=0.0,
                     ece=1.0, max_dd=0.0, sharpe_per_trade=0.0, corr_funded=corr_funded, score=0.0, fitness=0.0, dsr=0.0)
    r, ret = trades["r"].to_numpy(float), trades["ret"].to_numpy(float)
    mean_r, sd_r = float(r.mean()), float(r.std(ddof=1))
    sr = float(ret.mean() / ret.std(ddof=1)) if ret.std(ddof=1) > 0 else 0.0
    ece = calibration_ece(trades["p"].to_numpy(float), trades["target_hit"].to_numpy(float))
    dd = max_drawdown(ret)
    score = sr * (1 - ece) * (1 - 0.5 * min(dd / DD_HAIRCUT_AT, 1.0)) * (1 - max(0.0, corr_funded))
    dsr = deflated_sharpe(sr, max(n_population, 1), n, float(stats.skew(ret)), float(stats.kurtosis(ret, fisher=False)),
                          var_sr_trials=1.0 / n)
    return Score(n=n, expectancy_r=mean_r, expectancy_lower80_r=mean_r - LOWER_80_Z * sd_r / math.sqrt(n),
                 hit_rate=float((ret > 0).mean()), ece=ece, max_dd=dd, sharpe_per_trade=sr, corr_funded=corr_funded,
                 score=float(score), fitness=float(max(score, 0.0)), dsr=dsr)


def mutate_config(base: dict[str, Any], rng: random.Random, n_keys: int = 2) -> dict[str, Any]:
    """Perturb 1-n_keys numeric config values by a factor from MUTATION_FACTORS (ints rounded, never unchanged)."""
    numeric = [k for k, v in base.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0
               and k not in (FEATURE_SEED_KEY, TIMEFRAME_KEY)]
    if not numeric:
        raise ValueError("no numeric config to mutate")
    keys = rng.sample(numeric, k=min(len(numeric), rng.randint(1, n_keys)))
    out = dict(base)
    for k in keys:
        v = base[k]
        f = rng.choice(MUTATION_FACTORS)
        out[k] = max(1, int(round(v * f))) if isinstance(v, int) else round(float(v) * f, 4)
        if out[k] == v:           # small ints can round back: step by one instead
            out[k] = v + 1 if isinstance(v, int) else round(float(v) * 1.1, 4)
    return out


MUTATION_KINDS = (("params", 0.6), ("features", 0.25), ("timeframe", 0.15))


def mutate_agent(family: str, base: dict[str, Any], rng: random.Random) -> tuple[str, dict[str, Any]]:
    """One child config (design: perturbed barriers or trigger thresholds, a different feature subset within the
    40-feature cap, or a different timeframe). Returns (kind, config); kinds a family cannot take fall back to params.

    A timeframe move rescales `max_bars` so the holding horizon in hours stays the same."""
    cls = SPECIALISTS[family]
    kind = rng.choices([k for k, _ in MUTATION_KINDS], weights=[w for _, w in MUTATION_KINDS])[0]
    current_tf = base.get(TIMEFRAME_KEY, cls.timeframe)
    options = [tf for tf in (cls.timeframe, *cls.timeframes) if tf != current_tf]
    if kind == "timeframe" and options:
        tf = rng.choice(options)
        out = {**base, TIMEFRAME_KEY: tf}
        if "max_bars" in base:
            out["max_bars"] = max(1, int(round(base["max_bars"] * tf_seconds(current_tf) / tf_seconds(tf))))
        return "timeframe", out
    if kind == "features":
        seed = rng.randrange(1, 2**31)
        while seed == base.get(FEATURE_SEED_KEY):
            seed = rng.randrange(1, 2**31)
        return "features", {**base, FEATURE_SEED_KEY: seed}
    return "params", mutate_config(base, rng)


# ---------------------------------------------------------------------------------------------- population
class Population:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        raw = json.loads(self.path.read_text()) if self.path.exists() else []
        self.members: dict[str, Member] = {m["agent_id"]: Member.model_validate(m) for m in raw}

    def ensure_founders(self, now: pd.Timestamp) -> list[str]:
        """Every registered specialist family has a generation-0 agent with its default config."""
        added = []
        for fam, cls in sorted(SPECIALISTS.items()):
            ident = AgentIdentity(family=fam, config=dict(cls.default_config))
            if ident.agent_id not in self.members:
                self.members[ident.agent_id] = Member(agent_id=ident.agent_id, family=fam, config=dict(cls.default_config),
                                                      created_utc=now, status_since_utc=now, notes=["founder"])
                added.append(ident.agent_id)
        return added

    def active(self, *statuses: AgentStatus) -> list[Member]:
        return [m for m in self.members.values() if m.status in statuses]

    def in_book(self) -> list[Member]:
        return [m for m in self.members.values() if m.in_shadow_book]

    def _set_status(self, m: Member, status: AgentStatus, now: pd.Timestamp, note: str) -> None:
        m.status, m.status_since_utc = status, now
        m.notes.append(f"{now:%Y-%m-%d} {note}")
        if status != "live":
            m.capital_weight = 0.0

    def tournament(self, book: ShadowBook, now: pd.Timestamp, seed: str | None = None,
                   research_passed: Callable[[Member], bool] | None = None) -> dict[str, Any]:
        """One round: score, record the month's quartiles, retire, promote, clone, weight. Returns a summary.

        research_passed: when given, a shadow agent is promoted to live only if its configuration has a research trial
        that passed the design's gates (1,500 candidates, 60 per test fold, three positive years incl. 2021-22); the
        scheduler passes TrialRegistry.passed_gates. Agents held back are listed under "awaiting_research"."""
        self.ensure_founders(now)
        trades = agent_trades(book)
        n_pop = len(self.members)
        weekly = {a: weekly_returns(t) for a, t in trades.items()}
        live_ids = {m.agent_id for m in self.active("live")}
        summary: dict[str, Any] = {"retired": [], "promoted": [], "cloned": [], "expired": [], "awaiting_research": []}

        scores: dict[str, Score] = {}
        for m in self.members.values():
            t = trades.get(m.agent_id, pd.DataFrame(columns=["ret", "r", "p", "target_hit", "exit_ts"]))
            others = [weekly[a] for a in live_ids if a != m.agent_id and a in weekly]
            scores[m.agent_id] = s = score_agent(t, correlation_with(weekly.get(m.agent_id, pd.Series(dtype=float)), others), n_pop)
            m.fitness = s.fitness
            m.stats = s.model_dump()

        # quartiles within each generation, among ranked active agents
        month = f"{now:%Y-%m}"
        ranked = [m for m in self.active("live", "shadow") if scores[m.agent_id].n >= MIN_RANK_TRADES]
        for gen in {m.generation for m in ranked}:
            peers = sorted((m for m in ranked if m.generation == gen), key=lambda m: -scores[m.agent_id].score)
            if len(peers) < 4:
                continue
            for i, m in enumerate(peers):
                m.monthly_quartile[month] = min(3, i * 4 // len(peers))

        prev_month = f"{(now.tz_convert('UTC').tz_localize(None).to_period('M') - 1)}"
        for m in self.active("live", "shadow"):
            s = scores[m.agent_id]
            if s.n >= RETIRE_MIN_TRADES and s.expectancy_lower80_r is not None and s.expectancy_lower80_r < 0:
                self._set_status(m, "retired", now, f"retired: lower 80% bound on expectancy {s.expectancy_lower80_r:.3f}R after {s.n} trades")
                summary["retired"].append(m.agent_id)
            elif m.monthly_quartile.get(month) == 3 and m.monthly_quartile.get(prev_month) == 3:
                self._set_status(m, "retired", now, "retired: bottom quartile of its generation two months running")
                summary["retired"].append(m.agent_id)

        for m in self.active("retired"):
            if not m.shadow_expired and now - m.status_since_utc > pd.Timedelta(days=RETIRED_SHADOW_DAYS):
                m.shadow_expired = True
                summary["expired"].append(m.agent_id)

        candidates = sorted((m for m in self.active("shadow") if scores[m.agent_id].n >= MIN_RANK_TRADES),
                            key=lambda m: -scores[m.agent_id].score)
        for m in candidates:
            s = scores[m.agent_id]
            if len(self.active("live")) >= LIVE_CAP:
                break
            if s.dsr > DSR_PROMOTE and s.fitness > 0:
                if research_passed is not None and not research_passed(m):
                    summary["awaiting_research"].append(m.agent_id)
                    continue
                self._set_status(m, "live", now, f"promoted to live: DSR {s.dsr:.3f} with {n_pop} trials")
                summary["promoted"].append(m.agent_id)

        rng = random.Random(seed or f"{now:%Y-%m-%d}")
        live_ranked = sorted((m for m in self.active("live") if scores[m.agent_id].n >= MIN_RANK_TRADES and m.fitness > 0),
                             key=lambda m: -scores[m.agent_id].score)
        winners = live_ranked[: max(1, len(live_ranked) // 4)] if live_ranked else []
        for w in winners:
            alive_children = [c for c in self.members.values() if c.parent_id == w.agent_id and c.status != "retired"]
            want = rng.randint(*CHILDREN_PER_WINNER)
            for _ in range(max(0, min(want, MAX_LIVE_CHILDREN - len(alive_children)))):
                if len(self.active("shadow")) >= SHADOW_CAP:
                    break
                for _attempt in range(10):
                    kind, cfg = mutate_agent(w.family, w.config, rng)
                    child = w.identity().mutate(cfg)
                    if child.agent_id not in self.members:
                        break
                else:
                    continue
                self.members[child.agent_id] = Member(agent_id=child.agent_id, family=w.family, config=child.config,
                                                      parent_id=w.agent_id, generation=child.generation, created_utc=now,
                                                      status_since_utc=now, notes=[f"clone of {w.agent_id} ({kind})"])
                summary["cloned"].append(child.agent_id)

        # capital: within a family, live agents split by fitness share
        for fam in {m.family for m in self.members.values()}:
            live = [m for m in self.active("live") if m.family == fam]
            total = sum(m.fitness for m in live)
            for m in live:
                m.capital_weight = m.fitness / total if total > 0 else 0.0
        summary["live"] = sorted(m.agent_id for m in self.active("live"))
        summary["shadow"] = sorted(m.agent_id for m in self.active("shadow"))
        return summary

    def league(self) -> list[dict[str, Any]]:
        """Rows for the dashboard's Agents screen (goldbot.api.schema.AgentRow)."""
        out = []
        for m in self.members.values():
            if m.shadow_expired:
                continue
            s = m.stats or {}
            out.append({"agent_id": m.agent_id, "family": m.family, "generation": m.generation, "parent_id": m.parent_id,
                        "status": m.status, "n_trades": int(s.get("n", 0)), "expectancy_r": float(s.get("expectancy_r", 0.0)),
                        "hit_rate": float(s.get("hit_rate", 0.0)), "calibration_ece": float(s.get("ece", 1.0)),
                        "fitness": m.fitness, "capital_weight": m.capital_weight})
        return out

    def save(self, agents_json: Path | None = None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps([m.model_dump(mode="json") for m in self.members.values()], indent=1))
        tmp.replace(self.path)
        if agents_json is not None:
            agents_json.write_text(json.dumps(self.league(), indent=1))
