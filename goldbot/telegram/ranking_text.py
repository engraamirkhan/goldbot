"""The strategy ranking in the owner's Telegram reports (docs/design/trades-ux-2026-10-27.md §7.5, backlog 137; ADR
0053): the morning report's ranking section and the weekly digest's. The figures are goldbot/api/strategy_ranking.py's,
built from the same trade ledger as `GET /api/strategies/ranking`, so Telegram and the dashboard never disagree.
Reporting only: nothing here changes an order, a stop, a budget or a read.

Copy rules kept (§4, §7.4, §7.5): at most LINES_MAX lines; the heading line first; one line per family, "1. Opening
range · +0.03 R/trade at least (avg +0.12, 412 fills) · hit 48% · +£38.10 · vs random +0.10 R (interim) · early,
not proven"; the true minus; no tables; family names only (never an arm id, account slug, login or host); every row says
"early, not proven" until its read. The section is appended after the blinding guard of the lab lines
(labs.guard): ADR 0053 lifted blinding for the ranking, and a family named like an arm id ("NR4 break") must still show.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from goldbot.api import strategy_board, strategy_labels, trades_ledger
from goldbot.api.schema import RankingView, RankRow, RankStatus
from goldbot.api.strategy_ranking import MIN_DAYS, MIN_FILLS, Ranker, rank_moves
from goldbot.telegram.labs import money, pct, signed

__all__ = ["LINES_MAX", "LINK", "STATUS_WORDS", "daily_lines", "family_line", "load_ranker", "ranking_lines",
           "weekly", "weekly_lines"]

LINES_MAX = 12                      # §7.5: "≤ 12 lines"
TOP, BOTTOM, BOTTOM_FROM = 5, 2, 8  # the top 5; the 2 lowest when 8 or more are ranked
WEEK_TOP = 3
LINK = "Full ranking: dashboard › Trades › Strategies"
STATUS_WORDS: dict[RankStatus, str] = {
    "early": "early, not proven", "candidate": "candidate, not proven yet", "proven": "proven",
    "not_proven": "not proven", "retired": "retired at its stop"}


def load_ranker(state_dir: str | Path, now: pd.Timestamp) -> Ranker:
    """The ranking over the trade ledger as the API builds it (same sources, same catalog, same retired arms)."""
    led = trades_ledger.build_ledger(state_dir, now)
    retired, ok = strategy_board.retired_arms(state_dir, now)
    return Ranker(led.rows, strategy_labels.catalog(), now, retired=retired,
                  unreadable=[*led.unreadable, *([] if ok else ["arm status"])])


def _r(x: float) -> str:
    return signed(x, 2)


def family_line(row: RankRow, move: str | None = None) -> str:
    """One family on one line, ranked or not (an unranked row says why instead of a lower bound)."""
    f = row.figures
    head = f"{row.rank}. {row.family_label}" if row.rank is not None else row.family_label
    if move is not None and move != "same":
        head += f" ({move})"
    key = (f"{_r(f.lower_adjusted)} R/trade at least" if f.lower_adjusted is not None
           else row.unranked_reason or "not ranked")
    avg = _r(f.net_r_per_trade) if f.net_r_per_trade is not None else "—"
    parts = [head, f"{key} (avg {avg}, {f.n:,} fills)"]
    if f.hit_rate is not None:
        parts.append(f"hit {pct(f.hit_rate)}")
    if f.net_pnl is not None:
        parts.append(money(f.net_pnl, f.currency))
    if f.edge_vs_control_r is not None:
        parts.append(f"vs random {_r(f.edge_vs_control_r)} R ({f.edge_source})")
    parts.append(STATUS_WORDS[row.status])
    return " · ".join(parts)


def _counts(view: RankingView, shown: int) -> list[str]:
    """"3 more ranked · 2 too few to rank yet"; a zero part is left out, and the line with it (no "0 too few")."""
    ranked = sum(r.rank is not None for r in view.rows)
    few, rest = len(view.rows) - ranked, ranked - shown
    parts = [*([f"{rest} more ranked"] if rest else []), *([f"{few} too few to rank yet"] if few else [])]
    return [" · ".join(parts)] if parts else []


def ranking_lines(view: RankingView) -> list[str]:
    """The morning report's section: the heading, the top 5, the 2 lowest (8+ ranked), the counts and the link."""
    ranked = [r for r in view.rows if r.rank is not None]
    if not view.rows:
        return []
    out = [f"🏁 {view.heading}"]
    if not ranked:
        out.append(f"None ranked yet: a strategy needs {MIN_FILLS} fills on {MIN_DAYS} days for a place")
    top = ranked[:TOP]
    low = ranked[TOP:][-BOTTOM:] if len(ranked) >= BOTTOM_FROM else []
    out += [family_line(r) for r in top]
    if low:
        out.append("…")
        out += [family_line(r) for r in low]
    out += [*_counts(view, len(top) + len(low)), LINK]
    return out[:LINES_MAX]


def weekly_lines(week: RankingView, life: RankingView, before: RankingView) -> list[str]:
    """The weekly digest's section: the version's life ranking with its move since last week, then the week's own
    top families, the counts and the link."""
    if not life.rows and not week.rows:
        return []
    moves = rank_moves(life, before)
    ranked = [r for r in life.rows if r.rank is not None]
    out = [f"🏁 {life.heading}", "Since each version began (move since last week):"]
    out += [family_line(r, moves.get(r.key)) for r in ranked[:TOP]] or [
        f"None ranked yet: a strategy needs {MIN_FILLS} fills on {MIN_DAYS} days for a place"]
    week_ranked = [r for r in week.rows if r.rank is not None][:WEEK_TOP]
    if week_ranked:
        out.append("This week:")
        out += [family_line(r) for r in week_ranked]
    out += [*_counts(life, min(len(ranked), TOP)), LINK]
    return out[:LINES_MAX]


def daily_lines(state_dir: str | Path, day_end: pd.Timestamp) -> list[str]:
    """The morning report's ranking for the opportunity day ending `day_end`, over each version's life."""
    return ranking_lines(load_ranker(state_dir, day_end)("all"))


def weekly(state_dir: str | Path, week_end: pd.Timestamp) -> list[str]:
    """The weekly digest's ranking for the week ending `week_end` (Fri 22:00 UTC)."""
    now = load_ranker(state_dir, week_end)
    before = replace(now, as_of=week_end - pd.Timedelta(days=7), answers={})    # the same rows, a week earlier
    return weekly_lines(now("7d"), now("all"), before("all"))
