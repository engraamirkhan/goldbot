"""The registry link of the discovery search (ADR 0058 decision 2, "the registry's K includes each batch's k_contrib";
F3, a phase-3 precondition; design 3.1 "Registry link").

    sync_discovery_k(registry, ledger, *, generator, timeframe="", lock=True) -> dict | None

Tops the registry's discovery sidecar up to the ledger's K_search: k_contrib = the ledger's distinct search specs not yet
counted for that ledger (`SearchLedger.ledger_id`). The nightly job calls it after each search, so each batch's row
carries that night's new distinct hashes; the confirm stage calls it again before it reads K, so a lost sidecar or a
night that crashed before its row is counted before any discovered spec reaches a confirm trial. Idempotent (nothing new:
no row). A synthetic ledger never reaches the registry (ValueError). The batch row is never a trial and never a budget
slot (`TrialRegistry.record_discovery_batch`).

Searches run before the registry link existed are counted once as fixed rows (`PRIOR_SEARCHES`; quant review of phase 3,
F1): grammar v1, the 2026-10-30 Mac runs of 288 specs on each of 15m, 1h and 4h (K 864), whose ledgers were not kept as
the discovery ledger. `dsr_k_offset` is the K the explore DSR adds to the ledger's K_search, so the nightly DSR is read
at the registry's `n_trials_discovery`."""
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.registry import TrialRegistry

GRAMMAR_V1_K = 864
# (ledger_id, generator, k_contrib): one row each, written once
PRIOR_SEARCHES: tuple[tuple[str, str, int], ...] = (
    ("prior:grammar-v1-2026-10-30", "grammar-v1 (A4)", GRAMMAR_V1_K),
)


def ensure_prior_searches(registry: TrialRegistry) -> None:
    """Record every PRIOR_SEARCHES row the registry's sidecar lacks (caller holds the registry lock)."""
    have = {b.get("ledger_id") for b in registry.discovery_batches()}
    for lid, generator, k in PRIOR_SEARCHES:
        if lid not in have:
            registry.record_discovery_batch(k_contrib=k, generator=generator, ledger_head=(0, ""), ledger_id=lid)


def dsr_k_offset(registry: TrialRegistry, ledger: SearchLedger) -> int:
    """The registry's discovery K outside this ledger (registry trials, other ledgers, prior searches): the explore DSR
    is read at this + the ledger's K_search, i.e. `n_trials_discovery` once the ledger is synced."""
    with registry.locked():
        ensure_prior_searches(registry)
    return registry.n_trials_discovery - counted(registry, ledger.ledger_id())


def counted(registry: TrialRegistry, ledger_id: str) -> int:
    """k_contrib already recorded for the ledger `ledger_id`."""
    return sum(int(b.get("k_contrib") or 0) for b in registry.discovery_batches() if b.get("ledger_id") == ledger_id)


def sync_discovery_k(registry: TrialRegistry, ledger: SearchLedger, *, generator: str, timeframe: str = "",
                     lock: bool = True) -> dict[str, Any] | None:
    """Record the ledger's uncounted K_search as one discovery batch (module docstring); None when nothing is new."""
    if ledger.mode() != "real":
        raise ValueError(f"{ledger.path} is not a real search ledger (synthetic or unmarked): its specs never reach "
                         "the trial registry's K")
    guard: AbstractContextManager[Any] = registry.locked() if lock else nullcontext()
    with guard:
        ensure_prior_searches(registry)
        lid = ledger.ledger_id()
        new = ledger.k_search() - counted(registry, lid)
        if new <= 0:
            return None
        return registry.record_discovery_batch(k_contrib=new, generator=generator, ledger_head=ledger.head(),
                                               ledger_id=lid, timeframe=timeframe)
