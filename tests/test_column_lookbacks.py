"""Every declared per-column lookback (goldbot.features.registry.COLUMN_LOOKBACKS) is proved by truncation (quant ruling
on PR #472, ADR 0104 4): perturbing every bar older than the declared lookback changes the column by less than 1 % of
its scale on every fixture seed, and perturbing every bar older than one bar less (the bar just inside included)
breaks that bound on some seed. Definition and fixture: goldbot/research/column_lookback.py."""
from __future__ import annotations

from collections.abc import Callable

import pytest

from goldbot.features.registry import COLUMN_LOOKBACKS, column_lookback
from goldbot.research import column_lookback as CL
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F

TFS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")


def _cases() -> list[tuple[str, str, str]]:
    prod = D.column_producers()
    out = []
    for col in sorted(set(F.SPEC.columns)):
        feat, kind = prod[col]
        if kind == D.CONTEXT:
            out.append((feat, col[len(D.CONTEXT) + 1:], "1d"))
        else:
            out.extend((feat, col, tf) for tf in TFS)
    return sorted(set(out))


def test_every_fs_v1_column_has_a_declared_lookback() -> None:
    for _, col, tf in _cases():
        assert column_lookback(col, tf) >= 1, (col, tf)
    assert set(COLUMN_LOOKBACKS) >= {c for _, c, _ in _cases()}


@pytest.mark.parametrize(("feature", "column", "tf"), _cases(), ids=lambda x: str(x))
def test_declared_lookback_passes_and_is_tight(feature: str, column: str, tf: str) -> None:
    lb = column_lookback(column, tf)
    assert CL.passes(feature, column, lb, tf), f"{column}@{tf}: perturbing bars older than {lb} moves it >= 1 %"
    assert CL.tight(feature, column, lb, tf), f"{column}@{tf}: {lb - 1} bars already pass: the lookback is not tight"


def _floor() -> dict[str, dict[str, int]]:
    import json
    from pathlib import Path
    return dict(json.loads((Path(__file__).parent / "fixtures" / "column_lookbacks_floor.json").read_text()))


def _shrunk(floor: dict[str, dict[str, int]], lookup: Callable[[str, str], int],
            rereview: dict[str, str]) -> list[str]:
    out = []
    for col, by_tf in floor.items():
        for tf, lb in by_tf.items():
            now = lookup(col, tf)
            if now < lb and not str(rereview.get(col, "")).strip():
                out.append(f"{col}@{tf}: {now} < signed {lb}")
    return out


def test_declared_lookbacks_never_shrink_without_a_quant_rereview() -> None:
    """ADR 0104 4: "Declared lookbacks never shrink without a quant re-review." The floor is the last quant-signed
    declaration (tests/fixtures/column_lookbacks_floor.json). A column may go below it only when
    `registry.COLUMN_LOOKBACK_REREVIEW` names it with the re-review reference; raising the floor follows a sign-off."""
    from goldbot.features.registry import COLUMN_LOOKBACK_REREVIEW
    floor = _floor()
    assert not _shrunk(floor, column_lookback, COLUMN_LOOKBACK_REREVIEW)
    assert set(floor) <= set(COLUMN_LOOKBACKS), "a signed column lost its declaration"
    # the guard bites: one bar less on one column fails unless that column carries a re-review marker
    def lower(col: str, tf: str) -> int:
        return column_lookback(col, tf) - (1 if col == "rsi14" else 0)
    assert _shrunk(floor, lower, {}) == [f"rsi14@{tf}: {floor['rsi14'][tf] - 1} < signed {floor['rsi14'][tf]}"
                                         for tf in floor["rsi14"]]
    assert not _shrunk(floor, lower, {"rsi14": "PR #NNN quant re-review 2026-12-01"})
