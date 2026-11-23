"""KnightsofGold weekly "RED BOX TARGETS" extractor (prereg-2026-11-23-kog-weekly-redbox.md section 2).

STRICT by design: it reads only the "RED BOX TARGETS" block of a KnightsofGold post titled exactly "THE KOG REPORT"
and published on a Sunday (UTC), and abstains on anything else. It never guesses: a side whose line does not match
the grammar below in full, or whose numbers fail the consistency checks, is abstained, and the abstain reason is
returned so it can be counted.

Grammar (case-insensitive, one setup per line, in the block that follows the header line):

    header  := "RED BOX TARGETS" <anything to end of line>          (exactly one header in the post)
    setup   := "BREAK" ("ABOVE" | "BELOW") PRICE "for" LIST [ "in extension of the move" ] ["."]
    LIST    := PRICE ( SEP PRICE )*          SEP := "," | "." | "&" | "and" | ", and"
    PRICE   := 4 digits with an optional decimal part

The block is the run of lines after the header up to the first non-blank line that is not a setup line. At most one
ABOVE and one BELOW line; a repeated side abstains both of its lines.

Consistency (per side, else that side abstains): ABOVE targets strictly increasing and all above the break level;
BELOW targets strictly decreasing and all below it; the ABOVE level above the BELOW level when both are present (else
both abstain); every number within +/-15 % of the mean of the break levels.

Input is the post's FIRST snapshot (earliest `tv_idea_versions.seen_utc`, or the back catalogue's original
description). Author notes (`tv_idea_updates`) are never input. No price data is read here; this module has no
outcome path, places no order and feeds no engine.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import Field

from goldbot.base import FrozenRecord

__all__ = ["AUTHOR", "EXTRACTOR_VERSION", "Extraction", "Setup", "extract", "is_weekly_report"]

AUTHOR = "KnightsofGold"
EXTRACTOR_VERSION = "kogw-1"
BAND = 0.15

_TITLE = "THE KOG REPORT"
_HEADER = re.compile(r"^\s*RED\s+BOX\s+TARGETS\b.*$", re.IGNORECASE)
_PRICE = r"\d{4}(?:\.\d+)?"
_SEP = r"(?:\s*,\s*and\s+|\s*,\s*|\s*&\s*|\s+and\s+|\.\s+)"
_SETUP = re.compile(
    rf"^\s*BREAK\s+(ABOVE|BELOW)\s+({_PRICE})\s+for\s+({_PRICE}(?:{_SEP}{_PRICE})*)"
    rf"(?:\s+in\s+extension\s+of\s+the\s+move)?\s*\.?\s*$",
    re.IGNORECASE)
_BREAK_START = re.compile(r"^\s*BREAK\s+(ABOVE|BELOW)\b", re.IGNORECASE)


class Setup(FrozenRecord):
    side: Literal["long", "short"]          # ABOVE -> long, BELOW -> short
    level: float
    targets: tuple[float, ...] = Field(min_length=1)


class Extraction(FrozenRecord):
    setups: tuple[Setup, ...] = ()
    abstain: tuple[str, ...] = ()           # reasons, one per abstained post or side

    @property
    def abstained_post(self) -> bool:
        return not self.setups


def is_weekly_report(author: str, title: str, published_utc: datetime) -> bool:
    """A KnightsofGold post titled exactly "THE KOG REPORT" (case, spaces and a trailing ':' ignored), published on a
    Sunday in UTC. Every other KoG post ("- Update", "- NFP", "- Weekly", mid-week reports) is out of scope."""
    norm = re.sub(r"\s+", " ", title).strip().rstrip(":").strip().upper()
    return author == AUTHOR and norm == _TITLE and published_utc.weekday() == 6


def _numbers(s: str) -> list[float]:
    return [float(x) for x in re.findall(_PRICE, s)]


def extract(text: str) -> Extraction:
    """The setups of the post's single RED BOX TARGETS block, or the abstain reasons."""
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines) if _HEADER.match(ln)]
    if not heads:
        return Extraction(abstain=("no_header",))
    if len(heads) > 1:
        return Extraction(abstain=("multiple_headers",))
    found: dict[str, list[tuple[float, list[float]]]] = {"ABOVE": [], "BELOW": []}
    reasons: list[str] = []
    for ln in lines[heads[0] + 1:]:
        if not ln.strip():
            continue
        m = _SETUP.match(ln)
        if m is None:
            b = _BREAK_START.match(ln)
            if b is not None:                       # a setup line we cannot read in full: abstain that side
                found[b.group(1).upper()].append((float("nan"), []))
                continue
            break
        found[m.group(1).upper()].append((float(m.group(2)), _numbers(m.group(3))))
    sides: dict[str, tuple[float, list[float]]] = {}
    for k, rows in found.items():
        if len(rows) > 1:
            reasons.append(f"{k.lower()}:repeated")
        elif len(rows) == 1:
            lvl, tg = rows[0]
            if lvl != lvl:                          # nan marker
                reasons.append(f"{k.lower()}:grammar")
            else:
                sides[k] = (lvl, tg)
    if not sides and not reasons:
        return Extraction(abstain=("empty_block",))
    if "ABOVE" in sides and "BELOW" in sides and not sides["ABOVE"][0] > sides["BELOW"][0]:
        return Extraction(abstain=(*reasons, "above_not_above_below"))
    mid = sum(v[0] for v in sides.values()) / len(sides) if sides else 0.0
    out: list[Setup] = []
    for k, (lvl, tg) in sides.items():
        seq = [lvl, *tg]
        mono = all(b > a for a, b in zip(seq, seq[1:])) if k == "ABOVE" else all(b < a for a, b in zip(seq, seq[1:]))
        if not mono:
            reasons.append(f"{k.lower()}:not_monotone")
            continue
        if any(abs(x - mid) > BAND * mid for x in seq):
            reasons.append(f"{k.lower()}:out_of_band")
            continue
        out.append(Setup(side="long" if k == "ABOVE" else "short", level=lvl, targets=tuple(tg)))
    out.sort(key=lambda s: s.side)
    return Extraction(setups=tuple(out), abstain=tuple(reasons))
