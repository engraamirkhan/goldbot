"""H-06 macro-conditioned slow TSMOM (docs/research/preregistration-2027Q1.md, BACKLOG 48): H-01's candidates kept only
when the 20-day change of DFII10 and of the broad dollar both agree with the side (falling for longs, rising for
shorts), from `macro-v1` via `asof_join` on `available_utc` as known at the signal bar's close; a missing value drops
the candidate. Added as the tsmom preset `slow_macro` without touching `slow` (H-01) or tsmom's default."""
import ast
import inspect
import textwrap

import numpy as np
import pandas as pd
import pytest

from goldbot.data.macro import fred_frame
from goldbot.data.resample import resample_bars, ticks_to_1m
from goldbot.data.synthetic import synthetic_ticks
from goldbot.features.mtf import TF_LABEL, context_tfs
from goldbot.research.pipeline import build_decision_frame, prepare
from goldbot.specialists.time_series_momentum import TimeSeriesMomentumSpecialist as TSMOM

H01_ID = "tsmom-g0-c12c9afb24"       # the H-01 agent id pinned in the pre-registration
H06_ID = "tsmom-g0-fed4baec9f"       # the H-06 preset; a change here is a different trial


def slow(**overrides) -> TSMOM:
    return TSMOM(**{**TSMOM.presets["slow"], **overrides})


def h06(**overrides) -> TSMOM:
    return TSMOM(**{**TSMOM.presets["slow_macro"], **overrides})


def _context(b1: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {TF_LABEL[x]: resample_bars(b1, x) for x in context_tfs("4h")}


def synthetic_macro(seed: int = 7, start: str = "2021-06-01", end: str = "2024-01-31") -> pd.DataFrame:
    """DFII10 (daily, next-business-day stamp) and DTWEXBGS (weekly H.10 stamp) random walks in the macro-v1 schema."""
    rng = np.random.default_rng(seed)
    vd = pd.bdate_range(start, end)
    ry = 0.5 + np.cumsum(0.04 * rng.standard_normal(len(vd)))
    usd = 115 * np.exp(np.cumsum(0.003 * rng.standard_normal(len(vd))))
    vint = pd.Timestamp("2024-03-01")
    return pd.concat([fred_frame("DFII10", pd.DataFrame({"value_date": vd, "value": ry}), vint),
                      fred_frame("DTWEXBGS", pd.DataFrame({"value_date": vd, "value": usd}), vint)],
                     ignore_index=True)


@pytest.fixture(scope="module")
def two_years() -> pd.DataFrame:
    return ticks_to_1m(synthetic_ticks("2022-01-01", "2024-01-01", ticks_per_minute=1, seed=21))


@pytest.fixture(scope="module")
def frame(two_years):
    dec = resample_bars(two_years, "4h").reset_index(drop=True)
    context = _context(two_years)
    m, X = build_decision_frame(dec, context)
    return dec, context, m, X


def expected_changes(macro: pd.DataFrame, at: pd.Timestamp) -> tuple[float, float]:
    """Independent reading: of the observations public at `at` (available_utc <= at, first release per value_date),
    the last value minus the value 20 observations before it (the dollar as a ratio - 1); NaN with fewer than 21."""
    out = []
    for sid in ("DFII10", "DTWEXBGS"):
        s = macro[(macro["series_id"] == sid) & (pd.to_datetime(macro["available_utc"], utc=True) <= at)]
        s = s.sort_values("available_utc").drop_duplicates("value_date").sort_values("value_date")
        v = s["value"].to_numpy(dtype=float)
        if len(v) < 21:
            out.append(np.nan)
        else:
            out.append(v[-1] - v[-21] if sid == "DFII10" else v[-1] / v[-21] - 1)
    return out[0], out[1]


# --------------------------------------------------------------------------- configuration and agent id
def test_slow_macro_preset_is_h01_plus_the_macro_gate_and_nothing_else():
    s, g = slow(), h06()
    assert TSMOM.presets["slow_macro"] == {**TSMOM.presets["slow"], "macro_gate": True}
    assert {k: v for k, v in g.config.items() if k != "macro_gate"} == s.config and g.config["macro_gate"] is True
    assert g.label_spec == s.label_spec and g.walkforward == s.walkforward and g.timeframe == "4h"
    assert g.model_features == s.model_features                    # "H-01 with ...": the same declared inputs


def test_h06_agent_id_is_pinned_and_h01_and_the_default_ids_are_unchanged():
    assert slow().agent_id == H01_ID
    assert TSMOM().agent_id == "tsmom-g0-d5e1b0ac6f"                # tsmom's default before H-01 and H-06
    assert h06().agent_id == H06_ID
    assert TSMOM(identity=h06().identity).agent_id == H06_ID        # rebuilt from its identity
    # an off switch changes nothing, so it cannot make a second id for H-01's rule
    assert slow(macro_gate=None).agent_id == slow(macro_gate=False).agent_id == H01_ID


# --------------------------------------------------------------------------- candidate generation
def test_gate_keeps_exactly_the_h01_candidates_whose_real_yield_and_dollar_changes_agree_with_the_side(frame):
    dec, context, m, X = frame
    macro = synthetic_macro()
    base = slow().candidates_in_context(m, X, context)
    gated = h06().candidates_in_context(m, X, context, ctx={"macro": macro})
    assert len(base) > 50
    close = pd.DatetimeIndex(m["visible_at"])
    keep = []
    for i, side in zip(base["idx"], base["side"], strict=True):
        ry, usd = expected_changes(macro, close[i])
        keep.append((side == 1 and ry < 0 and usd < 0) or (side == -1 and ry > 0 and usd > 0))
    expected = base[np.array(keep)].reset_index(drop=True)
    assert 5 < len(expected) < len(base)                           # the gate binds and keeps some of both
    pd.testing.assert_frame_equal(gated[["idx", "side"]].reset_index(drop=True), expected, check_dtype=False)
    assert set(gated["side"]) == {-1, 1}


def test_macro_agreement_is_the_change_known_at_the_signal_bars_close_not_its_open(frame):
    _, _, m, _ = frame
    macro = synthetic_macro()
    close = pd.DatetimeIndex(m["visible_at"])
    # the next DFII10 value made public exactly at one decision bar's close: seen by that bar, not by the one before
    i = int(np.flatnonzero(close > pd.Timestamp("2023-03-01", tz="UTC"))[0])
    stamp = close[i]
    avail = pd.DatetimeIndex(pd.to_datetime(macro["available_utc"], utc=True))
    nxt = int(np.flatnonzero(np.asarray((macro["series_id"] == "DFII10") & (avail > stamp)))[0])
    early = macro.copy()
    early.loc[nxt, ["available_utc", "ts_utc"]] = stamp
    moved = h06().macro_agreement(m, early)
    plain = h06().macro_agreement(m, macro)
    assert moved["real_yield_chg20"].iloc[i] != plain["real_yield_chg20"].iloc[i]
    np.testing.assert_allclose(moved["real_yield_chg20"].iloc[:i], plain["real_yield_chg20"].iloc[:i], equal_nan=True)
    for j in (i - 1, i, i + 40):
        np.testing.assert_allclose(plain.iloc[j].to_numpy(dtype=float), expected_changes(macro, close[j]),
                                   equal_nan=True)


def test_a_missing_macro_value_drops_the_candidate_and_no_macro_table_is_refused(frame):
    dec, context, m, X = frame
    macro = synthetic_macro()
    base = slow().candidates_in_context(m, X, context)
    close = pd.DatetimeIndex(m["visible_at"])[base["idx"]]
    gap_from, gap_to = close[len(close) // 3], close[2 * len(close) // 3]
    # DFII10 starts only after the gap's end: every candidate before then has no 20-day change and is dropped
    late_start = macro[(macro["series_id"] != "DFII10") | (pd.to_datetime(macro["available_utc"], utc=True) > gap_to)]
    gated = h06().candidates_in_context(m, X, context, ctx={"macro": late_start})
    kept_close = pd.DatetimeIndex(m["visible_at"])[gated["idx"]]
    assert len(gated) > 0 and (kept_close > gap_to).all() and gap_from < gap_to
    no_dollar = macro[macro["series_id"] != "DTWEXBGS"]
    assert h06().candidates_in_context(m, X, context, ctx={"macro": no_dollar}).empty
    with pytest.raises(ValueError, match="macro"):
        h06().candidates_in_context(m, X, context)
    with pytest.raises(ValueError, match="macro"):
        prepare(h06(), dec, context, frame=(m, X))


def test_a_zero_change_agrees_with_neither_side(frame):
    _, context, m, X = frame
    vd = pd.bdate_range("2021-06-01", "2024-01-31")
    flat = pd.concat([fred_frame(s, pd.DataFrame({"value_date": vd, "value": np.full(len(vd), 1.0)}),
                                 pd.Timestamp("2024-03-01")) for s in ("DFII10", "DTWEXBGS")], ignore_index=True)
    assert len(slow().candidates_in_context(m, X, context)) > 0
    assert h06().candidates_in_context(m, X, context, ctx={"macro": flat}).empty


def test_the_live_call_without_context_proposes_nothing(frame):
    _, _, m, X = frame
    assert h06().candidates(m, X).empty


def test_research_prepare_passes_the_macro_table_to_the_gate(frame):
    dec, context, m, X = frame
    macro = synthetic_macro()
    p = prepare(h06(), dec, context, ctx={"macro": macro}, frame=(m, X))
    gated = h06().candidates_in_context(m, X, context, ctx={"macro": macro})
    assert len(p.gross) > 0 and set(p.gross["idx"]) <= set(gated["idx"])
    assert len(prepare(slow(), dec, context, ctx={"macro": macro}, frame=(m, X)).gross) > len(p.gross)


# --------------------------------------------------------------------------- no look-ahead
def test_no_macro_row_published_after_a_decision_close_changes_that_decision(frame):
    _, context, m, X = frame
    macro = synthetic_macro()
    full = h06().candidates_in_context(m, X, context, ctx={"macro": macro})
    close = pd.DatetimeIndex(m["visible_at"])
    base = slow().candidates_in_context(m, X, context)
    avail = pd.DatetimeIndex(pd.to_datetime(macro["available_utc"], utc=True))
    rng = np.random.default_rng(3)
    # every H-01 candidate is a decision the gate makes (keep or drop): check a spread of them, kept and dropped
    for i in base["idx"].to_numpy()[:: max(1, len(base) // 12)]:
        at = close[i]
        dropped = macro[avail <= at]
        perturbed = macro.copy()
        after = np.asarray(avail > at)
        perturbed.loc[after, "value"] = perturbed.loc[after, "value"].to_numpy() * rng.uniform(0.5, 1.5, after.sum())
        for variant in (dropped, perturbed):
            part = h06().candidates_in_context(m, X, context, ctx={"macro": variant})
            assert (i in set(part["idx"])) == (i in set(full["idx"])), at


def test_decisions_up_to_a_cut_are_unchanged_when_bars_and_macro_after_it_are_removed(two_years, frame):
    _, context, m, X = frame
    macro = synthetic_macro()
    full = h06().candidates_in_context(m, X, context, ctx={"macro": macro})
    assert len(full) > 5
    for cut in (pd.Timestamp("2023-06-14 16:00", tz="UTC"), pd.Timestamp("2023-09-20 20:00", tz="UTC")):
        b_cut = two_years[two_years["visible_at"] <= cut]
        dec_c = resample_bars(b_cut, "4h").reset_index(drop=True)
        ctx_c = {k: v[pd.to_datetime(v["visible_at"], utc=True) <= cut] for k, v in _context(b_cut).items()}
        m_c, X_c = build_decision_frame(dec_c, ctx_c)
        macro_c = macro[pd.to_datetime(macro["available_utc"], utc=True) <= cut]
        part = h06().candidates_in_context(m_c, X_c, ctx_c, ctx={"macro": macro_c})
        upto = full[full["idx"] < len(m_c)].reset_index(drop=True)
        pd.testing.assert_frame_equal(part.reset_index(drop=True), upto, check_dtype=False)


FORBIDDEN = {"triple_barrier", "one_at_a_time", "expectancy", "label_spec", "exit_spec", "ret", "pnl", "t_exit",
             "exit", "target_hit", "close", "high", "low", "open"}


def test_the_gate_reads_no_price_label_or_return():
    """The gate's own code names no price column and no label or return function: it reads bar times and macro rows."""
    for fn in (TSMOM.macro_agreement, TSMOM._macro_gate):
        tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        assert not (names | strings) & FORBIDDEN, (fn.__name__, sorted((names | strings) & FORBIDDEN))
