"""The command registry: each module in goldbot/ops/commands registers its commands, `goldbot.ops.run.main` looks the
command up here instead of an if/elif chain every lane appended to.

A command registers one handler under one or more names at an explicit POSition; `commands()` lists them in ascending
position, so the order (the old dispatcher's, kept for listings and reviews) never depends on import order. A name is
taken once and a position once; a clash raises ValueError at import, so it fails in every test.

A handler is the body of the old dispatcher branch: `handler(cmd, args)` with `cmd` the command name and `args` the
words after it (sys.argv[2:]). A service runs and returns (or never does); a one-shot command ends with sys.exit(code),
both exactly as before. The registry record is a frozen dataclass, not a FrozenRecord, on purpose: importing
goldbot.ops.run must load no third-party package (goldbot/ops/services/_base.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

Handler = Callable[[str, list[str]], None]
H = TypeVar("H", bound=Handler)


@dataclass(frozen=True)
class CommandSpec:
    """One registered command: the names it answers to, its position in the listing, its handler."""

    names: tuple[str, ...]
    pos: int
    handler: Handler


_REGISTRY: dict[int, CommandSpec] = {}


def register_command(*names: str, pos: int) -> Callable[[H], H]:
    """Decorator: register `handler` under `names` at `pos`; the handler itself is returned unchanged."""
    if not names:
        raise ValueError("a command needs at least one name")

    def add(handler: H) -> H:
        taken = _REGISTRY.get(pos)
        if taken is not None and taken.handler is not handler:
            raise ValueError(f"command position {pos} is taken by {taken.names}")
        for other in _REGISTRY.values():
            clash = set(other.names) & set(names)
            if clash and other.handler is not handler:
                raise ValueError(f"command {sorted(clash)} is already registered at position {other.pos}")
        _REGISTRY[pos] = CommandSpec(names=tuple(names), pos=pos, handler=handler)
        return handler
    return add


def commands() -> list[CommandSpec]:
    """Every registered command, in ascending position."""
    return [_REGISTRY[p] for p in sorted(_REGISTRY)]


def find(cmd: str) -> CommandSpec | None:
    """The command registered under `cmd`, else None (the caller prints the usage and exits 1)."""
    return next((spec for spec in _REGISTRY.values() if cmd in spec.names), None)
