"""The phase-gate commands: `record-gate` (appends to state/phase_state.json; moved verbatim from goldbot/ops/run.py,
which re-exports `record_gate_cli`), `gates` and `gate-evidence` (goldbot/ops/gates_phase.py)."""
from __future__ import annotations

import sys

from goldbot.ops.commands.registry import register_command
from goldbot.ops.services._base import resolve


def record_gate_cli(argv: list[str]) -> int:
    """record-gate <gate_name> --evidence <path or text>"""
    import argparse

    from goldbot.ops import accounts
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run record-gate")
    ap.add_argument("gate", help="one of: " + ", ".join(accounts.GATES))
    ap.add_argument("--evidence", required=True, help="path to the evidence file (hashed) or a short text")
    args = ap.parse_args(argv)
    try:
        st = accounts.record_gate(args.gate, args.evidence)
    except ValueError as exc:
        print(f"refused: {exc}")
        return 1
    print(f"recorded {args.gate}: phase {st['phase']}, gates passed {st['gates_passed']}")
    return 0


@register_command("record-gate", pos=100)
def _record_gate(cmd: str, args: list[str]) -> None:
    sys.exit(resolve(record_gate_cli)(args))


@register_command("gates", pos=110)
def _gates(cmd: str, args: list[str]) -> None:
    from goldbot.ops.gates_phase import main as gates_main
    sys.exit(gates_main(args))


@register_command("gate-evidence", pos=120)
def _gate_evidence(cmd: str, args: list[str]) -> None:
    from goldbot.ops.gates_phase import evidence_main
    sys.exit(evidence_main(args))
