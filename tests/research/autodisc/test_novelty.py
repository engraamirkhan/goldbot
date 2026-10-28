import numpy as np

from goldbot.research.autodisc.novelty import (
    Reference,
    check_novelty,
    existing_names,
    incremental_alpha_t,
    jaccard,
    variant_advances,
)

rng = np.random.default_rng(0)
N, D = 3000, 300


def ref(name, seed, p=0.2):
    r = np.random.default_rng(seed)
    return Reference(name, r.random(N) < p, r.normal(0, 1, D))


def test_existing_arm_as_spec_is_variant_of_that_arm():
    parent = ref("box_break_close", 1)
    other = ref("tsmom", 2)
    res = check_novelty("box_break_close", parent.entry_mask.copy(), parent.daily_r.copy(), [other, parent])
    assert not res.novel and res.nearest == "box_break_close" and res.label == "variant_of:box_break_close"
    assert res.jaccard == 1.0


def test_independent_spec_is_novel():
    refs = [ref("tsmom", 2), ref("nr4_break", 3)]
    c = ref("candidate", 99)
    res = check_novelty("candidate_unknown", c.entry_mask, c.daily_r, refs, known_names=frozenset({"tsmom"}))
    assert res.novel and res.label == "novel"
    assert res.jaccard is not None and res.jaccard < 0.30 and res.corr is not None and res.corr < 0.5


def test_correlated_daily_returns_make_a_variant_even_with_disjoint_entries():
    a = ref("tsmom", 2)
    mask = ~a.entry_mask & (np.random.default_rng(4).random(N) < 0.2)
    res = check_novelty("x", mask, a.daily_r * 2 + 0.01, [a], known_names=frozenset())
    assert not res.novel and res.reason == "correlated" and res.nearest == "tsmom"


def test_filtered_subset_of_parent_is_a_variant():
    p = ref("tsmom", 2, p=0.3)
    sub = p.entry_mask & (np.random.default_rng(5).random(N) < 0.6)
    res = check_novelty("tsmom", sub, p.daily_r, [p])
    assert not res.novel and res.nearest == "tsmom"


def test_named_family_without_reference_is_not_novel():
    res = check_novelty("tsmom", np.ones(N, bool), np.zeros(D), [], known_names=frozenset({"tsmom"}))
    assert not res.novel and res.reason == "named"


def test_existing_names_cover_rules_and_arm_ids():
    n = existing_names()
    assert {"tsmom", "box_break_close", "W1A", "S1A"} <= n and "blocked" not in n


def test_jaccard_and_incremental_alpha():
    a = np.array([1, 1, 0, 0], bool)
    assert jaccard(a, a) == 1.0 and jaccard(a, ~a) == 0.0 and jaccard(a & ~a, a & ~a) == 0.0
    parent = rng.normal(0, 1, 500)
    same = parent * 0.9 + rng.normal(0, 0.05, 500)
    plus = parent * 0.9 + 0.5 + rng.normal(0, 0.5, 500)
    assert not variant_advances(same, parent)
    assert incremental_alpha_t(plus, parent) > 2 and variant_advances(plus, parent)
