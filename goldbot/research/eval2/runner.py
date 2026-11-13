"""EVAL-2 runner integrity: the only entry points a batch runner or forward reader uses to read real cells
(BACKLOG 149 and 150).

BACKLOG 150: "`scripts/eval2_selftest.assert_harness_sane(harness_hash, forward=False)` (Lane 4) refuses unless the
latest full self-test report for the harness hash has S0-S4 and S6 passed (S5 too for forward reads) ... the Lane 3
L2/L3 runner and the Lane 5A forward reader call it with the current `harness_hash()` before reading any real cell
(`forward=True` for forward reads)." `harness_gate` does that for `record_batch_p`, `run_l3_read`, `run_l2_read` and
`run_forward_read`, before any unit is touched. It fails closed: a self-test module that does not load, a missing,
BLOCKED, failed or other-hash report all raise `ReadRefused`, and nothing is computed or written.

BACKLOG 149 (Lane 3 quant review, round 2), "the Lane 3 statistics take inputs whose integrity only the Lane 5 batch
runner can check":
(a) "the `registry_row` passed to `screen.futility_terms` is looked up in the registry before the screen reads it: an
    id with no pre-registered row (same config hash, written before the read) raises, so 'counted in K' is checked
    against the registry, not only that the id is non-empty" (`checked_futility_terms`; the id is
    `registry_row_id(row)`, config hash @ timestamp, the pair `registry.refers_to` names a row by);
(b) "the runner cuts the L3 units at decision + the registered maximum hold before 2025-10-01 (an instance whose
    registered hold would reach the holdout is not read), on top of `l3_read`'s decision-instant guard"
    (`cut_at_holdout`, applied to the read units, the batch p units and the corroboration). A carried cell with no
    registered time exit has no maximum hold: it is refused, never cut to nothing (an empty read would close the
    family as NOT CONFIRMED on no data);
(c) "the runner writes every family's `confirm.family_cv_p` for batch j (m_j of them, `len(batch_p)` equal to the
    card's m_j) to `state/catalogue_reads.jsonl` before any `l3_read` of that batch, and `l3_read` reads them back from
    there" (`record_batch_p`, then `run_l3_read`, which takes `batch_p` only from the ledger; `confirm.l3_read`'s frozen
    signature is unchanged). A batch's row is written once: a second write for the same window and j is refused. The
    row carries `kind` and `read_window`, never a `window` key, so `catalogue.run.read_batch_index` and `_claim` do not
    count it as a read.

No function here writes to the trial registry; K is unchanged.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal

import pandas as pd

from goldbot.research import screen
from goldbot.research.catalogue import run as catalogue_run
from goldbot.research.catalogue.data import DAY, TF_NS
from goldbot.research.eval2 import confirm as C
from goldbot.research.eval2 import forward as FW
from goldbot.research.eval2 import hier as H
from goldbot.research.registry import PREREGISTERED, TrialRegistry, config_hash

ROOT: Final = Path(__file__).resolve().parents[3]
SELFTEST_PATH: Final = ROOT / "scripts" / "eval2_selftest.py"
SELFTEST_MODULE: Final = "eval2_selftest"
BATCH_P_KIND: Final = "l3_batch_p"
HOLDOUT_NS: Final = C.HOLDOUT_FROM_DAY * DAY          # 2025-10-01 00:00 UTC


class ReadRefused(RuntimeError):
    """An EVAL-2 read the runner will not start: the harness, the registry or the reads ledger does not back it."""


@dataclass(frozen=True, slots=True)
class FamilyCard:
    """The frozen card's fields one family's L3 read needs. `cells`: carried cell -> (tf, registered time exit in
    bars, or None for no time exit), the shape `confirm.card_holds_multi_day` takes."""
    family: str
    carried: tuple[str, ...]
    weights: Mapping[str, float]
    cells: Mapping[str, tuple[str, int | None]]
    mean_block: float
    seed_key: str
    confirm_seen: frozenset[str] = field(default_factory=frozenset)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(ts: str) -> datetime:
    t = datetime.fromisoformat(ts)
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ 150: the harness gate
def selftest() -> ModuleType:
    """scripts/eval2_selftest.py as a module (the one already imported, if any)."""
    mod = sys.modules.get(SELFTEST_MODULE)
    if mod is not None:
        return mod
    spec = importlib.util.spec_from_file_location(SELFTEST_MODULE, SELFTEST_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SELFTEST_PATH}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[SELFTEST_MODULE] = mod
    try:
        spec.loader.exec_module(mod)
    except BaseException:
        sys.modules.pop(SELFTEST_MODULE, None)
        raise
    return mod


def harness_gate(*, forward: bool) -> str:
    """`assert_harness_sane(harness_hash(), forward=forward)` on the current tree; the harness hash on success,
    `ReadRefused` on any failure, the self-test module failing to load included (fail closed)."""
    try:
        st = selftest()
        hh = str(st.harness_hash())
        st.assert_harness_sane(hh, forward=forward)
    except Exception as e:
        raise ReadRefused(f"EVAL-2 read refused: the harness self-test check failed ({e}); run "
                          f"`python scripts/eval2_selftest.py` (full, not --fast) on this tree first") from e
    return hh


# ------------------------------------------------------------------ 149 (a): the futility registry row
def registry_row_id(row: Mapping[str, Any]) -> str:
    """The id a futility read names its pre-registration by: config hash @ timestamp (`registry.refers_to`)."""
    return f"{row.get('config_hash')}@{row.get('ts')}"


def checked_futility_terms(registry: TrialRegistry, *, family: str, config: dict[str, Any], registry_row: str,
                           statistic: Literal["gross", "delta"], mean_r: float, se_r: float,
                           read_ts: datetime | None = None) -> dict[str, Any]:
    """`screen.futility_terms` once `registry_row` is a pre-registration of this family and config (same config
    hash) written strictly before the read (`read_ts`, default now); `ReadRefused` otherwise."""
    read_ts = read_ts or _now()
    h = config_hash(config)
    row = next((r for r in registry._rows()
                if r.get("status") == PREREGISTERED and registry_row and registry_row_id(r) == registry_row), None)
    if row is None:
        raise ReadRefused(f"futility read refused: {registry_row!r} names no pre-registered row in {registry.path}")
    if row.get("family") != family or row.get("config_hash") != h:
        raise ReadRefused(f"futility read refused: pre-registration {registry_row} is for {row.get('family')} config "
                          f"{row.get('config_hash')}, not {family} config {h}")
    if _utc(str(row["ts"])) >= read_ts:
        raise ReadRefused(f"futility read refused: pre-registration {registry_row} was not written before the read "
                          f"({read_ts.isoformat()})")
    return screen.futility_terms(statistic=statistic, mean_r=mean_r, se_r=se_r, registry_row=registry_row)


# ------------------------------------------------------------------ 149 (b): the holdout cut
def cut_at_holdout(units: pd.DataFrame, card: FamilyCard) -> pd.DataFrame:
    """The card family's carried-cell units whose decision + registered maximum hold (time exit x bar length) ends
    before 2025-10-01. A carried cell without a registered time exit is refused (module docstring)."""
    cells = [str(c) for c in card.carried]
    lim: dict[str, int] = {}
    for c in cells:
        tf, bars = card.cells.get(c, ("", None))
        if bars is None or tf not in TF_NS:
            raise ReadRefused(f"{card.family} cell {c} has no registered maximum hold (time exit): the runner cannot "
                              f"show its instances end before the holdout (2025-10-01); not read")
        lim[c] = HOLDOUT_NS - int(bars) * TF_NS[tf]
    sel = (units["family"].astype(str) == card.family).to_numpy() & units["cell_id"].astype(str).isin(cells).to_numpy()
    u = units[sel]
    keep = u["decision_ns"].to_numpy("int64") < u["cell_id"].astype(str).map(lim).to_numpy("int64")
    return u[keep].reset_index(drop=True)


# ------------------------------------------------------------------ 149 (c): the batch p ledger
def default_ledger() -> Path:
    """state/catalogue_reads.jsonl next to the research registry (`catalogue.run.READS_LEDGER`)."""
    return catalogue_run.default_registry().with_name(catalogue_run.READS_LEDGER)


def _batch_rows(ledger: Path, window: str, j: int) -> list[dict[str, Any]]:
    if not ledger.exists():
        return []
    rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
    return [r for r in rows if r.get("kind") == BATCH_P_KIND and r.get("read_window") == window
            and int(r.get("batch_j", -1)) == j]


def read_batch_p(ledger: Path, *, window: str, j: int) -> dict[str, float] | None:
    """Family -> p of batch j on `window` as recorded, in batch order; None when not recorded. More than one row, or
    a row whose p count differs from its m_j, is refused."""
    rows = _batch_rows(ledger, window, j)
    if not rows:
        return None
    if len(rows) > 1:
        raise ReadRefused(f"{len(rows)} batch p rows for {window} batch {j} in {ledger}: the batch is ambiguous")
    pairs = rows[0]["p"]
    if len(pairs) != int(rows[0]["m_j"]):
        raise ReadRefused(f"the batch p row for {window} batch {j} holds {len(pairs)} p, not its m_j {rows[0]['m_j']}")
    return {str(f): float(p) for f, p in pairs}


def record_batch_p(units: pd.DataFrame, cards: Sequence[FamilyCard], *, batch: C.BatchPRDS, j: int, card_m_j: int,
                   ledger: Path | None = None) -> dict[str, float]:
    """Every family of batch j: its `confirm.family_cv_p` on its holdout-cut units, written as one row to the reads
    ledger before any `l3_read` of the batch. `cards` must be the card's m_j families; written once per window and j."""
    hh = harness_gate(forward=False)
    led = ledger or default_ledger()
    fams = [c.family for c in cards]
    if len(cards) != card_m_j:
        raise ReadRefused(f"batch {j} holds {len(cards)} families, the card's m_j is {card_m_j}: not read")
    if len(set(fams)) != len(fams):
        raise ReadRefused(f"batch {j} names a family twice: {fams}")
    if _batch_rows(led, batch.window, j):
        raise ReadRefused(f"the p of {batch.window} batch {j} are already recorded in {led}: never rewritten")
    alpha_j = batch.level(j, card_m_j)
    p: dict[str, float] = {}
    for card in cards:
        p[card.family] = float(C.family_cv_p(cut_at_holdout(units, card), family=card.family,
                                             carried=list(card.carried), weights=card.weights, m_j=card_m_j,
                                             alpha_j=alpha_j, mean_block=card.mean_block, seed_key=card.seed_key))
    led.parent.mkdir(parents=True, exist_ok=True)
    with open(led, "a") as fh:
        fh.write(json.dumps({"kind": BATCH_P_KIND, "read_window": batch.window, "batch_j": j, "m_j": card_m_j,
                             "alpha_j": alpha_j, "p": [[f, v] for f, v in p.items()], "harness_hash": hh,
                             "ts": _now().isoformat()}) + "\n")
    return p


def run_l3_read(units: pd.DataFrame, card: FamilyCard, *, batch: C.BatchPRDS, j: int,
                corroboration: pd.DataFrame | None, e9: bool | None, ledger: Path | None = None) -> C.L3Result:
    """The family's L3 read: harness gate, `batch_p` from the ledger (refused when the batch was not recorded or this
    family is not in it), the holdout cut on the units and the corroboration, then `confirm.l3_read`."""
    harness_gate(forward=False)
    led = ledger or default_ledger()
    p = read_batch_p(led, window=batch.window, j=j)
    if p is None:
        raise ReadRefused(f"no batch p recorded for {batch.window} batch {j} in {led}: record_batch_p first")
    if card.family not in p:
        raise ReadRefused(f"{card.family} is not in the recorded {batch.window} batch {j} ({list(p)}): not read")
    corr = cut_at_holdout(corroboration, card) if corroboration is not None else None
    return C.l3_read(cut_at_holdout(units, card), family=card.family, carried=list(card.carried),
                     weights=card.weights, batch=batch, j=j, batch_p=list(p.values()), corroboration=corr,
                     mean_block=card.mean_block, seed_key=card.seed_key, confirm_seen=card.confirm_seen,
                     holds_multi_day=C.card_holds_multi_day({c: card.cells[c] for c in card.carried}), e9=e9)


# ------------------------------------------------------------------ 150: L2 and forward reads
def run_l2_read(cells: pd.DataFrame, *, family: str, spec: H.HierSpec,
                deciding_cost_r: Mapping[str, float]) -> H.L2Decision:
    """The family's L2 read on a sane harness: the leave-one-family-out prior, its cells' posteriors, the decision."""
    harness_gate(forward=False)
    prior = H.fit_prior(cells, leave_out_family=family, spec=spec)
    own = cells[(cells["family"].astype(str) == family).to_numpy()].reset_index(drop=True)
    return H.l2_decide(H.posteriors(own, prior, spec=spec, deciding_cost_r=deciding_cost_r), family=family, spec=spec)


def run_forward_read(units: pd.DataFrame, *, family: str, l3_shrunk_r: float, s5_passed: bool,
                     exit_prior: Mapping[str, float] | None = None,
                     exit_prior_hash: str | None = None) -> FW.ForwardState:
    """The family's L4 forward read on a harness whose S0-S6 (S5 included) passed."""
    harness_gate(forward=True)
    return FW.forward_state(units, family=family, l3_shrunk_r=l3_shrunk_r, s5_passed=s5_passed,
                            exit_prior=exit_prior, exit_prior_hash=exit_prior_hash)
