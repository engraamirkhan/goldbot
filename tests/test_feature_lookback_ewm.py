"""Every registered feature's `lookback` covers the 1 %-weight horizon of every EWM it uses (quant C-3 / R-a on PR
#472): dl-v1's tier check reads `lookback` decision bars back, so a lookback shorter than an EWM's memory would admit a
row whose input still carries an excluded month's prices at more than 1 % weight.

The horizon of an EWM with smoothing alpha is the smallest k with (1 - alpha)^k <= 0.01 (the weight left beyond lag k).
The scan is static (AST): direct `.ewm(alpha=1 / n | span=n)` calls and the technical helpers (`atr`, `rsi`, `adx`:
Wilder alpha 1/n, default 14; `ema`: span n), through module-level helper functions transitively. An argument the scan
cannot resolve to a number fails the test (extend the scan, never skip)."""
from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from goldbot.features import registry  # noqa: F401  (registers every feature)
from goldbot.features.registry import FEATURES

ROOT = Path(__file__).resolve().parents[1]
FEATURE_DIR = ROOT / "goldbot" / "features"
HELPERS = {"atr": ("wilder", 14), "rsi": ("wilder", 14), "adx": ("wilder", 14), "ema": ("span", None)}


def horizon(kind: str, n: float) -> int:
    a = 1.0 / n if kind == "wilder" else 2.0 / (n + 1)
    return math.ceil(math.log(0.01) / math.log(1.0 - a))


class _Mod:
    def __init__(self, tree: ast.Module, consts: dict[str, object]):
        self.tree = tree
        self.consts = consts
        self.funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def _value(node: ast.AST | None, env: dict[str, list[float]], consts: dict[str, object]) -> list[float] | None:
    """Every number `node` can take: a literal, a module constant, a loop variable over literals, max(), + - * /."""
    if node is None:
        return None
    try:
        v = ast.literal_eval(node)
        return [float(v)] if isinstance(v, (int, float)) else None
    except (ValueError, SyntaxError, TypeError):
        pass
    if isinstance(node, ast.Name):
        if node.id in env:
            return env[node.id]
        c = consts.get(node.id)
        if isinstance(c, (int, float)):
            return [float(c)]
        if isinstance(c, (tuple, list)) and all(isinstance(x, (int, float)) for x in c):
            return [float(max(c))]
        return None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("max", "min"):
        vals = [_value(a, env, consts) for a in node.args]
        if all(v is not None for v in vals):
            flat = [x for v in vals for x in v]  # type: ignore[union-attr]
            return [max(flat) if node.func.id == "max" else min(flat)]
        return None
    if isinstance(node, ast.BinOp):
        lv, rv = _value(node.left, env, consts), _value(node.right, env, consts)
        if lv is None or rv is None:
            return None
        ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b, ast.Mult: lambda a, b: a * b}
        op = ops.get(type(node.op))
        return [op(a, b) for a in lv for b in rv] if op else None
    return None


def _loop_env(fn: ast.AST, consts: dict[str, object]) -> dict[str, list[float]]:
    """Loop and comprehension variables bound to a literal sequence: every value they take."""
    env: dict[str, list[float]] = {}
    for n in ast.walk(fn):
        tgt, it = (n.target, n.iter) if isinstance(n, (ast.For, ast.comprehension)) else (None, None)
        if isinstance(tgt, ast.Name) and it is not None:
            try:
                seq = ast.literal_eval(it)
            except (ValueError, SyntaxError, TypeError):
                seq = consts.get(it.id) if isinstance(it, ast.Name) else None
            if isinstance(seq, (tuple, list)) and seq and all(isinstance(x, (int, float)) for x in seq):
                env[tgt.id] = [float(x) for x in seq]
    return env


def ewm_horizons(mod: _Mod, fn: ast.FunctionDef, seen: frozenset[str] = frozenset()) -> tuple[list[int], list[str]]:
    env = _loop_env(fn, mod.consts)
    hs: list[int] = []
    bad: list[str] = []
    for c in ast.walk(fn):
        if not isinstance(c, ast.Call):
            continue
        f = c.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        if name in HELPERS:
            kind, default = HELPERS[name]
            arg = c.args[1] if len(c.args) > 1 else next((k.value for k in c.keywords if k.arg == "n"), None)
            vals = _value(arg, env, mod.consts) if arg is not None else ([float(default)] if default else None)
            if vals is None:
                bad.append(ast.unparse(c))
            else:
                hs += [horizon(kind, v) for v in vals]
        elif name == "ewm":
            for k in c.keywords:
                if k.arg == "alpha" and isinstance(k.value, ast.BinOp) and isinstance(k.value.op, ast.Div):
                    vals = _value(k.value.right, env, mod.consts)
                    kind = "wilder"
                elif k.arg == "span":
                    vals, kind = _value(k.value, env, mod.consts), "span"
                elif k.arg in ("alpha", "com", "halflife"):
                    vals, kind = None, ""
                else:
                    continue
                if vals is None:
                    bad.append(ast.unparse(c))
                else:
                    hs += [horizon(kind, v) for v in vals]
        elif name in mod.funcs and name not in seen and name not in HELPERS:
            sub, sb = ewm_horizons(mod, mod.funcs[name], seen | {fn.name})
            hs += sub
            bad += sb
    return hs, bad


def _scan() -> list[tuple[str, int, int, list[str]]]:
    out = []
    for p in sorted(FEATURE_DIR.glob("*.py")):
        if p.name in ("technical_helpers.py",):
            continue
        tree = ast.parse(p.read_text())
        consts: dict[str, object] = {}
        for n in tree.body:
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
                try:
                    consts[n.targets[0].id] = ast.literal_eval(n.value)
                except (ValueError, SyntaxError, TypeError):
                    pass
        mod = _Mod(tree, consts)
        for fn in mod.funcs.values():
            for d in fn.decorator_list:
                if not (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "feature"):
                    continue
                if any(k.arg == "legacy" for k in d.keywords):
                    continue
                name = ast.literal_eval(d.args[0])
                hs, bad = ewm_horizons(mod, fn)
                out.append((name, FEATURES[name].lookback, max(hs, default=0), bad))
    return out


def test_horizon_formula() -> None:
    assert horizon("wilder", 14) == 63          # (13/14)^63 <= 0.01 < (13/14)^62
    assert horizon("wilder", 100) == 459
    assert 0.99 ** 459 <= 0.01 < 0.99 ** 458


@pytest.mark.parametrize("row", _scan(), ids=lambda r: r[0])
def test_registry_lookback_covers_every_ewm(row: tuple[str, int, int, list[str]]) -> None:
    name, lookback, need, unresolved = row
    assert not unresolved, f"{name}: EWM spans the scan cannot resolve: {unresolved}"
    assert lookback >= need, f"{name}: lookback {lookback} < the 1 %-weight horizon {need} of an EWM it uses"
