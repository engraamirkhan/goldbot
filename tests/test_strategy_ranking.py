"""Backlog 136 (docs/design/trades-ux-2026-10-27.md §7; ADR 0053 with the quant reviewer's binding conditions): one
pure ranking builder. Families sort on the Bonferroni-adjusted lower bound of net R per trade after costs (clustered by
account and opportunity day, t with G-1 df); under 30 fills or 10 opportunity days a row is greyed and unranked;
controls are the yardstick, retired families sit apart, shadow models and cost probes are never ranked; a rule edit
starts a new row; and nothing that trades, stops, anneals, reads or promotes may import the ranking."""
from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from scipy.stats import t as student_t

from goldbot.api import strategy_ranking as sr
from goldbot.api.schema import CostSplit, RankingWindow, TradeEvidence, TradeKind, TradeRow
from goldbot.api.strategy_labels import ArmInfo, ArmRead, Catalog, Labels

NOW = pd.Timestamp("2026-10-27 12:00", tz="UTC")
ROOT = Path(__file__).parents[1]


def _info(arm: str, family: str, tf: str = "15m", **kw: Any) -> ArmInfo:
    return ArmInfo(arm_id=arm, family=family, timeframe=tf, style=kw.pop("style", "intraday"), unit=kw.pop("unit", arm),
                   **kw)


def _cat(*infos: ArmInfo, reads: dict[str, ArmRead] | None = None) -> Catalog:
    return Catalog(labels=Labels(), arms={i.arm_id: i for i in infos}, reads=reads or {})


CAT = _cat(_info("A1", "alpha", control_arm="C1"), _info("A2", "alpha", "1h", control_arm="C1"),
           _info("B1", "beta", control_arm="C1"), _info("G1", "gamma", control_arm="C1"),
           _info("D1", "delta", control_arm="C1"), _info("C1", "random_bracket", is_control=True, read_n=None))
_SEQ = iter(range(10 ** 9))


def _row(arm: str | None, r: float, *, day: int = 0, account: str = "icm-demo", kind: TradeKind = "exploration",
         family: str = "alpha", pnl: float | None = None, cost: float = 0.5, risk: float = 10.0, tf: str = "15m",
         evidence: TradeEvidence = "counted", currency: str = "GBP", minute: int = 0) -> TradeRow:
    exit_utc = NOW - timedelta(days=day, hours=1, minutes=minute)
    i = next(_SEQ)
    return TradeRow(
        trade_id=f"t{i}", kind=kind, status="closed", account=account, account_label="Day/swing lab", mode="demo",
        family=family, family_label=family.title(), variant=tf, timeframe=tf, arm_id=arm,
        is_control=kind == "control", side="long", lots=0.01, entry=2400.0, stop=2390.0, stop_now=None,
        target=2420.0, exit=2401.0, exit_utc=exit_utc, exit_reason="target", censored=False,
        opened_utc=exit_utc - timedelta(minutes=30), held_s=1800.0, hold_limit_s=3600.0, risk_amount=risk,
        currency=currency, pnl=round(r * risk, 2) if pnl is None else pnl, r=r, gross=None, pnl_now=None, r_now=None,
        price_now=None, costs=CostSplit(spread=cost, slippage=0.0, commission=0.0, swap=0.0, total=cost, complete=True),
        why="x", evidence=evidence, excluded_reason=None, stale=False)


def _spread(arm: str | None, rs: list[float], days: int, **kw: Any) -> list[TradeRow]:
    """`rs` dealt round-robin over `days` opportunity days."""
    return [_row(arm, r, day=i % days, minute=i // days, **kw) for i, r in enumerate(rs)]


def _by(view: Any, key: str) -> Any:
    return next(r for r in [*view.rows, *view.controls, *view.retired] if r.key == key)


# ------------------------------------------------------------------------------------------------ the key
def test_a_lucky_short_run_ranks_below_a_long_small_edge_and_a_12_fill_family_is_not_ranked():
    """§7.2: +0.8 R over 12 fills is under the minimum (greyed, after the ranked rows); a noisy +1.0 R over 40 fills
    ranks below a steady +0.10 R over 400 fills on the adjusted lower bound, though its mean is ten times higher."""
    rows = [*_spread("A1", [0.6, -0.4] * 200, 20),                          # +0.10 R, 400 fills, 20 days
            *_spread("B1", [9.0, -7.0] * 20, 10, family="beta"),            # +1.0 R, 40 fills, 10 days, very noisy
            *_spread("G1", [0.8] * 12, 6, family="gamma")]                  # +0.8 R, 12 fills
    view = sr.rank(rows, CAT, NOW)
    assert [r.key for r in view.rows] == ["alpha", "beta", "gamma"]
    alpha, beta, gamma = (_by(view, k) for k in ("alpha", "beta", "gamma"))
    assert beta.figures.net_r_per_trade > alpha.figures.net_r_per_trade        # the mean would have put beta first
    assert (alpha.rank, beta.rank, gamma.rank, gamma.band) == (1, 2, None, "too_few")
    assert gamma.unranked_reason == "too few to rank (12 of 30 fills, 6 of 10 days)"


def test_a_70_percent_hit_rate_losing_after_costs_ranks_below_a_45_percent_family_that_earns():
    """§7.2: 70% winners at +0.3 R and 30% losers at -1 R is -0.09 R a trade: "accurate" and losing money."""
    accurate = [0.3] * 7 + [-1.0] * 3
    earner = [4 / 3] * 9 + [-1.0] * 11
    view = sr.rank([*_spread("A1", accurate * 40, 21), *_spread("B1", earner * 20, 21, family="beta")], CAT, NOW)
    alpha, beta = _by(view, "alpha"), _by(view, "beta")
    assert alpha.figures.hit_rate == 0.7 and beta.figures.hit_rate == 0.45
    assert alpha.figures.net_r_per_trade == pytest.approx(-0.09) and beta.figures.net_r_per_trade == pytest.approx(0.05)
    assert [r.key for r in view.rows] == ["beta", "alpha"]


def test_the_key_is_the_bonferroni_adjusted_lower_bound_with_t_on_g_minus_1_df_and_the_95_ci_beside_it():
    rows = [*_spread("A1", [0.5, -0.3, 0.2, 0.1] * 15, 12), *_spread("B1", [0.4, -0.2] * 30, 15, family="beta")]
    view = sr.rank(rows, CAT, NOW)
    assert view.k_ranked == 2
    alpha = _by(view, "alpha")
    # the oracle, by hand: CR1 over (account, day) clusters, G/(G-1) x sum (S_g - n_g m)^2 / N^2
    a1 = [r for r in rows if r.arm_id == "A1"]
    cells: dict[tuple[Any, Any], list[float]] = {}
    for r in a1:
        assert r.r is not None and r.exit_utc is not None
        cells.setdefault((r.account, r.exit_utc.date()), []).append(r.r)
    n, g = len(a1), len(cells)
    m = sum(sum(c) for c in cells.values()) / n
    se = (g / (g - 1) * sum((sum(c) - len(c) * m) ** 2 for c in cells.values()) / n ** 2) ** 0.5
    assert g == alpha.figures.clusters == 12
    assert alpha.figures.lower_adjusted == round(m - student_t.ppf(1 - 0.025 / 2, g - 1) * se, 3)
    half = student_t.ppf(0.975, g - 1) * se
    assert alpha.figures.ci95 == (round(m - half, 3), round(m + half, 3))
    assert alpha.figures.lower_adjusted < alpha.figures.ci95[0]                # wider than the unadjusted bound


def test_the_interval_is_clustered_by_account_and_day_so_a_days_correlated_fills_are_not_independent():
    """The same 300 R values: on 10 days where each day's fills move together the interval is far wider than on 300
    days with one fill each (and the 10-day version has G = 10)."""
    vals = [1.0 if d % 2 else -0.8 for d in range(10) for _ in range(30)]
    together = [_row("A1", v, day=i // 30, minute=i % 30) for i, v in enumerate(vals)]
    apart = [_row("B1", v, day=i, family="beta") for i, v in enumerate(vals)]
    view = sr.rank([*together, *apart], CAT, NOW)
    a, b = _by(view, "alpha").figures, _by(view, "beta").figures
    assert a.net_r_per_trade == b.net_r_per_trade and (a.clusters, b.clusters) == (10, 300)
    assert a.ci95[1] - a.ci95[0] > 4 * (b.ci95[1] - b.ci95[0])


# ------------------------------------------------------------------------------------------------ the columns
def test_every_column_is_defined_as_the_spec_says():
    rows = [_row("A1", 1.0, day=d, minute=0) for d in range(10)]                        # +1, ... in close order
    rows += [_row("A1", -1.0, day=d, minute=1) for d in range(10)]
    rows += [_row("A1", 0.0, pnl=0.0, day=d, minute=2) for d in range(10)]              # break-even: not a win
    alpha = _by(sr.rank(rows, CAT, NOW), "alpha").figures
    assert (alpha.n, alpha.days, alpha.hit_rate) == (30, 10, round(10 / 30, 3))
    assert (alpha.net_pnl, alpha.net_per_trade, alpha.currency) == (0.0, 0.0, "GBP")
    assert alpha.profit_factor == 1.0 and not alpha.no_losses
    assert (alpha.cost_per_trade, alpha.cost_per_trade_r) == (0.5, 0.05)
    assert alpha.max_drawdown_r == 1.0                    # each day: +1 ... -1 (newest first is minute 0 = latest)


def test_max_drawdown_is_the_largest_peak_to_trough_of_cumulative_r_in_close_order():
    seq = [2.0, -1.0, -1.5, 3.0, -0.5]                                  # peak 2, trough -0.5: drawdown 2.5
    rows = [_row("A1", r, day=len(seq) - i) for i, r in enumerate(seq)]
    assert _by(sr.rank(rows, CAT, NOW), "alpha").figures.max_drawdown_r == 2.5


def test_profit_factor_says_no_losses_yet_when_there_are_none():
    f = _by(sr.rank([_row("A1", 0.5, day=d) for d in range(3)], CAT, NOW), "alpha").figures
    assert f.profit_factor is None and f.no_losses


def test_money_is_never_summed_across_currencies():
    rows = [_row("A1", 0.5, day=1), _row("A1", 0.5, day=2, currency="USD")]
    f = _by(sr.rank(rows, CAT, NOW), "alpha").figures
    assert (f.net_pnl, f.currency, f.profit_factor, f.cost_per_trade) == (None, None, None, None)
    assert f.net_r_per_trade == 0.5


def test_edge_vs_random_is_interim_against_the_paired_control_and_the_evaluators_recorded_figure_after_the_read():
    rows = [*_spread("A1", [0.3, 0.1] * 20, 10), *_spread("C1", [-0.1, 0.1] * 20, 10, kind="control",
                                                         family="random_bracket")]
    f = _by(sr.rank(rows, CAT, NOW), "alpha").figures
    assert (f.edge_vs_control_r, f.edge_source, f.edge_ci_level) == (0.2, "interim", 0.95) and f.edge_ci is not None
    read = ArmRead(read_utc=NOW - timedelta(days=1), verdict="edge", state="C1", vs_control_r=0.17,
                   vs_control_ci90=(0.02, 0.32))
    cat = _cat(*CAT.arms.values(), reads={"A1": read})
    rows2 = [r for r in rows if r.arm_id != "A1" or r.timeframe == "15m"]
    row = _by(sr.rank(rows2, cat, NOW), "alpha")
    assert (row.figures.edge_vs_control_r, row.figures.edge_ci, row.figures.edge_ci_level,
            row.figures.edge_source, row.status) == (0.17, (0.02, 0.32), 0.90, "read", "candidate")


def test_the_7_and_30_day_columns_carry_their_n_and_no_trend_word_under_the_minimum():
    rows = _spread("A1", [0.2] * 40, 20)
    f = _by(sr.rank(rows, CAT, NOW, window="7d"), "alpha").figures
    assert f.n == 14 and f.last_7d.n == 14 and f.last_30d.n == 40 and f.trend is None
    many = [*_spread("A1", [0.5] * 60, 6), *[_row("A1", -0.5, day=10 + d) for d in range(15)]]
    assert _by(sr.rank(many, CAT, NOW), "alpha").figures.trend == "up"


# ------------------------------------------------------------------------------------------------ honesty
def test_under_30_fills_or_10_days_a_row_is_unranked_and_sorts_below_the_ranked_rows_by_n():
    rows = [*_spread("A1", [0.1, 0.2] * 50, 9),                         # 100 fills, 9 days: too few days
            *_spread("B1", [0.1, 0.05] * 14, 14, family="beta"),       # 28 fills: too few fills
            *_spread("G1", [-0.2, 0.0] * 20, 10, family="gamma")]      # 40 fills, 10 days, losing: ranked
    view = sr.rank(rows, CAT, NOW)
    assert [(r.key, r.rank, r.band) for r in view.rows] == [
        ("gamma", 1, "ranked"), ("alpha", None, "too_few"), ("beta", None, "too_few")]
    assert view.k_ranked == 1 and _by(view, "alpha").figures.lower_adjusted is None
    assert _by(view, "beta").unranked_reason == "too few to rank (28 of 30 fills, 14 of 10 days)"


def test_every_row_before_its_read_says_early_and_the_heading_line_heads_the_ranking():
    view = sr.rank([*_spread("A1", [0.3] * 40, 10), *_spread(None, [0.1] * 40, 10, kind="strategy",
                                                              family="box_break")], CAT, NOW)
    assert {r.status for r in view.rows} == {"early"}
    assert view.heading.startswith("Ranked by the least each trade is likely earning after costs.")
    assert "nothing is proven before its read" in view.heading


def test_controls_are_the_yardstick_never_ranked_and_shadow_and_cost_probes_are_left_out():
    rows = [*_spread("C1", [0.1] * 40, 10, kind="control", family="random_bracket"),
            *_spread(None, [0.4] * 40, 10, kind="shadow", family="model"),
            *_spread(None, [0.4] * 40, 10, kind="cost_probe", family="cost_probe"),
            *_spread("A1", [0.1] * 40, 10)]
    view = sr.rank(rows, CAT, NOW)
    assert [r.key for r in view.rows] == ["alpha"]
    assert [(r.key, r.band, r.rank) for r in view.controls] == [("control:random_bracket", "control", None)]


def test_a_family_retired_at_its_stop_sits_in_the_retired_band_unranked():
    view = sr.rank([*_spread("A1", [-1.0] * 40, 10), *_spread("B1", [0.1] * 40, 10, family="beta")], CAT, NOW,
                   retired={"A1"})
    assert [r.key for r in view.retired] == ["alpha"] and view.retired[0].status == "retired"
    assert [r.key for r in view.rows] == ["beta"] and view.k_ranked == 1


def test_a_voided_fill_is_left_out_as_in_the_reads_n():
    rows = [_row("A1", 0.5, day=1), _row("A1", 5.0, day=2, evidence="excluded")]
    assert _by(sr.rank(rows, CAT, NOW), "alpha").figures.n == 1


def test_a_rule_edit_under_a_new_config_hash_starts_a_new_row_at_n_0():
    cat = _cat(*CAT.arms.values(), _info("A3", "alpha", control_arm="C1", version=2))
    view = sr.rank([*_spread("A1", [0.2] * 40, 10), *_spread("A3", [0.4] * 5, 5)], cat, NOW)
    v1, v2 = _by(view, "alpha"), _by(view, "alpha:v2")
    assert (v1.figures.n, v2.figures.n, v2.version, v2.family_label) == (40, 5, 2, "Alpha v2")


def test_a_family_expands_to_its_timeframe_variants_with_the_same_columns():
    view = sr.rank([*_spread("A1", [0.2] * 30, 10), *_spread("A2", [0.1] * 12, 6, tf="1h")], CAT, NOW)
    alpha = _by(view, "alpha")
    assert alpha.figures.n == 42 and alpha.timeframes == ["15m", "1h"]
    assert [(v.timeframe, v.figures.n) for v in alpha.variants] == [("15m", 30), ("1h", 12)]


def test_account_style_and_window_filters_narrow_the_ranking():
    rows = [*_spread("A1", [0.2] * 40, 20), *_spread("A1", [0.2] * 10, 5, account="vantage-demo")]
    assert _by(sr.rank(rows, CAT, NOW, account="vantage-demo"), "alpha").figures.n == 10
    assert sr.rank(rows, CAT, NOW, style="swing").rows == []
    assert _by(sr.rank(rows, CAT, NOW, window="30d"), "alpha").figures.n == 50


def test_rank_moves_say_up_down_same_or_new():
    before = sr.rank([*_spread("A1", [0.3, 0.1] * 20, 10), *_spread("B1", [0.1, 0.0] * 20, 10, family="beta")],
                     CAT, NOW)
    now = sr.rank([*_spread("A1", [0.0, 0.1] * 20, 10), *_spread("B1", [0.5, 0.3] * 20, 10, family="beta"),
                   *_spread("G1", [0.2, 0.1] * 20, 10, family="gamma")], CAT, NOW)
    assert sr.rank_moves(now, before) == {"beta": "up 1", "gamma": "new", "alpha": "down 2"}


# ------------------------------------------------------------------------------------------------ out of trading
def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
    return out


def test_no_engine_allocator_stop_annealing_evaluator_or_promotion_module_imports_the_ranking():
    """ADR 0053 decision 6f: promotion reads only the recorded verdict. Only RANKING_READERS may import the ranking."""
    offenders = []
    for path in (ROOT / "goldbot").rglob("*.py"):
        mod = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        if mod == "goldbot.api.strategy_ranking" or mod in sr.RANKING_READERS:
            continue
        if "goldbot.api.strategy_ranking" in _imports(path):
            offenders.append(mod)
    assert offenders == []


TRADING = ("goldbot.engine", "goldbot.execution", "goldbot.allocator", "goldbot.risk", "goldbot.research",
           "goldbot.specialists", "goldbot.labels", "goldbot.ops.gates_phase", "goldbot.ops.commands.gates")


def test_nothing_that_trades_stops_anneals_reads_or_promotes_imports_the_ranking_or_a_module_that_shows_it():
    """No indirect path either: no trading, allocation, stop, annealing, evaluator or promotion module imports one of
    the RANKING_READERS (the API and the Telegram reports), so none can reach the ranking through them."""
    banned = {"goldbot.api.strategy_ranking", *sr.RANKING_READERS}
    offenders = []
    for path in (ROOT / "goldbot").rglob("*.py"):
        mod = ".".join(path.relative_to(ROOT).with_suffix("").parts)
        if mod.startswith(TRADING) and (hit := sorted(banned & _imports(path))):
            offenders.append((mod, hit))
    assert offenders == []
    assert not any(m.startswith(TRADING) for m in sr.RANKING_READERS)


class _Tripwire:
    """A row store that must not be touched: the ranker's requests read its numpy index, never the rows."""
    def __iter__(self) -> Any:
        raise AssertionError("a ranking request iterated the ledger's rows")

    def __len__(self) -> int:
        raise AssertionError("a ranking request measured the ledger's rows")

    def __getitem__(self, i: Any) -> Any:
        raise AssertionError("a ranking request read a ledger row")


def test_the_ranking_reads_each_of_50k_fills_once_to_build_and_never_on_a_request():
    """Backlog 136 (8). Was "within 300 ms at p95" and "a cold build under a second" (CI #212: a stalled runner failed
    it on an unrelated PR). The plan is asserted and no clock is read: the build makes one pass over the fills
    (iterated once, every row seen once) and a request answers from the numpy index, never from the rows."""
    arms = ["A1", "A2", "B1", "G1", "D1", "C1"]
    fams = {"A1": "alpha", "A2": "alpha", "B1": "beta", "G1": "gamma", "D1": "delta", "C1": "random_bracket"}
    rows = [_row(a, (i % 7 - 3) / 10, day=i % 60, minute=i // 60 % 600, family=fams[a],
                 kind="control" if a == "C1" else "exploration", tf="1h" if a == "A2" else "15m")
            for i in range(50_000) for a in [arms[i % 6]]]

    class OnePass:
        passes = 0
        seen = 0

        def __iter__(self) -> Any:
            OnePass.passes += 1
            for r in rows:
                OnePass.seen += 1
                yield r

    sr.rank(OnePass(), CAT, NOW)
    assert (OnePass.passes, OnePass.seen) == (1, 50_000)
    ranker = sr.Ranker(rows, CAT, NOW)
    ranker.rows = _Tripwire()  # type: ignore[assignment]                                                 # the build is done: requests must not need rows
    shapes: list[tuple[RankingWindow, str | None, str | None]] = [("all", None, None), ("30d", None, None),
                                                                ("7d", "icm-demo", None), ("all", None, "intraday")]
    views = [ranker(w, acct, style) for w, acct, style in shapes]
    assert all(v is not None for v in views) and views[0].rows
    assert ranker("all") is ranker("all")                                     # a repeat is the remembered answer


# ------------------------------------------------------------------------------------------------ review of 39a335be
def test_a_gross_engine_fill_is_not_counted_in_n():
    """Quant: an engine row whose record is gross (pnl unknown) carries a gross R; only net fills are ranked."""
    net = _spread(None, [0.2] * 30, 10, kind="strategy", family="box_break")
    gross = [r.model_copy(update={"pnl": None}) for r in _spread(None, [5.0] * 10, 10, kind="strategy",
                                                                   family="box_break")]
    f = _by(sr.rank([*net, *gross], CAT, NOW), "engine:box_break").figures
    assert (f.n, f.net_r_per_trade) == (30, 0.2)


def test_a_window_lists_only_families_with_a_fill_in_it():
    """Code review: a family last traded 20 days ago is not a 7-day row at n 0 (nor counted as too few to rank)."""
    rows = [*_spread("A1", [0.2] * 14, 7), *[_row("B1", 0.3, day=20 + d, family="beta") for d in range(5)]]
    assert [r.key for r in sr.rank(rows, CAT, NOW, window="7d").rows] == ["alpha"]
    assert {r.key for r in sr.rank(rows, CAT, NOW, window="30d").rows} == {"alpha", "beta"}


def test_one_arm_read_and_another_stopped_unread_takes_the_recorded_verdict():
    """Code review: a recorded read outranks "retired" for the row's status."""
    read = ArmRead(read_utc=NOW - timedelta(days=1), verdict="edge", state="C1")
    cat = _cat(*CAT.arms.values(), reads={"A1": read})
    rows = [*_spread("A1", [0.2] * 30, 10), *_spread("A2", [-0.5] * 10, 10, tf="1h")]
    row = _by(sr.rank(rows, cat, NOW, retired={"A2"}), "alpha")
    assert row.status == "candidate" and row.band != "retired"


def test_read_progress_counts_the_rows_own_counted_fills_on_its_read_unit():
    """UI review: a family with 12 fills never reads "0 of 300 to the read"; progress counts the same counted fills,
    on the read unit of config/arm_reads.yaml, the unread unit closest to its read."""
    rows = [*_spread("A1", [0.2] * 12, 6), _row("A1", 0.4, day=2, evidence="censored")]
    p = _by(sr.rank(rows, CAT, NOW), "alpha").read_progress
    assert p is not None and (p.unit, p.n_counted, p.n_read_at) == ("A1", 12, 300)


def test_read_progress_of_a_pooled_family_counts_every_counted_arm_of_its_unit_on_any_account():
    pooled = _cat(_info("A1", "alpha", unit="U1"), _info("A2", "alpha", "1h", unit="U1"),
                  _info("C1", "random_bracket", is_control=True, read_n=None))
    rows = [*_spread("A1", [0.2] * 10, 5), *_spread("A2", [0.1] * 7, 7, tf="1h", account="vantage-demo")]
    p = sr.rank(rows, pooled, NOW, account="icm-demo").rows[0].read_progress
    assert p is not None and (p.unit, p.n_counted) == ("U1", 17)
    read = _cat(*pooled.arms.values(), reads={"U1": ArmRead(read_utc=NOW, verdict="no edge")})
    assert sr.rank(rows, read, NOW).retired[0].read_progress is None          # read: no progress to show
