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
    res = check_novelty("", c.entry_mask, c.daily_r, refs, known_names=frozenset({"tsmom"}))
    assert res.novel and res.label == "novel"
    assert res.jaccard is not None and res.jaccard < 0.30 and res.corr is not None and res.corr < 0.5


def test_correlated_daily_returns_make_a_variant_even_with_disjoint_entries():
    a = ref("tsmom", 2)
    mask = ~a.entry_mask & (np.random.default_rng(4).random(N) < 0.2)
    res = check_novelty("", mask, a.daily_r * 2 + 0.01, [a], known_names=frozenset())
    assert not res.novel and res.reason == "correlated" and res.nearest == "tsmom"


def test_filtered_subset_of_parent_is_a_variant():
    p = ref("tsmom", 2, p=0.3)
    sub = p.entry_mask & (np.random.default_rng(5).random(N) < 0.6)
    res = check_novelty("tsmom", sub, p.daily_r, [p])
    assert not res.novel and res.nearest == "tsmom"


def test_named_family_without_reference_is_not_novel():
    res = check_novelty("tsmom", np.ones(N, bool), np.zeros(D), [], known_names=frozenset({"tsmom"}))
    assert not res.novel and res.reason == "family"


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


def test_any_g1_family_spec_is_a_variant_whatever_its_overlap():
    p = ref("tsmom", 2, p=0.3)
    monday = np.zeros(N, bool)
    monday[::50] = p.entry_mask[::50]                     # a thin subset: Jaccard far below 0.30
    res = check_novelty("tsmom", monday, np.random.default_rng(9).normal(0, 1, D), [p], known_names=frozenset({"tsmom"}))
    assert not res.novel and res.label == "variant_of:tsmom" and res.reason == "family"
    free = check_novelty("brand_new_family", monday, np.random.default_rng(9).normal(0, 1, D), [p],
                         known_names=frozenset({"tsmom"}))
    assert not free.novel and free.nearest == "brand_new_family"
