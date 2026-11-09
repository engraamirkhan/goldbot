"""Realised-field lint for EVAL-2 cards (method section 4, prereg 2026-11-09 section 4.2).

Method section 4: "Inputs at decision time only. A lint refuses any filter on a realised field (that filter turns the
5m null from -0.28 R to +0.21 R)." A realised field is known only once the trade has been filled or closed: realised
cost, slippage, overshoot, exit price/bar/reason, P&L, net/gross R, excess R, labels, hold length. Selecting trades on
one selects outcomes (stop-outs pay the overshoot, so low realised cost keeps the winners) and manufactures an edge
from a random null.

As implemented
  scope     only the selection parts of a card: any value under a key in SELECTION_KEYS (filters, gates, admission,
            regime, where, ...), at any depth. The statistic, reports and the exit rule may name realised fields.
  fields    every identifier in that subtree: mapping keys and every identifier inside a string value (so
            "overshoot_usd < 0.1" and {"field": "cost_r"} are both read).
  realised  an identifier whose snake-case tokens contain a realised marker (REALISED_TOKENS), start with a realised
            prefix (REALISED_PREFIXES), or equal a realised column (REALISED_NAMES, the sim's per-trade columns).
  allowed   identifiers starting with a decision-time prefix (deciding_, decision_, ex_ante_) and DECISION_TIME
            (the deciding cost and its terms, rt_over_stop) are never flagged: decision-time cost is allowed."""
from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from typing import Any

SELECTION_KEYS = frozenset({
    "filter", "filters", "gate", "gates", "admission", "regime", "regimes", "where", "when", "condition",
    "conditions", "select", "selection", "include", "exclude", "exclusions", "keep", "drop", "mask",
    "entry_filter", "entry_filters"})
REALISED_TOKENS = frozenset({
    "realised", "realized", "pnl", "profit", "overshoot", "slippage", "slip", "mfe", "mae", "outcome", "fill",
    "filled", "exit"})
REALISED_PREFIXES = ("actual_", "label_", "hold_", "swap_nights")
REALISED_NAMES = frozenset({
    "cost_r", "cost_usd", "net_r", "gross_r", "excess_r", "excess", "twin_mean_net_r", "ctrl_gross", "drift_adj",
    "r_multiple", "return_r", "win", "won"})
DECISION_TIME_PREFIXES = ("deciding_", "decision_", "ex_ante_")
DECISION_TIME = frozenset({
    "today_r", "bp_engine_r", "stress_cost", "stress_cost_r", "rt_over_stop", "rt_over_stop_max", "gate_cost_mult",
    "expected_gross_vs_deciding_cost", "atr_pin"})
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class RealisedFieldFilter(ValueError):
    """A card filters or gates on a realised trade field: refused (EVAL-2 inputs at decision time only)."""


def realised(name: str) -> bool:
    """True when `name` is a realised trade field (module docstring)."""
    n = name.lower()
    if n.startswith(DECISION_TIME_PREFIXES) or n in DECISION_TIME:
        return False
    return n in REALISED_NAMES or n.startswith(REALISED_PREFIXES) or bool(REALISED_TOKENS & set(n.split("_")))


def _idents(node: Any, path: str) -> Iterator[tuple[str, str]]:
    if isinstance(node, Mapping):
        for k, v in node.items():
            yield from ((m, path) for m in _IDENT.findall(str(k)))
            yield from _idents(v, f"{path}.{k}")
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _idents(v, f"{path}[{i}]")
    elif isinstance(node, str):
        yield from ((m, path) for m in _IDENT.findall(node))


def _selections(node: Any, path: str) -> Iterator[tuple[Any, str]]:
    if isinstance(node, Mapping):
        for k, v in node.items():
            p = f"{path}.{k}" if path else str(k)
            if str(k).lower() in SELECTION_KEYS:
                yield v, p
            else:
                yield from _selections(v, p)
    elif isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            yield from _selections(v, f"{path}[{i}]")


def lint_card(card: Mapping[str, Any]) -> list[str]:
    """One problem per realised field used in a filter or gate of `card`; empty = clean."""
    out: list[str] = []
    for sub, path in _selections(card, ""):
        for name, where in _idents(sub, path):
            if realised(name):
                msg = (f"{where}: '{name}' is a realised trade field; filters and gates use decision-time inputs "
                       f"only (EVAL-2 section 4)")
                if msg not in out:
                    out.append(msg)
    return out


def assert_decision_time(card: Mapping[str, Any]) -> None:
    """Raise RealisedFieldFilter listing every realised field a filter or gate of `card` uses."""
    problems = lint_card(card)
    if problems:
        raise RealisedFieldFilter("; ".join(problems))
