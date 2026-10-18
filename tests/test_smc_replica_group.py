"""D-TVR (research director, 2026-10-18; prereg-tv-replica-2026-10-18.md B4a item 2): the `smc_replica` columns are ONE
feature group in both discovery group maps, O-4 (H-14, 15m: family mode, `context_groups: per_timeframe`) and H-02
(4h: cluster mode, `H02_CLUSTERS`). Conditions pinned here: (i) one group each, so K_eff +1 per map and +2 in all, and
no registry trial; (ii) the group holds only replica columns, never a column already in `structure` (`bos_*`,
`sweep_*`); (iii) at most `max_selected` (39) columns; (iv) the replica runs on our own bars, never on charting-site
data (a TVR fail does not remove the group: features are not rules)."""
from __future__ import annotations

import ast
import inspect
from collections import Counter
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import resample_bars
from goldbot.features import FEATURES, smc_replica
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.research.discovery import (
    H02_CLUSTERS,
    DiscoveryConfig,
    check_group_sizes,
    column_groups,
)
from goldbot.research.pipeline import build_decision_frame, research_feature_names
from tests.test_count_owed_events import _bars_1m
from tests.test_group_size_before_preregister import O4, O4_GROUP_MODE

GROUP = smc_replica.FAMILY
H02: dict[str, Any] = dict(top_k=39, max_selected=39, survivor_unit="group")


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return _bars_1m("2023-01-02", "2023-02-06")


@pytest.fixture(scope="module")
def names() -> tuple[list[str], list[str]]:
    base = research_feature_names(None)
    smc_replica.enable()
    return base, [*base, smc_replica.NAME]


def _frame(b1: pd.DataFrame, tf: str, names_: list[str]) -> tuple[pd.DataFrame, list[str]]:
    b = resample_bars(b1, tf).reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs(tf)}
    m, X = build_decision_frame(b, context, feature_names=names_)
    return m, [c for c in X.columns if c != "ts_utc"]


def _o4(b1, names_):
    m, cols = _frame(b1, "15m", names_)
    return cols, column_groups(cols, O4_GROUP_MODE, m, names_, context_groups="per_timeframe")


def _h02(b1, names_):
    m, cols = _frame(b1, "4h", names_)
    return cols, column_groups(cols, "cluster", m, names_, clusters=H02_CLUSTERS)


@pytest.mark.parametrize("which", [_o4, _h02], ids=["O-4 15m", "H-02 4h"])
def test_the_replica_columns_form_exactly_one_group_in_each_discovery_map_and_nothing_else_joins_it(b1, names, which):
    base, with_replica = names
    cols, groups = which(b1, with_replica)
    members = {c for c, g in groups.items() if g == GROUP}
    assert members == set(smc_replica.COLUMNS)
    base_cols, base_groups = which(b1, base)
    assert set(cols) - set(base_cols) == members                          # the frame gained the replica columns only
    assert {c: g for c, g in groups.items() if c not in members} == base_groups   # no other column moved group


@pytest.mark.parametrize("which", [_o4, _h02], ids=["O-4 15m", "H-02 4h"])
def test_each_map_gains_exactly_one_group_so_k_eff_rises_by_one_per_map_and_by_two_in_all(b1, names, which):
    base, with_replica = names
    assert len(set(which(b1, with_replica)[1].values())) == len(set(which(b1, base)[1].values())) + 1


def test_the_group_fits_one_model_and_the_pinned_group_maps_still_pass_the_size_check(b1, names):
    _, with_replica = names
    _, g4 = _o4(b1, with_replica)
    assert Counter(g4.values())[GROUP] == len(smc_replica.COLUMNS) <= DiscoveryConfig(**O4).max_selected == 39
    check_group_sizes(g4, DiscoveryConfig(**O4, context_groups="per_timeframe"), O4_GROUP_MODE)
    check_group_sizes(_h02(b1, with_replica)[1], DiscoveryConfig(**H02), "cluster")


def test_the_group_holds_no_structure_column_and_no_replica_name_is_produced_by_another_feature(b1, names):
    base, with_replica = names
    _, groups = _o4(b1, with_replica)
    m, _ = _frame(b1, "15m", with_replica)
    tail = m.tail(500).reset_index(drop=True)
    others: set[str] = set()
    for n in base:
        try:
            others |= {str(c) for c in FEATURES[n].fn(tail, {}).columns}
        except Exception:     # a feature that needs an input this frame lacks
            continue
    assert not others & set(smc_replica.COLUMNS)
    structure = {c for c, g in groups.items() if g == "structure"}
    assert structure and not structure & set(smc_replica.COLUMNS)
    assert not any(c.startswith("sweep_") for c in smc_replica.COLUMNS)  # H-02's H-08 cluster glob


def test_the_replica_is_computed_from_our_bars_alone_with_no_charting_site_dependency():
    tree = ast.parse(inspect.getsource(smc_replica))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in imported if "tv_" in m or "replica_parity" in m or "requests" in m or "http" in m], imported
    assert list(inspect.signature(smc_replica.replica_frame).parameters)[:1] == ["df"]     # bars in, columns out


def test_research_decision_frame_builds_smc_replica_while_a_plain_tail_build_still_raises(b1, names):
    """The research frame is built on the whole series (declared FULL_HISTORY); a plain tail build is refused."""
    from goldbot.features.registry import build_features
    m, cols = _frame(b1, "1h", names[1])
    assert cols and len(m) > 100
    with pytest.raises(ValueError, match="needs a carried state"):
        build_features(m.tail(200).reset_index(drop=True), [smc_replica.NAME])
