"""Realised-field lint for EVAL-2 cards (method section 4, prereg 2026-11-09 section 4.2).

Method section 4: "Inputs at decision time only. A lint refuses any filter on a realised field (that filter turns the
5m null from -0.28 R to +0.21 R)." A realised field is known only once the trade has been filled or closed: realised
cost, slippage, overshoot, exit price/bar/reason, P&L, net/gross R, excess R, labels, hold length. Selecting trades on
one selects outcomes (stop-outs pay the overshoot, so low realised cost keeps the winners) and manufactures an edge
from a random null.

As implemented (fail closed: an identifier is refused unless it is clearly decision-time)
  scope     the whole card except the output parts: every key not in OUTPUT_KEYS (statistic, report, exit, notes,
            ...), at any depth. Filters, gates, entry triggers, regimes and sizing are all linted.
  fields    every identifier in scope: mapping keys and every identifier inside a string value (so
            "overshoot_usd < 0.1" and {"field": "cost_r"} are both read).
  realised  an identifier whose snake-case tokens contain a realised marker (REALISED_TOKENS: realised cost, exit,
            P&L, forward/next/lookahead returns, hits, holds, wins), start with a realised prefix
            (REALISED_PREFIXES), or equal a realised column (REALISED_NAMES, the sim's per-trade columns).
  allowed   only DECISION_TIME (the deciding cost and its terms, rt_over_stop, the admission ratio). A decision-time
            prefix (deciding_, decision_, ex_ante_) is stripped and the remainder is tested: decision_exit_price is
            still refused."""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

OUTPUT_KEYS = frozenset({
    "statistic", "statistics", "report", "reports", "exit", "exits", "output", "outputs", "notes", "rationale",
    "reading_rule"})
REALISED_TOKENS = frozenset({
    "realised", "realized", "pnl", "profit", "overshoot", "slippage", "slip", "mfe", "mae", "outcome", "fill",
    "filled", "exit", "future", "fwd", "forward", "lead", "next", "lookahead", "hit", "held", "duration", "return",
    "ret", "gross", "net", "win", "loss", "winner", "loser", "stopped", "payoff"})
REALISED_PREFIXES = ("actual_", "label_", "hold_", "swap_nights")
REALISED_NAMES = frozenset({
    "cost_r", "cost_usd", "net_r", "gross_r", "excess_r", "excess", "twin_mean_net_r", "ctrl_gross", "drift_adj",
    "r_multiple", "return_r", "win", "won"})
DECISION_TIME_PREFIXES = ("deciding_", "decision_", "ex_ante_")
DECISION_TIME = frozenset({
    "deciding_cost", "deciding_cost_r", "today_r", "bp_engine_r", "stress_cost", "stress_cost_r", "rt_over_stop",
    "rt_over_stop_max", "gate_cost_mult", "expected_gross_vs_deciding_cost", "atr_pin"})
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class RealisedFieldFilter(ValueError):
    """A card filters or gates on a realised trade field: refused (EVAL-2 inputs at decision time only)."""


def realised(name: str) -> bool:
    """True when `name` is (or may be) a realised trade field (module docstring)."""
    n = name.lower()
    if n in DECISION_TIME:
        return False
    for pre in DECISION_TIME_PREFIXES:
        if n.startswith(pre):
            n = n[len(pre):]
            break
    return n in REALISED_NAMES or n.startswith(REALISED_PREFIXES) or bool(REALISED_TOKENS & set(n.split("_")))


def _idents(node: Any, path: str) -> Iterator[tuple[str, str]]:
    """Every identifier in `node` with its path, skipping OUTPUT_KEYS subtrees (key included)."""
    if isinstance(node, Mapping):
        for k, v in node.items():
            if str(k).lower() in OUTPUT_KEYS:
                continue
            p = f"{path}.{k}" if path else str(k)
            yield from ((m, p) for m in _IDENT.findall(str(k)))
            yield from _idents(v, p)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _idents(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield from ((m, path) for m in _IDENT.findall(node))


def lint_card(card: Mapping[str, Any]) -> list[str]:
    """One problem per realised field used outside the output parts of `card`; empty = clean."""
    out: list[str] = []
    for name, where in _idents(card, ""):
        if realised(name):
            msg = (f"{where}: '{name}' is a realised trade field; filters, gates and triggers use decision-time "
                   f"inputs only (EVAL-2 section 4)")
            if msg not in out:
                out.append(msg)
    return out


def assert_decision_time(card: Mapping[str, Any]) -> None:
    """Raise RealisedFieldFilter listing every realised field a filter or gate of `card` uses."""
    problems = lint_card(card)
    if problems:
        raise RealisedFieldFilter("; ".join(problems))
