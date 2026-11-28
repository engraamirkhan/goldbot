"""RL-X1: an offline fitted-Q exit policy on history (ML plan 2026-11-25 ruling R7(a) and item 12: "RL-X1, an offline
fitted-Q exit policy (+1), the one real multi-step RL problem"; ADR 0106 3: RL does "sizing and execution only", and
an exit is execution of a position the signal opened, never a signal). Never live: it produces a frozen policy
artifact and a report; nothing here imports the engine, the RiskGate or an order path (tested).

The problem, as coded:
- Episode (`Episode`, `episodes_from_bars`): one dl-v1 entry (side, entry, initial risk = the T2 stop distance,
  horizon end) replayed on its instrument x tf bars that open at or after the decision and close by the horizon end.
  Prices are oriented (up is favourable) in R from the entry. The template's own exits always stand: initial stop
  at -1 R, target +2 R, time exit at the horizon (T2). A gapped stop fills at the bar's open (adverse), a gapped
  target at the target (never a gap gain), a bar touching both is a stop (as dense_labels.first_passage).
- Actions at every bar close after entry (`ACTIONS`): hold; close at that close; breakeven = move the stop to the entry
  (shielded: only once, and only while the close is above the entry, so it can never stop the trade at once or widen
  a stop). Any learned rule rides on top of the template's stop and target, never instead of them.
- Reward = net R: the exit's gross R minus the exit cost of `rl_sim.SimCosts` (round trip, ub95 slippage, overshoot
  on a stop exit) and minus the swap of every rollover night each held bar crosses (realised, calendar-known). An
  entry charged at the D-3 cost of record (learn_labels with costs) pays its own round trip `cost_rt_bp` (gold:
  srv3_cost_record.gold_row_bp) instead of the constant, kept on the episode (`Episode.rt_bp`) so a rebuild charges
  the same.
- Full feedback (R7: "On history every action's reward is known"): every state's every allowed action is replayed
  exactly from the bars, so no propensity or importance weight is needed (`transitions`).
- Fitted Q iteration (Ernst et al. 2005), finite horizon, no discount: Q_0 = 0; Q_{i+1}(s, a) = r + max over the next
  state's allowed actions of Q_i, for `n_iter` (30 >= the longest judged episode in bars) iterations; each Q is a
  ridge regression per action on a fixed quadratic basis of standardised state features (`FEATURES`), episode
  weights given. Fixed basis, fixed ridge, fixed iterations: no grid, no selection.
- Report (`evaluate`, `run`): on held-out episodes (later in time, inside learn), the greedy policy's mean net R
  against the fixed exits of record (hold to the template's exit) as the reported baseline, per trade and per session
  day with a day-block bootstrap lower bound, plus the shuffled-state control (the same fit on permuted states; a real
  policy must beat it). Descriptive: ml-5's seven PASS legs and the K booking are the card's, not this module's.
- Windows (quant ruling R-d, 2026-11-26): "RL-X1 fits on learn 2010-01..2017-12, A/A-consistent graded rows only
  ... The 2006-01 embargo is never read. test_old 2006-02..2009-12 is never fit, tuned or used for early stopping; it
  is read once, under a booked read id, as RL-X1's test_old read ... The holdout is never read." Episodes deciding or
  ending outside learn are refused (the holdout first), and every entry must carry the tier filter of
  `learn_labels.load_learn` (`usable` True, `tier` A or A-consistent when present). learn_old is not used (gold has no
  A month there). The test_old read is the card's, under its booked id; nothing here opens it.
- Run record (ADR 0106 7, ADR 0109): `history_runs.record_rlx1` fits through a builder and writes the run manifest
  (family D); it carries no serving verdict, so `model_quality.require_servable` refuses it: RL-X1 is never live.
"""
from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns
from goldbot.labels.triple_barrier import rollover_nights
from goldbot.research import windows as WN
from goldbot.research.rl_sim import SimCosts

ACTIONS: tuple[str, ...] = ("hold", "close", "breakeven")
HOLD, CLOSE, BE = 0, 1, 2
FIT_TIERS = frozenset({"A", "A-consistent"})       # R-d: = feature_store.INTRADAY_TIERS
COST_RECORD_PREFIX = "srv3-cost-"   # srv3_cost_record versions; w2-point rows pay SimCosts.rt_bp
FEATURES: tuple[str, ...] = ("t_frac", "close_r", "mfe_r", "mae_r", "be", "range_r", "next_swap_r")


class RlSpec(FrozenRecord):
    spec_id: str = "rlx1-v1"
    template: str = "T2"
    stop_r: float = 1.0
    target_r: float = 2.0
    be_min_r: float = 0.0
    n_iter: int = 30
    ridge: float = 1.0
    seed: int = 20261126            # the shuffled-state control's permutation and the bootstrap
    boot: int = 2_000
    mean_block_days: int = 5


SPEC = RlSpec()


class Episode(FrozenRecord):
    """One entry's oriented path in R per bar after entry (open, high, low, close), the swap R charged in each bar, the
    exit-cost scale k (R per bp of price) and its session day."""
    open_r: np.ndarray
    high_r: np.ndarray
    low_r: np.ndarray
    close_r: np.ndarray
    swap_r: np.ndarray
    k: float
    day: str
    weight: float = 1.0
    rt_bp: float | None = None      # the entry's round trip of record (cost_rt_bp of a D-3 charged row); None = SimCosts

    def __len__(self) -> int:
        return len(self.close_r)


# ---------------------------------------------------------------------------------------------- episodes from bars
def _mid(bars: pd.DataFrame, k: str) -> np.ndarray:
    if k in bars.columns:
        return bars[k].to_numpy(dtype=float)
    return (bars[f"bid_{k}"].to_numpy(dtype=float) + bars[f"ask_{k}"].to_numpy(dtype=float)) / 2


def _refuse_outside_learn(dec: pd.DatetimeIndex, end: pd.DatetimeIndex) -> None:
    if len(dec) and (dec.max() >= WN.HOLDOUT_FROM or end.max() >= WN.HOLDOUT_FROM):
        raise WN.HoldoutSealed("RL-X1: an episode reaches the holdout (2025-10-01 onward)")
    lo, hi = WN.SPANS[WN.Window.LEARN]
    if len(dec) and (dec.min() < lo or end.max() > hi):
        raise ValueError("RL-X1 replays learn 2010-01..2017-12 only: an episode decides or ends outside learn")


def _record_rt(entries: pd.DataFrame, costs: SimCosts) -> np.ndarray:
    """Each entry's round trip of record (NaN where the row is not D-3 charged: SimCosts.rt_bp applies)."""
    if "cost_version" not in entries.columns:
        return np.full(len(entries), np.nan)
    charged = entries["cost_version"].astype(str).str.startswith(COST_RECORD_PREFIX).to_numpy()
    if not charged.any():
        return np.full(len(entries), np.nan)
    rt = np.where(charged, entries["cost_rt_bp"].to_numpy(dtype=float) if "cost_rt_bp" in entries.columns
                  else np.nan, np.nan)
    if not np.isfinite(rt[charged]).all():
        raise ValueError("RL-X1: a row charged at the cost of record has no finite cost_rt_bp")
    if costs.slippage_ub95_bp:
        raise ValueError("RL-X1: the cost of record's round trip already carries the ub95 slippage; a SimCosts "
                         "slippage_ub95_bp on top would charge it twice")
    return rt


def episodes_from_bars(entries: pd.DataFrame, bars: pd.DataFrame, *, costs: SimCosts | None = None
                       ) -> list[Episode]:
    """Episodes of one instrument x tf. `entries`: side, decision_utc, entry, risk_usd (the T2 stop distance),
    horizon_end_utc, optional w, optional cost_version / cost_rt_bp (a row charged at the D-3 cost of record by
    learn_labels pays its own round trip, rl_sim). `bars`: ts_utc (open), visible_at (close), mid or bid/ask OHLC. A bar
    is a step when it opens at or after the decision and closes by the horizon end. Entries without a step are
    dropped."""
    c = costs or SimCosts.w2()
    sw = c.swap
    if "usable" not in entries.columns or not entries["usable"].astype(bool).all():
        raise ValueError("RL-X1 replays tier-usable rows only (R-d): entries need usable True (learn_labels)")
    if "tier" in entries.columns and not entries["tier"].astype(str).isin(list(FIT_TIERS)).all():
        raise ValueError("RL-X1 fits A / A-consistent rows only (R-d)")
    dec = pd.DatetimeIndex(pd.to_datetime(entries["decision_utc"], utc=True))
    end = pd.DatetimeIndex(pd.to_datetime(entries["horizon_end_utc"], utc=True))
    _refuse_outside_learn(dec, end)
    b = bars.sort_values("ts_utc", kind="stable").reset_index(drop=True)
    ts, vis = epoch_ns(pd.DatetimeIndex(b["ts_utc"])), epoch_ns(pd.DatetimeIndex(b["visible_at"]))
    o, h, lo, cl = (_mid(b, x) for x in ("open", "high", "low", "close"))
    start_utc, close_utc = pd.DatetimeIndex(b["ts_utc"]), pd.DatetimeIndex(b["visible_at"])
    nights = rollover_nights(start_utc, close_utc, sw.server_tz, sw.triple_weekday, sw.seven_night).astype(float)
    out = []
    w = entries["w"].to_numpy(dtype=float) if "w" in entries.columns else np.ones(len(entries))
    rec_rt = _record_rt(entries, c)
    sides = entries["side"].to_numpy(dtype=int)
    entry_px, risk_px = entries["entry"].to_numpy(dtype=float), entries["risk_usd"].to_numpy(dtype=float)
    for i in range(len(entries)):
        a = int(np.searchsorted(ts, dec[i].value, side="left"))
        z = int(np.searchsorted(vis, end[i].value, side="right"))
        if z <= a:
            continue
        side, entry, risk = int(sides[i]), float(entry_px[i]), float(risk_px[i])
        sl = slice(a, z)

        def orient(x: np.ndarray, side: int = side, entry: float = entry, risk: float = risk) -> np.ndarray:
            return side * (x - entry) / risk

        hi_r, lo_r = (orient(h[sl]), orient(lo[sl])) if side > 0 else (orient(lo[sl]), orient(h[sl]))
        k = 1e-4 * entry / risk
        out.append(Episode(open_r=orient(o[sl]), high_r=hi_r, low_r=lo_r, close_r=orient(cl[sl]),
                           swap_r=c.swap_r(np.full(z - a, k), np.full(z - a, side), nights[sl]), k=k,
                           day=str(dec[i].normalize().date()), weight=float(w[i]),
                           rt_bp=float(rec_rt[i]) if np.isfinite(rec_rt[i]) else None))
    return out


# ---------------------------------------------------------------------------------------------- exact replay
def _bar(ep: Episode, j: int, stop: float, spec: RlSpec, costs: SimCosts) -> tuple[float, bool]:
    """Hold through bar j at stop level `stop`: (reward, exited). Time exit at the last bar's close."""
    o, h, lo, c = ep.open_r[j], ep.high_r[j], ep.low_r[j], ep.close_r[j]
    sw = float(ep.swap_r[j])
    if o <= stop:
        return float(o - _exit_cost(ep, costs, True)) - sw, True
    if lo <= stop:
        return float(stop - _exit_cost(ep, costs, True)) - sw, True
    if h >= spec.target_r:
        return float(spec.target_r - _exit_cost(ep, costs, False)) - sw, True
    if j == len(ep) - 1:
        return float(c - _exit_cost(ep, costs, False)) - sw, True
    return -sw, False


def _exit_cost(ep: Episode, costs: SimCosts, stop: bool) -> float:
    return float(costs.exit_cost_r(np.array([ep.k]), np.array([stop]), rt_bp=ep.rt_bp)[0])


def _close(ep: Episode, j: int, costs: SimCosts) -> float:
    return float(ep.close_r[j] - _exit_cost(ep, costs, False))


def _features(ep: Episode, j: int, be: int) -> np.ndarray:
    nxt = float(ep.swap_r[j + 1]) if j + 1 < len(ep) else 0.0
    return np.array([(j + 1) / len(ep), ep.close_r[j], ep.high_r[:j + 1].max(), ep.low_r[:j + 1].min(), float(be),
                     ep.high_r[j] - ep.low_r[j], nxt])


def allowed(ep: Episode, j: int, be: int, spec: RlSpec = SPEC) -> np.ndarray:
    """hold and close always; breakeven once, while the close is above be_min_r (shield)."""
    return np.array([True, True, be == 0 and ep.close_r[j] > spec.be_min_r])


def rollout(ep: Episode, choose: Callable[[np.ndarray, np.ndarray], int], spec: RlSpec = SPEC,
            costs: SimCosts | None = None) -> tuple[float, list[int]]:
    """Net R of one episode under `choose(features, allowed) -> action`, and the actions taken."""
    c = costs or SimCosts.w2()
    total, done = _bar(ep, 0, -spec.stop_r, spec, c)
    acts: list[int] = []
    be, j = 0, 0
    while not done:
        a = choose(_features(ep, j, be), allowed(ep, j, be, spec))
        acts.append(a)
        if a == CLOSE:
            return total + _close(ep, j, c), acts
        if a == BE:
            be = 1
        r, done = _bar(ep, j + 1, 0.0 if be else -spec.stop_r, spec, c)
        total += r
        j += 1
    return total, acts


def fixed_exit(ep: Episode, spec: RlSpec = SPEC, costs: SimCosts | None = None) -> float:
    """The fixed exits of record: hold to the template's stop, target or time exit."""
    return rollout(ep, lambda f, ok: HOLD, spec, costs)[0]


class Transitions(FrozenRecord):
    """Every reachable decision state x allowed action: features S, action A, reward R, next features S2 with its
    allowed mask OK2, terminal flag, row weight."""
    S: np.ndarray
    A: np.ndarray
    R: np.ndarray
    S2: np.ndarray
    OK2: np.ndarray
    done: np.ndarray
    w: np.ndarray


def transitions(eps: Sequence[Episode], spec: RlSpec = SPEC, costs: SimCosts | None = None) -> Transitions:
    """Full feedback: from every alive state (bar j, breakeven flag) every allowed action, replayed exactly. States
    with the stop at breakeven are reachable by moving at j or earlier (`alive1`, a forward pass)."""
    c = costs or SimCosts.w2()
    S, A, R, S2, OK2, D, W = [], [], [], [], [], [], []
    for ep in eps:
        _, out0 = _bar(ep, 0, -spec.stop_r, spec, c)
        if out0:
            continue
        T = len(ep)
        alive0 = np.zeros(T, bool)
        alive1 = np.zeros(T, bool)
        alive0[0] = True
        for j in range(T - 1):
            for be in (0, 1):
                if not (alive1[j] if be else alive0[j]):
                    continue
                ok = allowed(ep, j, be, spec)
                f = _features(ep, j, be)
                for a in np.flatnonzero(ok):
                    if a == CLOSE:
                        r, done, nb = _close(ep, j, c), True, be
                    else:
                        nb = 1 if (a == BE or be) else 0
                        r, done = _bar(ep, j + 1, 0.0 if nb else -spec.stop_r, spec, c)
                    S.append(f)
                    A.append(a)
                    R.append(r)
                    D.append(done)
                    W.append(ep.weight)
                    if done:
                        S2.append(np.zeros(len(FEATURES)))
                        OK2.append(np.zeros(len(ACTIONS), bool))
                    else:
                        S2.append(_features(ep, j + 1, nb))
                        OK2.append(allowed(ep, j + 1, nb, spec))
                        if nb:
                            alive1[j + 1] = True
                        else:
                            alive0[j + 1] = True
    if not S:
        raise ValueError("RL-X1: no decision state (every episode exits in its first bar)")
    return Transitions(S=np.array(S), A=np.array(A), R=np.array(R), S2=np.array(S2), OK2=np.array(OK2),
                       done=np.array(D), w=np.array(W))


# ---------------------------------------------------------------------------------------------- fitted Q
def _basis(Z: np.ndarray) -> np.ndarray:
    iu = np.triu_indices(Z.shape[1])
    quad = (Z[:, :, None] * Z[:, None, :])[:, iu[0], iu[1]]
    return np.column_stack([np.ones(len(Z)), Z, quad])


class ExitPolicy(FrozenRecord):
    """A frozen RL-X1 policy: the state standardisation, ridge coefficients per action on the quadratic basis, and the
    sha256 of all of it. Greedy over the allowed actions; a tie goes to hold (the fixed exit)."""
    spec: RlSpec
    mean: tuple[float, ...]
    scale: tuple[float, ...]
    coef: tuple[tuple[float, ...], ...]
    n_transitions: int
    n_episodes: int
    sha256: str
    live: Literal[False] = False

    def compute_sha256(self) -> str:
        return hashlib.sha256(self.model_dump_json(exclude={"sha256"}).encode()).hexdigest()

    def q(self, F: np.ndarray) -> np.ndarray:
        Z = (np.atleast_2d(F) - np.asarray(self.mean)) / np.asarray(self.scale)
        return _basis(Z) @ np.asarray(self.coef).T

    def score_row(self, f: np.ndarray) -> np.ndarray:
        """Q of one state, per action: the serving form, equal to `q` on the batch (tested)."""
        return self.q(np.asarray(f)[None])[0]

    def act(self, f: np.ndarray, ok: np.ndarray) -> int:
        q = np.where(ok, self.score_row(f), -np.inf)
        best = float(q.max())
        return HOLD if q[HOLD] >= best - 1e-12 else int(np.argmax(q))


def _ridge(Phi: np.ndarray, y: np.ndarray, w: np.ndarray, lam: float) -> np.ndarray:
    A = Phi.T @ (Phi * w[:, None])
    A[1:, 1:] += lam * np.eye(Phi.shape[1] - 1)
    return np.linalg.solve(A, Phi.T @ (w * y))


def fit(tr: Transitions, spec: RlSpec = SPEC, *, n_episodes: int = 0) -> ExitPolicy:
    """Fitted Q iteration, `n_iter` sweeps, one ridge per action."""
    mean = tr.S.mean(axis=0)
    sd = tr.S.std(axis=0)
    scale = np.where(sd > 1e-12, sd, 1.0)
    P = _basis((tr.S - mean) / scale)
    P2 = _basis((tr.S2 - mean) / scale)
    w = tr.w / tr.w.mean()
    coef = np.zeros((len(ACTIONS), P.shape[1]))
    for _ in range(spec.n_iter):
        q2 = np.where(tr.OK2, P2 @ coef.T, -np.inf).max(axis=1)
        y = tr.R + np.where(tr.done, 0.0, np.nan_to_num(q2, neginf=0.0))
        new = coef.copy()
        for a in range(len(ACTIONS)):
            m = tr.A == a
            if m.any():
                new[a] = _ridge(P[m], y[m], w[m], spec.ridge)
        coef = new
    pol = ExitPolicy(spec=spec, mean=tuple(mean.tolist()), scale=tuple(scale.tolist()),
                     coef=tuple(tuple(r) for r in coef.tolist()), n_transitions=len(tr.A), n_episodes=n_episodes,
                     sha256="")
    return pol.model_copy(update={"sha256": pol.compute_sha256()})


def load(raw: str) -> ExitPolicy:
    pol = ExitPolicy.model_validate_json(raw)
    if pol.compute_sha256() != pol.sha256:
        raise ValueError("RL-X1 policy sha mismatch (the coefficients are not the ones recorded)")
    return pol


# ---------------------------------------------------------------------------------------------- the report
def _day_block_lo(daily: np.ndarray, *, boot: int, seed: int, mean_block: int) -> float | None:
    """One-sided 95 % lower bound of the mean daily value, stationary bootstrap over days (Politis-Romano)."""
    n = len(daily)
    if boot <= 0 or n < 2:
        return None
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    means = np.empty(boot)
    for b in range(boot):
        idx = np.empty(n, dtype=np.int64)
        idx[0] = rng.integers(n)
        jump = rng.random(n) < p
        fresh = rng.integers(n, size=n)
        for t in range(1, n):
            idx[t] = fresh[t] if jump[t] else (idx[t - 1] + 1) % n
        means[b] = daily[idx].mean()
    return float(np.quantile(means, 0.05))


class RlReport(FrozenRecord):
    spec_id: str
    policy_sha: str
    n_train_episodes: int
    n_test_episodes: int
    n_test_days: int
    fixed_mean: float               # the fixed exits of record, net R per trade (the baseline)
    policy_mean: float
    delta_mean: float               # policy - fixed, net R per trade
    delta_per_day: float            # summed per session day, mean over days
    delta_per_day_lo: float | None  # day-block one-sided 95 % lower bound
    shuffled_delta_mean: float      # the shuffled-state control's delta per trade
    action_counts: dict[str, int]
    live: Literal[False] = False


def evaluate(pol: ExitPolicy, eps: Sequence[Episode], costs: SimCosts | None = None
             ) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """(policy net R, fixed net R, action counts) per test episode."""
    spec = pol.spec
    p, f = np.empty(len(eps)), np.empty(len(eps))
    counts = dict.fromkeys(ACTIONS, 0)
    for i, ep in enumerate(eps):
        p[i], acts = rollout(ep, pol.act, spec, costs)
        f[i] = fixed_exit(ep, spec, costs)
        for a in acts:
            counts[ACTIONS[a]] += 1
    return p, f, counts


def shuffled(tr: Transitions, seed: int) -> Transitions:
    """The control: states permuted across rows (and next states with them), actions and rewards kept."""
    perm = np.random.default_rng(seed).permutation(len(tr.A))
    return tr.model_copy(update={"S": tr.S[perm], "S2": tr.S2[perm], "OK2": tr.OK2[perm]})


def run(train: Sequence[Episode], test: Sequence[Episode], spec: RlSpec = SPEC,
        costs: SimCosts | None = None) -> tuple[ExitPolicy, RlReport]:
    """Fit on `train`, report on `test` against the fixed exits and the shuffled-state control."""
    tr = transitions(train, spec, costs)
    pol = fit(tr, spec, n_episodes=len(train))
    ctrl = fit(shuffled(tr, spec.seed), spec, n_episodes=len(train))
    p, f, counts = evaluate(pol, test, costs)
    pc, _, _ = evaluate(ctrl, test, costs)
    d = p - f
    days = pd.Series(d).groupby(np.array([e.day for e in test])).sum().to_numpy()
    return pol, RlReport(spec_id=spec.spec_id, policy_sha=pol.sha256, n_train_episodes=len(train),
                         n_test_episodes=len(test), n_test_days=len(days), fixed_mean=float(f.mean()),
                         policy_mean=float(p.mean()), delta_mean=float(d.mean()), delta_per_day=float(days.mean()),
                         delta_per_day_lo=_day_block_lo(days, boot=spec.boot, seed=spec.seed,
                                                        mean_block=spec.mean_block_days),
                         shuffled_delta_mean=float((pc - f).mean()), action_counts=counts)
