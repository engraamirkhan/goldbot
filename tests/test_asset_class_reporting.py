"""Owner directive 2026-11-25: "trading analysis must be broken down by instrument category, because a strategy can
perform differently on different instruments". One mapping from a symbol or arm id to its asset class
(`goldbot.ops.class_breakdown.asset_class_of`, read from config/instruments.yaml), one per-class figure builder and one split
flag (`goldbot.ops.class_breakdown`), read by every trading-performance report."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from goldbot.instruments import ASSET_CLASS_ORDER, UNKNOWN_CLASS
from goldbot.ops.class_breakdown import (
    MIN_SPLIT_FILLS,
    ClassFill,
    asset_class_of,
    class_figures,
    class_splits,
    class_voids,
)

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(("name", "cls"), [
    ("XAUUSD", "metal"),            # the instrument key
    ("XAUUSD+", "metal"),           # a broker's symbol (vantage)
    ("XAUUSD247", "metal"),         # a store / broker symbol
    ("COPPER-C", "metal"),          # a broker symbol with a dash is the broker's, not an arm suffix
    ("US500", "index"),
    ("EURUSD", "fx"),
    ("UST10Y", "bond"),
    ("BTCUSD", "crypto"),
    ("S0-SUGAR", "agri"),           # a non-gold exploration arm: its instrument suffix
    ("I0-XBRUSD", "energy"),
    ("S0-US500", "index"),
    ("S0", "metal"),                # a gold arm carries no suffix: its spec row names no instrument (XAUUSD)
    ("I2A", "metal"),
])
def test_a_symbol_broker_symbol_or_arm_id_maps_to_its_registry_asset_class(name: str, cls: str) -> None:
    assert asset_class_of(name) == cls


@pytest.mark.parametrize("name", ["NOT_A_SYMBOL", "S0-NOPE", "", None])
def test_an_unknown_name_is_unknown_never_gold(name: str | None) -> None:
    assert asset_class_of(name) == UNKNOWN_CLASS == "unknown"


def test_the_class_order_lists_every_registry_class_once_and_unknown_last() -> None:
    from goldbot.instruments import registry
    assert set(ASSET_CLASS_ORDER) >= {s.asset_class for s in registry().instruments.values()}
    assert ASSET_CLASS_ORDER[-1] == UNKNOWN_CLASS and len(set(ASSET_CLASS_ORDER)) == len(ASSET_CLASS_ORDER)


def test_no_module_but_the_registry_holds_a_symbol_to_asset_class_table() -> None:
    """One mapping: no dict literal anywhere else maps names to asset classes (they call asset_class_of)."""
    classes = set(ASSET_CLASS_ORDER)
    offenders = []
    for path in [*(ROOT / "goldbot").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Dict):
                vals = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
                if sum(v in classes for v in vals) >= 3:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == []


def _fills(family: str, cls: str, rs: list[float], *, risk: float = 10.0, days: int = 10) -> list[ClassFill]:
    return [ClassFill(family=family, asset_class=cls, pnl=round(r * risk, 2), r=r, currency="GBP", cluster=i % days)
            for i, r in enumerate(rs)]


def test_class_figures_give_net_trades_win_rate_money_per_trade_mean_r_and_t_per_class_and_family() -> None:
    fills = [*_fills("box", "metal", [1.0, -0.5, 0.5, 1.0]), *_fills("box", "fx", [-1.0, -1.0])]
    figs = class_figures(fills)
    metal = next(f for f in figs if f.family is None and f.asset_class == "metal")
    assert (metal.n, metal.net_pnl, metal.currency, metal.win_rate, metal.net_per_trade, metal.mean_r) == \
        (4, 20.0, "GBP", 0.75, 5.0, 0.5)
    assert metal.t is not None and metal.t > 0
    fx = next(f for f in figs if f.family == "box" and f.asset_class == "fx")
    assert (fx.n, fx.net_pnl, fx.win_rate, fx.mean_r) == (2, -20.0, 0.0, -1.0)
    assert fx.t is None                                     # zero spread: no t, never infinity
    assert [f.asset_class for f in figs if f.family is None] == ["metal", "fx"]     # registry class order


def test_money_is_never_summed_across_currencies_in_a_class() -> None:
    fills = [ClassFill("box", "metal", 10.0, 1.0, "GBP", 0), ClassFill("box", "metal", 10.0, 1.0, "USD", 1)]
    f = next(x for x in class_figures(fills) if x.family is None)
    assert (f.net_pnl, f.currency, f.net_per_trade, f.n, f.mean_r) == (None, None, None, 2, 1.0)


def test_a_family_positive_in_one_class_and_negative_in_another_is_flagged() -> None:
    n = MIN_SPLIT_FILLS
    figs = class_figures([*_fills("box", "metal", [0.6, -0.2] * n), *_fills("box", "fx", [-0.6, 0.2] * n),
                          *_fills("vwap", "metal", [0.3] * n * 2), *_fills("vwap", "fx", [0.1] * n * 2)])
    assert class_splits(figs) == ["box"]


def test_a_class_under_the_minimum_fills_never_flags_a_split() -> None:
    figs = class_figures([*_fills("box", "metal", [0.6, -0.2] * MIN_SPLIT_FILLS),
                          *_fills("box", "fx", [-1.0] * (MIN_SPLIT_FILLS - 1))])
    assert class_splits(figs) == []


def test_class_voids_reads_every_voided_label_and_every_n_evidence_void_and_tolerates_an_old_file() -> None:
    import sqlite3
    conn = sqlite3.connect(":memory:")
    assert class_voids(conn) == frozenset()                                 # an older aux.db: neither table
    conn.execute("CREATE TABLE exploration_label_void (candidate_id TEXT, replay_version TEXT, reason TEXT, "
                 "decided_ns INTEGER)")
    conn.execute("CREATE TABLE evidence_exclusions (client_order_id TEXT, scope TEXT, reason TEXT)")
    conn.executemany("INSERT INTO exploration_label_void VALUES (?, ?, 'non-gold on gold bars', 0)",
                     [("c1", "v1"), ("c1", "v2"), ("c2", "v2")])
    conn.executemany("INSERT INTO evidence_exclusions VALUES (?, ?, 'x')",
                     [("c3", '["n", "posterior"]'), ("c4", '["posterior"]')])
    assert class_voids(conn) == {"c1", "c2", "c3"}
