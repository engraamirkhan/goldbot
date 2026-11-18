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
   and adds nothing to K. The card is XAUUSD alone; its fresh gate is TEST-OLD (2006-02..2009) at Holm rank 1 of
   `confirm_configs_per_batch` configs at the ledger year's alpha_j (`promotion_confirm.batch_alpha`).
4. Budget. The night's planned K (every timeframe at its bound) must fit the quarter's trial budget with the
   pre-registered queue's reservation held back (`catalogue.run.assert_budget`), though discovery K is a sidecar and
   never a budget slot (k_link): a tightening. Wall clock: no timeframe starts once `budget_s` is spent, or when the
   last one took longer than what is left; the parent kills the child at budget_s + kill_grace_s.
5. Screen. `search_and_judge` (floors, the 1.5x cost stress, the G0 null of the whole search, SPA/PBO, the shift null,
   the plateau, the DSR at the registry's discovery K), costs from the cost of record v3 (ADR 0084, the broker's
   dearest charged cell, on top of the bar spread: conservative). The stage journal (`stages.StageJournal`) is read,
   never opened: a spec whose confirm or holdout read failed or was voided is never offered again ("the failed hash can
   never be re-confirmed"). This job opens no confirm read: ADR 0085 allows 4 confirm batches a ledger year, and a
   nightly job must not spend them.
6. Holdout sealed. The 1m bars come through `cost_of_record.load_research_bars` (it refuses a window reaching the
   holdout before the file is opened) and are cut to the explore window (2018+ raises in `windows.ExploreFrame`).
7. Out. Survivors that can run live (`arm_rules_spec`, prereg still to come) -> `handoff.candidate_arms`, the
   registration hand-off; the run record -> aux.db `discovery_runs` and the discovery ledger's `nightly_runs`; the
   report -> state/autodisc/discovery-<night>.md (explore numbers only). A dry run reads and plans and writes nothing.

"An explore survivor is a recorded fact, never evidence of edge." Nothing here trades, registers an arm or edits a
config: registration stays `ops/autodisc_register.py` (demo accounts; the order gate is unchanged)."""
from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
import yaml

from goldbot.base import FrozenRecord, write_atomic
from goldbot.config import DiscoverySettings, Settings
from goldbot.research.autodisc import handoff as H
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
LEARN, TEST_OLD = "learn", "test_old"
NEIGHBOUR_BOUND = 24          # plateau neighbours of one candidate at most (gen_gp.neighbours: +-1 rung a leaf, exits)
Status = Literal["ok", "budget", "over_budget", "dry_run"]
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


def cor_v3_extra(path: str | Path, broker: str) -> tuple[float, str, str]:
    """(USD per oz, version, cost line) of the cost of record v3 (ADR 0084): the broker's dearest `charged_usd` over
    every session and exit, charged on every round trip on top of the tensor's bar spread (conservative: v3 measures
    against the mid, so the bar spread is a second charge). The line carries no date (the report is explore-only)."""
    table = yaml.safe_load(Path(path).read_text())
    cells = table["cells"][broker]
    worst = max(float(c["charged_usd"]) for by_exit in cells.values() for c in by_exit.values())
    return worst, str(table["version"]), (f"cost of record v3 ({broker} dearest cell {worst:.3f} $/oz a round trip on "
                                          "top of the bar spread)")


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


def _sessions(ts: pd.Series) -> np.ndarray:
    from goldbot.data.calendar import DEFAULT_SESSIONS
    lab = DEFAULT_SESSIONS.session_label(pd.DatetimeIndex(pd.to_datetime(ts, utc=True)))
    return np.array([_SESSION.get(str(x), "") for x in lab], dtype=object)


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
def power_card(settings: Settings, *, card_id: str, timeframe: str, signals: int, clusters: int, learn_years: float,
               now: pd.Timestamp) -> PowerCard:
    """A G2 spec's (or a card's) power card: XAUUSD alone, the explore window as learn, one fresh gate, TEST-OLD, at
    Holm rank 1 of `confirm_configs_per_batch` configs at the ledger year's alpha_j."""
    from goldbot.research.power_first import holm_first_z
    cfg = settings.research.discovery
    z = holm_first_z(batch_alpha(ledger_year(pd.Timestamp(now).to_pydatetime())), cfg.confirm_configs_per_batch)
    return PowerCard(card_id=card_id, family=FAMILY, timeframe=timeframe, learn_signals=int(signals),
                     learn_signal_clusters=min(int(clusters), int(signals)), learn_years=float(learn_years),
                     instruments=[INSTRUMENT],
                     segments=[Segment(name=LEARN, years=float(learn_years)), Segment(name=TEST_OLD,
                                                                                      years=cfg.test_old_years)],
                     gates=[Gate(name=TEST_OLD, segments=(TEST_OLD,), z=z, fresh=True)])


def _category(reason: str) -> str:
    """A refusal's kind, from `power_first.evaluate`'s reason texts (the report carries kinds and counts only)."""
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
                 now: pd.Timestamp) -> None:
        self.settings, self.timeframe, self.day, self.learn_years, self.now = \
            settings, timeframe, np.asarray(day_of_bar), float(learn_years), now
        self._cache: dict[tuple[int, int], PowerVerdict] = {}
        self.admitted = self.refused = 0
        self.reasons: Counter[str] = Counter()

    def verdict(self, spec: StrategySpec, mask: np.ndarray, side: np.ndarray) -> PowerVerdict:
        sig = np.asarray(mask, bool) & (np.asarray(side) != 0)
        key = (int(sig.sum()), int(np.unique(self.day[sig]).size))
        if key not in self._cache:
            card = power_card(self.settings, card_id=f"G2 {self.timeframe} n{key[0]} d{key[1]}",
                              timeframe=self.timeframe, signals=key[0], clusters=key[1],
                              learn_years=self.learn_years, now=self.now)
            self._cache[key] = evaluate(card, self.settings)
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
    cap: int
    used: int
    reserved: int
    planned_k: int
    headroom: int | None = None
    refused: str | None = None


def planned_k(cfg: DiscoverySettings) -> int:
    """The night's K at its bound: every timeframe's search (population x (generations + 1)) and the plateau
    neighbours of its candidates (NEIGHBOUR_BOUND each). The G0 null counts in K_null, not here."""
    return len(cfg.timeframes) * (cfg.population * (cfg.generations + 1) + cfg.max_candidates * NEIGHBOUR_BOUND)


def budget_line(settings: Settings, slot: pd.Timestamp, used: Callable[[str], int]) -> BudgetLine:
    from goldbot.research.catalogue.run import BudgetAbort, assert_budget
    from goldbot.research.director import quarter_budget
    from goldbot.research.registry import quarter_of
    q = quarter_of(pd.Timestamp(slot).to_pydatetime())
    cap, u = int(quarter_budget(settings.research, q)), int(used(q))
    reserved = int(settings.research.reserved_trials_quarter.get(q, 0))
    k = planned_k(settings.research.discovery)
    try:
        left = assert_budget(k, u, cap, reserved=reserved)
    except BudgetAbort as exc:                    # a SystemExit: never let it reach the scheduler
        return BudgetLine(quarter=q, cap=cap, used=u, reserved=reserved, planned_k=k, refused=str(exc))
    return BudgetLine(quarter=q, cap=cap, used=u, reserved=reserved, planned_k=k, headroom=left)


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
    aux.db does not exist. A dry run opens it without migrating."""
    from goldbot.db.connection import Database, db_path
    from goldbot.research.exploration_data import GOLD, read_obs
    if not db_path(state_dir, "aux").exists():
        return pd.DataFrame()
    try:
        return read_obs(Database.open(state_dir, "aux", migrate=write), as_of=slot, evidence="posterior",
                        instrument=GOLD)
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
    timeframe, the cost of record v3."""

    def __init__(self, settings: Settings) -> None:
        cfg = settings.research.discovery
        if not cfg.bars_path or not Path(cfg.bars_path).exists():
            raise FileNotFoundError(f"research.discovery.bars_path {cfg.bars_path!r} does not exist: the job is enabled "
                                    "but has no research bars (RUNBOOK: discovery_nightly)")
        from goldbot.execution.costs import settings_swap
        self.extra, self.version, self.line = cor_v3_extra(cfg.cost_of_record, cfg.cost_broker)
        self.swap = settings_swap(settings)
        self.b1 = load_explore_bars(cfg.bars_path)
        self.provider: Any = None

    def __call__(self, settings: Settings, timeframe: str) -> Backends:
        b = backends_from_1m(self.b1, timeframe, extra_cost_usd=self.extra, swap=self.swap, cost_line=self.line,
                             provider=self.provider)
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
    candidate_arms: int = 0
    not_executable: int = 0
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
                  cards_dir: Path | None = None) -> DiscoveryRun:
    """The night (module docstring). Real backends write the registry's discovery K (`k_link`); synthetic ones never
    reach the registry. `dry_run`: everything up to the search (inputs, priors, power reachability, cards, budget),
    nothing written anywhere."""
    from goldbot.research.autodisc.k_link import dsr_k_offset, sync_discovery_k
    from goldbot.research.autodisc.ledger import SearchLedger
    from goldbot.research.autodisc.stages import StageJournal
    from goldbot.research.registry import TrialRegistry

    cfg = settings.research.discovery
    state_dir = Path(state_dir)
    night = f"{pd.Timestamp(slot):%Y-%m-%d}"
    start = clock()
    reg_path = Path(registry_path) if registry_path is not None else Path(settings.research.registry)
    cards = card_triage(cards_dir or cfg.cards_dir, settings, slot) if Path(cards_dir or cfg.cards_dir).is_dir() \
        else None
    budget = budget_line(settings, slot, budget_used or registry_used(reg_path))
    if budget.refused is not None:
        run = DiscoveryRun(night=night, dry_run=dry_run, status="over_budget", budget=budget, cards=cards,
                           timeframes=[TimeframeRun(timeframe=tf, skipped="over the quarter's trial budget")
                                       for tf in cfg.timeframes], budget_s=cfg.budget_s, elapsed_s=clock() - start)
        if not dry_run:
            record(state_dir, run, None)
        return run

    get = backends or BarBackends(settings)
    obs = (obs_reader or read_demo_obs)(settings, state_dir, slot, write=not dry_run)
    out: list[TimeframeRun] = []
    texts: list[str] = []
    offered: list[H.CandidateArm] = []
    status: Status = "dry_run" if dry_run else "ok"
    ledger_path: Path | None = None
    survivors: list[str] = []
    not_exec = closed = 0
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
        gate = SpecPowerGate(settings, timeframe=tf, day_of_bar=days, learn_years=years, now=slot)
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
                closed += 1
                continue
            spec = ledger.get_spec(s.spec_hash)
            assert spec is not None             # a survivor is a logged search spec (record_survivors)
            why = (executable or default_executable)(spec)
            if why is not None:
                not_exec += 1
                continue
            offered.append(H.CandidateArm(spec_hash=s.spec_hash, night=night, timeframe=tf,
                                          style=H.STYLE_BY_TF[tf], spec=canonical_json(spec), dsr=s.dsr,
                                          meets_confirm_dsr=s.meets_confirm_dsr, k_search=res.k_dsr,
                                          power=gate.verdict(spec, *_entries(b, spec)).model_dump(mode="json")))
        survivors += [s.spec_hash for s in res.survivors]
        texts.append(_tf_text(tf, gate.tally(), priors) + render(res))
        last = clock() - t0
        out.append(TimeframeRun(timeframe=tf, searched=True, reachable=True, power=gate.tally(), priors=priors,
                                families=len(b.families), k_search_tonight=ledger.k_search() - k0,
                                k_null=ledger.k_null() - n0, evaluated=res.evaluated, candidates=res.candidates,
                                survivors=[s.spec_hash for s in res.survivors], batch_ok=res.batch_ok, elapsed_s=last))
    run = DiscoveryRun(night=night, dry_run=dry_run, status=status, timeframes=out,
                       k_spent=sum(x.k_search_tonight for x in out), survivors=survivors,
                       candidate_arms=len(offered), not_executable=not_exec, stage_closed=closed, budget=budget,
                       cards=cards, cost_version=getattr(get, "version", ""), budget_s=cfg.budget_s,
                       elapsed_s=clock() - start)
    if not dry_run:
        if ledger_path is not None and offered:
            run = run.model_copy(update={"candidate_arms": H.write_candidates(ledger_path, offered)})
        write_atomic(state_dir / REPORT_DIR / f"discovery-{night}.md", report(run, texts))
        record(state_dir, run, ledger_path)
    return run


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
    head = [f"discovery nightly | status {run.status} | K spent {run.k_spent:,} | survivors {len(run.survivors):,} | "
            f"candidate arms offered {run.candidate_arms:,} (not executable live {run.not_executable:,}, stage read "
            f"closed {run.stage_closed:,})"]
    if b is not None:
        head.append(f"trial budget: cap {b.cap:,}, used {b.used:,}, reserved {b.reserved:,}, planned K {b.planned_k:,}")
    for x in run.timeframes:
        if not x.searched:
            head.append(f"{x.timeframe}: not searched: {x.skipped}")
    if run.cards is not None:
        c = run.cards
        head.append(f"catalogue cards: power-started {len(c.started):,} (await a pre-registration), refused "
                    f"{len(c.refused):,}, unscorable {len(c.unscorable):,}, out of focus {c.out_of_focus:,}")
    text = "\n".join(head) + "\n" + "".join(texts)
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


def record_killed(state_dir: Path, slot: pd.Timestamp, error: str) -> None:
    """The parent's record of a child killed at its budget: its K is in the ledger and the next night's
    `sync_discovery_k` counts it in the registry."""
    from goldbot.db.connection import Database
    night = f"{pd.Timestamp(slot):%Y-%m-%d}"
    with Database.open(state_dir, "aux").write() as conn:
        conn.execute("INSERT INTO discovery_runs (night, run_ns, status, k_spent, survivors, body) VALUES (?,?,?,?,?,?)",
                     (night, time.time_ns(), "killed", 0, 0, json.dumps({"error": error, "k_note": "the ledger holds "
                                                                         "the K; the next sync counts it"})))


def summary(run: DiscoveryRun) -> dict[str, Any]:
    per = {x.timeframe: (f"admitted {x.power.admitted:,} refused {x.power.refused:,} K {x.k_search_tonight:,}"
                         if x.searched else f"skipped ({x.skipped})") for x in run.timeframes}
    return {"status": run.status, "k_spent": run.k_spent, "survivors": len(run.survivors),
            "candidate_arms": run.candidate_arms, "timeframes": per,
            "report": f"{REPORT_DIR}/discovery-{run.night}.md" if not run.dry_run else None,
            "health": f"discovery {run.status}: K {run.k_spent:,}, survivors {len(run.survivors)}, offered "
                      f"{run.candidate_arms} (explore only, not evidence)"}


def run_job(settings: Settings, state_dir: Path, slot: pd.Timestamp) -> dict[str, Any]:
    """The child's entry point (ops/bounded.py): skip when off; enabled without bars fails (never a silent skip,
    which is what retired `autodisc_nightly`)."""
    cfg = settings.research.discovery
    if not cfg.enabled:
        return {"skipped": "research.discovery.enabled is false (ADR 0090: on per host with bars_path)"}
    if not cfg.bars_path or not Path(cfg.bars_path).exists():
        raise FileNotFoundError(f"research.discovery.bars_path {cfg.bars_path!r} does not exist: the job is enabled but "
                                "has no research bars (RUNBOOK: discovery_nightly)")
    return summary(run_discovery(settings, Path(state_dir), pd.Timestamp(slot)))


def main(argv: Sequence[str] | None = None) -> int:
    from goldbot.config import load_settings
    ap = argparse.ArgumentParser(prog="python -m goldbot.research.autodisc.discovery",
                                 description="One discovery night in the foreground (ADR 0090); --dry-run writes nothing")
    ap.add_argument("--state-dir", type=Path, default=Path("state"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    s = load_settings()
    run = run_discovery(s, a.state_dir, pd.Timestamp.now("UTC"), dry_run=a.dry_run)
    print(json.dumps(summary(run), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
