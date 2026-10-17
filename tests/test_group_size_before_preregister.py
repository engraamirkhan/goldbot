"""BACKLOG 81: a discovery's group-size check runs before its pre-registration row is written, and it counts the
h1/h4/d1 context columns a 15m decision frame carries.

Design rule (quant ruling Q-3, re-review MEDIUM 4): "a whole group must fit one model" (<= max_selected columns besides
`side`), checked "before any look at the data"; a refused config writes no registry row and spends no trial. At 15m
the context columns of `trend` and `volatility` push those family groups past 39, so O-4 (preregistration-2027Q1.md,
"O-4 H-14 15m scalping discovery", amendment "One splitting rule (pinned now)") pre-registers
`context_groups: per_timeframe`: "every context column goes to the group `ctx_<tf>` of its timeframe (`ctx_h1`,
`ctx_h4`, `ctx_d1`), across families ... Any group still over 39 columns after this rule refuses the run; it is never
truncated or re-split after a count."
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.resample import resample_bars
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.features.registry import _CONTEXT_PREFIX
from goldbot.research.discovery import (
    BREAK_EVEN_RULE,
    DiscoveryConfig,
    check_group_sizes,
    column_groups,
    discovery_pass,
)
from goldbot.research.pipeline import build_decision_frame, research_feature_names
from goldbot.research.registry import TrialRegistry
from goldbot.specialists import SPECIALISTS
from tests.test_count_owed_events import _bars_1m

DEFAULT_VERSION = "f-079deacc61"        # O-4's feature set as first pinned: the default families
# O-4's config as pinned in preregistration-2027Q1.md ("O-4 H-14 15m scalping discovery"): top_k 39 = max_selected 39,
# survivor_unit group, group_top_k 3, stability_threshold 0.7, the tercile rule at q = 2/3 (linear), grouped by family;
# context_groups per_timeframe is the amendment's splitting rule (written into the pinned dump by the research-director)
O4: dict[str, Any] = dict(top_k=39, max_selected=39, survivor_unit="group", group_top_k=3, stability_threshold=0.7,
          threshold_rule="tercile_inner", tercile_q=2 / 3, tercile_method="linear")
O4_GROUP_MODE = "family"


@pytest.fixture(scope="module")
def b1() -> pd.DataFrame:
    return _bars_1m("2023-01-02", "2023-02-06")


@pytest.fixture(scope="module")
def frame_15m(b1: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """The 15m decision frame of the default features with its h1/h4/d1 context, as discovery_pass builds it."""
    b = resample_bars(b1, "15m").reset_index(drop=True)
    context = {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs("15m")}
    m, X = build_decision_frame(b, context)
    assert X.attrs["feature_version"] == DEFAULT_VERSION
    return m, [c for c in X.columns if c != "ts_utc"]


def test_context_groups_join_is_the_default_and_leaves_every_existing_dump_and_hash_unchanged():
    assert "context_groups" not in DiscoveryConfig().model_dump()
    assert "context_groups" not in DiscoveryConfig(**O4).model_dump()
    split = DiscoveryConfig(**O4, context_groups="per_timeframe")
    assert split.model_dump()["context_groups"] == "per_timeframe"
    assert DiscoveryConfig(**split.model_dump()) == split
    assert DiscoveryConfig().threshold_rule == BREAK_EVEN_RULE


def test_per_timeframe_puts_every_context_column_in_one_group_per_timeframe_across_groups():
    cols = ["a", "h1_a", "h4_a", "d1_a", "w1_a", "b", "h1_b"]
    assert column_groups(cols, "column", context_groups="per_timeframe") == {c: c for c in cols}
    clusters = {"C": ["a"]}
    assert column_groups(cols, "cluster", clusters=clusters, context_groups="per_timeframe") == {
        "a": "C", "h1_a": "ctx_h1", "h4_a": "ctx_h4", "d1_a": "ctx_d1", "w1_a": "ctx_w1", "b": "b", "h1_b": "ctx_h1"}
    assert column_groups(cols, "cluster", clusters=clusters) == {                       # join: as before
        "a": "C", "h1_a": "C", "h4_a": "C", "d1_a": "C", "w1_a": "C", "b": "b", "h1_b": "h1_b"}


def test_o4s_15m_group_map_has_no_group_over_39_with_the_context_columns_counted(frame_15m):
    m, cols = frame_15m
    cfg = DiscoveryConfig(**O4, context_groups="per_timeframe")
    names = research_feature_names(None)
    groups = column_groups(cols, O4_GROUP_MODE, m, names, context_groups=cfg.context_groups)
    assert set(groups) == set(cols)
    ctx_cols = [c for c in cols if _CONTEXT_PREFIX.match(c)]
    assert ctx_cols and all(groups[c] == f"ctx_{c[:2]}" for c in ctx_cols)
    sizes = Counter(groups.values())
    assert max(sizes.values()) <= cfg.max_selected, sizes
    check_group_sizes(groups, cfg, O4_GROUP_MODE)
    # K_eff (survivor_unit group) = groups screened: 11 families + ctx_h1, ctx_h4, ctx_d1 (26 columns each)
    assert len(sizes) == 14 and {g: n for g, n in sizes.items() if g.startswith("ctx_")} == {
        "ctx_h1": 26, "ctx_h4": 26, "ctx_d1": 26}
    joined = column_groups(cols, O4_GROUP_MODE, m, names)
    with pytest.raises(ValueError, match="trend has 72 columns"):
        check_group_sizes(joined, DiscoveryConfig(**O4), O4_GROUP_MODE)


def test_a_discovery_refused_for_group_size_writes_no_registry_row_and_spends_no_trial(tmp_path: Path,
                                                                                      b1: pd.DataFrame):
    reg = TrialRegistry(tmp_path / "registry.jsonl")
    reg.record(agent_id="earlier", family="session_open", config={"a": 1}, feature_version=DEFAULT_VERSION,
               rationale="an earlier trial", results={})
    before, n_eff = reg.path.read_text(), reg.n_trials_effective
    with pytest.raises(ValueError, match="trend has .* over max_selected 39"):
        discovery_pass(SPECIALISTS["session_open"](), b1, reg, budget_cap=20, reserved_setting=0,
                       cfg=DiscoveryConfig(**O4), group_mode=O4_GROUP_MODE)
    assert reg.path.read_text() == before
    assert reg.n_trials_effective == n_eff
