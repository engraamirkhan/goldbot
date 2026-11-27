"""Every declared per-column lookback (goldbot.features.registry.COLUMN_LOOKBACKS) is proved by truncation (quant ruling
on PR #472, ADR 0104 4): perturbing every bar older than the declared lookback changes the column by less than 1 % of
its scale on every fixture seed, and perturbing every bar older than one bar less (the bar just inside included)
breaks that bound on some seed. Definition and fixture: goldbot/research/column_lookback.py."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from goldbot.features.registry import COLUMN_LOOKBACKS, column_lookback
from goldbot.research import column_lookback as CL
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F

TFS = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")


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


FLOOR = Path(__file__).parent / "fixtures" / "column_lookbacks_floor.json"
REF = re.compile(r"^(PR #\d+|ADR \d{4})\b")


def _floor(text: str | None = None) -> dict[str, dict[str, int]]:
    return dict(json.loads(FLOOR.read_text() if text is None else text))


def guard(floor: dict[str, dict[str, int]], lookup: Callable[[str, str], int],
          markers: dict[str, dict[str, Any]]) -> list[str]:
    """The no-shrink rule (code review LOW-3 on PR #478): a declared lookback below the floor needs a marker whose
    signed value at that tf equals the declaration; a marker needs a PR/ADR reference and a signed value for every
    floor tf equal to the declaration; a marker with no value below the floor is stale."""
    out = []
    for col, mk in markers.items():
        if col not in floor:
            out.append(f"{col}: marker for a column with no signed floor")
            continue
        if not REF.match(str(mk.get("ref", ""))):
            out.append(f"{col}: marker reference {mk.get('ref')!r} is not 'PR #N' or 'ADR NNNN'")
        signed = mk.get("signed") or {}
        if set(signed) != set(floor[col]):
            out.append(f"{col}: marker must sign every floor tf {sorted(floor[col])}")
            continue
        for tf, v in signed.items():
            if lookup(col, tf) != v:
                out.append(f"{col}@{tf}: declared {lookup(col, tf)} != the newly signed {v}")
        if not any(v < floor[col][tf] for tf, v in signed.items()):
            out.append(f"{col}: stale marker (no signed value below the floor)")
    for col, by_tf in floor.items():
        for tf, lb in by_tf.items():
            now = lookup(col, tf)
            if now < lb and (markers.get(col, {}).get("signed") or {}).get(tf) != now:
                out.append(f"{col}@{tf}: {now} < signed {lb} without a quant re-review marker")
    return out


def test_declared_lookbacks_never_shrink_without_a_quant_rereview() -> None:
    """ADR 0104 4: "Declared lookbacks never shrink without a quant re-review." The floor is the last quant-signed
    declaration; a shrink needs a marker in `registry.COLUMN_LOOKBACK_REREVIEW` (`guard`)."""
    from goldbot.features.registry import COLUMN_LOOKBACK_REREVIEW
    floor = _floor()
    assert not guard(floor, column_lookback, COLUMN_LOOKBACK_REREVIEW)
    assert set(floor) <= set(COLUMN_LOOKBACKS), "a signed column lost its declaration"


def test_the_guard_bites() -> None:
    floor = _floor()
    tfs = sorted(floor["rsi14"])

    def lower(col: str, tf: str) -> int:
        return column_lookback(col, tf) - (1 if col == "rsi14" else 0)

    new = {tf: floor["rsi14"][tf] - 1 for tf in tfs}
    assert len(guard(floor, lower, {})) == len(tfs)                                   # unmarked shrink
    assert not guard(floor, lower, {"rsi14": {"ref": "PR #999", "signed": new}})      # marked: allowed
    assert guard(floor, lower, {"rsi14": {"ref": "approved", "signed": new}})         # bad reference
    assert guard(floor, lower, {"rsi14": {"ref": "PR #999", "signed": {tfs[0]: new[tfs[0]]}}})   # not every tf
    assert guard(floor, lower, {"rsi14": {"ref": "PR #999",
                                          "signed": {tf: v - 1 for tf, v in new.items()}}})     # != declaration
    assert guard(floor, column_lookback, {"rsi14": {"ref": "ADR 0104", "signed": dict(floor["rsi14"])}})  # stale


def test_the_floor_is_pinned_in_adr_0104() -> None:
    """LOW-3: the floor changes only with the ADR: its sha256 is recorded in ADR 0104 (works on CI's shallow
    checkout, where the base branch may be absent)."""
    sha = hashlib.sha256(FLOOR.read_bytes()).hexdigest()
    adr = (Path(__file__).resolve().parents[1] / "docs" / "decisions" / "0104-dense-labels-dl-v1.md").read_text()
    assert any(sha in line and "floor" in line for line in adr.splitlines()), \
        f"record the floor's sha256 {sha} in ADR 0104 (a floor change is a quant-signed change)"


def test_the_floor_only_rises_against_the_base_branch() -> None:
    """LOW-3: against the base branch (GITHUB_BASE_REF, else origin/main), no floor value goes down unless a marker
    signs exactly the new value. Skipped only when the base is not in the checkout; the ADR pin holds regardless."""
    root = Path(__file__).resolve().parents[1]
    base = f"origin/{os.environ.get('GITHUB_BASE_REF') or 'main'}"
    r = subprocess.run(["git", "show", f"{base}:tests/fixtures/{FLOOR.name}"], cwd=root, capture_output=True,
                       text=True)
    if r.returncode != 0:
        pytest.skip(f"{base} has no floor file in this checkout")
    from goldbot.features.registry import COLUMN_LOOKBACK_REREVIEW
    old, new = _floor(r.stdout), _floor()
    down = [f"{c}@{tf}: {old[c][tf]} -> {new.get(c, {}).get(tf)}" for c in old for tf in old[c]
            if new.get(c, {}).get(tf, -1) < old[c][tf]
            and (COLUMN_LOOKBACK_REREVIEW.get(c, {}).get("signed") or {}).get(tf) != new.get(c, {}).get(tf)]
    assert not down, f"the signed floor went down without a re-review marker: {down}"
