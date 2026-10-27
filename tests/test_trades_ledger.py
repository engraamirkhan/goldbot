"""Item 129 (docs/design/trades-ux-2026-10-27.md §2.2, §3.2; ADR 0050): one trade ledger over every kind, `GET
/api/trades` and `GET /api/trades/{trade_id}`. One row per trade with human names, costs split to the penny, a "why"
in words and an exit reason from a closed list; filters, cursor paging and totals equal to the rows; unreadable
sources named. Blinding is server-side: an aggregate covering fewer than 3 unread arms, or a filter narrowed to
strategies with unread arms (a partly-read family included), withholds its money and blinds the unread arms' rows;
the seeded-arm leak test checks every response's payload."""
from __future__ import annotations

import json
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
    page = tl.query(led, _q(kind=("strategy", "cost_probe", "real")), _catalog(tmp_path, read=("S1A", "S0")))
    closed = [r for r in page.rows if r.status == "closed"]
    t = page.totals
    assert t.n == len(page.rows) and not t.withheld and t.currency == "GBP"
    assert t.net_pnl == round(sum(r.pnl or 0 for r in closed), 2) == round(1.41 + 3.95 + 7.10, 2)
    assert t.costs == round(sum(r.costs.total for r in closed if r.costs), 2)


def test_the_default_list_is_today_every_kind_but_shadow(tmp_path):
    _every_kind(tmp_path)
    _arm(tmp_path, "ex-old", "S1A", sent=NOW - pd.Timedelta(days=3))
    cat = _catalog(tmp_path)
    page = tl.query(_ledger(tmp_path, cat), _q(), cat)
    ids = {r.trade_id for r in page.rows}
    assert not any(i.startswith("shadow:") for i in ids) and not any(i.endswith("ex-old") for i in ids)
    assert any(i.endswith("ex-old") for i in (r.trade_id for r in tl.query(_ledger(tmp_path, cat), _q(period="7d"),
                                                                          cat).rows))


def test_filters_select_by_account_status_kind_and_timeframe(tmp_path):
    _every_kind(tmp_path)
    cat = _catalog(tmp_path)
    led = _ledger(tmp_path, cat)
    assert {r.account for r in tl.query(led, _q(account=("icm-demo",)), cat).rows} == {"icm-demo"}
    assert {r.status for r in tl.query(led, _q(status="open"), cat).rows} == {"open"}
    assert {r.kind for r in tl.query(led, _q(kind=("shadow",)), cat).rows} == {"shadow"}
    assert {r.timeframe for r in tl.query(led, _q(timeframe=("1h",)), cat).rows} == {"1h"}


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
        page = tl.query(led, _q(limit=3, cursor=cursor), cat)
        seen += [r.trade_id for r in page.rows]
        cursor = page.next_cursor
        if cursor is None:
            break
    assert seen == [r.trade_id for r in led.rows] and len(seen) == 7
    with pytest.raises(ValueError):
        tl.query(led, _q(cursor="not-a-cursor"), cat)


def test_an_unreadable_source_is_named_and_the_others_still_show(tmp_path):
    _every_kind(tmp_path)
    (tmp_path / "shadow_book.json").write_text("{torn")
    led = _ledger(tmp_path, _catalog(tmp_path))
    assert led.unreadable == ["shadow"] and any(r.kind == "exploration" for r in led.rows)


# ------------------------------------------------------------------------------------------------ blinding (ADR 0050)
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


def test_lab_totals_over_three_unread_arms_in_the_account_and_day_show(tmp_path):
    _labs(tmp_path)
    cat = _catalog(tmp_path, read=("I2A",))
    page = tl.query(_ledger(tmp_path, cat), _q(account=("icm-probe",)), cat)
    assert not page.totals.withheld and page.totals.net_pnl == round(11.11 + 22.22 - 33.33 + 4.44, 2)
    assert not any(r.blinded for r in page.rows)


def test_an_account_day_under_three_unread_arms_withholds_the_totals_and_blinds_that_lists_rows(tmp_path):
    _labs(tmp_path)
    cat = _catalog(tmp_path, read=("I2A",))
    page = tl.query(_ledger(tmp_path, cat), _q(account=("icm-demo",)), cat)          # MB15 + I2B unread: 2 arms
    assert page.totals.withheld and page.totals.net_pnl is None and page.totals.costs is None
    assert page.totals.n == 3
    by = {r.arm_id: r for r in page.rows}
    assert by["I2A"].pnl == 5.55 and not by["I2A"].blinded                           # read: its result shows
    assert by["MB15"].blinded and by["MB15"].pnl is None and by["MB15"].r is None and by["MB15"].exit_reason is None
    mixed = tl.query(_ledger(tmp_path, cat), _q(), cat)                              # both labs: one cell is short
    assert mixed.totals.withheld and not any(r.blinded for r in mixed.rows)        # single trades still show


def test_a_filter_on_one_unread_arm_withholds_and_blinds(tmp_path):
    _labs(tmp_path)
    cat = _catalog(tmp_path)
    page = tl.query(_ledger(tmp_path, cat), _q(arm=("S1A",)), cat)
    assert page.totals.withheld and page.totals.net_pnl is None
    assert all(r.blinded and r.pnl is None for r in page.rows)


def test_a_filter_on_a_partly_read_family_withholds_totals_and_blinds_only_the_unread_arms(tmp_path):
    _labs(tmp_path)
    cat = _catalog(tmp_path, read=("I2A",))
    page = tl.query(_ledger(tmp_path, cat), _q(family=("box_break_close",)), cat)
    assert page.totals.withheld and page.totals.net_pnl is None and page.totals.open_pnl is None
    by = {r.arm_id: r for r in page.rows}
    assert by["I2A"].pnl == 5.55 and not by["I2A"].blinded
    assert by["MB15"].blinded and by["I2B"].blinded and by["MB15"].pnl is None


def test_controls_alone_or_exploration_alone_is_a_narrowed_filter(tmp_path):
    _labs(tmp_path)
    cat = _catalog(tmp_path)
    for kind in (("control",), ("exploration",)):
        assert tl.query(_ledger(tmp_path, cat), _q(kind=kind), cat).totals.withheld
    both = tl.query(_ledger(tmp_path, cat), _q(account=("icm-probe",), kind=("exploration", "control")), cat)
    assert not both.totals.withheld


def test_the_detail_view_shows_a_single_trades_own_result_before_its_read(tmp_path):
    _labs(tmp_path)
    led = _ledger(tmp_path, _catalog(tmp_path))
    row = tl.detail(led, next(r.trade_id for r in led.rows if r.arm_id == "MB15"))
    assert row is not None and row.pnl == -6.67 and not row.blinded


def test_unreadable_arm_reads_mean_every_arm_stays_unread(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("reads: [not, a, map")
    assert load_catalog(reads_path=bad).reads == {}


def _payloads(c: TestClient, h: dict[str, str], led_ids: list[str]) -> list[str]:
    queries = ["", "?arm=S1A", "?arm=MB15", "?family=box_break_close", "?family=session_vwap_fade",
               "?account=icm-demo", "?kind=control", "?kind=exploration", "?timeframe=15m", "?exit_reason=target",
               "?evidence=counted", "?control=false", "?account=icm-demo&kind=exploration&kind=control",
               "?family=box_break_close&period=all", "?status=closed&account=icm-demo"]
    out = [c.get("/api/trades" + q, headers=h).text for q in queries]
    out.append(c.get("/api/today", headers=h).text)
    return out


def test_seeded_per_arm_results_never_appear_in_any_aggregate_or_narrowed_list(tmp_path, monkeypatch):
    """The payload-level leak test (ADR 0050): distinctive per-arm results are seeded (each arm's sum and, for the
    narrowed lists, its single trades); no `/api/trades` response withholding totals and no `/api/today` carries an
    unread arm's sum, nor a narrowed list any unread arm's own result. Includes a filter on a partly-read family."""
    state = tmp_path / "state"
    state.mkdir()
    _labs(state)
    _arm(state, "b2b", "MB15", account=DAY, tf="15m", pnl=-1.01, sent=NOW - pd.Timedelta(minutes=50))
    cat = _catalog(tmp_path, read=("I2A",))
    monkeypatch.setattr(tl, "catalog", lambda: cat)
    from goldbot.api import home_cards
    monkeypatch.setattr(home_cards, "catalog", lambda: cat)
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", owner_email="o@x.io")
    c = TestClient(app)
    h = _login(c, app)["viewer"]
    texts = _payloads(c, h, [])
    sums = ["-7.68", "−7.68", "15.21", "1.2"]                     # MB15's sum, MB15 + I2B, the Day/swing unread pair
    for text in texts:
        for s in sums:
            assert s not in text, s
    narrowed = [c.get("/api/trades" + q, headers=h).json() for q in ("?arm=MB15", "?family=box_break_close",
                                                                       "?kind=control", "?arm=S1A")]
    for page in narrowed:
        assert page["totals"]["withheld"] and page["totals"]["net_pnl"] is None
        for r in page["rows"]:
            if r["arm_id"] != "I2A":
                assert r["pnl"] is None and r["r"] is None and r["gross"] is None and r["blinded"]
    assert c.get("/api/trades", headers=h).json()["totals"]["withheld"]   # Day/swing cell under 3 unread arms


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


def test_a_filtered_page_over_50k_rows_answers_within_300_ms_at_p95(tmp_path):
    cat = _catalog(tmp_path)
    base: dict[str, Any] = dict(kind="exploration", status="closed", account_label="Scalp lab", mode="demo", family="session_vwap_fade",
                family_label="VWAP fade", variant="5m", timeframe="5m", is_control=False, side="long", lots=0.01,
                entry=2400.0, stop=2397.0, stop_now=None, target=2406.0, exit=2406.0, exit_reason="target",
                censored=False, held_s=600.0, hold_limit_s=3600.0, risk_amount=2.4, currency="GBP", pnl=1.0, r=0.4,
                gross=1.3, pnl_now=None, r_now=None, price_now=None, costs=None, why="VWAP fade",
                evidence="counted", excluded_reason=None, blinded=False, stale=False)
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
        tl.query(led, q, cat)
        times.append(time.perf_counter() - t0)
    assert sorted(times)[int(0.95 * len(times)) - 1] < 0.3


# ------------------------------------------------------------------------------------------------ quant conditions
def _net(led: tl.Ledger, cat: Any, **kw: Any) -> float | None:
    return tl.query(led, _q(account=(SCALP,), **kw), cat).totals.net_pnl


def test_no_two_range_difference_isolates_one_arm_closing_overnight(tmp_path):
    """Quant condition 1: a closed trade belongs to the day it CLOSED, as the blinding cells do. Arms a, b, c open on
    Monday and close on Tuesday; arm d opens and closes on Tuesday. Before the fix [Mon..Mon] held a, b, c (opened
    Monday) and [Tue..Tue] held all four: the difference was d alone. No pair of shown ranges may differ by one arm."""
    _engine(tmp_path, SCALP)
    mon = pd.Timestamp("2026-10-26 20:00", tz="UTC")
    for cid, arm in (("a", "S1A"), ("b", "S2A"), ("c", "S3A")):
        _arm(tmp_path, cid, arm, sent=mon, exit_after=pd.Timedelta(hours=4), pnl=1.0)    # closes Tue 00:00
    _arm(tmp_path, "d", "S5A", sent=NOW - pd.Timedelta(hours=2), exit_after=pd.Timedelta(minutes=10), pnl=9.99)
    cat = _catalog(tmp_path)
    led = _ledger(tmp_path, cat)
    days = [pd.Timestamp(d).date() for d in ("2026-10-26", "2026-10-27")]
    shown = [_net(led, cat, since=lo, until=hi) for lo in days for hi in days if lo <= hi]
    values = [v for v in shown if v is not None]
    assert 9.99 not in values
    assert all(abs(abs(a - b) - 9.99) > 0.005 for a in values for b in values)
    monday = tl.query(led, _q(account=(SCALP,), since=days[0], until=days[0]), cat)
    assert not monday.rows                                                   # a, b, c closed on Tuesday


def test_open_pnl_totals_under_three_unread_arms_are_withheld_but_open_rows_show(tmp_path):
    """Quant condition 3: an open-P&L total is an aggregate too; each account's unread open trades need 3 arms."""
    _engine(tmp_path, SCALP)
    for cid, arm in (("a1", "S1A"), ("a2", "S2A"), ("a3", "S3A")):
        _arm(tmp_path, cid, arm)
    _arm(tmp_path, "o1", "S1A", exit_after=None, sent=NOW - pd.Timedelta(minutes=20))
    _arm(tmp_path, "o2", "S2A", exit_after=None, sent=NOW - pd.Timedelta(minutes=15))
    cat = _catalog(tmp_path)
    page = tl.query(_ledger(tmp_path, cat), _q(account=(SCALP,)), cat)
    assert page.totals.net_pnl is not None                                   # closes: 3 arms in the day
    assert page.totals.open_pnl is None and page.totals.open_pnl_withheld
    assert all(r.pnl_now is not None for r in page.rows if r.status == "open")   # single open trades stay visible
    _arm(tmp_path, "o3", "S3A", exit_after=None, sent=NOW - pd.Timedelta(minutes=10))
    page = tl.query(_ledger(tmp_path, cat), _q(account=(SCALP,)), cat)
    assert page.totals.open_pnl is not None and not page.totals.open_pnl_withheld


def test_open_trades_belong_only_to_ranges_that_reach_now(tmp_path):
    _engine(tmp_path, SCALP)
    _arm(tmp_path, "o1", "S1A", exit_after=None, sent=NOW - pd.Timedelta(days=1))
    cat = _catalog(tmp_path)
    led = _ledger(tmp_path, cat)
    yesterday = (NOW - pd.Timedelta(days=1)).date()
    assert not tl.query(led, _q(since=yesterday, until=yesterday), cat).rows
    assert tl.query(led, _q(since=yesterday), cat).rows
