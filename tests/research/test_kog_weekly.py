"""The strict KoG weekly RED BOX TARGETS extractor (prereg-2026-11-23-kog-weekly-redbox.md section 2).
Synthetic text and levels only: no author text or author level lives in the repo."""
from __future__ import annotations

from datetime import UTC, datetime

from goldbot.research.kog_weekly import extract, is_weekly_report

BLOCK = """Intro paragraph mentioning 1000-1010 and break above 1234 loosely.

RED BOX TARGETS:

BREAK ABOVE 2010 for 2020, 2030 and 2045 in extension of the move
BREAK BELOW 1990 for 1980, 1975.5 and 1960 in extension of the move

Please like and follow 2000.
"""


def test_reads_both_sides_of_the_block_only() -> None:
    e = extract(BLOCK)
    assert [(s.side, s.level, s.targets) for s in e.setups] == [
        ("long", 2010.0, (2020.0, 2030.0, 2045.0)), ("short", 1990.0, (1980.0, 1975.5, 1960.0))]
    assert e.abstain == ()


def test_separator_variants_and_header_suffix() -> None:
    t = "RED BOX TARGETS - Intra-day:\nBREAK ABOVE 2010 for 2020. 2030 & 2045\nBREAK BELOW 1990 for 1980, and 1970.\n"
    e = extract(t)
    assert [s.targets for s in e.setups] == [(2020.0, 2030.0, 2045.0), (1980.0, 1970.0)]


def test_abstains_without_header_or_with_two() -> None:
    assert extract("BREAK ABOVE 2010 for 2020 and 2030").abstain == ("no_header",)
    assert extract(BLOCK + BLOCK).abstain == ("multiple_headers",)


def test_unreadable_line_abstains_that_side_only() -> None:
    t = "RED BOX TARGETS:\nBREAK ABOVE 2010 for 2020-30 region\nBREAK BELOW 1990 for 1980 and 1970\n"
    e = extract(t)
    assert [s.side for s in e.setups] == ["short"]
    assert e.abstain == ("above:grammar",)


def test_consistency_checks() -> None:
    assert "above:not_monotone" in extract("RED BOX TARGETS\nBREAK ABOVE 2010 for 2030, 2020\n").abstain
    assert "below:out_of_band" in extract(
        "RED BOX TARGETS\nBREAK ABOVE 2010 for 2020\nBREAK BELOW 1990 for 1980 and 1500\n").abstain
    assert extract("RED BOX TARGETS\nBREAK ABOVE 1990 for 2000\nBREAK BELOW 2010 for 2000\n").abstain == (
        "above_not_above_below",)
    assert extract("RED BOX TARGETS\nBREAK ABOVE 2010 for 2020\nBREAK ABOVE 2015 for 2025\n").abstain == (
        "above:repeated",)


def test_block_ends_at_first_other_line() -> None:
    t = "RED BOX TARGETS:\nBREAK ABOVE 2010 for 2020\nExpected range 1900 / 2100\nBREAK BELOW 1990 for 1980\n"
    assert [s.side for s in extract(t).setups] == ["long"]


def test_weekly_report_is_exact_title_on_sunday() -> None:
    sun, wed = datetime(2026, 11, 22, 18, tzinfo=UTC), datetime(2026, 11, 25, 18, tzinfo=UTC)
    assert is_weekly_report("KnightsofGold", "THE KOG REPORT ", sun)
    assert is_weekly_report("KnightsofGold", "the kog report:", sun)
    assert not is_weekly_report("KnightsofGold", "THE KOG REPORT", wed)
    assert not is_weekly_report("KnightsofGold", "THE KOG REPORT - Weekly", sun)
    assert not is_weekly_report("KnightsofGold", "THE KOG REPORT - Update", sun)
    assert not is_weekly_report("Goldviewfx", "THE KOG REPORT", sun)
