"""`autodisc_nightly` (ADR 0058 phase 2): one bounded G2 search on the explore window, judged by the honesty set.

    run_nightly_job(settings, state_dir, slot)   the scheduler job (goldbot/ops/jobs.py): skips unless
                                                 research.autodisc.nightly_enabled, then search_and_judge and the report
    search_and_judge(t, provider, ledger, cfg)   G2 search, G0 null of the whole search, then the honesty set
    render(result)                               the survivors report text (no confirm-window number in it)

Per design section 3.2, an explore survivor here has: floors, positive min-fold fitness and excess over the drift
baseline, the 1.5x cost stress (the evaluator's own checks); a fitness above the 95th percentile of best-of-search over
`null_reps` reruns of the WHOLE G2 search on random sides (step 7, "the reality check for an adaptive search"); a
batch whose Hansen SPA rejects at 10% and whose PBO is <= 0.30 (steps 8-9: "otherwise nothing from the batch
advances"); a circular-shift null p <= 5% (whole-week shifts in calendar time, excess R over the drift baseline); and a plateau (step 6) over its grid neighbours. The neighbours are searched
specs: each is ledgered before it is evaluated and counts in K. The DSR at the ledger's cumulative K_search (every
night) is reported per survivor against the 0.95 confirm bar (step 10, "informational at this stage; binding at
confirm"; ADR 0058 amendment 2026-10-30: advancing to confirm requires it), with the cumulative survivor count from the
ledger and one line per judged candidate that failed, naming the failed check. Novelty (step 12) is not labelled here: a G2 spec carries no family, and the novelty pass over survivors is
read from the report by the research director.

Selection reads only the explore window: the bars come through `windows.ExploreFrame` (2018+ raises) and only the
2010-2017 bar files are opened. "An explore survivor is a recorded fact, never evidence of edge." This module creates
no arm, writes no config and imports nothing that trades (phase 4 builds the interpreter)."""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.config import Settings
from goldbot.research.autodisc import stats as S
from goldbot.research.autodisc.gen_gp import (
    GENERATOR,
    GPCandidate,
    GPConfig,
    Resolver,
    evaluate_logged,
    gp_fitness,
    gp_null,
    neighbours,
    run_gp,
)
from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.autodisc.nulls import exit_index
from goldbot.research.autodisc.outcomes import OutcomeTensor
from goldbot.research.autodisc.stats import (
    CS_ALPHA,
    DSR_CONFIRM_BAR,
    PBOResult,
    Plateau,
    RealityCheck,
    batch_dsr,
    batch_passes,
    batch_pbo,
    circular_shift_null,
    excess_daily,
    plateau_check,
    reality_check,
)

REPORT_DIR = "autodisc"
LEDGER_FILE = {"real": "discovery_real.db", "synthetic": "discovery_synthetic.db"}
REPO_RAW = Path(__file__).resolve().parents[3] / "raw"
_CONFIRM_YEAR = re.compile(r"(?<![0-9A-Za-z])20(?:1[89]|[2-9]\d)(?![0-9A-Za-z])")
_CONFIRM_MONTH = re.compile(r"20(?:1[89]|[2-9]\d)-\d\d")
_BUILT_DATE = re.compile(r" built \d{4}-\d\d-\d\d")      # a cost table's build date (as scripts/autodisc.py strips it)


class Backends(FrozenRecord):
    """What the search reads: the explore-window outcome tensor, its mask provider and the families on its timeframe."""
    tensor: OutcomeTensor
    provider: Any
    families: tuple[str, ...]
    cost_line: str = ""
    mode: str = "real"


class Survivor(FrozenRecord):
    spec_hash: str
    spec_json: str
    fitness: float
    mean_r: float | None
    excess_r: float | None
    n_events: int
    shift_p: float
    plateau: Plateau
    dsr: float                                # at the cumulative K_search
    meets_confirm_dsr: bool                   # dsr >= DSR_CONFIRM_BAR: what advancing to confirm requires


class Rejected(FrozenRecord):
    """A judged candidate that is not a survivor, with every check it failed (quant review F5)."""
    spec_hash: str
    fitness: float
    shift_p: float
    plateau_share: float
    plateau_ratio: float | None
    failed: list[str]


class NightlyResult(FrozenRecord):
    generator: str
    timeframe: str
    k_search: int                             # cumulative over every night in the ledger: the K the DSR is read at
    k_search_tonight: int                     # new distinct specs this night added
    survivors_cumulative: int                 # distinct explore survivors over every night (ledger)
    k_null: int
    null_reps: int
    evaluated: int
    passed_floors: int
    null_threshold: float
    real_best: float
    real_best_null_percentile: float
    candidates: int                           # beat the null and the evaluator's own explore checks
    judged: int                               # candidates whose neighbours and shift null were run (max_candidates)
    neighbours_evaluated: int
    rc: RealityCheck | None
    pbo: PBOResult | None
    batch_ok: bool
    batch_why: list[str]
    survivors: list[Survivor]
    rejected: list[Rejected]
    head: tuple[int, str]
    cost_line: str = ""


def _day_ids(t: OutcomeTensor) -> tuple[np.ndarray, int]:
    codes, _ = pd.factorize(t.ts_utc.normalize())
    return codes.astype(np.int64), int(codes.max()) + 1


def _failed(p: float, pl: Plateau, batch_ok: bool) -> list[str]:
    why = [] if batch_ok else ["batch (SPA / PBO)"]
    if p > CS_ALPHA:
        why.append(f"shift p {p:.3f} > {CS_ALPHA:.2f}")
    if pl.share_positive < S.PLATEAU_MIN_SHARE:
        why.append(f"plateau share {pl.share_positive:.2f} < {S.PLATEAU_MIN_SHARE:.2f}")
    if pl.ratio is None or pl.ratio < S.PLATEAU_MIN_RATIO:
        why.append(f"plateau ratio {_f(pl.ratio)} < {S.PLATEAU_MIN_RATIO:.2f}")
    return why or ["plateau"]                           # e.g. no grid neighbours: nothing shows a plateau


def search_and_judge(t: OutcomeTensor, provider: Any, ledger: SearchLedger, cfg: GPConfig, *, reps: int,
                     max_candidates: int = 20, cost_line: str = "", night: str = "adhoc") -> NightlyResult:
    """The module docstring's funnel on one tensor. Every evaluated spec (search, null, neighbours) is ledgered first.
    The night's survivors are recorded in the ledger under `night`, so the report states the cumulative count."""
    k_before = ledger.k_search()
    res = Resolver(provider)
    real = run_gp(t, provider, cfg, ledger, resolver=res)
    null = gp_null(t, provider, cfg, ledger, reps=reps, resolver=res)
    thr = max(null.threshold(), 0.0)
    floors = [c for c in real.evaluated if np.isfinite(gp_fitness(c.evaluation))]
    day_id, n_days = _day_ids(t)
    cols: list[np.ndarray] = []
    for c in floors:
        mask, side = res.entries(c.spec)
        ev = t.select(mask, side, exit_index(t, c.spec))
        cols.append(excess_daily(t, ev.r(), ev.idx, ev.side_i, exit_index(t, c.spec), day_id, n_days))
    d = np.column_stack(cols) if cols else np.zeros((n_days, 0))
    rc = reality_check(d, seed=cfg.seed) if d.shape[1] >= 1 and n_days >= 20 else None
    pbo = batch_pbo(d, [gp_fitness(c.evaluation) for c in floors])
    batch_ok, why = batch_passes(rc, pbo)
    col_of = {c.spec_hash: j for j, c in enumerate(floors)}
    cands = sorted((c for c in floors if gp_fitness(c.evaluation) > thr and c.evaluation.explore_checks_passed),
                   key=lambda c: -gp_fitness(c.evaluation))
    judged: list[tuple[GPCandidate, float, Plateau]] = []
    n_nb = 0
    hour = t.ts_utc.hour.to_numpy()
    for c in cands[:max_candidates]:
        mask, side = res.entries(c.spec)
        ei = exit_index(t, c.spec)
        r, base = t.r(ei), np.nan_to_num(t.drift_table(ei)[:, hour], nan=0.0)        # base: (side, bar)
        shift = circular_shift_null(np.where(mask, side, 0).astype(np.int8), r[:, 0], r[:, 1], t.ts_utc,
                                    base_long=base[0], base_short=base[1], seed=cfg.seed)
        nbs = [evaluate_logged(t, res, nb, ledger, parents=(c.spec_hash,), generation=-1, min_events=cfg.min_events)
               for nb in neighbours(c.spec)]
        n_nb += len(nbs)
        fit = [f if np.isfinite(f := gp_fitness(nb.evaluation)) else None for nb in nbs]
        judged.append((c, shift.p_value, plateau_check(gp_fitness(c.evaluation), fit)))
    k_cum = ledger.k_search()                 # every night's specs: the multiplicity the confirm DSR pays for
    dsr = batch_dsr(d, [col_of[c.spec_hash] for c, _, _ in judged], k_cum) if judged else {}
    survivors: list[Survivor] = []
    rejected: list[Rejected] = []
    for c, p, pl in judged:
        f = gp_fitness(c.evaluation)
        if batch_ok and p <= CS_ALPHA and pl.passed:
            v = dsr[col_of[c.spec_hash]]
            survivors.append(Survivor(spec_hash=c.spec_hash, spec_json=c.spec.model_dump_json(), fitness=f,
                                      mean_r=c.evaluation.mean_r, excess_r=c.evaluation.excess_r,
                                      n_events=c.evaluation.n_events, shift_p=p, plateau=pl, dsr=v,
                                      meets_confirm_dsr=bool(v >= DSR_CONFIRM_BAR)))
        else:
            rejected.append(Rejected(spec_hash=c.spec_hash, fitness=f, shift_p=p, plateau_share=pl.share_positive,
                                     plateau_ratio=pl.ratio, failed=_failed(p, pl, batch_ok)))
    if survivors:
        ledger.record_survivors(night, [(s.spec_hash, s.dsr) for s in survivors], k_search=k_cum)
    return NightlyResult(generator=f"{GENERATOR} gp", timeframe=cfg.timeframe, k_search=k_cum,
                         k_search_tonight=k_cum - k_before, survivors_cumulative=ledger.n_survivors(),
                         k_null=ledger.k_null(), null_reps=reps, evaluated=len(real.evaluated), passed_floors=len(floors),
                         null_threshold=thr, real_best=real.best, real_best_null_percentile=null.percentile_of(real.best),
                         candidates=len(cands), judged=len(judged), neighbours_evaluated=n_nb, rc=rc, pbo=pbo,
                         batch_ok=batch_ok, batch_why=why, survivors=survivors, rejected=rejected, head=ledger.head(),
                         cost_line=cost_line)


def _n(x: int) -> str:
    """Counts with thousands separators: a bare four-digit count (2018-2099) would read as a confirm-window year."""
    return f"{x:,}"


def _f(x: float | None) -> str:
    return "n/a" if x is None or not np.isfinite(x) else f"{x:,.3f}"


def render(r: NightlyResult) -> str:
    rc = f"RC p {_f(r.rc.rc_p)}, SPA p {_f(r.rc.spa_p)} ({_n(r.rc.n_specs)} specs, {_n(r.rc.n_days)} days)" if r.rc \
        else "RC/SPA not computed"
    pbo = f"PBO {_f(r.pbo.pbo)} over the top {_n(r.pbo.n_configs)}" if r.pbo else "PBO not computed"
    lines = [
        f"autodisc nightly | generator {r.generator} | {r.timeframe} | explore window 2010-2017 only",
        f"K_search {_n(r.k_search)} cumulative over all nights ({_n(r.k_search_tonight)} new tonight) | K_null "
        f"{_n(r.k_null)} (null reruns of the whole search {_n(r.null_reps)})",
        *([f"costs: {_BUILT_DATE.sub('', r.cost_line)}"] if r.cost_line else []),
        f"evaluated {_n(r.evaluated)} | passed floors {_n(r.passed_floors)} | neighbours evaluated "
        f"{_n(r.neighbours_evaluated)}",
        f"null bar (95th pct of best-of-search excess-R fitness, floored at 0) {_f(r.null_threshold)} | real best "
        f"{_f(r.real_best)} at null percentile {_f(r.real_best_null_percentile)}",
        f"batch: {rc}; {pbo}; {'advances' if r.batch_ok else 'nothing advances: ' + '; '.join(r.batch_why)}",
        f"candidates (beat the null and the explore checks) {_n(r.candidates)}, judged {_n(r.judged)}",
        f"explore survivors tonight {_n(len(r.survivors))}; explore survivors, cumulative over all nights "
        f"{_n(r.survivors_cumulative)}: a recorded fact, not evidence of edge",
        f"rule (ADR 0058 amendment, pre-registered): advancing to confirm requires DSR >= {DSR_CONFIRM_BAR:.2f} at the "
        f"cumulative K_search; the DSR uses raw K_search (ONC K_eff deferred: conservative) and tonight's batch for "
        f"the variance of the Sharpe ratios",
    ]
    for s in r.survivors:
        lines.append(f"  {s.spec_hash[:12]}  fitness {_f(s.fitness)}  mean R {_f(s.mean_r)}  excess R {_f(s.excess_r)}  "
                     f"events {_n(s.n_events)}  shift p {_f(s.shift_p)}  plateau {_f(s.plateau.ratio)}")
        lines.append(f"    DSR at cumulative K_search {_n(r.k_search)}: {_f(s.dsr)} "
                     f"({'meets' if s.meets_confirm_dsr else 'below'} the {DSR_CONFIRM_BAR:.2f} confirm bar)")
        lines.append(f"    {s.spec_json}")
    lines.append(f"judged candidates that failed {_n(len(r.rejected))}")
    for x in r.rejected:
        lines.append(f"  {x.spec_hash[:12]}  fitness {_f(x.fitness)}  failed: {'; '.join(x.failed)}")
    lines.append(f"ledger head seq {_n(r.head[0])} digest {r.head[1]}")
    text = "\n".join(lines) + "\n"
    assert_no_confirm_numbers(text)
    return text


def assert_no_confirm_numbers(text: str) -> None:
    """The report is explore-window only: refuse any 2018+ year or date in it."""
    m = _CONFIRM_YEAR.search(text) or _CONFIRM_MONTH.search(text)
    if m:
        raise ValueError(f"report would contain a confirm/holdout-window date: {m.group(0)!r}")


def raw_dir(configured: str | None) -> Path:
    return Path(configured or os.environ.get("GOLDBOT_RAW_BARS") or REPO_RAW)


def real_backends(settings: Settings, timeframe: str, raw: Path | None = None, cost_table: str | None = None
                  ) -> Backends | None:
    """Explore-window 1m bars (only the 2010-2017 files are opened) -> provider, outcome tensor and families; None
    when no such bars are present (the brain, a fresh clone)."""
    from goldbot.data.release import read_bar_files
    from goldbot.execution.costs import load_research_cost_table, research_costs, settings_extra_cost_usd
    from goldbot.features.technical import atr
    from goldbot.research.autodisc.gen_combine import MAX_BARS, existing_family_names
    from goldbot.research.autodisc.outcomes import build_tensor, default_menu
    from goldbot.research.autodisc.primitives import PrimitiveProvider
    from goldbot.research.autodisc.windows import ExploreFrame, truncate_to_explore

    a = settings.research.autodisc
    src = raw if raw is not None else raw_dir(a.raw_dir)
    if not src.is_dir():
        return None
    try:
        b1 = truncate_to_explore(read_bar_files(src, 2010, 2017), "ts_utc")
    except FileNotFoundError:                                   # the dir holds no 2010-2017 file: skip, as for no dir
        return None
    if len(b1) == 0:
        return None
    prov = PrimitiveProvider(ExploreFrame.from_frame(b1, "ts_utc"))
    bars = prov.bars(timeframe)
    ct = cost_table if cost_table is not None else a.cost_table
    extra, swap, source = research_costs(settings, load_research_cost_table(ct) if ct else None)
    extra = max(extra, settings_extra_cost_usd(settings))     # conservative: never below the settings prior (ADR 0045)
    t = build_tensor(ExploreFrame.from_frame(bars, "ts_utc"), atr(prov.mid_bars(timeframe), 14),
                     default_menu(MAX_BARS[timeframe]), extra_cost_usd=extra, swap=swap)
    fams = tuple(f for f in existing_family_names() if f in prov.families(timeframe))
    return Backends(tensor=t, provider=prov, families=fams, cost_line=source, mode="real")


def _write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory and rename it over `path`: a crash or a failed render leaves the
    previous report whole, never a truncated one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        tmp.write_text(text)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def night_seed(base: int, slot: pd.Timestamp) -> int:
    """Each night's search seed: the base plus the slot's day number (deterministic, new ground every night)."""
    return int(base) + int(pd.Timestamp(slot).normalize().value // 86_400_000_000_000)


def run_nightly_job(settings: Settings, state_dir: Path, slot: pd.Timestamp, *,
                    backends: Callable[[Settings, str], Backends | None] | None = None,
                    min_events: int | None = None) -> dict[str, Any]:
    """The scheduler job: skip unless enabled and bars are present; otherwise search, judge, write
    state/autodisc/survivors-<day>.md and return the summary (its `health` line is what the job reports)."""
    a = settings.research.autodisc
    if not a.nightly_enabled:
        return {"skipped": "research.autodisc.nightly_enabled is false: the discovery search runs on the owner's Mac only "
                           "(ADR 0058 section 5)"}
    b = (backends or real_backends)(settings, a.timeframe)
    if b is None:
        return {"skipped": "no 2010-2017 1m bars in the raw dir (research.autodisc.raw_dir or GOLDBOT_RAW_BARS)"}
    ledger = SearchLedger(Path(state_dir) / LEDGER_FILE[b.mode], mode=b.mode)
    cfg = GPConfig(timeframe=a.timeframe, population=a.population, generations=a.generations,
                   seed=night_seed(a.seed, slot), min_events=min_events, families=b.families)
    res = search_and_judge(b.tensor, b.provider, ledger, cfg, reps=a.null_reps, max_candidates=a.max_candidates,
                           cost_line=b.cost_line, night=f"{pd.Timestamp(slot):%Y-%m-%d}")
    rel = f"{REPORT_DIR}/survivors-{pd.Timestamp(slot):%Y-%m-%d}.md"
    out = Path(state_dir) / rel
    _write_atomic(out, render(res))
    spa = "n/a" if res.rc is None else f"{res.rc.spa_p:.3f}"
    pbo = "n/a" if res.pbo is None else f"{res.pbo.pbo:.2f}"
    return {"report": rel, "k_search": res.k_search, "k_null": res.k_null, "evaluated": res.evaluated,
            "candidates": res.candidates, "survivors": len(res.survivors), "batch_ok": res.batch_ok,
            "survivors_cumulative": res.survivors_cumulative,
            "health": f"autodisc {res.timeframe}: {res.evaluated:,} specs, K_search {res.k_search:,}, SPA p {spa}, "
                      f"PBO {pbo}, survivors {len(res.survivors)} (explore only, not evidence)"}
