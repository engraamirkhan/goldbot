"""Deterministic replay of the adaptive limits (ADR 0031) from a state folder's record: one line per risk day. Validates
the frozen rules, never tunes them; record each run in ADR 0031's replay log. Read only. See
goldbot/ops/limits_replay.py.

    python scripts/replay_limits.py --account icm-demo --from 2026-10-20 --to 2026-11-30 [--state state]
        [--models models] [--data-root DIR]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from goldbot.ops.limits_replay import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
