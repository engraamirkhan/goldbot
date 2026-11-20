"""A deploy's own alert window (audit 2026-11-20, SRE finding 3; ADR 0094).

About 63% of the alerts in the two weeks to 2026-11-20 were caused by our own deploys: `tick_errors:<account>` (the
engine's BridgeUnavailable burst while a bridge restarts, remembered 15 min) and `bridge_compat:<account>` (a commit
or confirmation gap while the boxes roll out). While a window is open, health.py's alert rule HOLDS new alerts of
exactly those two checks (HELD_PREFIXES): they wait in its pending state, are never counted as flaps, and one still
bad when the window ends is told on the next poll (its first-seen time is kept). Nothing else is held: a bridge that
does not come back fails `bridge:<account>` (5 min, immediate), a dead engine `engine_loop:`.

Two files in the state folder, either may open a window:
* `deploy_window.json`: goldbot-deploy on the brain, from its switch to MUTE_AFTER_S (20 min) after its record.
* `deploy_window_manual.json`: `python -m goldbot.ops.deploy_window open --minutes N` (at most MAX_OPEN_MIN), which
  goldbot-deploy-all runs on the brain before it deploys the MT5 boxes (their deploys cannot write the brain's state).
  It only extends an open window, never shortens one; an extension that would span more than MAX_WINDOW_S (which
  `active` ignores) starts a new window at now instead, so the file always holds a window that is honoured.

Each is {"from": epoch s, "until": epoch s, "source": text}. One that cannot be read, or spans more than MAX_WINDOW_S,
holds nothing: a stale or junk file can never mute alerts for long.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd

from goldbot.base import FrozenRecord

HELD_PREFIXES = ("tick_errors:", "bridge_compat:")
FILES = ("deploy_window.json", "deploy_window_manual.json")
MANUAL = FILES[1]
MAX_WINDOW_S = 2 * 3600          # longer than any deploy (brain: 90 s watch + 8 min bridge wait + 20 min after)
MAX_OPEN_MIN = 30               # safety review L3: a manual window holds alerts 30 min at a time


class Window(FrozenRecord):
    """An alert window: epoch seconds, inclusive, and who opened it."""

    start: int
    until: int
    source: str


def _read(path: Path) -> Window | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")[:4096])
        start, until = raw["from"], raw["until"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not all(isinstance(x, int) and not isinstance(x, bool) for x in (start, until)):
        return None
    if not 0 <= until - start <= MAX_WINDOW_S:
        return None
    return Window(start=start, until=until, source=str(raw.get("source", ""))[:40])


def active(state_dir: str | Path, now: pd.Timestamp) -> Window | None:
    """The deploy window open at `now`, if any (the later-ending one when both files hold one)."""
    t = int(pd.Timestamp(now).timestamp())
    open_ = [w for f in FILES if (w := _read(Path(state_dir) / f)) is not None and w.start <= t <= w.until]
    return max(open_, key=lambda w: w.until) if open_ else None


def held(state_dir: str | Path, now: pd.Timestamp) -> tuple[str, ...]:
    """The check-name prefixes whose new alerts are held now: HELD_PREFIXES inside a window, else none."""
    return HELD_PREFIXES if active(state_dir, now) is not None else ()


def open_manual(state_dir: str | Path, minutes: int, *, now: pd.Timestamp | None = None) -> Window:
    """Open (or extend) the manual window for `minutes` from `now`; an open one keeps its start and its later end."""
    t = int((now if now is not None else pd.Timestamp.now("UTC")).timestamp())
    path = Path(state_dir) / MANUAL
    old = _read(path)
    start, until = t, t + int(minutes) * 60
    if old is not None and old.start <= t <= old.until:
        until = max(old.until, until)
        start = old.start if until - old.start <= MAX_WINDOW_S else t     # code review LOW1: never an ignored span
    w = Window(start=start, until=until, source="manual")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"from": w.start, "until": w.until, "source": w.source}) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return w


def main(argv: list[str]) -> int:
    """`python -m goldbot.ops.deploy_window open --minutes N [--state-dir state]` (1..MAX_OPEN_MIN) or `show`."""
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.deploy_window")
    ap.add_argument("action", choices=("open", "show"))
    ap.add_argument("--minutes", type=int, default=0)
    ap.add_argument("--state-dir", default="state")
    args = ap.parse_args(argv)
    if args.action == "show":
        w = active(args.state_dir, pd.Timestamp.now("UTC"))
        print("no deploy window open" if w is None else
              f"deploy window open ({w.source}) until {pd.Timestamp(w.until, unit='s', tz='UTC'):%H:%M:%S} UTC: "
              f"new {', '.join(HELD_PREFIXES)} alerts are held")
        return 0
    if not 1 <= args.minutes <= MAX_OPEN_MIN:
        print(f"--minutes must be 1..{MAX_OPEN_MIN}")
        return 2
    w = open_manual(args.state_dir, args.minutes)
    print(f"deploy window open until {pd.Timestamp(w.until, unit='s', tz='UTC'):%H:%M:%S} UTC")
    return 0


if __name__ == "__main__":  # pragma: no cover
    import sys
    raise SystemExit(main(sys.argv[1:]))
