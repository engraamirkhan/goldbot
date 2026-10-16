"""`drift-review [--clear "<note>"]`: show state/drift.json, or record the owner's review. Moved verbatim from
goldbot/ops/run.py, which re-exports `drift_review_cli`."""
from __future__ import annotations

import sys
from pathlib import Path

from goldbot.ops.commands.registry import register_command
from goldbot.ops.services._base import resolve


def drift_review_cli(argv: list[str], state_dir: str | Path = "state") -> int:
    """Show state/drift.json; `--clear "<note>"` records the owner's review, which lifts the current system halt and
    agent halts (drift_watch re-halts at once if the condition still holds)."""
    import json

    import pandas as pd
    path = Path(state_dir) / "drift.json"
    d = json.loads(path.read_text()) if path.exists() else {}
    print(json.dumps({k: d.get(k) for k in ("ts", "system_halt", "halted", "size_factor", "errors")}, indent=2))
    if argv[:1] == ["--clear"]:
        note = " ".join(argv[1:]).strip()
        if not note:
            print("give a note: drift-review --clear \"what was checked\"")
            return 2
        (Path(state_dir) / "drift_review.json").write_text(json.dumps({"cleared_utc": pd.Timestamp.now("UTC").isoformat(),
                                                                         "note": note}))
        if path.exists():                              # lift now; the next drift_watch re-evaluates from scratch
            d["system_halt"], d["halted"] = None, {}
            path.write_text(json.dumps(d))
        print("review recorded; halts lifted")
    return 0


@register_command("drift-review", pos=30)
def _drift_review(cmd: str, args: list[str]) -> None:
    sys.exit(resolve(drift_review_cli)(args))
