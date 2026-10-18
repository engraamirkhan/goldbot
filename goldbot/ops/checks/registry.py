"""The health-check registry: each area module registers its checks, `goldbot.ops.health.run_checks` iterates them.

Design (Deployment / observability): "all checks in a fixed order", each guarded so one that raises is a FAIL of its
own, never a missing report. The order is the report's contract (alert dedupe keys on names, the deploy baseline
compares them), so it is explicit here, not import order: a check registers into a BLOCK at a POSition, and the report
runs the blocks in ascending order, each block's checks in ascending position. Within a per-account block the loop is
account-major (every check of the block for the first account, then the next account); a per-service block loops over
its services the same way. A (block, pos) pair is taken once; a block has one scope.

  BLOCK_STATIC           settings, accounts, secrets, disk (the only block `--static` runs)
  BLOCK_CORE             supervisor, owner halt, phase, stop rule, phase gates
  BLOCK_ACCOUNT          per account: engine ... server clock
  BLOCK_SERVICE_SKEW     per service in SKEW_SERVICES: schema_skew:svc
  BLOCK_HEARTBEATS       per service in HEARTBEAT_SERVICES: heartbeat
  BLOCK_OPTIONAL_HEARTBEATS  webhook, tv-pull: heartbeat (only once one ran on this host; WARN)
  BLOCK_SCHEDULER        scheduler and its jobs
  BLOCK_COSTS            per account: cost table, P&L basis
  BLOCK_RECONCILIATION   per account: feed reconciliation (D12)
  BLOCK_COSTS_PUBLISHED  the published cost table (only with costs.publish_release)
  BLOCK_TAIL             data quality ... dead man's switch

A check is called through `goldbot.ops.health` when that module exports its name (`resolve`): health is the public
surface callers and tests import and patch, exactly as when run_checks was written out by hand there. Every registered
check must be re-exported from goldbot/ops/health.py (tests/test_health_registry.py enforces it), which also catches two
checks sharing a function name.
"""
from __future__ import annotations

import importlib
from typing import Any, Callable, Literal, TypeVar

from goldbot.base import FrozenRecord

Scope = Literal["static", "global", "account", "service"]
F = TypeVar("F", bound=Callable[..., Any])

BLOCK_STATIC = 10
BLOCK_CORE = 20
BLOCK_ACCOUNT = 30
BLOCK_SERVICE_SKEW = 40
BLOCK_HEARTBEATS = 50
BLOCK_OPTIONAL_HEARTBEATS = 55
BLOCK_SCHEDULER = 60
BLOCK_COSTS = 70
BLOCK_RECONCILIATION = 80
BLOCK_COSTS_PUBLISHED = 90
BLOCK_TAIL = 100


class CheckSpec(FrozenRecord):
    """One registered check. `guard`: the name of the FAIL when it raises (":<account>" or ":<service>" appended)."""

    block: int
    pos: int
    scope: Scope
    fn: Callable[..., Any]
    guard: str
    pass_account: bool = False            # account scope: called with the AccountRef, not its id
    services: tuple[str, ...] = ()        # service scope: the services it loops over
    when: Callable[[Any], bool] | None = None   # (ctx) -> run it at all


_REGISTRY: dict[tuple[int, int], CheckSpec] = {}


def register_check(scope: Scope, *, block: int, pos: int, guard: str | None = None, pass_account: bool = False,
                   services: tuple[str, ...] = (), when: Callable[[Any], bool] | None = None) -> Callable[[F], F]:
    """Decorator: register `fn` at (block, pos); the function itself is returned unchanged. The guard name defaults
    to the function's name without `check_`. Raises ValueError on a taken slot or a block with mixed scopes, so a
    mistake fails at import, in every test."""
    def add(fn: F) -> F:
        spec = CheckSpec(block=block, pos=pos, scope=scope, fn=fn, guard=guard or fn.__name__.removeprefix("check_"),
                         pass_account=pass_account, services=services, when=when)
        if pass_account and scope != "account":
            raise ValueError(f"{fn.__name__}: pass_account needs the account scope")
        if (scope == "service") != bool(services):
            raise ValueError(f"{fn.__name__}: services are given exactly for the service scope")
        taken = _REGISTRY.get((block, pos))
        if taken is not None and taken.fn is not fn:
            raise ValueError(f"health check slot ({block}, {pos}) is taken by {taken.fn.__name__}")
        for other in _REGISTRY.values():
            if other.block == block and other.fn is not fn and (other.scope, other.services) != (scope, services):
                raise ValueError(f"{fn.__name__}: block {block} is {other.scope} {other.services}, not {scope} {services}")
        _REGISTRY[(block, pos)] = spec
        return fn
    return add


def blocks(*, static_only: bool = False) -> list[tuple[Scope, list[CheckSpec]]]:
    """The registered checks as (scope, checks) per block, in report order."""
    out: list[tuple[Scope, list[CheckSpec]]] = []
    for block in sorted({b for b, _ in _REGISTRY}):
        specs = [_REGISTRY[k] for k in sorted(k for k in _REGISTRY if k[0] == block)]
        if static_only and specs[0].scope != "static":
            continue
        out.append((specs[0].scope, specs))
    return out


def resolve(fn: F) -> F:
    """`fn` as goldbot.ops.health exports it (the patch point), else `fn` itself."""
    health = importlib.import_module("goldbot.ops.health")
    found: F = getattr(health, fn.__name__, fn)
    return found
