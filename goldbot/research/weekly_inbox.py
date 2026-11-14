"""The research director's inbox of weekly roll-up proposals (BACKLOG 216).

The Monday roll-up (state/aux/daily_review/weekly-<sunday>.json, goldbot.research.daily_review) proposes at most four
variants. The brain's `research_director` job is off without an Anthropic key, so the research director is the Mac
Claude session: scripts/weekly_proposals.sh runs this module on the brain over ssh, as the goldbot user.

  python -m goldbot.research.weekly_inbox list [--state-dir state] [--json]
      every roll-up with proposals that no consumer marked read, and the unreadable ones; reads only
  python -m goldbot.research.weekly_inbox mark weekly-<day>.json [--state-dir state] [--by research_director] < DECISION
      record the decision (stdin, never a command-line argument) through daily_review.mark_weekly_consumed; the first
      record stands. The feedback-loop check (goldbot/ops/checks/loop.py `proposals`) then passes for that roll-up.

Marking records that the proposals were read and what was decided; it runs nothing. A proposal the director wants to
test still needs its own pre-registration (registry trial, K counted) like any other. An unreadable or missing
roll-up is never marked. Exit codes: 0 ok, 1 nothing recorded (already marked), 2 refused or unreadable state.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from goldbot.base import FrozenRecord, UtcTimestamp, load_record_json
from goldbot.research.daily_review import (
    VariantProposal,
    WeeklyRollup,
    mark_weekly_consumed,
    review_root,
    weekly_consumed,
)

NAME = re.compile(r"weekly-\d{4}-\d{2}-\d{2}\.json")
DECISION_MAX_CHARS = 1000
DEFAULT_BY = "research_director"


class InboxItem(FrozenRecord):
    name: str
    week_ending: str
    generated_utc: UtcTimestamp
    proposals: list[VariantProposal]


def _load(state_dir: Path, name: str) -> WeeklyRollup:
    p = review_root(state_dir) / name
    return load_record_json(WeeklyRollup, p.read_text(encoding="utf-8"), source=p.name)


def unread(state_dir: str | Path) -> tuple[list[InboxItem], list[str]]:
    """(roll-ups with proposals not marked read, oldest first; names of roll-ups that cannot be read). Raises
    ValueError or OSError when consumed.json is unreadable."""
    sd = Path(state_dir)
    root = review_root(sd)
    if not root.is_dir():
        return [], []
    read = weekly_consumed(sd)
    items: list[InboxItem] = []
    bad: list[str] = []
    for p in sorted(root.glob("weekly-*.json")):
        if not NAME.fullmatch(p.name) or p.name in read:
            continue
        try:
            roll = _load(sd, p.name)
        except (ValueError, OSError):
            bad.append(p.name)
            continue
        if roll.proposals:
            items.append(InboxItem(name=p.name, week_ending=roll.week_ending, generated_utc=roll.generated_utc,
                                   proposals=list(roll.proposals)))
    return items, bad


def mark(state_dir: str | Path, name: str, *, decision: str, by: str, now: pd.Timestamp) -> bool:
    """Mark roll-up `name` read with `decision`; True when recorded, False when it was marked already. Raises
    ValueError for a bad name, an empty or over-long decision, or a roll-up that is missing or unreadable."""
    if not NAME.fullmatch(name):
        raise ValueError(f"not a weekly roll-up name: {name!r}")
    text = decision.strip()
    if not text:
        raise ValueError("an empty decision is not recorded")
    if len(text) > DECISION_MAX_CHARS:
        raise ValueError(f"decision longer than {DECISION_MAX_CHARS} characters")
    sd = Path(state_dir)
    if not (review_root(sd) / name).exists():
        raise ValueError(f"{name} does not exist")
    try:
        _load(sd, name)
    except (ValueError, OSError) as exc:
        raise ValueError(f"{name} is unreadable ({type(exc).__name__}): not marked") from exc
    return mark_weekly_consumed(sd, name, by=by, now=now, decision=text)


def render(items: Sequence[InboxItem], bad: Sequence[str]) -> str:
    if not items and not bad:
        return "no unread weekly proposals"
    lines: list[str] = []
    for it in items:
        lines.append(f"{it.name}  (week ending {it.week_ending}, written {it.generated_utc:%Y-%m-%d %H:%M} UTC)")
        for i, v in enumerate(it.proposals, 1):
            lines.append(f"  {i}. {v.title}: {v.rationale} [{v.cost}; {v.status}]")
    lines += [f"{n}  UNREADABLE: never marked; fix or remove the file" for n in bad]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.research.weekly_inbox", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list", help="unread roll-ups with proposals (read-only)")
    ls.add_argument("--state-dir", default="state")
    ls.add_argument("--json", action="store_true")
    mk = sub.add_parser("mark", help="mark a roll-up read; the decision is read from stdin")
    mk.add_argument("name")
    mk.add_argument("--state-dir", default="state")
    mk.add_argument("--by", default=DEFAULT_BY)
    a = ap.parse_args(argv)
    if a.cmd == "list":
        try:
            items, bad = unread(a.state_dir)
        except (ValueError, OSError) as exc:
            print(f"consumed.json unreadable ({type(exc).__name__}): nothing listed", file=sys.stderr)
            return 2
        if a.json:
            print(json.dumps({"unread": [json.loads(i.model_dump_json()) for i in items], "unreadable": bad}))
        else:
            print(render(items, bad))
        return 0
    try:
        done = mark(a.state_dir, a.name, decision=sys.stdin.read(), by=a.by, now=pd.Timestamp.now(tz="UTC"))
    except (ValueError, OSError) as exc:
        print(f"not marked: {exc}", file=sys.stderr)
        return 2
    print(f"{a.name}: {'marked read' if done else 'already marked; the first record stands'}")
    return 0 if done else 1


if __name__ == "__main__":
    raise SystemExit(main())
