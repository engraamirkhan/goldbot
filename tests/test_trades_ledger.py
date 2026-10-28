"""Item 129 (docs/design/trades-ux-2026-10-27.md §2.2, §3.2; ADR 0050): one trade ledger over every kind, `GET
/api/trades` and `GET /api/trades/{trade_id}`. One row per trade with human names, costs split to the penny, a "why"
in words and an exit reason from a closed list; filters, cursor paging and totals equal to the rows; unreadable
sources named. Backlog 139 (ADR 0053): no blinding is left, so a family's filter shows the totals its ranking row
shows; the List sorts by result too."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from goldbot.api import trades_ledger as tl
from goldbot.api.app import create_app
from goldbot.api.schema import TradeRow
from goldbot.api.strategy_labels import load_catalog
from goldbot.db import Database
from goldbot.engine import cost_probe, exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.ops import gates_phase
from goldbot.ops.gates_phase import ClosedTrade
from tests.test_api_views import _login

NOW = pd.Timestamp("2026-10-27 12:00", tz="UTC")       # a Tuesday
SCALP, DAY, LIVE = "icm-probe", "icm-demo", "icm-live"
VOCAB = {"target", "stop", "trail", "time_limit", "friday_cut", "manual", "kill_switch", "broker_stop_out", "other"}


@pytest.fixture(autouse=True)
def _accounts(enabled_accounts):
    enabled_accounts(SCALP, DAY, LIVE)


def _catalog(tmp_path: Path, read: tuple[str, ...] = ()) -> Any:
    f = tmp_path / "reads.yaml"
    f.write_text(json.dumps({"reads": {a: {"read_utc": "2026-10-20T00:00:00Z", "verdict": "no edge"} for a in read}}))
    return load_catalog(reads_path=f)


def _engine(state: Path, account: str, mode: str = "demo", **over: Any) -> None:
    e = {"account": account, "mode": mode, "currency": "GBP", "usd_to_account": 0.8, "ts": NOW.timestamp(),
         "usd_to_account_utc": (NOW - pd.Timedelta(seconds=30)).isoformat(), "price": 2410.0,
         "lot_terms": {"contract_oz": 100.0}, "equity": 10_050.0, "balance": 10_040.0, "day_start_equity": 10_000.0,
         "week_start_equity": 10_100.0, "daily_loss_limit": 200.0, "daily_loss_remaining": 180.0,
         "limits": {"weekly_cap": 0.05}, "open_positions": 1, "open_risk": 2.4}
    e.update(over)
    (state / f"engine_{account}.json").write_text(json.dumps(e))


def _db(state: Path) -> Database:
    return Database.open(state, "aux")


def _arm(state: Path, cid: str, arm: str, *, account: str = SCALP, sent: pd.Timestamp = NOW - pd.Timedelta(hours=1),
         exit_after: pd.Timedelta | None = pd.Timedelta(minutes=10), pnl: float | None = 1.23, side: int = 1,
         reason: str = "target", tf: str = "5m", stop_loss: float = 2.4, **kw: Any) -> None:
    closed = exit_after is not None
    rec = ExplorationRecord(
        client_order_id=cid, account_id=account, arm_id=arm, family="x", tf=tf, side=side, lots=0.01,
        signal_utc=sent, sent_utc=sent, atr_usd=3.0, stop_usd=3.0, target_usd=6.0, hold_s=3600,
        requested_price=2400.0, spread_usd=0.2, fill_price=2400.1, stop_loss=stop_loss, position_id=kw.pop("pid", None),
        exit_utc=sent + exit_after if exit_after is not None else None, exit_price=2406.1 if closed else None,
        exit_reason=reason if closed else None, swap=-0.03 if closed else None, commission=0.07 if closed else None,
        pnl=pnl if closed else None, censored=kw.pop("censored", False) if closed else None, fx_acct_per_usd=0.8, **kw)
    with _db(state).write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def _probe(state: Path) -> None:
    with _db(state).write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-1", account_id=DAY, agent_id="mean_reversion-g0-ab", side=-1, lots=0.01,
            requested_price=2401.0, spread_usd=0.25, sent_utc=NOW - pd.Timedelta(hours=2), fill_price=2400.9,
            exit_utc=NOW - pd.Timedelta(hours=1), exit_price=2399.0, exit_reason="time_exit", swap=0.0,
            commission=0.07, pnl=1.41, stop_loss=3.0, fx_acct_per_usd=0.8))


def _closed(state: Path, account: str, mode: Literal["demo", "live"], pid: int, pnl: float, reason: str) -> None:
    gates_phase.append_closed_trade(state, ClosedTrade(
        account_id=account, broker="icm", mode=mode, exit_utc=NOW - pd.Timedelta(minutes=30), ret=0.001, pnl=pnl,
        equity_before=10_000.0, position_id=pid, agent_id="box_break_close-g0-1h", side=1, lots=0.05,
        entry_utc=NOW - pd.Timedelta(hours=3), entry_price=2400.0, exit_price=2404.0, exit_reason=reason, r=2.0,
        commission=0.35, swap=-0.12, timeframe="1h", initial_sl=2398.0, final_tp=2404.0))


def _shadow(state: Path) -> None:
    book = ShadowBook(state)
    book.track("v1", NOW - pd.Timedelta(days=1))
    book.books["v1"].closed.append(ShadowTrade(
        version="v1", agent_id="tsmom-g0-x", side=1, entry_ts=NOW - pd.Timedelta(hours=5), entry=2400.0, stop=2390.0,
        target=2420.0, max_bars=12, timeframe="1h", p=0.62, exit_ts=NOW - pd.Timedelta(hours=2), exit=2420.0,
        barrier="target", ret=20 / 2400))
    book.save(NOW)


def _orders(state: Path) -> None:
    trade = {"position_id": 77, "agent_id": "box_break_close-g0-1h", "side": 1, "lots": 0.05, "max_bars": 8,
             "timeframe": "1h", "entry_bar_ts": (NOW - pd.Timedelta(hours=2)).isoformat(), "open_price": 2400.0,
             "sl": 2398.0, "tp": 2410.0, "initial_sl": 2397.0, "opened_utc": (NOW - pd.Timedelta(hours=1)).isoformat()}
    arm = {**trade, "position_id": 5, "agent_id": "explore:S1A", "sl": 2399.5, "lots": 0.01, "timeframe": "5m"}
    (state / f"orders_{DAY}.json").write_text(json.dumps({"sent": {}, "open": {"77": trade}}))
    (state / f"orders_{SCALP}.json").write_text(json.dumps({"sent": {}, "open": {"5": arm}}))


def _every_kind(state: Path) -> None:
    for acc in (SCALP, DAY):
        _engine(state, acc)
    _engine(state, LIVE, mode="live")
    _arm(state, "ex-1", "S1A")
    _arm(state, "ex-2", "S0", pnl=-2.5, reason="stop", side=-1)
    _arm(state, "ex-open", "S1A", exit_after=None, sent=NOW - pd.Timedelta(minutes=20), pid=5)
    _probe(state)
    _closed(state, DAY, "demo", 41, 3.95, "trail_close")
    _closed(state, LIVE, "live", 42, 7.10, "weekend_close_loser")
    _shadow(state)
    _orders(state)


def _ledger(state: Path, cat: Any) -> tl.Ledger:
    return tl.build_ledger(state, NOW, cat=cat)


def _q(**kw: Any) -> tl.TradeQuery:
    return tl.TradeQuery(**kw)


# ------------------------------------------------------------------------------------------------ rows
def test_every_kind_appears_once_per_trade_with_human_names_never_ids(tmp_path):
    _every_kind(tmp_path)
    cat = _catalog(tmp_path)
    rows = _ledger(tmp_path, cat).rows
    kinds = sorted(r.kind for r in rows)
    assert kinds == ["control", "cost_probe", "exploration", "exploration", "real", "shadow", "strategy", "strategy"]
    assert len({r.trade_id for r in rows}) == len(rows)       # one row per trade, opened and closed merged
    ex = next(r for r in rows if r.trade_id.endswith("ex-1"))
    assert (ex.account_label, ex.family_label, ex.variant) == ("Scalp lab", "VWAP fade", "5m · target VWAP")
    ctl = next(r for r in rows if r.kind == "control")
    assert ctl.is_control and ctl.family_label == "Random control"
    real = next(r for r in rows if r.kind == "real")
    assert real.account_label == "Live (ICM)" and real.family_label == "Box break + close"
    assert next(r for r in rows if r.kind == "shadow").account_label == "Shadow (paper)"
    for r in rows:
        assert "_" not in r.family_label and "-g0" not in r.family_label and r.account_label not in (SCALP, DAY, LIVE)


def test_a_closed_rows_net_is_after_costs_split_to_the_penny(tmp_path):
    """`pnl` is net of costs; costs split spread, slippage, commission and swap, and gross - net = their sum exactly."""
    _every_kind(tmp_path)
    rows = _ledger(tmp_path, _catalog(tmp_path)).rows
    ex = next(r for r in rows if r.trade_id.endswith("ex-1"))
    assert ex.costs is not None and ex.costs.complete
    # spread 0.2 x 1 oz x 0.8 = 0.16; slippage (2400.1 - 2400) x 1 x 0.8 = 0.08; commission 0.07; swap paid 0.03
    assert (ex.costs.spread, ex.costs.slippage, ex.costs.commission, ex.costs.swap) == (0.16, 0.08, 0.07, 0.03)
    for r in rows:
        if r.status == "closed" and r.pnl is not None and r.costs is not None:
            parts = sum(p for p in (r.costs.spread, r.costs.slippage, r.costs.commission, r.costs.swap) if p is not None)
            assert r.gross is not None and round(r.gross - r.pnl, 2) == round(parts, 2) == r.costs.total, r.trade_id
    pos = next(r for r in rows if r.kind == "strategy" and r.status == "closed")
    assert pos.costs is not None and not pos.costs.complete and pos.costs.spread is None   # kept in points only
    assert ex.costs.spread_exit_estimated and not ex.costs.slippage_exit_known   # no exit quote on an arm's row


def test_slippage_counts_both_fills_favourable_as_well_as_adverse_and_a_measured_exit_spread(tmp_path):
    """Finance audit 2026-10-27 (LOW): a short probe sold 0.10 above its request (favourable, -0.10/oz) and bought
    back 0.20 above the ask the close was sent on (adverse, +0.20/oz): slippage +0.10/oz x 1 oz x 0.8 = £0.08. Spread
    is half the entry's 0.25 plus half the exit's measured 0.30: 0.275 x 0.8 = £0.22."""
    _engine(tmp_path, DAY)
    with _db(tmp_path).write() as conn:
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="cp-2", account_id=DAY, agent_id="mean_reversion-g0-ab", side=-1, lots=0.01,
            requested_price=2400.8, spread_usd=0.25, sent_utc=NOW - pd.Timedelta(hours=2), fill_price=2400.9,
            exit_utc=NOW - pd.Timedelta(hours=1), exit_price=2399.0, exit_bid=2398.5, exit_ask=2398.8,
            exit_reason="time_exit", swap=0.0, commission=0.07, pnl=1.30, stop_loss=3.0, fx_acct_per_usd=0.8))
    row = _ledger(tmp_path, _catalog(tmp_path)).rows[0]
    assert row.costs is not None and (row.costs.slippage, row.costs.spread) == (0.08, 0.22)
    assert row.costs.slippage_exit_known and not row.costs.spread_exit_estimated
    assert row.gross is not None and round(row.gross - 1.30, 2) == row.costs.total


def test_a_total_missing_a_closed_trades_net_says_so_with_the_count_and_the_partial_sum(tmp_path):
    """Finance audit 2026-10-27 (HIGH): an unknown net is counted and flagged, never silently dropped."""
    _engine(tmp_path, SCALP)
    for cid, arm in (("a1", "S1A"), ("a2", "S2A"), ("a3", "S3A")):
        _arm(tmp_path, cid, arm, pnl=1.0)
    _arm(tmp_path, "a4", "S4", pnl=None)                         # the broker's deals were unreadable
    cat = _catalog(tmp_path)
    t = tl.query(_ledger(tmp_path, cat), _q(account=(SCALP,))).totals
    assert (t.net_pnl, t.net_unknown, t.net_partial) == (None, 1, 3.0)


def test_every_closed_row_has_an_exit_reason_from_the_closed_vocabulary(tmp_path):
    _every_kind(tmp_path)
    rows = _ledger(tmp_path, _catalog(tmp_path)).rows
    reasons = {r.trade_id.split(":")[0] + ":" + r.trade_id.rsplit(":", 1)[-1]: r.exit_reason for r in rows
               if r.status == "closed"}
    assert all(v in VOCAB for v in reasons.values())
    by = {r.kind: r.exit_reason for r in rows if r.status == "closed"}
    assert (by["control"], by["cost_probe"], by["strategy"], by["real"], by["shadow"]) == \
        ("stop", "time_limit", "trail", "friday_cut", "target")


@pytest.mark.parametrize(("raw", "word"), [
    ("tp", "target"), ("sl", "stop"), ("engine_stop_close", "stop"), ("exploration_hold", "time_limit"),
    ("hard_flat", "time_limit"), ("weekend_close_no_quote", "friday_cut"), ("kill_switch_close", "kill_switch"),
    ("stop_out", "broker_stop_out"), ("broker_close", "manual"), ("provisional", "other"), (None, "other")])
def test_raw_exit_codes_map_to_the_closed_vocabulary_never_none(raw, word):
    assert tl.exit_reason(raw) == word


def test_why_is_a_plain_words_sentence_for_every_row_of_every_kind(tmp_path):
    _every_kind(tmp_path)
    for r in _ledger(tmp_path, _catalog(tmp_path)).rows:
        assert r.why and len(r.why) <= 80 and " " in r.why and "_" not in r.why, r


def test_open_rows_carry_risk_and_money_now_and_the_engines_current_stop(tmp_path):
    """Item 128 on the ledger: an exploration trade's open row has its risk, P&L now (rate published) and R."""
    _every_kind(tmp_path)
    rows = _ledger(tmp_path, _catalog(tmp_path)).rows
    ex = next(r for r in rows if r.trade_id.endswith("ex-open"))
    assert ex.status == "open" and ex.evidence == "pending" and ex.exit_reason is None
    assert ex.risk_amount == 2.4 and ex.pnl_now == round((2410.0 - 2400.1) * 1 * 0.8, 2) and ex.r_now is not None
    assert ex.stop_now == 2399.5 and not ex.stale
    pos = next(r for r in rows if r.kind == "strategy" and r.status == "open")
    assert pos.risk_amount == 12.0 and pos.pnl_now == 40.0 and pos.hold_limit_s == 8 * 3600


def test_filter_totals_equal_the_sum_of_the_rows(tmp_path):
    _every_kind(tmp_path)
    led = _ledger(tmp_path, _catalog(tmp_path, read=("S1A", "S0")))
    page = tl.query(led, _q(kind=("strategy", "cost_probe", "real")))
    closed = [r for r in page.rows if r.status == "closed"]
    t = page.totals
    assert t.n == len(page.rows) and t.currency == "GBP"
    assert t.net_pnl == round(sum(r.pnl or 0 for r in closed), 2) == round(1.41 + 3.95 + 7.10, 2)
    assert t.costs == round(sum(r.costs.total for r in closed if r.costs), 2)


def test_totals_count_the_cost_probes_on_a_line_of_their_own(tmp_path):
    """volume-500 plan section 1: probes count toward the day's trades but "Reports show the count split into
    strategy arms, controls and probes": `n` includes them and `n_probes` says how many of `n` are probes."""
    _every_kind(tmp_path)
    led = _ledger(tmp_path, _catalog(tmp_path))
    page = tl.query(led, _q())
    probes = [r for r in page.rows if r.kind == "cost_probe"]
    assert probes and page.totals.n == len(page.rows) and page.totals.n_probes == len(probes)
    none = tl.query(led, _q(kind=("strategy", "real")))
    assert none.totals.n_probes == 0 and none.totals.n == len(none.rows)


def test_the_default_list_is_today_every_kind_but_shadow(tmp_path):
    _every_kind(tmp_path)
    _arm(tmp_path, "ex-old", "S1A", sent=NOW - pd.Timedelta(days=3))
    cat = _catalog(tmp_path)
    page = tl.query(_ledger(tmp_path, cat), _q())
    ids = {r.trade_id for r in page.rows}
    assert not any(i.startswith("shadow:") for i in ids) and not any(i.endswith("ex-old") for i in ids)
    week = tl.query(_ledger(tmp_path, cat), _q(period="7d")).rows
    assert any(r.trade_id.endswith("ex-old") for r in week)


def test_filters_select_by_account_status_kind_and_timeframe(tmp_path):
    _every_kind(tmp_path)
    cat = _catalog(tmp_path)
    led = _ledger(tmp_path, cat)
    assert {r.account for r in tl.query(led, _q(account=("icm-demo",))).rows} == {"icm-demo"}
    assert {r.status for r in tl.query(led, _q(status="open")).rows} == {"open"}
    assert {r.kind for r in tl.query(led, _q(kind=("shadow",))).rows} == {"shadow"}
    assert {r.timeframe for r in tl.query(led, _q(timeframe=("1h",))).rows} == {"1h"}


def test_a_cursor_pages_through_every_row_once_newest_first(tmp_path):
    for acc in (SCALP, DAY):
        _engine(tmp_path, acc)
    for i in range(7):
        _arm(tmp_path, f"ex-{i}", "S1A", sent=NOW - pd.Timedelta(minutes=60 + i))
    cat = _catalog(tmp_path, read=("S1A",))
    led = _ledger(tmp_path, cat)
    seen: list[str] = []
    cursor = None
    while True:
        page = tl.query(led, _q(limit=3, cursor=cursor))
        seen += [r.trade_id for r in page.rows]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == [r.trade_id for r in led.rows] and len(seen) == 7
    with pytest.raises(ValueError):
        tl.query(led, _q(cursor="not-a-cursor"))


def test_an_unreadable_source_is_named_and_the_others_still_show(tmp_path):
    _every_kind(tmp_path)
    (tmp_path / "shadow_book.json").write_text("{torn")
    led = _ledger(tmp_path, _catalog(tmp_path))
    assert led.unreadable == ["shadow"] and any(r.kind == "exploration" for r in led.rows)


# ------------------------------------------------------------------------------------------------ no blinding (139)
def _labs(state: Path) -> None:
    """Scalp lab today: three unread strategy arms (S1A, S2A, S3A) and a control; Day/swing lab: the box family
    15m with I2A read and MB15 unread (a partly-read family), and one more unread arm (I2B)."""
    for acc in (SCALP, DAY):
        _engine(state, acc)
    _arm(state, "a1", "S1A", pnl=11.11)
    _arm(state, "a2", "S2A", pnl=22.22)
    _arm(state, "a3", "S3A", pnl=-33.33)
    _arm(state, "c1", "S0", pnl=4.44)
    _arm(state, "b1", "I2A", account=DAY, tf="15m", pnl=5.55)
    _arm(state, "b2", "MB15", account=DAY, tf="15m", pnl=-6.67)
    _arm(state, "b3", "I2B", account=DAY, tf="15m", pnl=8.88)


def test_an_unread_arms_rows_and_any_filters_totals_show_whatever_its_read(tmp_path):
    """ADR 0053 supersedes ADR 0050 decisions 2-3: one unread arm, two unread arms in an account-day, a partly-read
    family, controls alone: every list carries its rows' results and its money totals."""
    _labs(tmp_path)
    cat = _catalog(tmp_path, read=("I2A",))
    led = _ledger(tmp_path, cat)
    for q, net in ((_q(arm=("S1A",)), 11.11), (_q(account=(DAY,)), round(5.55 - 6.67 + 8.88, 2)),
                   (_q(family=("box_break_close",)), round(5.55 - 6.67 + 8.88, 2)), (_q(kind=("control",)), 4.44)):
        page = tl.query(led, q)
        assert page.totals.net_pnl == net and page.totals.costs is not None, q
        assert all(r.pnl is not None and r.exit_reason is not None for r in page.rows), q


def test_a_family_filters_totals_equal_that_familys_ranking_row_for_the_same_window(tmp_path):
    """Backlog 139 (1): the leak tests became equality tests. A family's 7-day filter and its 7-day ranking row agree
    on the fills and the net after costs."""
    from goldbot.api import strategy_ranking as sr
    _labs(tmp_path)
    for i in range(6):
        _arm(tmp_path, f"m{i}", "MB15", account=DAY, tf="15m", pnl=1.5 - i, sent=NOW - pd.Timedelta(days=i, hours=2))
    cat = _catalog(tmp_path, read=("I2A",))
    led = _ledger(tmp_path, cat)
    page = tl.query(led, _q(family=("box_break_close",), period="7d", status="closed"))
    row = next(r for r in sr.rank(led.rows, cat, NOW, window="7d").rows if r.family == "box_break_close")
    assert (page.totals.n_closed, page.totals.net_pnl) == (row.figures.n, row.figures.net_pnl)


def test_the_list_sorts_by_result_best_or_worst_first_and_pages_in_that_order(tmp_path):
    """Backlog 139 (2): sorting by result is enabled; an unknown result sorts last; the cursor follows the order."""
    _labs(tmp_path)
    _arm(tmp_path, "u1", "S4", pnl=None)
    led = _ledger(tmp_path, _catalog(tmp_path))
    best = [r.pnl for r in tl.query(led, _q(sort="best", limit=50)).rows]
    assert best[:3] == [22.22, 11.11, 8.88] and best[-1] is None
    worst = [r.pnl for r in tl.query(led, _q(sort="worst", limit=50)).rows]
    assert worst[:2] == [-33.33, -6.67] and worst[-1] is None
    seen, cursor = [], None
    while True:
        page = tl.query(led, _q(sort="best", limit=3, cursor=cursor))
        seen += [r.trade_id for r in page.rows]
        if (cursor := page.next_cursor) is None:
            break
    assert seen == [r.trade_id for r in tl.query(led, _q(sort="best", limit=50)).rows]


def test_the_result_sort_is_by_r_across_currencies_and_open_trades_follow_the_closed(tmp_path):
    """Code review LOW: £ and $ are never compared; a closed trade is placed by its R, an open one (its result still
    moves) after the closed ones, newest first."""
    _labs(tmp_path)
    _arm(tmp_path, "o1", "S1A", exit_after=None, sent=NOW - pd.Timedelta(minutes=5))
    led = _ledger(tmp_path, _catalog(tmp_path))
    usd = led.rows[-1].model_copy(update={"trade_id": "x:usd", "currency": "USD", "pnl": 100.0, "r": 0.1})
    rows = sorted([*led.rows, usd], key=lambda r: (r.exit_utc or r.opened_utc, r.trade_id), reverse=True)
    led = tl.Ledger(as_of=NOW, rows=rows, unreadable=[])
    best = tl.query(led, _q(sort="best", limit=50)).rows
    rs = [r.r for r in best if r.status == "closed" and r.r is not None]
    assert rs == sorted(rs, reverse=True) and best[-1].status == "open"
    assert best[0].trade_id != "x:usd"                  # $100.00 at 0.1 R is not the best trade


def test_result_pages_neither_repeat_nor_skip_when_the_next_build_moves_an_open_trades_pnl(tmp_path):
    """Code review LOW: the cursor carries the row's immutable sort key, so a fresh 10 s build (open P&L moved, one
    more close) continues after the last row given."""
    _labs(tmp_path)
    for i in range(3):
        _arm(tmp_path, f"o{i}", "S1A", exit_after=None, sent=NOW - pd.Timedelta(minutes=5 + i))
    led = _ledger(tmp_path, _catalog(tmp_path))
    first = tl.query(led, _q(sort="best", limit=4))
    moved = [r.model_copy(update={"pnl_now": 999.0, "r_now": 99.0}) if r.status == "open" else r for r in led.rows]
    led2 = tl.Ledger(as_of=NOW, rows=moved, unreadable=[])
    rest: list[str] = []
    cursor = first.next_cursor
    while cursor is not None:
        page = tl.query(led2, _q(sort="best", limit=4, cursor=cursor))
        rest += [r.trade_id for r in page.rows]
        cursor = page.next_cursor
    ids = [r.trade_id for r in first.rows] + rest
    assert sorted(ids) == sorted(r.trade_id for r in tl.query(led2, _q(sort="best", limit=50)).rows)
    assert len(ids) == len(set(ids))


def test_results_hidden_copy_is_gone_from_the_web_and_telegram():
    """Backlog 139 (4): no screen or message says a result is hidden for an unread arm."""
    root = Path(__file__).parents[1]
    files = [*(root / "web" / "src").rglob("*.ts*"), *(root / "goldbot" / "telegram").rglob("*.py")]
    pat = re.compile(r"Results hidden|hidden until (the|this|its) (arm|arms'|read)|Totals hidden", re.I)
    hits = [str(f.relative_to(root)) for f in files if f.name != "schema.d.ts" and pat.search(f.read_text())]
    assert hits == []


def test_the_detail_view_shows_a_single_trades_own_result(tmp_path):
    _labs(tmp_path)
    led = _ledger(tmp_path, _catalog(tmp_path))
    row = tl.detail(led, next(r.trade_id for r in led.rows if r.arm_id == "MB15"))
    assert row is not None and row.pnl == -6.67


def test_unreadable_arm_reads_mean_no_arm_is_read(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("reads: [not, a, map")
    assert load_catalog(reads_path=bad).reads == {}


# ------------------------------------------------------------------------------------------------ the API
def test_the_routes_need_a_sign_in_page_and_answer_one_trade(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    _every_kind(state)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)["viewer"]
    assert c.get("/api/trades").status_code == 401
    page = c.get("/api/trades?period=7d&limit=2", headers=h).json()
    assert len(page["rows"]) == 2 and page["next_cursor"]
    tid = page["rows"][0]["trade_id"]
    one = c.get(f"/api/trades/{tid}", headers=h)
    assert one.status_code == 200 and one.json()["trade_id"] == tid
    assert c.get("/api/trades/nope:x:1", headers=h).status_code == 404
    assert c.get("/api/trades?cursor=zzz", headers=h).status_code == 400
    assert c.get("/api/trades/overview", headers=h).json()["periods"]                 # not shadowed by the detail
    assert "51234567" not in c.get("/api/trades?period=all", headers=h).text


def test_a_filtered_page_over_50k_rows_answers_within_300_ms_at_p95():
    base: dict[str, Any] = dict(kind="exploration", status="closed", account_label="Scalp lab", mode="demo", family="session_vwap_fade",
                family_label="VWAP fade", variant="5m", timeframe="5m", is_control=False, side="long", lots=0.01,
                entry=2400.0, stop=2397.0, stop_now=None, target=2406.0, exit=2406.0, exit_reason="target",
                censored=False, held_s=600.0, hold_limit_s=3600.0, risk_amount=2.4, currency="GBP", pnl=1.0, r=0.4,
                gross=1.3, pnl_now=None, r_now=None, price_now=None, costs=None, why="VWAP fade",
                evidence="counted", excluded_reason=None, stale=False)
    arms = ["S1A", "S1B", "S2A", "S2B", "S3A", "S3B", "S4", "S5A"]
    rows = [TradeRow.model_construct(**base, trade_id=f"exploration:icm-probe:{i}", account="icm-probe",
                                     arm_id=arms[i % len(arms)], opened_utc=NOW - pd.Timedelta(minutes=i % 4000 + 10),
                                     exit_utc=NOW - pd.Timedelta(minutes=i % 4000))
            for i in range(50_000)]
    rows.sort(key=lambda r: (r.exit_utc, r.trade_id), reverse=True)
    led = tl.Ledger(as_of=NOW, rows=rows, unreadable=[])
    times = []
    for q in [_q(period="30d"), _q(period="30d", account=("icm-probe",)), _q(period="all", family=("ema_pullback",)),
              _q(period="7d", status="closed")] * 5:
        t0 = time.perf_counter()
        tl.query(led, q)
        times.append(time.perf_counter() - t0)
    assert sorted(times)[int(0.95 * len(times)) - 1] < 0.3


def test_the_indexed_query_gives_the_same_rows_and_totals_as_a_full_scan(tmp_path):
    """PR #206 speed fix: bisected date slices, the plain path and the one-pass scan answer exactly what a scan of
    every row with `totals` answers, for every filter shape."""
    _every_kind(tmp_path)
    _labs(tmp_path)
    _arm(tmp_path, "old", "S2A", sent=NOW - pd.Timedelta(days=9))
    cat = _catalog(tmp_path, read=("I2A",))
    led = _ledger(tmp_path, cat)
    yesterday, today = (NOW - pd.Timedelta(days=1)).date(), NOW.date()
    queries = [_q(), _q(period="7d"), _q(period="30d"), _q(period="all"), _q(account=(SCALP,)),
               _q(account=(SCALP, DAY), period="all"), _q(since=yesterday, until=today), _q(since=yesterday),
               _q(until=yesterday, period="all"), _q(kind=("shadow",), period="all"), _q(status="open"),
               _q(family=("box_break_close",)), _q(kind=("exploration", "control")), _q(arm=("S1A",), period="all")]
    for q in queries:
        lo, hi = tl.bounds(q, NOW)
        match = tl._matcher(q, lo, hi, NOW)
        want = [r for r in led.rows if match(r)]
        page = tl.query(led, q.model_copy(update={"limit": tl.MAX_PAGE}))
        assert [r.trade_id for r in page.rows] == [r.trade_id for r in want], q
        assert page.totals == tl.totals(want), q
        assert tl.query(led, q.model_copy(update={"limit": tl.MAX_PAGE})) == page        # a remembered answer


# ------------------------------------------------------------------------------------------------ ranges
def test_a_closed_trade_belongs_to_the_day_it_closed(tmp_path):
    """Arms a, b, c open on Monday and close on Tuesday: Monday's list is empty, Tuesday's holds them (the day the
    money lands, as Today and the ranking count it)."""
    _engine(tmp_path, SCALP)
    mon = pd.Timestamp("2026-10-26 20:00", tz="UTC")
    for cid, arm in (("a", "S1A"), ("b", "S2A"), ("c", "S3A")):
        _arm(tmp_path, cid, arm, sent=mon, exit_after=pd.Timedelta(hours=4), pnl=1.0)    # closes Tue 00:00
    led = _ledger(tmp_path, _catalog(tmp_path))
    days = [pd.Timestamp(d).date() for d in ("2026-10-26", "2026-10-27")]
    assert not tl.query(led, _q(account=(SCALP,), since=days[0], until=days[0])).rows
    assert tl.query(led, _q(account=(SCALP,), since=days[1], until=days[1])).totals.net_pnl == 3.0


def test_the_open_pnl_total_shows_with_each_open_rows_pnl(tmp_path):
    _engine(tmp_path, SCALP)
    _arm(tmp_path, "o1", "S1A", exit_after=None, sent=NOW - pd.Timedelta(minutes=20))
    page = tl.query(_ledger(tmp_path, _catalog(tmp_path)), _q(account=(SCALP,)))
    assert page.totals.open_pnl is not None and page.totals.open_pnl == page.rows[0].pnl_now


def test_open_trades_belong_only_to_ranges_that_reach_now(tmp_path):
    _engine(tmp_path, SCALP)
    _arm(tmp_path, "o1", "S1A", exit_after=None, sent=NOW - pd.Timedelta(days=1))
    cat = _catalog(tmp_path)
    led = _ledger(tmp_path, cat)
    yesterday = (NOW - pd.Timedelta(days=1)).date()
    assert not tl.query(led, _q(since=yesterday, until=yesterday)).rows
    assert tl.query(led, _q(since=yesterday)).rows
