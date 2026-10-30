"""The registry link of the discovery search (ADR 0058 decision 2, "the registry's K includes each batch's k_contrib";
F3, a phase-3 precondition; design 3.1 "Registry link").

    sync_discovery_k(registry, ledger, *, generator, timeframe="", lock=True) -> dict | None

Tops the registry's discovery sidecar up to the ledger's K_search: k_contrib = the ledger's distinct search specs not yet
counted for that ledger (`SearchLedger.ledger_id`). The nightly job calls it after each search, so each batch's row
carries that night's new distinct hashes; the confirm stage calls it again before it reads K, so a lost sidecar or a
night that crashed before its row is counted before any discovered spec reaches a confirm trial. Idempotent (nothing new:
no row). A synthetic ledger never reaches the registry (ValueError). The batch row is never a trial and never a budget
slot (`TrialRegistry.record_discovery_batch`)."""
from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext
from typing import Any

from goldbot.research.autodisc.ledger import SearchLedger
from goldbot.research.registry import TrialRegistry


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
        lid = ledger.ledger_id()
        new = ledger.k_search() - counted(registry, lid)
        if new <= 0:
            return None
        return registry.record_discovery_batch(k_contrib=new, generator=generator, ledger_head=ledger.head(),
                                               ledger_id=lid, timeframe=timeframe)
