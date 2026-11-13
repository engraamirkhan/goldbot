"""EVAL-2 runner integrity: the only entry points a batch runner or forward reader uses to read real cells
(BACKLOG 149 and 150, with the quant review of f18881ab).

BACKLOG 150: "`scripts/eval2_selftest.assert_harness_sane(harness_hash, forward=False)` (Lane 4) refuses unless the
latest full self-test report for the harness hash has S0-S4 and S6 passed (S5 too for forward reads) ... the Lane 3
L2/L3 runner and the Lane 5A forward reader call it with the current `harness_hash()` before reading any real cell
(`forward=True` for forward reads)." `harness_gate` does that for `record_batch_p`, `run_l3_read`, `run_l2_read` and
`run_forward_read`, before any unit is touched. It fails closed: a self-test module that does not load, a missing,
BLOCKED, failed or other-hash report all raise `ReadRefused`, and nothing is computed or written. The self-test module
is executed from the current bytes of scripts/eval2_selftest.py (cached by their sha256), never taken from whatever
`sys.modules["eval2_selftest"]` holds. `tests/test_eval2_runner.py` fails if anything else in goldbot/ or scripts/
calls `confirm.l3_read`, `confirm.family_cv_p` or `forward.forward_state`.

BACKLOG 149 (Lane 3 quant review, round 2), "the Lane 3 statistics take inputs whose integrity only the Lane 5 batch
runner can check":
(a) "the `registry_row` passed to `screen.futility_terms` is looked up in the registry before the screen reads it: an
    id with no pre-registered row (same config hash, written before the read) raises, so 'counted in K' is checked
    against the registry, not only that the id is non-empty" (`checked_futility_terms`; the id is
    `registry_row_id(row)`, config hash @ timestamp, the pair `registry.refers_to` names a row by). A withdrawn
    pre-registration, or an ad-hoc one past `registry.STALE_PREREG_DAYS` (it already counts as a trial of its own), is
    refused too (quant review MED).
(b) "the runner cuts the L3 units at decision + the registered maximum hold before 2025-10-01 (an instance whose
    registered hold would reach the holdout is not read), on top of `l3_read`'s decision-instant guard". Quant review
    HIGH: the bound is counted in the sim's bars, not calendar time, and includes decision -> fill (up to `valid` bars,
    sim.py `_fill`) and the kind-3 extra bar (`start = f + 1`): every unit carries `bound_end_ns` = the open time of
    bar i + valid + hold + 1 on the cell's bars (`unit_bound_end_ns`; past the last bar it is unknown and counts as
    reaching the holdout), and `cut_at_holdout` keeps only bound_end_ns < 2025-10-01. A unit without it is refused.
    The bound is computed here from the sim's own Events and Bars rather than emitted by catalogue/sim.py, because any
    edit under goldbot/research/catalogue fails `catalogue.run.code_matches` and voids the frozen catalogue screen.
    BACKLOG 207 (quant ruling): every carried cell registers its sim `ev.hold` as its time exit in the card before L2;
    a carried cell without one is refused (cutting it to nothing would close the family as NOT CONFIRMED on no data).
(c) "the runner writes every family's `confirm.family_cv_p` for batch j (m_j of them, `len(batch_p)` equal to the
    card's m_j) to `state/catalogue_reads.jsonl` before any `l3_read` of that batch, and `l3_read` reads them back from
    there" (`record_batch_p`, then `run_l3_read`, which takes `batch_p` only from the ledger; `confirm.l3_read`'s frozen
    signature is unchanged). Quant review MED: the batch membership (the families, so m_j) comes from the frozen batch
    card (`freeze_batch_card`, checked by its sha256 on every load), never from the caller; the row records the card
    hash and each family's units hash, and `run_l3_read` refuses units other than those its p was computed on. A
    batch's row is written once. The row carries `kind` and `read_window`, never a `window` key, so
    `catalogue.run.read_batch_index` and `_claim` do not count it as a read.

No function here writes to the trial registry; K is unchanged.
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Final, Literal

import numpy as np
import pandas as pd

from goldbot.research import screen
from goldbot.research.catalogue import run as catalogue_run
from goldbot.research.catalogue.core import Events
from goldbot.research.catalogue.data import DAY, TF_NS, Bars
from goldbot.research.eval2 import confirm as C
from goldbot.research.eval2 import forward as FW
from goldbot.research.eval2 import hier as H
from goldbot.research.registry import (
    PREREGISTERED,
    STALE_PREREG_DAYS,
    TrialRegistry,
    config_hash,
    exempting_withdrawal,
)

ROOT: Final = Path(__file__).resolve().parents[3]
SELFTEST_PATH: Final = ROOT / "scripts" / "eval2_selftest.py"
BATCH_P_KIND: Final = "l3_batch_p"
CARD_KEY: Final = "l3_batch"
HOLDOUT_NS: Final = C.HOLDOUT_FROM_DAY * DAY          # 2025-10-01 00:00 UTC
UNKNOWN_END_NS: Final = int(np.iinfo(np.int64).max)   # a bound past the last bar: never before the holdout

_SELFTEST: dict[str, ModuleType] = {}


class ReadRefused(RuntimeError):
    """An EVAL-2 read the runner will not start: the harness, the registry, the card or the reads ledger does not
    back it."""


@dataclass(frozen=True, slots=True)
class FamilyCard:
    """One family of a frozen batch card. `cells`: carried cell -> (tf, registered time exit in bars = the sim's
    ev.hold, or None), the shape `confirm.card_holds_multi_day` takes."""
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


def _sha(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ------------------------------------------------------------------ 150: the harness gate
def selftest() -> ModuleType:
    """scripts/eval2_selftest.py executed from its current bytes, cached by their sha256 under a private module name.
    A module someone else registered as `eval2_selftest` is never used (quant review LOW)."""
    src = SELFTEST_PATH.read_bytes()
    h = hashlib.sha256(src).hexdigest()
    mod = _SELFTEST.get(h)
    if mod is not None:
        return mod
    name = f"_eval2_selftest_gate_{h[:16]}"
    mod = ModuleType(name)
    mod.__file__ = str(SELFTEST_PATH)
    sys.modules[name] = mod                       # dataclasses resolve their module while the body runs
    try:
        exec(compile(src, str(SELFTEST_PATH), "exec"), mod.__dict__)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    _SELFTEST[h] = mod
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
    """`screen.futility_terms` once `registry_row` is a live pre-registration of this family and config (same config
    hash), written strictly before the read (`read_ts`, default now), not withdrawn and, if ad hoc, not past
    STALE_PREREG_DAYS; `ReadRefused` otherwise."""
    from goldbot.research.director import is_withdrawn  # director imports the registry module
    read_ts = read_ts or _now()
    h = config_hash(config)
    rows = registry._rows()
    row = next((r for r in rows
                if r.get("status") == PREREGISTERED and registry_row and registry_row_id(r) == registry_row), None)
    if row is None:
        raise ReadRefused(f"futility read refused: {registry_row!r} names no pre-registered row in {registry.path}")
    if row.get("family") != family or row.get("config_hash") != h:
        raise ReadRefused(f"futility read refused: pre-registration {registry_row} is for {row.get('family')} config "
                          f"{row.get('config_hash')}, not {family} config {h}")
    written = _utc(str(row["ts"]))
    if written >= read_ts:
        raise ReadRefused(f"futility read refused: pre-registration {registry_row} was not written before the read "
                          f"({read_ts.isoformat()})")
    if is_withdrawn(rows, row) or exempting_withdrawal(rows, row) is not None:
        raise ReadRefused(f"futility read refused: pre-registration {registry_row} is withdrawn")
    if row.get("queue") is False and read_ts - written > timedelta(days=STALE_PREREG_DAYS):
        raise ReadRefused(f"futility read refused: the ad-hoc pre-registration {registry_row} expired after "
                          f"{STALE_PREREG_DAYS} days (it counts as a trial already); pre-register again")
    return screen.futility_terms(statistic=statistic, mean_r=mean_r, se_r=se_r, registry_row=registry_row)


# ------------------------------------------------------------------ 149 (b): the holdout cut
def unit_bound_end_ns(b: Bars, ev: Events, decision_bar: np.ndarray) -> np.ndarray:
    """Per unit (its decision bar index i on `b`): the open time of bar i + valid + hold + 1, with valid (>= 1) and
    hold the largest of the cell's events at bar i. The sim exits at the latest at the close of bar i + valid + hold
    (a stop/limit fill by bar i + valid, a kind-3 fill walking from i + 1), so no exit is at or after this instant.
    Past the last bar: UNKNOWN_END_NS (never before the holdout). A unit with no event at its bar raises."""
    n = len(b)
    out = np.empty(len(decision_bar), np.int64)
    for k, i in enumerate(np.asarray(decision_bar, np.int64)):
        at = ev.i == i
        if not at.any():
            raise ReadRefused(f"no event at decision bar {int(i)}: the unit's bound cannot be formed")
        idx = int(i) + max(1, int(ev.valid[at].max())) + int(ev.hold[at].max()) + 1
        out[k] = int(b.t[idx]) if idx < n else UNKNOWN_END_NS
    return out


def cut_at_holdout(units: pd.DataFrame, card: FamilyCard) -> pd.DataFrame:
    """The card family's carried-cell units whose `bound_end_ns` (`unit_bound_end_ns`) is before 2025-10-01. Refused:
    units without `bound_end_ns`, and a carried cell with no registered time exit (BACKLOG 207)."""
    cells = [str(c) for c in card.carried]
    for c in cells:
        tf, bars = card.cells.get(c, ("", None))
        if bars is None or tf not in TF_NS:
            raise ReadRefused(f"{card.family} cell {c} has no registered maximum hold (time exit = the sim's ev.hold, "
                              f"BACKLOG 207): not read")
    if "bound_end_ns" not in units.columns:
        raise ReadRefused("units carry no bound_end_ns (unit_bound_end_ns): the holdout cut cannot be checked")
    sel = (units["family"].astype(str) == card.family).to_numpy() & units["cell_id"].astype(str).isin(cells).to_numpy()
    u = units[sel]
    return u[u["bound_end_ns"].to_numpy("int64") < HOLDOUT_NS].reset_index(drop=True)


# ------------------------------------------------------------------ 149 (c): the frozen batch card
def _card_entry(c: FamilyCard) -> dict[str, Any]:
    return {"family": c.family, "carried": [str(x) for x in c.carried],
            "weights": {str(k): float(v) for k, v in c.weights.items()},
            "cells": {str(k): [tf, None if bars is None else int(bars)] for k, (tf, bars) in c.cells.items()},
            "mean_block": float(c.mean_block), "seed_key": c.seed_key, "confirm_seen": sorted(c.confirm_seen)}


def _read_doc(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.read_text().strip():
        return {}
    if path.suffix in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(path.read_text()) or {}
    return dict(json.loads(path.read_text()))


def freeze_batch_card(card_path: Path, *, window: Literal["test_old", "confirm"], cards: Sequence[FamilyCard]) -> str:
    """Write the batch's families into the card (`l3_batch: {body, sha256}`, JSON or YAML) once, before any read of
    the batch, and return the sha256 to record in the pre-registration."""
    path = Path(card_path)
    doc = _read_doc(path)
    if CARD_KEY in doc:
        raise ReadRefused(f"{path} already holds a frozen L3 batch ({doc[CARD_KEY].get('sha256')})")
    fams = [c.family for c in cards]
    if not fams or len(set(fams)) != len(fams):
        raise ReadRefused(f"a batch card names each family once and at least one: {fams}")
    body = {"window": window, "families": [_card_entry(c) for c in cards]}
    h = _sha(body)
    doc[CARD_KEY] = {"body": body, "sha256": h}
    if path.suffix in (".yaml", ".yml"):
        import yaml
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    else:
        path.write_text(json.dumps(doc, indent=2) + "\n")
    return h


def load_batch_card(card_path: Path, card_sha256: str, *, window: str) -> list[FamilyCard]:
    """The batch's families from the frozen card, refused unless the card's body hashes to `card_sha256` and it was
    frozen for `window`."""
    try:
        frozen = _read_doc(Path(card_path))[CARD_KEY]
        body = frozen["body"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise ReadRefused(f"no frozen L3 batch card at {card_path}: {e}") from e
    h = _sha(body)
    if h != card_sha256 or frozen.get("sha256") != card_sha256:
        raise ReadRefused(f"the batch card {card_path} hashes to {h[:12]}, not the registered {card_sha256[:12]}")
    if body.get("window") != window:
        raise ReadRefused(f"the batch card was frozen for the {body.get('window')} window, not {window}")
    return [FamilyCard(family=str(f["family"]), carried=tuple(f["carried"]), weights=dict(f["weights"]),
                       cells={k: (str(v[0]), None if v[1] is None else int(v[1])) for k, v in f["cells"].items()},
                       mean_block=float(f["mean_block"]), seed_key=str(f["seed_key"]),
                       confirm_seen=frozenset(f["confirm_seen"])) for f in body["families"]]


def units_sha256(units: pd.DataFrame) -> str:
    """sha256 of the units' values (columns in name order, row order kept), the hash a batch-p row records."""
    u = units[sorted(units.columns)]
    h = hashlib.sha256(json.dumps(list(u.columns)).encode())
    h.update(pd.util.hash_pandas_object(u, index=False).to_numpy().tobytes())
    return h.hexdigest()


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


def _batch_row(ledger: Path, window: str, j: int) -> dict[str, Any] | None:
    rows = _batch_rows(ledger, window, j)
    if not rows:
        return None
    if len(rows) > 1:
        raise ReadRefused(f"{len(rows)} batch p rows for {window} batch {j} in {ledger}: the batch is ambiguous")
    if len(rows[0]["p"]) != int(rows[0]["m_j"]):
        raise ReadRefused(f"the batch p row for {window} batch {j} holds {len(rows[0]['p'])} p, not its m_j "
                          f"{rows[0]['m_j']}")
    return rows[0]


def read_batch_p(ledger: Path, *, window: str, j: int) -> dict[str, float] | None:
    """Family -> p of batch j on `window` as recorded, in batch order; None when not recorded."""
    row = _batch_row(ledger, window, j)
    return None if row is None else {str(f): float(p) for f, p in row["p"]}


def record_batch_p(units: pd.DataFrame, *, card_path: Path, card_sha256: str, batch: C.BatchPRDS, j: int,
                   ledger: Path | None = None) -> dict[str, float]:
    """Every family of the frozen batch card: its `confirm.family_cv_p` on its holdout-cut units (m_j = the card's
    family count), written as one row to the reads ledger, with the card hash and each family's units hash, before
    any `l3_read` of the batch. Written once per window and j."""
    hh = harness_gate(forward=False)
    cards = load_batch_card(card_path, card_sha256, window=batch.window)
    led = ledger or default_ledger()
    if _batch_rows(led, batch.window, j):
        raise ReadRefused(f"the p of {batch.window} batch {j} are already recorded in {led}: never rewritten")
    m_j = len(cards)
    alpha_j = batch.level(j, m_j)
    p: dict[str, float] = {}
    uh: dict[str, str] = {}
    for card in cards:
        u = cut_at_holdout(units, card)
        uh[card.family] = units_sha256(u)
        p[card.family] = float(C.family_cv_p(u, family=card.family, carried=list(card.carried), weights=card.weights,
                                             m_j=m_j, alpha_j=alpha_j, mean_block=card.mean_block,
                                             seed_key=card.seed_key))
    led.parent.mkdir(parents=True, exist_ok=True)
    with open(led, "a") as fh:
        fh.write(json.dumps({"kind": BATCH_P_KIND, "read_window": batch.window, "batch_j": j, "m_j": m_j,
                             "alpha_j": alpha_j, "p": [[f, v] for f, v in p.items()], "card_sha256": card_sha256,
                             "units_sha256": uh, "harness_hash": hh, "ts": _now().isoformat()}) + "\n")
    return p


def run_l3_read(units: pd.DataFrame, *, family: str, card_path: Path, card_sha256: str, batch: C.BatchPRDS, j: int,
                corroboration: pd.DataFrame | None, e9: bool | None, ledger: Path | None = None) -> C.L3Result:
    """The family's L3 read: harness gate, the family's entry of the frozen card, `batch_p` from the ledger (refused
    when the batch was not recorded, was recorded for another card, or its p came from other units), the holdout cut
    on the units and the corroboration, then `confirm.l3_read`."""
    harness_gate(forward=False)
    cards = {c.family: c for c in load_batch_card(card_path, card_sha256, window=batch.window)}
    if family not in cards:
        raise ReadRefused(f"{family} is not in the frozen batch card ({list(cards)}): not read")
    card = cards[family]
    led = ledger or default_ledger()
    row = _batch_row(led, batch.window, j)
    if row is None:
        raise ReadRefused(f"no batch p recorded for {batch.window} batch {j} in {led}: record_batch_p first")
    if row.get("card_sha256") != card_sha256:
        raise ReadRefused(f"{batch.window} batch {j} was recorded for card {str(row.get('card_sha256'))[:12]}, not "
                          f"{card_sha256[:12]}")
    u = cut_at_holdout(units, card)
    if units_sha256(u) != row.get("units_sha256", {}).get(family):
        raise ReadRefused(f"{family}'s units differ from those its batch p was computed on: not read")
    corr = cut_at_holdout(corroboration, card) if corroboration is not None else None
    return C.l3_read(u, family=family, carried=list(card.carried), weights=card.weights, batch=batch, j=j,
                     batch_p=[float(p) for _, p in row["p"]], corroboration=corr, mean_block=card.mean_block,
                     seed_key=card.seed_key, confirm_seen=card.confirm_seen,
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
