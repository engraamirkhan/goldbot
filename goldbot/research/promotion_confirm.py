"""ADR 0085 confirm for RESEARCH and DEMO promotion ("Option A"): Holm on fresh data, alpha from one global ledger.

The owner's paper -> tiny-live phase gate does not read this module: it keeps today's criteria through
`registry.phase_gate_refusal` and `TrialRegistry.passed_gates` (tests/test_phase_gate_pin.py). Live unlock, the
real-money confirmation and RiskGate are untouched.

The panel ruling of 2026-11-15 (quant-reviewer, on docs/research/power-first-gates-2026-11.md), as coded here:
* "Confirm = option D with HOLM (not BH): per-batch FWER <= alpha_j under any dependence" (`holm`, reused from
  eval2.confirm).
* "a fixed convergent schedule q_y = q.2^-y, q = 0.05, batches per year fixed in the ADR before any batch is read":
  BATCHES_PER_YEAR = 4 equal shares, alpha_j = q 2^-y / 4 for every batch of ledger year y (`batch_alpha`); the sum
  over all years is q. Ledger years run from LEDGER_EPOCH (the ADR's date); a year's unused slots expire.
* "ONE global never-reset ledger that may not coexist with EVAL-2's q=0.10 ledger as a second pot": the ledger is the
  PROMOTION_CONFIRM rows of the trial registry (append-only, synced; `PromotionLedger`), and
  `research_promotion_refusal` accepts only a confirm stamped LEDGER_ID: an EVAL-2 BatchPRDS rejection (q 0.10) or an
  autodiscovery monthly Holm pass is never research or demo promotion evidence.
* "fresh data = untouched for that family (TEST-OLD once per family; a family already read on TEST-OLD under EVAL-2
  can't reuse it; ADR 0067 embargo)": `fresh_refusal`, with the EVAL-2 families required of the caller
  (`eval2_test_old_families` derives them from the reads ledger); two holdout reads of one config in a batch refused.
* "forward replay frozen before data (registry stamps card hash, code SHA, feature version, cost-of-record version;
  each look a scheduled charged alpha spend)": every read's `FrozenStamp` must equal the one its card's
  pre-registration froze (`stamp_refusal`); every read is a `run_batch` call that takes a ledger slot and writes
  trial rows (K). Ledger years are UTC, and a year below the highest used is never reopened.
* "holdout at most once per config, charged, not a routine source".
* "clustered p-values (existing stationary day-block bootstrap; cross-instrument day clusters for pooled)": the
  units' `day` is the session day across instruments, resampled by eval2.confirm's stationary day blocks.
* "net of measured costs at the upper CI": `ConfirmItem.cost_basis` must be "measured_upper_ci".
* "size from fresh-data effect": `size_basis_mean_r` is the fresh window's mean net R.
* "registry-K DSR stays REPORTED (with K_eff) but becomes report-only for research/demo promotion".
* Count minimums, rule-only path only: the 200-trade minimum becomes MIN_FRESH_N_EFF clustered observations on the
  fresh-data test; a model card still needs the 1,500 candidate / 60 per fold minimums of its learn row.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Collection, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final, Literal, NamedTuple

import numpy as np
import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.research.eval2.confirm import _day_count_chunks, _DayBlocks, _p_from, confirm_resamples, holm
from goldbot.research.metrics import MIN_TRADES_FOR_DSR, DsrInputs
from goldbot.research.registry import (
    PREREGISTERED,
    TrialRegistry,
    config_hash,
    n_trials_effective,
    refers_to,
    row_hash,
)

__all__ = ["BATCHES_PER_YEAR", "LEDGER_EPOCH", "LEDGER_ID", "MIN_FRESH_N_EFF", "PROMOTION_CONFIRM", "Q_PROMOTION",
           "ConfirmItem", "FrozenStamp", "LedgerFull", "LedgerSlot", "NotFresh", "PromotionLedger", "batch_alpha",
           "clustered_n_eff", "eval2_test_old_families", "fresh_refusal", "holm", "ledger_year", "research_promotion_refusal", "run_batch", "stamp_refusal"]

PROMOTION_CONFIRM: Final = "promotion_confirm"     # a trial row (a look): counted in K like every read
Q_PROMOTION: Final = 0.05                          # the false-promotion rate over all promotions ever
BATCHES_PER_YEAR: Final = 4                        # fixed by ADR 0085 before any batch is read
LEDGER_EPOCH: Final = datetime(2026, 11, 15, tzinfo=timezone.utc)
LEDGER_ID: Final = "adr0085:holm:q0.05:2^-y:4/yr"
# ADR 0085: the asymptotic floor of the old 200-trade minimum, now on clustered effective observations of the
# fresh-data test (n_eff, not trades), so same-day and cross-instrument repeats are not counted as evidence
MIN_FRESH_N_EFF: Final = 200.0
Source = Literal["test_old", "holdout", "forward"]
Kind = Literal["rule_only", "model"]


class LedgerFull(RuntimeError):
    """The ledger year's BATCHES_PER_YEAR slots are spent: no read until the next ledger year."""


class NotFresh(ValueError):
    """The window is not fresh for this family or config (ADR 0085 fresh-data rule); nothing was read or written."""


class FrozenStamp(FrozenRecord):
    """What a read is frozen to before any data: the card, the code, the features and the cost of record."""
    card_hash: str = Field(min_length=1)
    code_sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    feature_version: str = Field(min_length=1)
    cost_of_record_version: str = Field(min_length=1)


class ConfirmItem(FrozenRecord):
    """One config carried to the fresh-data confirm. `learn_gates`: its learn-window rule-only gates
    (`gates.rule_only_promotion_gates` on the family's pooled trades); `learn_ref`: its learn (walk-forward) row."""
    family: str = Field(min_length=1)
    config: dict[str, Any]
    source: Source
    kind: Kind
    stamp: FrozenStamp
    cost_basis: Literal["measured_upper_ci"]
    learn_gates: dict[str, Any]
    learn_ref: dict[str, Any]


class LedgerSlot(FrozenRecord):
    year: int
    slot: int
    alpha_j: float

    @property
    def batch_id(self) -> str:
        return f"y{self.year}b{self.slot}"


def batch_alpha(year: int) -> float:
    """alpha_j of every batch in ledger year `year`: q 2^-year / BATCHES_PER_YEAR (sums to q over all years)."""
    if year < 1:
        raise ValueError(f"ledger years start at 1, got {year}")
    return Q_PROMOTION * 2.0 ** -year / BATCHES_PER_YEAR


def ledger_year(now: datetime) -> int:
    """The ledger year of `now`, read in UTC: 1 from LEDGER_EPOCH to its first anniversary, then 2, ... A naive time
    is refused (its zone would decide the year)."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("ledger_year needs a timezone-aware time")
    now = now.astimezone(timezone.utc)
    if now < LEDGER_EPOCH:
        raise ValueError(f"no promotion confirm before the ADR 0085 ledger epoch {LEDGER_EPOCH.date()}")
    y = now.year - LEDGER_EPOCH.year
    if (now.month, now.day) < (LEDGER_EPOCH.month, LEDGER_EPOCH.day):
        y -= 1
    return y + 1


def _confirms(rows: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any], dict[str, Any]]]:
    return [(i, r, (r.get("results") or {}).get("promotion_confirm") or {}) for i, r in enumerate(rows)
            if r.get("status") == PROMOTION_CONFIRM]


class PromotionLedger:
    """The one global ledger, derived from the registry's PROMOTION_CONFIRM rows (append-only, never reset)."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.used = {(int(pc["year"]), int(pc["slot"])) for _, _, pc in _confirms(rows)
                     if pc.get("ledger_id") == LEDGER_ID and "year" in pc and "slot" in pc}

    def next_slot(self, now: datetime) -> LedgerSlot:
        """The next slot of `now`'s ledger year; a year below the highest already used is never reopened (a
        backdated clock would otherwise spend a lapsed year's larger alpha)."""
        y = ledger_year(now)
        top = max((yy for yy, _ in self.used), default=0)
        if y < top:
            raise LedgerFull(f"ledger year {y} is below year {top} already used: no backdated reopening (ADR 0085)")
        taken = [s for (yy, s) in self.used if yy == y]
        if len(taken) >= BATCHES_PER_YEAR:
            raise LedgerFull(f"ledger year {y} has spent its {BATCHES_PER_YEAR} batches (ADR 0085)")
        return LedgerSlot(year=y, slot=max(taken, default=0) + 1, alpha_j=batch_alpha(y))


def fresh_refusal(rows: list[dict[str, Any]], item: ConfirmItem, eval2_test_old_families: Collection[str]) -> str | None:
    """Why `item`'s window is not fresh, or None. TEST-OLD: once per family, never after an EVAL-2 TEST-OLD read of the
    family. Holdout: once per config (a scoring row or an earlier holdout confirm). Forward: a frozen replay, fresh by
    construction (the stamp is required on every read)."""
    h = config_hash(item.config)
    if item.source == "test_old":
        if item.family in eval2_test_old_families:
            return f"{item.family} was already read on TEST-OLD under EVAL-2: it cannot reuse it"
        if any(r.get("family") == item.family and pc.get("source") == "test_old" for _, r, pc in _confirms(rows)):
            return f"{item.family} was already read on TEST-OLD: once per family"
    if item.source == "holdout":
        seen = any(r.get("family") == item.family and row_hash(r) == h
                   and (r.get("status") == "holdout" or pc.get("source") == "holdout")
                   for r, pc in ((r, (r.get("results") or {}).get("promotion_confirm") or {}) for r in rows))
        if seen:
            return f"the holdout was already read for {item.family} {h}: at most once per config"
    return None


def stamp_refusal(rows: list[dict[str, Any]], item: ConfirmItem) -> str | None:
    """The read's frozen stamp must equal the one its card's pre-registration froze before any data
    (`plan.frozen_stamp` of the latest PREREGISTERED row of the family and config): card hash, code SHA, feature
    version and cost-of-record version."""
    h = config_hash(item.config)
    pre = [r for r in rows if r.get("status") == PREREGISTERED and r.get("family") == item.family
           and r.get("config_hash") == h]
    frozen = ((pre[-1].get("plan") or {}).get("frozen_stamp") if pre else None) or None
    if not isinstance(frozen, dict):
        return f"{item.family} {h}: no pre-registration with a frozen stamp"
    diff = [k for k, v in item.stamp.model_dump().items() if frozen.get(k) != v]
    return f"{item.family} {h}: the stamp differs from its pre-registration in {', '.join(diff)}" if diff else None


def eval2_test_old_families(ledger: Path) -> set[str]:
    """The families EVAL-2 read on TEST-OLD: every family of an `l3_batch_p` row with read_window "test_old" in the
    reads ledger (state/catalogue_reads.jsonl, `eval2.runner.default_ledger`). A missing ledger holds none."""
    from goldbot.research.eval2.runner import BATCH_P_KIND  # the runner pulls the catalogue: import on use
    if not ledger.exists():
        return set()
    rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
    return {str(f) for r in rows if r.get("kind") == BATCH_P_KIND and r.get("read_window") == "test_old"
            for f, _ in r.get("p") or []}


def clustered_n_eff(x: np.ndarray, day: np.ndarray) -> float:
    """Effective independent observations of the mean of `x` clustered by `day`: s^2 / se_cluster^2, with
    se_cluster^2 = sum_d (sum_{i in d} (x_i - mean))^2 / n^2. One trade a day gives about n; identical same-day
    trades give about the number of days."""
    x = np.asarray(x, float)
    n = len(x)
    if n < 2:
        return float(n)
    dev = x - x.mean()
    _, inv = np.unique(np.asarray(day), return_inverse=True)
    se2 = float((np.bincount(inv.reshape(-1), weights=dev) ** 2).sum()) / n ** 2
    s2 = float(x.var(ddof=1))
    return s2 / se2 if se2 > 0 else float(n)


class _Read(NamedTuple):
    p: float
    n: int
    mean: float
    n_eff: float
    dsr: float | None


def _mean_p(units: pd.DataFrame, *, mean_block: float, resamples: int, seed_key: str) -> float:
    """One-sided basic-bootstrap p of H0: mean net R <= 0, stationary day blocks (eval2.confirm's draws)."""
    net = units["net_r"].to_numpy(float)
    blocks = _DayBlocks(units)
    if not blocks.n or not np.isfinite(net).all():
        return 1.0
    s = np.bincount(blocks.day_of_row, weights=net, minlength=blocks.n)
    k = np.bincount(blocks.day_of_row, minlength=blocks.n).astype(float)
    t = np.concatenate([(c @ s) / (c @ k) for c in _day_count_chunks(blocks, mean_block=mean_block,
                                                                     resamples=resamples, seed_key=seed_key)])
    return _p_from(float(net.mean()), t)


def run_batch(registry: TrialRegistry, batch: Sequence[tuple[ConfirmItem, pd.DataFrame]], *, now: datetime,
              mean_block: float, agent_id: str, eval2_test_old_families: Collection[str]) -> list[dict[str, Any]]:
    """Read one confirm batch ONCE under the registry lock: take the ledger's next slot (alpha_j), refuse the whole
    batch if any window is not fresh or any stamp differs from its pre-registration (nothing read, nothing written,
    no slot spent; `eval2_test_old_families` is required, e.g. `eval2_test_old_families(ledger)`), compute each config's clustered
    p on its fresh units (columns `day`, `net_r` net of measured costs at the upper CI), Holm at alpha_j over the
    batch, and write one PROMOTION_CONFIRM trial row per config. A pass needs the Holm rejection, mean net R > 0 and
    at least MIN_FRESH_N_EFF clustered observations. The registry-K DSR is reported, never decides."""
    if not batch:
        raise ValueError("an empty confirm batch")
    with registry.locked():
        rows = registry._rows()
        slot = PromotionLedger(rows).next_slot(now)
        seen: set[str] = set()
        holdouts: set[tuple[str, str]] = set()
        for it, _ in batch:
            why = fresh_refusal(rows, it, eval2_test_old_families) or stamp_refusal(rows, it)
            hk = (it.family, config_hash(it.config))
            if why is None and it.source == "test_old" and it.family in seen:
                why = f"{it.family} appears twice on TEST-OLD in one batch: once per family"
            if why is None and it.source == "holdout" and hk in holdouts:
                why = f"{it.family} {hk[1]} appears twice on the holdout in one batch: at most once per config"
            if why is not None:
                raise NotFresh(why)
            if it.source == "test_old":
                seen.add(it.family)
            if it.source == "holdout":
                holdouts.add(hk)
        m = len(batch)
        resamples = confirm_resamples(m, slot.alpha_j)
        k = n_trials_effective(rows) + m
        reads = []
        for it, u in batch:
            h = config_hash(it.config)
            key = hashlib.sha256(f"{LEDGER_ID}|{slot.batch_id}|{it.family}|{h}".encode()).hexdigest()[:16]
            net = u["net_r"].to_numpy(float)
            reads.append(_Read(p=_mean_p(u, mean_block=mean_block, resamples=resamples, seed_key=key), n=len(net),
                               mean=float(net.mean()) if len(net) else math.nan,
                               n_eff=clustered_n_eff(net, u["day"].to_numpy()),
                               dsr=DsrInputs.of(net).dsr(k) if len(net) >= MIN_TRADES_FOR_DSR else None))
        rejected = holm([r.p for r in reads], slot.alpha_j)
        out = []
        for (it, _), r, rej in zip(batch, reads, rejected):
            fails = [w for w, bad in (("Holm did not reject", not rej), ("mean net R <= 0", not r.mean > 0),
                                      (f"clustered n_eff {r.n_eff:.0f} < {MIN_FRESH_N_EFF:.0f}",
                                       r.n_eff < MIN_FRESH_N_EFF)) if bad]
            res = {"ledger_id": LEDGER_ID, "year": slot.year, "slot": slot.slot, "batch_id": slot.batch_id,
                   "alpha_j": slot.alpha_j, "m_j": m, "resamples": resamples, "source": it.source, "kind": it.kind,
                   "p": r.p, "holm_rejected": bool(rej), "n_trades": r.n, "n_eff": r.n_eff, "mean_r": r.mean,
                   "size_basis_mean_r": r.mean, "passed": not fails, "why": "; ".join(fails),
                   "stamp": it.stamp.model_dump(), "cost_basis": it.cost_basis, "learn_gates": it.learn_gates,
                   "learn_ref": it.learn_ref,
                   "dsr_report_only": {"dsr": r.dsr, "k": k, "basis": "n_trials_effective (K_eff included)"}}
            out.append(registry.record(agent_id=agent_id, family=it.family, config=it.config,
                                       feature_version=it.stamp.feature_version,
                                       rationale=f"ADR 0085 promotion confirm {slot.batch_id} on {it.source}",
                                       results={"promotion_confirm": res}, status=PROMOTION_CONFIRM))
    return out


def _count_minimums_refusal(rows: list[dict[str, Any]], ref: dict[str, Any]) -> str | None:
    """A model card keeps the 1,500 candidate and 60 per fold minimums on its learn (walk-forward) row."""
    learn = next((r for r in rows if refers_to(ref, r)), None)
    if learn is None:
        return "model card: its learn row is not in the registry"
    checks = {c.get("name"): c for c in ((learn.get("results") or {}).get("gates") or {}).get("checks", [])}
    failed = [n for n in ("candidates", "per_fold") if not (checks.get(n) or {}).get("passed")]
    return f"model card: the learn row's {', '.join(failed)} minimum did not pass" if failed else None


def research_promotion_refusal(row: dict[str, Any], rows: list[dict[str, Any]]) -> str | None:
    """Why `row` is not a basis for RESEARCH or DEMO promotion under ADR 0085, or None when it is: a passed confirm of
    the one global ledger, not revoked by a later failed confirm of the same config, with its learn-window rule-only
    gates passed and its frozen stamp, and for a model card the learn row's count minimums. Never the phase gate
    (`registry.phase_gate_refusal`)."""
    if row.get("status") != PROMOTION_CONFIRM:
        return "not an ADR 0085 promotion confirm"
    pc = (row.get("results") or {}).get("promotion_confirm") or {}
    if pc.get("ledger_id") != LEDGER_ID:
        return f"confirmed on another ledger ({pc.get('ledger_id')}): ADR 0085 allows one global ledger, no second pot"
    if not pc.get("passed"):
        return f"did not pass its confirm: {pc.get('why') or 'no verdict'}"
    if not pc.get("stamp"):
        return "no frozen stamp (card hash, code SHA, feature version, cost of record)"
    idx = next((i for i, r in enumerate(rows) if r is row or r == row), -1)
    h, fam = row_hash(row), row.get("family")
    for i, r, later in _confirms(rows):
        if i > idx and r.get("family") == fam and row_hash(r) == h and not later.get("passed"):
            return f"a later confirm of the same config (trial #{r.get('trial')}) failed: it decides"
    if not (pc.get("learn_gates") or {}).get("passed"):
        return "its learn-window rule-only gates (regime years) did not pass"
    if pc.get("kind") == "model":
        return _count_minimums_refusal(rows, pc.get("learn_ref") or {})
    return None
