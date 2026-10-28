from goldbot.research.autodisc import spec_hash
from goldbot.research.autodisc.contracts import EXIT_MENU, exit_key
from goldbot.research.autodisc.gen_combine import (
    DEFAULT_TIMEFRAMES,
    existing_family_names,
    filters_for,
    generate,
    grid_size,
)


def test_families_exclude_controls_and_meta():
    f = existing_family_names()
    assert "tsmom" in f and "box_break_close" in f
    assert not [n for n in f if n.endswith("_control") or n in ("random_bracket", "specialist", "blocked")]


def test_grid_is_finite_unique_deterministic_and_counted():
    fams = ["tsmom", "box_break_close"]
    a = list(generate(fams))
    assert len(a) == grid_size(2) == len({spec_hash(s) for s in a})
    assert [spec_hash(s) for s in a] == [spec_hash(s) for s in generate(fams)]
    assert {exit_key(s.exit) for s in a} == set(EXIT_MENU)
    assert {s.timeframe for s in a} == set(DEFAULT_TIMEFRAMES)


def test_macro_filters_only_on_4h_and_1d_and_at_most_one_filter():
    assert not any(f.kind == "macro" for f in filters_for("15m"))
    assert any(f.kind == "macro" for f in filters_for("4h"))
    for s in generate(["tsmom"], timeframes=("15m", "4h")):
        assert s.filter is None or getattr(s.filter, "kind", "") != "macro" or s.timeframe == "4h"
        assert s.family == "tsmom" and s.side_rule == "with"


def test_baselines_and_exit_menu():
    base = list(generate(["tsmom"], filters=False))
    assert len(base) == len(DEFAULT_TIMEFRAMES) * len(EXIT_MENU) and all(s.filter is None for s in base)


def test_default_grid_is_about_ten_thousand_or_more():
    assert grid_size(len(existing_family_names())) >= 10_000
