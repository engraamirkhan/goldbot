"""The brain's nightly discovery search (ADR 0090; job `discovery_nightly`, run in a bounded child by ops/bounded.py).

The owner's directive of 2026-11-18, "the system finds new strategies itself", as one bounded night:

1. Inputs. The G2 grammar (`gen_gp`, the honesty set of `nightly.search_and_judge`); grammar-node PRIORS mined from our
   own demo labels (aux.db `exploration_obs`, non-control rows with a net-R label: by family and decision session)
   and read back from each discovered arm's forward results (its spec's nodes: "each arm's forward result is read back
   as a prior on its grammar node"); the catalogue cards (config/research/candidates/), power-triaged only: a card is
   free text with no interpreter, so a started card waits for a pre-registration and costs nothing here.
2. Focus 1h, 4h, 1d: "the why-fail audit found no gross edge in single rules at 1m-15m". A timeframe on which no spec
   could reach the ADR 0085 power (one that trades every bar) is not searched at all (`timeframe_reachable`).
3. Gate. ADR 0085: "Start only if every power_g >= 0.5, simulated joint power >= 0.25, and n_eff >= n_eff_floor (200)
   at each fresh-data gate". Every spec of the search, its G0 null and its plateau neighbours gets that verdict from its
   outcome-blind signal counts BEFORE it is ledgered (`SpecPowerGate`, `gen_gp.Admit`): a refused spec reads no outcome
   and adds nothing to K; the search redraws it at sampling (`gen_gp.run_gp`). The card is XAUUSD alone; its fresh
   gate is TEST-OLD (2006-02..2009) at Holm rank 1 of `confirm_configs_per_batch` configs at the ledger year's alpha_j
   (`promotion_confirm.batch_alpha`). TEST-OLD is read once per family and every G2 spec is family `autodisc:G2`: once
   EVAL-2's reads ledger or a promotion read has read that family on TEST-OLD, no spec is admitted (`test_old_spent`).
   A survivor is offered as a demo learning arm only if its kill rule can read it (`kill_unreadable_rule`, quant
   ruling (a) of 2026-11-18): at the explore rate, at least MIN_N replayed candidates and MIN_DAYS signal days by its
   `until` (KILL_READ_DAYS). The ADR 0085 verdict of the forward window (`forward_verdict`) is reported on the row,
   not evidence and not a gate: over the learning horizon no spec can reach its n_eff floor.
4. Budget. Two checks, both before the search. The night writes at most one registry row (a discovery batch) per
   timeframe: those rows must fit the quarter's trial budget with the pre-registered queue's reservation held back
   (`catalogue.run.assert_budget`). Its K is bounded on its own scale: the quarter's summed discovery k_contrib plus
   the night's planned K (every timeframe at its bound) must stay within the quarter's entry of `k_cap_quarter`, the
   research director's per-quarter map (docs/research/discovery-k-cap-2026-11-18.md); a quarter not listed, or 0,
   refuses every night. Power-gate refusals are counted and reported (`n_refused_power`), never booked as K. Wall
   clock: no timeframe starts once `budget_s` is spent, or when the last one took longer than what is left; the
   parent kills the child at budget_s + kill_grace_s.
5. Screen. `search_and_judge` (floors, the 1.5x cost stress, the G0 null of the whole search, SPA/PBO, the shift null,
   the plateau, the DSR at the registry's discovery K). Costs (`cor_v3_cells`): the cost of record v3 (ADR 0084) per
   (session, exit) cell, in bps of the price where it was measured, so a 2010-2017 trade pays the same fraction of
   price. v3 measures fills against the mid and has no spread split, so the labels read the mid and each label pays
   its full cell: the spread is charged once. Swap is the settings prior, not measured swap. The stage journal
   (`stages.StageJournal`) is read,
   never opened: a spec whose confirm or holdout read failed or was voided is never offered again ("the failed hash can
   never be re-confirmed"). This job opens no confirm read: ADR 0085 allows 4 confirm batches a ledger year, and a
   nightly job must not spend them.
6. Holdout sealed. The 1m bars come through `cost_of_record.load_research_bars` (it refuses a window reaching the
   holdout before the file is opened) and are cut to the explore window (2018+ raises in `windows.ExploreFrame`). A
   demo label decided before `HOLDOUT_END` never reaches the priors.
7. Out. Survivors that can run live (`arm_rules_spec`, prereg still to come) -> `handoff.candidate_arms`, the
   registration hand-off; the run record -> aux.db `discovery_runs` and the discovery ledger's `nightly_runs`; the
   report -> state/autodisc/discovery-<night>.md (explore numbers only). A dry run reads and plans and writes nothing.
8. First enabled night (quant GO 2026-11-18, condition (b): "the first night is reviewed by quant before any write").
   While `research.discovery.first_night_dry` is true, an enabled night runs the full search and its candidate rows
   are WITHHELD: nothing is handed off (no `candidate_arms` row, so `autodisc_register` has nothing to register), and
   the report lists what it would have offered. Its K is NOT withheld: "a dry search that evaluates outcomes without
   booking K is an uncounted trial", so the night ledgers every spec it evaluated, syncs that K to the registry and
   writes its run record exactly as any night. A later night may find it again; its K is already counted, so a
   re-offer adds no trial. The reviewer flips the flag to false after reading that report (until then every night
   stays dry and still spends K).

"An explore survivor is a recorded fact, never evidence of edge." Nothing here trades, registers an arm or edits a
config: registration stays `ops/autodisc_register.py` (demo accounts; the order gate is unchanged)."""
from __future__ import annotations

import argparse
import fcntl
import json
import logging
import math
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml

from goldbot.base import FrozenRecord, write_atomic
from goldbot.config import TF_SECONDS, DiscoverySettings, Settings
from goldbot.research.autodisc import handoff as H
from goldbot.research.autodisc.gen_combine import MAX_BARS
from goldbot.research.autodisc.gen_gp import GPConfig, node_key
from goldbot.research.autodisc.nightly import (
    LEDGER_FILE,
    REPORT_DIR,
    Backends,
    assert_no_confirm_numbers,
    backends_from_1m,
    night_seed,
    render,
    search_and_judge,
)
from goldbot.research.autodisc.outcomes import OutcomeTensor
from goldbot.research.autodisc.spec import StrategySpec, canonical_json, leaves
from goldbot.research.autodisc.windows import (
    EXPLORE_CUTOFF,
    EXPLORE_START,
    WindowViolation,
    truncate_to_explore,
)
from goldbot.research.power_first import Gate, PowerCard, PowerVerdict, Segment, evaluate
from goldbot.research.promotion_confirm import batch_alpha, ledger_year

log = logging.getLogger(__name__)

INSTRUMENT = "XAUUSD"
FAMILY = "autodisc:G2"
LEARN, TEST_OLD, FORWARD = "learn", "test_old", "forward"
# the demo learning arm's read (ADR 0061 amendment 2026-11-02): it stops at arms.LEARNING_MAX_DAYS or at this many
# admissible fills, whichever is first (engine.exploration_learning.LEARNING_READ_FILLS; not imported: nothing here
# imports the engine, a test holds the two equal), and is read one-sided at 5 %
LEARNING_READ_FILLS = 300
FORWARD_Z = 1.6448536269514722
# quant ruling (a), 2026-11-18: the hand-off needs a kill rule that can read the arm by its `until`: at the explore
# rate over KILL_READ_DAYS, at least MIN_N replayed candidates and MIN_DAYS distinct signal days
KILL_READ_DAYS = 42
MIN_N = 50
MIN_DAYS = 10
FORWARD_READING = "reported, not evidence"
# the registration bar's explore read (autodisc_register.ExploreRecord), copied from the ledger result to the offered row
EXPLORE_FIELDS = ("n_events", "n_bars", "n_days", "n_signal_days", "gross_usd_mean", "net_usd_mean")
NEIGHBOUR_BOUND = 24          # plateau neighbours of one candidate at most (gen_gp.neighbours: +-1 rung a leaf, exits)
Status = Literal["ok", "budget", "over_budget", "dry_run"]
LOCK_FILE = "discovery.lock"
_EXIT_CELL = {"stop": "stop", "trail": "stop", "target": "target", "time": "time", "flat": "time"}
_WINDOW = re.compile(r"((?:19|20)\d\d)\s*(?:-|–|\.\.)\s*((?:19|20)\d\d)")
_SESSION = {"asia": "asia", "london": "london", "newyork": "ny"}
_YEAR = re.compile(r"_((?:19|20)\d\d)\.parquet$")


# ------------------------------------------------------------------ the holdout stays sealed
def load_explore_bars(path: str | Path, *, lo: pd.Timestamp = EXPLORE_START, hi: pd.Timestamp = EXPLORE_CUTOFF
                      ) -> pd.DataFrame:
    """The research 1m bars of [lo, hi) inside the explore window, from one parquet file or a directory of per-year
    files (`<name>_<year>.parquet`, only the years of the window opened). A window past the explore cutoff (the confirm
    years and the holdout) is refused before any file is opened; every read goes through
    `cost_of_record.load_research_bars`, which refuses a padded window reaching the holdout and stops on a holdout row;
    rows outside [lo, hi) that the padded read returns are dropped, and `truncate_to_explore` keeps 2018+ out."""
    from goldbot.research.cost_of_record import load_research_bars
    from goldbot.research.exploration_data import HOLDOUT_START
    lo, hi = pd.Timestamp(lo), pd.Timestamp(hi)
    if hi > EXPLORE_CUTOFF:
        sealed = " (it reaches the holdout, which stays sealed)" if hi > HOLDOUT_START else ""
        raise WindowViolation(f"discovery reads the explore window only: {hi.date()} is past "
                              f"{EXPLORE_CUTOFF.date()}{sealed}")
    p = Path(path)
    if p.is_dir():
        years = range(lo.year, (hi - pd.Timedelta(1)).year + 1)
        files = [f for f in sorted(p.glob("*.parquet")) if (m := _YEAR.search(f.name)) and int(m.group(1)) in years]
        if not files:
            raise FileNotFoundError(f"no per-year 1m parquet for {lo.year}-{(hi - pd.Timedelta(1)).year} in {p}")
        b1 = pd.concat([load_research_bars(f, lo, hi) for f in files], ignore_index=True)
    else:
        b1 = load_research_bars(p, lo, hi)
    b1 = b1.assign(ts_utc=pd.to_datetime(b1["ts_utc"], utc=True))
    return truncate_to_explore(b1[(b1["ts_utc"] >= lo) & (b1["ts_utc"] < hi)].reset_index(drop=True), "ts_utc")


class CostCells(FrozenRecord):
    """The cost of record per (session, exit) cell, as a round trip in bps of the price it was measured at."""
    version: str
    broker: str
    price_usd: float
    split: bool                                   # the cells carry their spread part: bid/ask labels, the rest charged
    bps: dict[str, dict[str, float]]              # session (asia/london/newyork) -> exit (stop/target/time) -> bps
    line: str


def cor_v3_cells(path: str | Path, broker: str, price_usd: float) -> CostCells:
    """The cost of record v3 (ADR 0084) of `broker` per (session, exit) cell, in bps of `price_usd` (the gold price
    where it was measured), so a 2010-2017 trade pays the same fraction of price as a fill today: v3's $/oz on a
    2010-2017 price would be about three times the cost. v3 measures fills against the research bars' mid and charges
    `charged_usd`. A table whose every cell also states `spread_usd` keeps the bid/ask labels and charges only the rest
    (the spread paid once, by the labels); v3 states none, so the labels read the mid and each full cell is charged
    (the spread paid once, inside the cell). No cell is charged below its `commission_usd`. The line carries no date
    (the report is explore-only)."""
    table = yaml.safe_load(Path(path).read_text())
    sessions = {k: v for k, v in table["cells"][broker].items() if k != "all"}
    cells = [c for by_exit in sessions.values() for e, c in by_exit.items() if e != "all"]
    split = bool(cells) and all("spread_usd" in c for c in cells)

    def usd(c: Mapping[str, Any]) -> float:            # never below the cell's own commission (quant advisory)
        return max(float(c.get("commission_usd") or 0.0),
                   float(c["charged_usd"]) - (float(c["spread_usd"]) if split else 0.0))

    bps = {sess: {e: usd(c) / float(price_usd) * 1e4 for e, c in by_exit.items() if e != "all"}
           for sess, by_exit in sessions.items()}
    dear = max(v for by in bps.values() for v in by.values())
    how = ("bid/ask labels + each cell's non-spread part" if split else
           "mid labels + each full cell (v3 has no spread split: the spread is charged once, inside the cell)")
    line = (f"cost of record v3 ({broker}) per session and exit cell in bps of {float(price_usd):,.0f} $/oz, {how}, "
            f"dearest cell {dear:.2f} bp a round trip; swap: the settings swap prior, not measured swap")
    return CostCells(version=str(table["version"]), broker=broker, price_usd=float(price_usd), split=split, bps=bps,
                     line=line)


def cell_charge(cells: CostCells) -> Callable[[pd.DataFrame], np.ndarray]:
    """Each label row's round trip as a fraction of price: the cell of the session its decision bar is in and of the
    exit it took (a trail exit is a stop order, a hard flat a time exit); a cell the table lacks pays the dearest one."""
    dearest = max(v for by in cells.bps.values() for v in by.values())

    def charge(lab: pd.DataFrame) -> np.ndarray:
        sess = _session_labels(lab["ts_utc"])
        hit = lab["barrier_hit"].astype(str).to_numpy()
        return np.array([cells.bps.get(str(s), {}).get(_EXIT_CELL.get(h, ""), dearest) for s, h in zip(sess, hit,
                                                                                                         strict=True)],
                        dtype=float) * 1e-4
    return charge


# ------------------------------------------------------------------ priors
class NodeEvidence(FrozenRecord):
    n: int = 0
    sum_r: float = 0.0


def prior_weights(evidence: Mapping[str, NodeEvidence], cfg: DiscoverySettings) -> dict[str, float]:
    """A node's sampling multiplier: exp(prior_gain x sum R / (n + prior_n0)), clipped to [prior_min, prior_max]."""
    out: dict[str, float] = {}
    for k, e in evidence.items():
        if e.n > 0:
            w = math.exp(cfg.prior_gain * e.sum_r / (e.n + cfg.prior_n0))
            out[k] = float(min(cfg.prior_max, max(cfg.prior_min, w)))
    return out


def spec_nodes(spec: StrategySpec) -> list[str]:
    """The grammar nodes a spec is made of: its entry and filter leaves and its side primitive."""
    lv = [*leaves(spec.entry), *(leaves(spec.filter) if spec.filter is not None else []),
          *([spec.side_primitive] if spec.side_primitive is not None else [])]
    return sorted({node_key(x) for x in lv})


def _session_labels(ts: pd.Series) -> np.ndarray:
    """The gold session of each stamp (asia/london/newyork; the thin hours count as asia)."""
    from goldbot.data.calendar import DEFAULT_SESSIONS
    return DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(ts, utc=True)))


def _sessions(ts: pd.Series) -> np.ndarray:
    return np.array([_SESSION.get(str(x), "") for x in _session_labels(ts)], dtype=object)


def after_holdout(obs: pd.DataFrame) -> pd.DataFrame:
    """Only demo labels decided on or after HOLDOUT_END (2026-10-01): an earlier row (the holdout year, or before it)
    never reaches a prior (quant M1), whatever reader supplied it. A frame without `decision_utc` cannot show that, so
    none of it does (fails closed: uniform priors)."""
    from goldbot.research.exploration_data import HOLDOUT_END
    if obs.empty:
        return obs
    if "decision_utc" not in obs:
        log.warning("discovery: demo labels without decision_utc: no priors from them tonight")
        return obs.iloc[0:0]
    return obs[pd.to_datetime(obs["decision_utc"], utc=True) >= HOLDOUT_END].reset_index(drop=True)


def node_evidence(obs: pd.DataFrame, *, families: Sequence[str], registered: Mapping[str, StrategySpec],
                  timeframes: Sequence[str]) -> dict[str, dict[str, NodeEvidence]]:
    """timeframe -> node -> evidence. A discovered arm's labels (its forward result) go to every node of its spec; any
    other non-control labelled candidate goes to its family's node (when the family is a G2 leaf) and the node of the
    session it decided in. Controls and unlabelled rows never count."""
    out: dict[str, dict[str, NodeEvidence]] = {tf: {} for tf in timeframes}
    if obs.empty:
        return out
    x = obs[(~obs["is_control"].astype(bool)) & obs["label_sim_r"].notna() & obs["tf"].isin(list(timeframes))]
    if x.empty:
        return out
    x = x.assign(session=_sessions(x["decision_utc"]))
    fams = set(families)
    for key, g in x.groupby(["tf", "arm_id", "family", "session"], sort=True):
        tf, arm, fam, sess = (str(k) for k in key)
        n, s = int(len(g)), float(g["label_sim_r"].sum())
        nodes = spec_nodes(registered[arm]) if arm in registered else \
            ([f"structure:family_rule:{fam}"] if fam in fams else []) + ([f"time:{sess}"] if sess else [])
        book = out[tf]
        for k in nodes:
            e = book.get(k, NodeEvidence())
            book[k] = NodeEvidence(n=e.n + n, sum_r=e.sum_r + s)
    return out


# ------------------------------------------------------------------ the ADR 0085 power gate
def hold_days(timeframe: str) -> float:
    """A spec's label horizon in days, the card's `hold_days` (ADR 0096): its time barrier, MAX_BARS bars of its
    timeframe (every exit of the menu is capped there). The longest hold, not the mean: the mean needs outcomes."""
    return MAX_BARS[timeframe] * TF_SECONDS[timeframe] / 86_400


def power_card(settings: Settings, *, card_id: str, timeframe: str, signals: int, clusters: int, learn_years: float,
               now: pd.Timestamp) -> PowerCard:
    """A G2 spec's (or a card's) power card: XAUUSD alone, the explore window as learn, one fresh gate, TEST-OLD, at
    Holm rank 1 of `confirm_configs_per_batch` configs at the ledger year's alpha_j."""
    from goldbot.research.power_first import holm_first_z
    cfg = settings.research.discovery
    z = holm_first_z(batch_alpha(ledger_year(pd.Timestamp(now).to_pydatetime())), cfg.confirm_configs_per_batch)
    return PowerCard(card_id=card_id, family=FAMILY, timeframe=timeframe, learn_signals=int(signals),
                     learn_signal_clusters=min(int(clusters), int(signals)), learn_years=float(learn_years),
                     hold_days=hold_days(timeframe), instruments=[INSTRUMENT],
                     segments=[Segment(name=LEARN, years=float(learn_years)), Segment(name=TEST_OLD,
                                                                                      years=cfg.test_old_years)],
                     gates=[Gate(name=TEST_OLD, segments=(TEST_OLD,), z=z, fresh=True)])


def test_old_spent(registry_path: Path, reads_ledger: Path) -> str | None:
    """Why TEST-OLD is no longer fresh for any G2 spec, or None. TEST-OLD is read once per family
    (`promotion_confirm.fresh_refusal`) and every G2 spec is family `autodisc:G2`: an EVAL-2 TEST-OLD read of it in the
    reads ledger, or a promotion read of it on TEST-OLD in the registry, closes the gate for all of them. Read-only."""
    from goldbot.research.promotion_confirm import PROMOTION_CONFIRM, eval2_test_old_families
    if FAMILY in eval2_test_old_families(Path(reads_ledger)):
        return f"{FAMILY} was already read on TEST-OLD under EVAL-2: no G2 spec can reuse it"
    if Path(registry_path).exists():
        for line in Path(registry_path).read_text().splitlines():
            r = json.loads(line) if line.strip() else {}
            pc = (r.get("results") or {}).get("promotion_confirm") or {}
            if r.get("status") == PROMOTION_CONFIRM and r.get("family") == FAMILY and pc.get("source") == TEST_OLD:
                return f"{FAMILY} was already read on TEST-OLD: once per family, and every G2 spec is {FAMILY}"
    return None


def forward_verdict(settings: Settings, timeframe: str, *, signals: int, clusters: int, learn_years: float
                    ) -> PowerVerdict:
    """The ADR 0085 verdict of the window a discovered spec is judged on as a demo learning arm: the forward demo
    read over the kill rule's horizon, LEARNING_MAX_DAYS or LEARNING_READ_FILLS fills at the learn rate, whichever is
    first; a fresh-data gate (its n_eff floor applies), one-sided 5 %."""
    from goldbot.research.arms import LEARNING_MAX_DAYS
    rate = signals / learn_years if learn_years > 0 else 0.0
    days = LEARNING_MAX_DAYS / 365.25
    years = min(days, LEARNING_READ_FILLS / rate) if rate > 0 else days
    card = PowerCard(card_id=f"G2 {timeframe} forward n{signals} d{clusters}", family=FAMILY, timeframe=timeframe,
                     learn_signals=int(signals), learn_signal_clusters=min(int(clusters), int(signals)),
                     learn_years=float(learn_years), hold_days=hold_days(timeframe), instruments=[INSTRUMENT],
                     segments=[Segment(name=LEARN, years=float(learn_years)), Segment(name=FORWARD, years=years)],
                     gates=[Gate(name=FORWARD, segments=(FORWARD,), z=FORWARD_Z, fresh=True)])
    return evaluate(card, settings)


def kill_unreadable_rule() -> Callable[[str, int, int, float], str | None]:
    """(timeframe, signals, signal days, learn years) -> why the learning arm's kill rule could not read the spec by
    its `until`, or None: expected replayed candidates (explore rate x KILL_READ_DAYS) < MIN_N, or expected signal days
    < MIN_DAYS. Outcome-blind: counts only."""
    def why(timeframe: str, signals: int, clusters: int, learn_years: float) -> str | None:
        span = KILL_READ_DAYS / 365.25 / learn_years if learn_years > 0 else 0.0
        n, days = signals * span, clusters * span
        out = []
        if n < MIN_N:
            out.append(f"{timeframe}: {n:,.1f} expected candidates by until < {MIN_N}")
        if days < MIN_DAYS:
            out.append(f"{timeframe}: {days:,.1f} expected signal days by until < {MIN_DAYS}")
        return "; ".join(out) or None
    return why


def _category(reason: str) -> str:
    """A refusal's kind, from `power_first.evaluate`'s reason texts (the report carries kinds and counts only)."""
    if reason.startswith(FAMILY):
        return "TEST-OLD spent"
    if reason.startswith("no learn signals"):
        return "no signals"
    if reason.startswith("joint power"):
        return "joint power"
    return "fresh n_eff floor" if "fresh-data n_eff" in reason else "gate power"


class PowerTally(FrozenRecord):
    """Verdicts over every spec the night proposed on a timeframe: the search, its G0 null reruns and the plateau
    neighbours (only the admitted search and neighbour specs count in K_search; null ones in K_null)."""
    admitted: int = 0
    refused: int = 0
    reasons: dict[str, int] = {}             # refusals by kind (a refusal may have several)


class SpecPowerGate:
    """`gen_gp.Admit`: the ADR 0085 verdict of a spec from its signal count and its distinct signal days (outcome-blind:
    the entry mask and side only), cached by those two numbers; tallies what it admitted and refused."""

    def __init__(self, settings: Settings, *, timeframe: str, day_of_bar: np.ndarray, learn_years: float,
                 now: pd.Timestamp, closed: str | None = None) -> None:
        self.settings, self.timeframe, self.day, self.learn_years, self.now, self.closed = \
            settings, timeframe, np.asarray(day_of_bar), float(learn_years), now, closed
        self._cache: dict[tuple[int, int], PowerVerdict] = {}
        self.admitted = self.refused = 0
        self.reasons: Counter[str] = Counter()

    def counts(self, mask: np.ndarray, side: np.ndarray) -> tuple[int, int]:
        """(signals, distinct signal days): all the verdict reads."""
        sig = np.asarray(mask, bool) & (np.asarray(side) != 0)
        return int(sig.sum()), int(np.unique(self.day[sig]).size)

    def verdict(self, spec: StrategySpec, mask: np.ndarray, side: np.ndarray) -> PowerVerdict:
        """`closed` (`test_old_spent`): TEST-OLD is spent for the family, so every spec is refused with that reason."""
        key = self.counts(mask, side)
        if key not in self._cache:
            card = power_card(self.settings, card_id=f"G2 {self.timeframe} n{key[0]} d{key[1]}",
                              timeframe=self.timeframe, signals=key[0], clusters=key[1],
                              learn_years=self.learn_years, now=self.now)
            v = evaluate(card, self.settings)
            if self.closed is not None:
                v = v.model_copy(update={"started": False, "reasons": [self.closed, *v.reasons]})
            self._cache[key] = v
        return self._cache[key]

    def __call__(self, spec: StrategySpec, mask: np.ndarray, side: np.ndarray) -> list[str]:
        v = self.verdict(spec, mask, side)
        if v.started:
            self.admitted += 1
            return []
        self.refused += 1
        self.reasons.update({_category(r) for r in v.reasons})
        return list(v.reasons)

    def tally(self) -> PowerTally:
        return PowerTally(admitted=self.admitted, refused=self.refused, reasons=dict(sorted(self.reasons.items())))


def timeframe_reachable(settings: Settings, timeframe: str, *, n_bars: int, n_days: int, learn_years: float,
                        now: pd.Timestamp) -> PowerVerdict:
    """The verdict of the most frequent spec possible, one that trades every bar: refused means no spec on this
    timeframe can be powered, so the timeframe is not searched."""
    card = power_card(settings, card_id=f"G2 {timeframe} every bar", timeframe=timeframe, signals=n_bars,
                      clusters=n_days, learn_years=learn_years, now=now)
    return evaluate(card, settings)


# ------------------------------------------------------------------ catalogue cards
class CardTriage(FrozenRecord):
    started: list[str] = []                  # powered: each waits for its own pre-registration (K +1 there)
    refused: list[str] = []
    unscorable: list[str] = []               # no learn signal count or window stated
    out_of_focus: int = 0                    # timeframe not one of research.discovery.timeframes


def _window_years(window: Any) -> float | None:
    m = _WINDOW.search(str(window or ""))
    return float(int(m.group(2)) - int(m.group(1)) + 1) if m else None


def card_triage(cards_dir: str | Path, settings: Settings, now: pd.Timestamp) -> CardTriage:
    """The ADR 0085 verdict of every catalogue card on a focus timeframe that states its learn signal count
    (`evidence.n`) and window. A card states no signal days, so each signal is taken as its own day (m_day 1): the most
    favourable reading, so a refusal stands and a start is only a candidate for pre-registration."""
    started: list[str] = []
    refused: list[str] = []
    unscorable: list[str] = []
    out = 0
    focus = set(settings.research.discovery.timeframes)
    for f in sorted(Path(cards_dir).glob("*.yaml")):
        for c in (yaml.safe_load(f.read_text()) or {}).get("candidates") or []:
            tf = str(c.get("tf", "")).strip()
            if tf not in focus:
                out += 1
                continue
            ev = c.get("evidence") if isinstance(c.get("evidence"), dict) else {}
            n, years = ev.get("n"), _window_years(ev.get("window"))
            if not isinstance(n, int) or isinstance(n, bool) or n < 0 or years is None:
                unscorable.append(str(c.get("id")))
                continue
            v = evaluate(power_card(settings, card_id=str(c.get("id")), timeframe=tf, signals=n, clusters=n,
                                    learn_years=years, now=now), settings)
            (started if v.started else refused).append(str(c.get("id")))
    return CardTriage(started=started, refused=refused, unscorable=unscorable, out_of_focus=out)


# ------------------------------------------------------------------ the trial budget
class BudgetLine(FrozenRecord):
    quarter: str
    cap: int                                  # the quarter's trial budget (registry rows)
    used: int
    reserved: int
    units: int                                # registry rows the night can write: one discovery batch a timeframe
    planned_k: int                            # the night's K at its bound
    k_cap: int                                # research.discovery.k_cap_quarter[quarter] (0: not listed or closed)
    k_used: int                               # the quarter's discovery K so far (summed k_contrib)
    k_unsynced: int = 0                       # K in the real ledger no batch row counts yet (a killed or failed night)
    headroom: int | None = None
    refused: str | None = None


def planned_k(cfg: DiscoverySettings) -> int:
    """The night's K at its bound: every timeframe's search (population x (generations + 1)) and the plateau
    neighbours of its candidates (NEIGHBOUR_BOUND each). The G0 null counts in K_null, not here."""
    return len(cfg.timeframes) * (cfg.population * (cfg.generations + 1) + cfg.max_candidates * NEIGHBOUR_BOUND)


def budget_line(settings: Settings, slot: pd.Timestamp, used: Callable[[str], int],
                k_used: Callable[[str], int], k_unsynced: int = 0) -> BudgetLine:
    """Both budget checks of the night (module docstring, 4): registry rows on the trial budget's scale, K on its own."""
    from goldbot.research.catalogue.run import BudgetAbort, assert_budget
    from goldbot.research.director import quarter_budget
    from goldbot.research.registry import quarter_of
    cfg = settings.research.discovery
    q = quarter_of(pd.Timestamp(slot).to_pydatetime())
    cap, u = int(quarter_budget(settings.research, q)), int(used(q))
    reserved = int(settings.research.reserved_trials_quarter.get(q, 0))
    units, k, ku = len(cfg.timeframes), planned_k(cfg), int(k_used(q))
    line = BudgetLine(quarter=q, cap=cap, used=u, reserved=reserved, units=units, planned_k=k,
                      k_cap=cfg.k_cap_for(q), k_used=ku)
    try:
        left = assert_budget(units, u, cap, reserved=reserved)
    except BudgetAbort as exc:                    # a SystemExit: never let it reach the scheduler
        return line.model_copy(update={"refused": str(exc)})
    if q not in cfg.k_cap_quarter:
        return line.model_copy(update={"refused": (
            f"discovery K: {q} is not listed in research.discovery.k_cap_quarter: every night refused until the "
            "research director's written read adds it")})
    line = line.model_copy(update={"k_unsynced": int(k_unsynced)})
    ku += int(k_unsynced)
    if ku + k > line.k_cap:
        return line.model_copy(update={"refused": (
            f"discovery K: research.discovery.k_cap_quarter {q} {line.k_cap:,}, used {ku:,} ({int(k_unsynced):,} not "
            f"yet synced), tonight's planned K "
            f"{k:,} -> over by {ku + k - line.k_cap:,}")})
    return line.model_copy(update={"headroom": left})


def quarter_k_used(path: Path) -> Callable[[str], int]:
    """The quarter's discovery K: the registry's discovery batches stamped in it, summed k_contrib. The prior-search
    seed rows (`k_link.PRIOR_SEARCHES`, a one-off count of searches from before the link, stamped when first synced)
    are not a quarter's spend. A registry that does not exist has none (and is not created here)."""
    def used(q: str) -> int:
        if not path.exists():
            return 0
        from goldbot.research.autodisc.k_link import PRIOR_SEARCHES
        from goldbot.research.registry import TrialRegistry, quarter_of
        seed = {lid for lid, _, _ in PRIOR_SEARCHES}
        return sum(int(b.get("k_contrib") or 0) for b in TrialRegistry(path).discovery_batches()
                   if b.get("ledger_id") not in seed and quarter_of(datetime.fromisoformat(str(b["ts"]))) == q)
    return used


def unsynced_k(state_dir: Path, registry_path: Path) -> int:
    """K in the real discovery ledger that no registry batch counts yet: a night killed or failed before its
    `sync_discovery_k` (quant re-review 1). Counted in tonight's cap check; read-only (a ledger or registry that does
    not exist is not created)."""
    path = Path(state_dir) / LEDGER_FILE["real"]
    if not path.exists():
        return 0
    from goldbot.research.autodisc.k_link import counted
    from goldbot.research.autodisc.ledger import SearchLedger
    from goldbot.research.registry import TrialRegistry
    led = SearchLedger(path, mode="real")
    synced = counted(TrialRegistry(registry_path), led.ledger_id()) if Path(registry_path).exists() else 0
    return max(0, led.k_search() - synced)


def registry_used(path: Path) -> Callable[[str], int]:
    """The quarter's registry trials; a registry that does not exist has none (and is not created here)."""
    def used(q: str) -> int:
        if not path.exists():
            return 0
        from goldbot.research.registry import TrialRegistry
        return int(TrialRegistry(path).budget_used(q))
    return used


# ------------------------------------------------------------------ inputs
def read_demo_obs(settings: Settings, state_dir: Path, slot: pd.Timestamp, *, write: bool) -> pd.DataFrame:
    """Gold exploration candidates labelled by `slot`, net of the void evidence (the learner's own read); empty when
    aux.db does not exist. A dry run opens it without migrating. This read pools brokers, so a label costed while a
    refit cell was stale (`exploration_data.drop_stale_cost_labels`, research-director 2026-11-27) is left out."""
    from goldbot.db.connection import Database, db_path
    from goldbot.research import exploration_data as ex
    if not db_path(state_dir, "aux").exists():
        return pd.DataFrame()
    try:
        return ex.drop_stale_cost_labels(ex.read_obs(Database.open(state_dir, "aux", migrate=write), as_of=slot,
                                                     evidence="posterior", instrument=ex.GOLD))
    except Exception as exc:                 # noqa: BLE001 - no priors is a valid night (uniform grammar)
        log.warning("discovery: demo labels unreadable (%s): no priors from them tonight", type(exc).__name__)
        return pd.DataFrame()


def registered_specs(ledger_path: Path) -> dict[str, StrategySpec]:
    """arm id -> spec of every registered discovered arm, read-only from the ledger's database."""
    import sqlite3
    reg = H.registered(ledger_path)
    if not reg:
        return {}
    con = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    try:
        out = {}
        for arm, h in reg.items():
            row = con.execute("SELECT payload FROM entries WHERE kind='spec' AND spec_hash=? ORDER BY seq LIMIT 1",
                              (h,)).fetchone()
            if row is not None:
                out[arm] = StrategySpec.model_validate(json.loads(row[0])["spec"])
        return out
    finally:
        con.close()


class BarBackends:
    """The default backends: the research 1m bars read once (explore window only), one provider shared by every
    timeframe, the cost of record v3 per (session, exit) cell in bps (`cor_v3_cells`; mid labels unless the table
    splits out the spread)."""

    def __init__(self, settings: Settings) -> None:
        cfg = settings.research.discovery
        if not cfg.bars_path or not Path(cfg.bars_path).exists():
            raise FileNotFoundError(f"research.discovery.bars_path {cfg.bars_path!r} does not exist: the job is enabled "
                                    "but has no research bars (RUNBOOK: discovery_nightly)")
        from goldbot.execution.costs import settings_swap
        self.cells = cor_v3_cells(cfg.cost_of_record, cfg.cost_broker, cfg.cost_price_usd)
        self.version = self.cells.version
        self.swap = settings_swap(settings)
        self.b1 = load_explore_bars(cfg.bars_path)
        self.provider: Any = None

    def __call__(self, settings: Settings, timeframe: str) -> Backends:
        b = backends_from_1m(self.b1, timeframe, extra_cost_usd=0.0, swap=self.swap, cost_line=self.cells.line,
                             provider=self.provider, mid_labels=not self.cells.split,
                             extra_cost_frac=cell_charge(self.cells))
        self.provider = b.provider
        return b


def span_years(t: OutcomeTensor) -> float:
    return max((t.ts_utc[-1] - t.ts_utc[0]) / pd.Timedelta(days=365.25), 1e-6)


def default_executable(spec: StrategySpec) -> str | None:
    """Why the spec cannot run live as a demo arm (`arm_rules_spec.check` through `Arm`), None when it can."""
    from goldbot.research.arms import SPEC_RULE, Arm
    from goldbot.research.autodisc.spec import spec_hash
    try:
        Arm(id="DCAND", rule=SPEC_RULE, timeframe=spec.timeframe, max_open=1, forward_only=True,
            params={"spec": canonical_json(spec), "spec_hash": spec_hash(spec), "prereg": "pending"})
    except ValueError as exc:
        return str(exc).splitlines()[0][:200]
    return None


# ------------------------------------------------------------------ the run
class TimeframeRun(FrozenRecord):
    timeframe: str
    searched: bool = False
    skipped: str | None = None
    reachable: bool | None = None
    power: PowerTally = PowerTally()
    priors: dict[str, float] = {}
    families: int = 0
    k_search_tonight: int = 0
    k_null: int = 0
    evaluated: int = 0
    candidates: int = 0
    survivors: list[str] = []
    batch_ok: bool = False
    elapsed_s: float = 0.0


class DiscoveryRun(FrozenRecord):
    night: str
    dry_run: bool
    status: Status
    timeframes: list[TimeframeRun]
    k_spent: int = 0
    survivors: list[str] = []
    candidate_arms: int = 0                   # rows written to the hand-off (0 on a withheld night)
    would_offer: int = 0                      # survivors that passed every hand-off check
    withheld: bool = False                    # first_night_dry: K booked, the hand-off withheld (module docstring, 8)
    offered: list[str] = []                   # the offerable specs (hashes), listed in the report
    not_executable: int = 0
    kill_unreadable: int = 0                  # survivors their kill rule could not read by `until`: never offered
    n_refused_power: int = 0                  # specs the power gate refused (search, null, neighbours): never K
    stage_closed: int = 0                     # survivors whose confirm/holdout read already failed or was voided
    budget: BudgetLine | None = None
    cards: CardTriage | None = None
    cost_version: str = ""
    elapsed_s: float = 0.0
    budget_s: float = 0.0


def run_discovery(settings: Settings, state_dir: Path, slot: pd.Timestamp, *,
                  backends: Callable[[Settings, str], Backends] | None = None,
                  obs_reader: Callable[..., pd.DataFrame] | None = None,
                  registry_path: Path | None = None, budget_used: Callable[[str], int] | None = None,
                  clock: Callable[[], float] = time.monotonic, dry_run: bool = False,
                  executable: Callable[[StrategySpec], str | None] | None = None, min_events: int | None = None,
                  learn_years: Callable[[OutcomeTensor], float] = span_years,
                  cards_dir: Path | None = None, k_used: Callable[[str], int] | None = None,
                  reads_ledger: Path | None = None,
                  kill_unreadable: Callable[[str, int, int, float], str | None] | None = None) -> DiscoveryRun:
    """The night (module docstring). Real backends write the registry's discovery K (`k_link`); synthetic ones never
    reach the registry. `dry_run`: everything up to the search (inputs, priors, power reachability, cards, budget),
    nothing written anywhere. `reads_ledger`: EVAL-2's reads ledger (default beside the registry);
    `kill_unreadable`: why a survivor's kill rule could not read it (default `kill_unreadable_rule`).
    `research.discovery.first_night_dry` (module docstring, 8): K booked, the candidate rows withheld."""
    from goldbot.research.autodisc.k_link import dsr_k_offset, sync_discovery_k
    from goldbot.research.autodisc.ledger import SearchLedger
    from goldbot.research.autodisc.stages import StageJournal
    from goldbot.research.catalogue.run import READS_LEDGER
    from goldbot.research.registry import TrialRegistry

    cfg = settings.research.discovery
    state_dir = Path(state_dir)
    night = f"{pd.Timestamp(slot):%Y-%m-%d}"
    start = clock()
    reg_path = Path(registry_path) if registry_path is not None else Path(settings.research.registry)
    cards = card_triage(cards_dir or cfg.cards_dir, settings, slot) if Path(cards_dir or cfg.cards_dir).is_dir() \
        else None
    budget = budget_line(settings, slot, budget_used or registry_used(reg_path), k_used or quarter_k_used(reg_path),
                         unsynced_k(state_dir, reg_path))
    if budget.refused is not None:
        run = DiscoveryRun(night=night, dry_run=dry_run, status="over_budget", budget=budget, cards=cards,
                           timeframes=[TimeframeRun(timeframe=tf, skipped="over the quarter's budget")
                                       for tf in cfg.timeframes], budget_s=cfg.budget_s, elapsed_s=clock() - start)
        if not dry_run:
            write_atomic(state_dir / REPORT_DIR / f"discovery-{night}.md", report(run, []))
            record(state_dir, run, None)
        return run
    closed = test_old_spent(reg_path, Path(reads_ledger) if reads_ledger is not None
                            else reg_path.with_name(READS_LEDGER))
    if closed is not None:                        # no G2 spec can be admitted: nothing is loaded or searched
        run = DiscoveryRun(night=night, dry_run=dry_run, status="dry_run" if dry_run else "ok", budget=budget,
                           cards=cards, timeframes=[TimeframeRun(timeframe=tf, reachable=False,
                                                                 skipped=f"power: TEST-OLD spent ({closed})")
                                                    for tf in cfg.timeframes],
                           budget_s=cfg.budget_s, elapsed_s=clock() - start)
        if not dry_run:
            write_atomic(state_dir / REPORT_DIR / f"discovery-{night}.md", report(run, []))
            record(state_dir, run, None)
        return run

    get = backends or BarBackends(settings)
    obs = after_holdout((obs_reader or read_demo_obs)(settings, state_dir, slot, write=not dry_run))
    why_kill_unreadable = kill_unreadable or kill_unreadable_rule()
    out: list[TimeframeRun] = []
    texts: list[str] = []
    offered: list[H.CandidateArm] = []
    status: Status = "dry_run" if dry_run else "ok"
    ledger_path: Path | None = None
    survivors: list[str] = []
    not_exec = stage_closed = unreadable = 0
    last = 0.0
    for tf in cfg.timeframes:
        t0 = clock()
        spent = t0 - start
        if spent >= cfg.budget_s or (last and cfg.budget_s - spent < last):
            out.append(TimeframeRun(timeframe=tf, skipped=f"time budget: {spent:,.0f} s of {cfg.budget_s:,.0f} s spent, "
                                                          f"the last timeframe took {last:,.0f} s"))
            status = "budget" if not dry_run else status
            continue
        b = get(settings, tf)
        t = b.tensor
        days = pd.factorize(t.ts_utc.normalize())[0]
        years = learn_years(t)
        reach = timeframe_reachable(settings, tf, n_bars=t.n_bars, n_days=int(days.max()) + 1, learn_years=years,
                                    now=slot)
        if ledger_path is None:
            ledger_path = state_dir / LEDGER_FILE[b.mode]
        reg_specs = registered_specs(ledger_path) if ledger_path.exists() else {}
        ev = node_evidence(obs, families=b.families, registered=reg_specs, timeframes=(tf,))[tf]
        priors = prior_weights(ev, cfg)
        if not reach.started:
            out.append(TimeframeRun(timeframe=tf, reachable=False, priors=priors, families=len(b.families),
                                    skipped="power: no spec on this timeframe can reach ADR 0085's power, not even one "
                                            "that trades every bar"))
            continue
        if dry_run:
            out.append(TimeframeRun(timeframe=tf, reachable=True, priors=priors, families=len(b.families),
                                    skipped="dry run"))
            continue
        ledger = SearchLedger(ledger_path, mode=b.mode)
        registry = TrialRegistry(reg_path) if b.mode == "real" else None
        offset = dsr_k_offset(registry, ledger) if registry is not None else 0
        gate = SpecPowerGate(settings, timeframe=tf, day_of_bar=days, learn_years=years, now=slot, closed=closed)
        gp = GPConfig(timeframe=tf, population=cfg.population, generations=cfg.generations,
                      seed=night_seed(cfg.seed, slot), min_events=min_events, families=b.families, leaf_priors=priors)
        k0, n0 = ledger.k_search(), ledger.k_null()
        res = search_and_judge(t, b.provider, ledger, gp, reps=cfg.null_reps, max_candidates=cfg.max_candidates,
                               cost_line=b.cost_line, night=night, k_offset=offset, admit=gate)
        if registry is not None:
            sync_discovery_k(registry, ledger, generator=res.generator, timeframe=tf)
        journal = StageJournal(ledger_path)
        for s in res.survivors:
            st = journal.state(s.spec_hash)
            if any(x is not None and x != "passed" for x in (st.confirm, st.holdout)):
                stage_closed += 1
                continue
            spec = ledger.get_spec(s.spec_hash)
            assert spec is not None             # a survivor is a logged search spec (record_survivors)
            why = (executable or default_executable)(spec)
            if why is not None:
                not_exec += 1
                continue
            entries = _entries(b, spec)
            counts = gate.counts(*entries)
            if why_kill_unreadable(tf, *counts, years) is not None:
                unreadable += 1
                continue
            fwd = forward_verdict(settings, tf, signals=counts[0], clusters=counts[1], learn_years=years)
            offered.append(H.CandidateArm(spec_hash=s.spec_hash, night=night, timeframe=tf,
                                          style=H.STYLE_BY_TF[tf], spec=canonical_json(spec), dsr=s.dsr,
                                          meets_confirm_dsr=s.meets_confirm_dsr, k_search=res.k_dsr,
                                          power=gate.verdict(spec, *entries).model_dump(mode="json"),
                                          neighbours_admitted=s.neighbours_admitted,
                                          neighbours_total=s.neighbours_total,
                                          power_forward={"reading": FORWARD_READING,
                                                         **fwd.model_dump(mode="json")},
                                          explore={k: v for k, v in (ledger.result(s.spec_hash) or {}).items()
                                                   if k in EXPLORE_FIELDS}))
        survivors += [s.spec_hash for s in res.survivors]
        texts.append(_tf_text(tf, gate.tally(), priors) + render(res))
        last = clock() - t0
        out.append(TimeframeRun(timeframe=tf, searched=True, reachable=True, power=gate.tally(), priors=priors,
                                families=len(b.families), k_search_tonight=ledger.k_search() - k0,
                                k_null=ledger.k_null() - n0, evaluated=res.evaluated, candidates=res.candidates,
                                survivors=[s.spec_hash for s in res.survivors], batch_ok=res.batch_ok, elapsed_s=last))
    withhold = cfg.first_night_dry and not dry_run
    run = DiscoveryRun(night=night, dry_run=dry_run, status=status, timeframes=out,
                       k_spent=sum(x.k_search_tonight for x in out), survivors=survivors,
                       n_refused_power=sum(x.power.refused for x in out),
                       candidate_arms=0 if withhold else len(offered), would_offer=len(offered), withheld=withhold,
                       offered=[c.spec_hash for c in offered], not_executable=not_exec, kill_unreadable=unreadable,
                       stage_closed=stage_closed, budget=budget,
                       cards=cards, cost_version=getattr(get, "version", ""), budget_s=cfg.budget_s,
                       elapsed_s=clock() - start)
    if not dry_run:
        if ledger_path is not None and offered and not withhold:     # the hand-off; never on a withheld night
            run = run.model_copy(update={"candidate_arms": H.write_candidates(ledger_path, offered)})
        write_atomic(state_dir / REPORT_DIR / f"discovery-{night}.md", report(run, texts))
        record(state_dir, run, ledger_path)
    return run


def _withheld_text(run: DiscoveryRun) -> str:
    """The withheld night's section: its K is booked, and the specs it would have offered (hash, explore only)."""
    rows = "".join(f"- {h[:12]} ({h})\n" for h in run.offered) or "- none\n"
    return (f"\n## first night dry (research.discovery.first_night_dry, quant GO condition (b))\n"
            f"K booked {run.k_spent:,} (ledger and registry, as on any night); hand-off withheld: "
            f"{run.would_offer:,} candidate arm(s) would have been offered, none written. A quant reviewer reads this "
            f"report, then sets first_night_dry false (RUNBOOK 'Discovery nightly on the brain').\n{rows}")


def _entries(b: Backends, spec: StrategySpec) -> tuple[np.ndarray, np.ndarray]:
    from goldbot.research.autodisc.gen_gp import Resolver
    return Resolver(b.provider).entries(spec)


def _tf_text(tf: str, p: PowerTally, priors: Mapping[str, float]) -> str:
    why = ", ".join(f"{k} {v:,}" for k, v in p.reasons.items()) or "none"
    tilt = ", ".join(f"{k} x{w:.2f}" for k, w in sorted(priors.items())) or "none (uniform grammar)"
    return (f"\n## {tf}\npower-first (ADR 0085): admitted {p.admitted:,}, refused {p.refused:,} before any K "
            f"(refusals by kind: {why})\ngrammar-node priors: {tilt}\n")


def report(run: DiscoveryRun, texts: Sequence[str]) -> str:
    """The night's report: explore-window numbers only (no confirm or holdout date), as the nightly report."""
    b = run.budget
    head = [f"discovery nightly | status {run.status} | K spent {run.k_spent:,} | power-gate refusals "
            f"{run.n_refused_power:,} (not K) | survivors {len(run.survivors):,} | "
            f"candidate arms offered {run.candidate_arms:,} (not executable live {run.not_executable:,}, "
            f"kill rule unreadable {run.kill_unreadable:,}, stage read closed {run.stage_closed:,})"]
    if b is not None:
        head.append(f"trial budget: cap {b.cap:,}, used {b.used:,}, reserved {b.reserved:,}, tonight's registry rows "
                    f"{b.units:,} | discovery K: cap {b.k_cap:,}, used {b.k_used:,}, planned {b.planned_k:,}")
        if b.refused:
            head.append(f"refused: {b.refused}")
    for x in run.timeframes:
        if not x.searched:
            head.append(f"{x.timeframe}: not searched: {x.skipped}")
    if run.cards is not None:
        c = run.cards
        head.append(f"catalogue cards: power-started {len(c.started):,} (await a pre-registration), refused "
                    f"{len(c.refused):,}, unscorable {len(c.unscorable):,}, out of focus {c.out_of_focus:,}")
    text = "\n".join(head) + "\n" + (_withheld_text(run) if run.withheld else "") + "".join(texts)
    assert_no_confirm_numbers(text)
    return text


def record(state_dir: Path, run: DiscoveryRun, ledger_path: Path | None) -> None:
    """The run record: aux.db `discovery_runs` (one row a run) and, when a ledger was opened, its `nightly_runs`."""
    from goldbot.db.connection import Database
    body = run.model_dump(mode="json")
    with Database.open(state_dir, "aux").write() as conn:
        conn.execute("INSERT INTO discovery_runs (night, run_ns, status, k_spent, survivors, body) VALUES (?,?,?,?,?,?)",
                     (run.night, time.time_ns(), run.status, run.k_spent, len(run.survivors),
                      json.dumps(body, sort_keys=True)))
    if ledger_path is not None and ledger_path.exists():
        H.record_run(ledger_path, run.night, body)


def record_failed(state_dir: Path, slot: pd.Timestamp, status: Literal["killed", "failed"], error: str) -> None:
    """The parent's record of a child that did not finish: `killed` at its budget, or `failed` (an error, MemoryError,
    the OOM killer). Whatever K it spent is in the ledger and the next night's `sync_discovery_k` counts it in the
    registry."""
    from goldbot.db.connection import Database
    night = f"{pd.Timestamp(slot):%Y-%m-%d}"
    with Database.open(state_dir, "aux").write() as conn:
        conn.execute("INSERT INTO discovery_runs (night, run_ns, status, k_spent, survivors, body) VALUES (?,?,?,?,?,?)",
                     (night, time.time_ns(), status, 0, 0, json.dumps({"error": error, "k_note": "the ledger holds "
                                                                       "the K; the next sync counts it"})))


def summary(run: DiscoveryRun) -> dict[str, Any]:
    per = {x.timeframe: (f"admitted {x.power.admitted:,} refused {x.power.refused:,} K {x.k_search_tonight:,}"
                         if x.searched else f"skipped ({x.skipped})") for x in run.timeframes}
    return {"status": run.status, "k_spent": run.k_spent, "n_refused_power": run.n_refused_power,
            "survivors": len(run.survivors),
            "candidate_arms": run.candidate_arms, "timeframes": per, "elapsed_s": round(run.elapsed_s, 1),
            "report": f"{REPORT_DIR}/discovery-{run.night}.md" if not run.dry_run else None,
            "withheld": run.withheld,
            "health": f"discovery {run.status}: K {run.k_spent:,}, survivors {len(run.survivors)}, offered "
                      f"{run.candidate_arms} (explore only, not evidence)"
                      + (f"; first night dry: {run.would_offer} withheld for the quant review (K booked)"
                         if run.withheld else "")}


@contextmanager
def run_lock(state_dir: Path) -> Iterator[None]:
    """One discovery run at a time on a host (the scheduler's night and a foreground run would share the ledger and
    the registry sync): a non-blocking flock on state/autodisc/discovery.lock, refused when another run holds it. The
    kernel drops the lock with its process, so a killed child never leaves it held."""
    path = Path(state_dir) / REPORT_DIR / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError(f"another discovery run holds {path} (the run lock): refused") from exc
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run_job(settings: Settings, state_dir: Path, slot: pd.Timestamp) -> dict[str, Any]:
    """The child's entry point (ops/bounded.py): skip when stopped or off; refused while another run holds the lock; enabled
    without bars fails (never a silent skip, which is what retired `autodisc_nightly`)."""
    cfg = settings.research.discovery
    if cfg.stopped:
        return {"skipped": "research.discovery.stopped (plan 2026-11-25 S6: discovery stops; no run is logged)"}
    if not cfg.enabled:
        return {"skipped": "research.discovery.enabled is false (ADR 0090: on per host with bars_path)"}
    with run_lock(Path(state_dir)):
        if not cfg.bars_path or not Path(cfg.bars_path).exists():
            raise FileNotFoundError(f"research.discovery.bars_path {cfg.bars_path!r} does not exist: the job is enabled "
                                    "but has no research bars (RUNBOOK: discovery_nightly)")
        return summary(run_discovery(settings, Path(state_dir), pd.Timestamp(slot)))


def main(argv: Sequence[str] | None = None) -> int:
    from goldbot.config import load_settings
    ap = argparse.ArgumentParser(prog="python -m goldbot.research.autodisc.discovery",
                                 description="One discovery night in the foreground (ADR 0090); --dry-run writes nothing")
    ap.add_argument("--state-dir", type=Path, default=Path("state"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    s = load_settings()
    if a.dry_run:
        run = run_discovery(s, a.state_dir, pd.Timestamp.now("UTC"), dry_run=True)
    elif s.research.discovery.stopped:
        print("refused: research.discovery.stopped (plan 2026-11-25 S6); --dry-run writes nothing and still runs")
        return 2
    else:
        with run_lock(a.state_dir):
            run = run_discovery(s, a.state_dir, pd.Timestamp.now("UTC"))
    from goldbot.ops.bounded import peak_rss_mb
    print(json.dumps(summary(run) | {"max_rss_mb": round(peak_rss_mb(), 1)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
