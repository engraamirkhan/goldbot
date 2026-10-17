"""Broker ledger (finance audit GAP-07, GAP-08; checks RC-8, RC-9): daily balance snapshots with deposits and
withdrawals from the broker's balance deals, and the reconciliation of goldbot's trade records with the broker."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import load_settings
from goldbot.data import crossfeed as cf
from goldbot.data.store import Store
from goldbot.db import Database
from goldbot.db.migrations import AUX, compat_back_to
from goldbot.execution.bridge import _decode, _encode
from goldbot.execution.broker import AccountInfo, Position
from goldbot.ops import ledger
from goldbot.ops.accounts import Account
from goldbot.ops.gates_phase import ClosedTrade, append_closed_trade
from goldbot.ops.health import AccountRef, HealthContext, run_checks
from goldbot.ops.jobs import JOBS, JobContext, broker_ledger
from goldbot.research.model_registry import ModelRegistry
from goldbot.research.population import Population
from goldbot.research.registry import TrialRegistry

ACC_ID = "icm-demo"
MAGIC = 260100
LOGIN = 51234567                                   # a made-up terminal login: it must never reach aux.db
DAY1 = pd.Timestamp("2026-10-06 23:25", tz="UTC")
DAY2 = pd.Timestamp("2026-10-07 23:25", tz="UTC")
ACC = Account(account_id=ACC_ID, broker="icm", mode="demo", server="ICMarketsSC-Demo", login=None, terminal_path="",
              server_tz="Europe/Athens", symbol="XAUUSD", magic_base=MAGIC, enabled=True)
SETTINGS = load_settings().ledger


# ------------------------------------------------------------------------------------------------ fixtures
def _deal(ticket: int, ts: str, type_: int, *, entry: int = 0, pid: int = 0, volume: float = 0.0, price: float = 0.0,
          profit: float = 0.0, commission: float = 0.0, swap: float = 0.0, fee: float = 0.0, magic: int = 0,
          comment: str = "") -> dict[str, Any]:
    """One row of MT5 history_deals_get as MT5Broker.deals_since returns it (ts_utc added)."""
    return {"ticket": ticket, "order": ticket, "time_msc": 0, "type": type_, "entry": entry, "magic": magic,
            "position_id": pid, "reason": 0, "volume": volume, "price": price, "commission": commission, "swap": swap,
            "profit": profit, "fee": fee, "symbol": "XAUUSD" if type_ in (0, 1) else "", "comment": comment,
            "ts_utc": pd.Timestamp(ts, tz="UTC")}


def _trade(pid: int, side: int, lots: float, entry_px: float, exit_px: float, opened: str, closed: str | None,
           *, ticket: int, magic: int = MAGIC, swap: float = 0.0) -> list[dict[str, Any]]:
    """Entry and (optionally) exit deals of one position: 3.5 per lot and side commission, 100 oz contract."""
    out = [_deal(ticket, opened, 0 if side > 0 else 1, entry=0, pid=pid, volume=lots, price=entry_px,
                 commission=-3.5 * lots, magic=magic)]
    if closed is not None:
        out.append(_deal(ticket + 1, closed, 1 if side > 0 else 0, entry=1, pid=pid, volume=lots, price=exit_px,
                         profit=round(side * (exit_px - entry_px) * lots * 100, 2), commission=-3.5 * lots,
                         swap=swap, magic=magic))
    return out


def _net(deals: list[dict[str, Any]]) -> float:
    return round(sum(d["profit"] + d["commission"] + d["swap"] + d["fee"] for d in deals), 2)


class FakeBroker:
    """The read-only part of the Broker protocol the ledger uses: account, positions, deals_since."""

    def __init__(self, balance: float, deals: list[dict[str, Any]], positions: list[Position] | None = None,
                 equity: float | None = None, currency: str = "GBP") -> None:
        self.balance, self.deals, self.pos = balance, deals, positions or []
        self.equity, self.currency = equity, currency
        self.deal_reads = 0

    def account(self) -> AccountInfo:
        eq = self.balance if self.equity is None else self.equity
        return AccountInfo(login=LOGIN, equity=eq, balance=self.balance, margin=120.0, margin_free=eq - 120.0,
                           leverage=30, currency=self.currency, server="ICMarketsSC-Demo", trade_mode="demo",
                           margin_mode="hedging")

    def positions(self, magic_prefix: int | None = None) -> list[Position]:
        return list(self.pos)

    def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
        self.deal_reads += 1
        df = pd.DataFrame(self.deals)
        return df[df["ts_utc"] >= since_utc].reset_index(drop=True) if not df.empty else df


def _position(pid: int, side: int = 1, lots: float = 0.1, price: float = 2400.0, magic: int = MAGIC) -> Position:
    return Position(position_id=pid, symbol="XAUUSD", side=side, lots=lots, open_price=price, sl=None, tp=None,
                    magic=magic, comment="", open_time_utc=pd.Timestamp("2026-10-07 10:00", tz="UTC"), profit=0.0)


def _record(state_dir: Path, pid: int, *, side: int = 1, lots: float = 0.1, entry: float = 2400.0,
            exit_: float = 2410.0, pnl: float | None = None, exit_utc: str = "2026-10-07 15:00",
            pnl_currency: str | None = "GBP") -> None:
    gross = side * (exit_ - entry) * lots * 100
    append_closed_trade(state_dir, ClosedTrade(
        account_id=ACC_ID, broker="icm", mode="demo", exit_utc=pd.Timestamp(exit_utc, tz="UTC"), ret=0.001,
        pnl=round(gross - 7.0 * lots, 2) if pnl is None else pnl, equity_before=1000.0, position_id=pid, side=side,
        lots=lots, entry_utc=pd.Timestamp("2026-10-07 10:00", tz="UTC"), entry_price=entry, exit_price=exit_,
        pnl_currency=pnl_currency))


def _open_file(state_dir: Path, rows: dict[int, dict[str, Any]]) -> None:
    (state_dir / f"orders_{ACC_ID}.json").write_text(json.dumps({"sent": {}, "open": {
        str(pid): {"position_id": pid, "agent_id": "a", "side": r.get("side", 1), "lots": r.get("lots", 0.1),
                   "entry_bar_ts": "2026-10-07T09:45:00+00:00", "max_bars": 16, "open_price": r.get("price", 2400.0),
                   "opened_utc": "2026-10-07T10:00:00+00:00"} for pid, r in rows.items()}, "closing": [],
        "records_lost": []}))


def _db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


def _run(tmp_path: Path, broker: FakeBroker, now: pd.Timestamp = DAY2, *, read_at: pd.Timestamp | None = None,
         sleeps: list[float] | None = None) -> ledger.LedgerReport:
    """One run at slot `now`, the broker read at `read_at` (default: the slot); recheck waits recorded, not slept."""
    at = now if read_at is None else read_at
    return ledger.run_ledger(_db(tmp_path), broker, ACC, tmp_path, now, SETTINGS, clock=lambda: at,
                             sleep=(sleeps if sleeps is not None else []).append)


def _baseline(tmp_path: Path, balance: float = 1000.0) -> None:
    """Day 1: the first snapshot (a baseline, no tie-out)."""
    _run(tmp_path, FakeBroker(balance, [_deal(1, "2026-10-01 08:00", 2, profit=balance, comment="Deposit")]), DAY1)


# ------------------------------------------------------------------------------------------------ snapshot
def test_snapshot_keeps_balance_equity_margin_and_currency_but_never_the_login(tmp_path):
    rep = _run(tmp_path, FakeBroker(1000.0, [], equity=990.0), DAY1)
    snaps = ledger.snapshots(_db(tmp_path), ACC_ID)
    assert len(snaps) == 1
    s = snaps[0]
    assert (s.balance, s.equity, s.margin, s.currency, s.ts_utc) == (1000.0, 990.0, 120.0, "GBP", DAY1)
    assert rep.tie_out is None and "baseline" in rep.note             # nothing to tie out against on day one
    with sqlite3.connect(tmp_path / "aux.db") as c:
        dump = "\n".join(c.iterdump())
    assert str(LOGIN) not in dump and "ICMarketsSC" not in dump


def test_balance_deals_are_captured_as_deposits_and_withdrawals_with_their_time(tmp_path):
    _baseline(tmp_path)
    deals = [_deal(1, "2026-10-01 08:00", 2, profit=1000.0, comment="Deposit"),
             _deal(20, "2026-10-07 09:00", 2, profit=500.0, comment="Deposit"),
             _deal(21, "2026-10-07 12:00", 2, profit=-200.0, comment="Withdrawal"),
             _deal(22, "2026-10-07 13:00", 3, profit=50.0, comment="Credit")]
    broker = FakeBroker(1300.0, deals)
    _run(tmp_path, broker)
    _run(tmp_path, broker)                                             # a re-run never books a deal twice
    got = {(d.kind, d.amount, d.ts_utc) for d in ledger.balance_deals(_db(tmp_path), ACC_ID)}
    # the deposit before the first snapshot is in its opening balance, not booked as a deal of the ledger
    assert got == {("deposit", 500.0, pd.Timestamp("2026-10-07 09:00", tz="UTC")),
                   ("withdrawal", -200.0, pd.Timestamp("2026-10-07 12:00", tz="UTC")),
                   ("credit", 50.0, pd.Timestamp("2026-10-07 13:00", tz="UTC"))}


def test_deal_history_that_changes_during_the_snapshot_is_read_again(tmp_path):
    late = _deal(30, "2026-10-07 23:24", 2, profit=10.0)

    class Racing(FakeBroker):
        def account(self) -> AccountInfo:
            if late not in self.deals:                                 # a deposit lands while the account is read
                self.deals.append(late)
                self.balance += 10.0
            return super().account()
    _baseline(tmp_path)
    broker = Racing(1000.0, [_deal(1, "2026-10-01 08:00", 2, profit=1000.0)])
    rep = _run(tmp_path, broker)
    assert broker.deal_reads >= 4 and rep.tie_out is not None and rep.status == "ok"


# ------------------------------------------------------------------------------------------------ balance tie-out
def test_balance_change_explained_by_pnl_commission_swap_and_deposits_ties_out(tmp_path):
    _baseline(tmp_path)
    trade = _trade(7, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", "2026-10-07 15:00", ticket=100, swap=-1.25)
    dep = _deal(40, "2026-10-07 09:00", 2, profit=250.0)
    deals = [_deal(1, "2026-10-01 08:00", 2, profit=1000.0)] + trade + [dep]
    _record(tmp_path, 7, pnl=_net(trade))
    rep = _run(tmp_path, FakeBroker(1000.0 + _net(trade) + 250.0, deals))
    t = rep.tie_out
    assert t is not None and rep.status == "ok"
    assert (t.opening, t.trade_pnl, t.commission, t.swap, t.deposits) == (1000.0, 100.0, -0.7, -1.25, 250.0)
    assert abs(t.unexplained) < 1e-9


@pytest.mark.parametrize("off, status", [(0.01, "ok"), (5.0, "fail")])
def test_balance_change_beyond_the_tolerance_fails(tmp_path, off, status):
    _baseline(tmp_path)
    rep = _run(tmp_path, FakeBroker(1000.0 + off, [_deal(1, "2026-10-01 08:00", 2, profit=1000.0)]))
    assert rep.status == status and rep.tie_out is not None and rep.tie_out.unexplained == pytest.approx(off)


def test_broker_adjustments_are_counted_but_warn(tmp_path):
    _baseline(tmp_path)
    deals = [_deal(1, "2026-10-01 08:00", 2, profit=1000.0), _deal(50, "2026-10-07 01:00", 5, profit=-3.0)]  # correction
    rep = _run(tmp_path, FakeBroker(997.0, deals))
    assert rep.tie_out is not None and rep.tie_out.adjustments == -3.0 and rep.status == "warn"


# ------------------------------------------------------------------------------------------------ trades (RC-8)
def test_closed_trade_matching_the_broker_deals_reconciles_clean(tmp_path):
    trade = _trade(7, -1, 0.2, 2400.0, 2390.0, "2026-10-07 10:00", "2026-10-07 15:00", ticket=100)
    _record(tmp_path, 7, side=-1, lots=0.2, entry=2400.0, exit_=2390.0, pnl=_net(trade))
    rep = _run(tmp_path, FakeBroker(1000.0, trade), DAY2)
    assert rep.diffs == [] and rep.matched == 1 and rep.positions_checked == 1


def test_broker_closed_position_without_a_closed_record_is_missing(tmp_path):
    trade = _trade(7, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", "2026-10-07 15:00", ticket=100)
    rep = _run(tmp_path, FakeBroker(1000.0, trade))
    assert [(d.position_id, d.kind, d.where) for d in rep.diffs] == [(7, "missing", "closed")]
    assert rep.status == "warn"


def test_closed_record_without_broker_deals_is_extra(tmp_path):
    _record(tmp_path, 8)
    rep = _run(tmp_path, FakeBroker(1000.0, []))
    assert [(d.position_id, d.kind, d.where) for d in rep.diffs] == [(8, "extra", "closed")]


@pytest.mark.parametrize("change, field", [({"lots": 0.2}, "lots"), ({"side": -1}, "side"),
                                           ({"entry": 2399.0}, "entry_price"), ({"exit_": 2411.0}, "exit_price"),
                                           ({"pnl": 50.0}, "pnl"), ({"pnl_currency": "USD"}, "pnl_currency")])
def test_closed_record_differing_from_the_broker_is_mismatched_on_that_field(tmp_path, change, field):
    trade = _trade(7, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", "2026-10-07 15:00", ticket=100)
    kw: dict[str, Any] = {"side": 1, "lots": 0.1, "entry": 2400.0, "exit_": 2410.0, "pnl": _net(trade)} | change
    _record(tmp_path, 7, **kw)
    rep = _run(tmp_path, FakeBroker(1000.0, trade))
    assert [(d.kind, d.fields) for d in rep.diffs] == [("mismatch", [field])]


def test_open_positions_are_compared_and_reported_missing_or_extra(tmp_path):
    entries = (_trade(1, 1, 0.1, 2400.0, 0, "2026-10-07 10:00", None, ticket=200)
               + _trade(2, 1, 0.1, 2400.0, 0, "2026-10-07 10:00", None, ticket=210)
               + _trade(3, 1, 0.1, 2400.0, 0, "2026-10-07 10:00", None, ticket=220))
    _open_file(tmp_path, {1: {}, 2: {"lots": 0.3}, 4: {}})
    broker = FakeBroker(1000.0, entries, positions=[_position(1), _position(2), _position(3)])
    got = {(d.position_id, d.kind, tuple(d.fields)) for d in _run(tmp_path, broker).diffs}
    assert got == {(2, "mismatch", ("lots",)),      # the engine thinks 0.3 lots, the broker holds 0.1
                   (3, "missing", ()),              # open at the broker, unknown to the engine
                   (4, "extra", ())}                # the engine holds it open, the broker has no trace of it


def test_position_closed_moments_ago_is_not_reported_missing_within_the_grace(tmp_path):
    closed = (DAY2 - pd.Timedelta(minutes=SETTINGS.close_grace_minutes - 1)).isoformat()
    trade = _trade(7, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", closed, ticket=100)
    _open_file(tmp_path, {7: {}})                   # the engine has not written the record yet
    rep = _run(tmp_path, FakeBroker(1000.0, trade))
    assert rep.diffs == [] and rep.pending == 1


def test_smoke_positions_are_listed_not_rc8_mismatches(tmp_path):
    """ADR 0021 / plan W1-1: the bridge smoke's positions (magic_base + 99) have no engine record by design (X7
    departure), so they are listed in the report, never counted missing; the balance tie-out still explains them."""
    from goldbot.execution.broker import SMOKE_MAGIC_OFFSET
    smoke = MAGIC + SMOKE_MAGIC_OFFSET
    closed = _trade(21, 1, 0.01, 2400.0, 2400.5, "2026-10-07 10:00", "2026-10-07 10:01", ticket=500, magic=smoke)
    still_open = _trade(22, 1, 0.01, 2400.0, 0, "2026-10-07 11:00", None, ticket=510, magic=smoke)
    engine = _trade(7, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", "2026-10-07 15:00", ticket=100)
    _baseline(tmp_path)
    broker = FakeBroker(1000.0 + _net(closed + still_open + engine), closed + still_open + engine,
                        positions=[_position(22, lots=0.01, magic=smoke)])
    rep = _run(tmp_path, broker)
    assert [(d.position_id, d.kind) for d in rep.diffs] == [(7, "missing")]     # the engine's own trade still counts
    assert rep.smoke_positions == [21, 22] and rep.positions_checked == 1
    assert rep.summary()["smoke"] == 2 and "smoke" in rep.note
    assert rep.summary()["balance"] == "ties out"                               # RC-9 still counts their P&L


def test_positions_outside_the_magic_range_are_ignored(tmp_path):
    manual = _trade(9, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", "2026-10-07 15:00", ticket=100, magic=0)
    other = _trade(10, 1, 0.1, 2400.0, 0, "2026-10-07 10:00", None, ticket=300, magic=MAGIC + 100)
    rep = _run(tmp_path, FakeBroker(1000.0, manual + other, positions=[_position(10, magic=MAGIC + 100)]))
    assert rep.diffs == [] and rep.positions_checked == 0


# ------------------------------------------------------------------------------------------------ job and health
def _job_ctx(tmp_path: Path, broker_for: Any) -> JobContext:
    return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                      models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "trials.jsonl"),
                      accounts=[ACC], population=Population(tmp_path / "population.json"), broker_for=broker_for)


def test_broker_ledger_job_runs_per_account_skips_without_a_broker_and_reports_an_unreachable_terminal(tmp_path):
    assert JOBS["broker_ledger"] is broker_ledger
    out = broker_ledger(_job_ctx(tmp_path, lambda acc: FakeBroker(1000.0, [])), DAY1)
    assert out[ACC_ID]["status"] == "ok" and ledger.latest_report(tmp_path, ACC_ID) is not None
    assert "1000" not in json.dumps(out)                              # amounts stay in aux.db, not in job output
    assert broker_ledger(_job_ctx(tmp_path, lambda acc: None), DAY1)[ACC_ID]["skipped"]
    assert "skipped" in broker_ledger(_job_ctx(tmp_path, None), DAY1)

    def boom(acc: Account) -> Any:
        raise RuntimeError("bridge down")
    with pytest.raises(RuntimeError, match="icm-demo: RuntimeError: bridge down"):
        broker_ledger(_job_ctx(tmp_path, boom), DAY1)


def _health(tmp_path: Path, now: pd.Timestamp = DAY2) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=now, settings=load_settings(), accounts=[AccountRef(account_id=ACC_ID)],
                         get_secret=lambda k: None, disk_usage=lambda p: (100e9, 50e9, 50e9))


def test_reconcile_check_warns_on_a_trade_mismatch_and_fails_on_an_unexplained_balance_change(tmp_path):
    day3 = DAY2 + pd.Timedelta(days=1)
    _run(tmp_path, FakeBroker(1000.0, _trade(7, 1, 0.1, 2400.0, 2410.0, "2026-10-07 10:00", "2026-10-07 15:00",
                                             ticket=100)), DAY2)
    c = cf.check_reconciliation(_health(tmp_path), ACC_ID)
    assert c.name == f"reconcile:{ACC_ID}" and c.status == "warn" and "1 missing" in c.reason
    _run(tmp_path, FakeBroker(1234.56, []), day3)
    c = cf.check_reconciliation(_health(tmp_path, day3), ACC_ID)
    assert c.status == "fail" and "balance" in c.reason
    assert "1234" not in c.reason and "234.56" not in c.reason          # amounts stay in local state
    assert {x.name: x.status for x in run_checks(_health(tmp_path, day3)).checks}[f"reconcile:{ACC_ID}"] == "fail"


def test_reconcile_check_takes_the_worse_of_the_feed_and_the_ledger_report(tmp_path):
    cf.ReconcileReport(account_id=ACC_ID, ts_utc=DAY2, window_from=DAY1, window_to=DAY2, tolerance=0.02,
                       broker_minutes=1300, engine_minutes=1300, matched=1300, close_mismatch=30, missing_engine=0,
                       missing_broker=0, max_close_diff=0.03, divergence=30 / 1300, severity="warning").save(tmp_path)
    _run(tmp_path, FakeBroker(1000.0, []), DAY2)
    c = cf.check_reconciliation(_health(tmp_path), ACC_ID)
    assert c.status == "warn" and "ledger" in c.reason


def test_reconcile_check_warns_when_the_ledger_report_is_stale(tmp_path):
    _run(tmp_path, FakeBroker(1000.0, []), DAY1)
    c = cf.check_reconciliation(_health(tmp_path, DAY1 + pd.Timedelta(hours=SETTINGS.max_age_hours + 1)), ACC_ID)
    assert c.status == "warn" and "ago" in c.reason


# ------------------------------------------------------------------------------------------------ storage and bridge
def test_the_ledger_migration_is_additive_so_a_rollback_still_opens_aux_db():
    m = AUX[6]
    assert m.name == "broker_ledger" and m.additive
    assert compat_back_to(AUX, m.version) == compat_back_to(AUX, m.version - 1)


def test_balance_deals_cross_the_bridge_unchanged():
    """No new bridge method: deals_since already returns every deal type, balance deals included."""
    raw = pd.DataFrame([_deal(1, "2026-10-07 09:00", 2, profit=500.0)])
    back = _decode(_encode(raw), response=True)
    assert back.loc[0, "type"] == 2 and back.loc[0, "profit"] == 500.0 and back.loc[0, "ts_utc"] == raw.loc[0, "ts_utc"]


def test_a_deal_history_that_never_settles_raises_instead_of_snapshotting(tmp_path):
    class Restless(FakeBroker):
        def account(self) -> AccountInfo:
            self.deals.append(_deal(1000 + len(self.deals), "2026-10-07 23:00", 2, profit=1.0))
            return super().account()
    with pytest.raises(ledger.LedgerReadError, match="changed"):
        _run(tmp_path, Restless(1000.0, []))
    assert ledger.snapshots(_db(tmp_path), ACC_ID) == []


def test_an_unreadable_orders_file_warns_and_open_positions_are_not_compared(tmp_path):
    (tmp_path / f"orders_{ACC_ID}.json").write_text("{torn")
    rep = _run(tmp_path, FakeBroker(1000.0, [], positions=[_position(3)]))
    assert rep.status == "warn" and "unreadable" in rep.note and rep.diffs == []


def test_show_prints_the_latest_report_snapshots_and_balance_deals(tmp_path, capsys):
    _baseline(tmp_path)
    _run(tmp_path, FakeBroker(1500.0, [_deal(20, "2026-10-07 09:00", 2, profit=500.0)]))
    assert ledger.main(["show", ACC_ID, "--state", str(tmp_path), "--days", "3650"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["report"]["status"] == "ok" and len(out["snapshots"]) == 2
    assert [d["amount"] for d in out["balance_deals"]] == [500.0]


# ------------------------------------------------------------------------------------------------ review fixes
def test_forty_idle_days_and_a_late_deposit_tie_out_without_a_new_baseline(tmp_path):
    """SRE/trading-safety MEDIUM: with no deals the cursor drifted back an hour a run until a 31-day baseline lost the
    deposit. The cursor never trails the read by more than a day."""
    deals: list[dict[str, Any]] = []
    broker = FakeBroker(1000.0, deals)
    for day in range(40):
        slot = DAY1 + pd.Timedelta(days=day)
        if day == 38:
            deals.append(_deal(70, (slot - pd.Timedelta(hours=3)).isoformat(), 2, profit=300.0))
            broker.balance = 1300.0
        rep = _run(tmp_path, broker, slot)
        if day:
            assert rep.tie_out is not None and "baseline" not in rep.note and rep.status == "ok", (day, rep.note)
    booked = ledger.balance_deals(_db(tmp_path), ACC_ID)
    assert [(d.amount, d.baseline) for d in booked] == [(300.0, False)]


def test_a_baseline_day_books_its_balance_deals_flagged_as_baseline(tmp_path):
    _run(tmp_path, FakeBroker(1500.0, [_deal(9, "2026-10-06 20:00", 2, profit=500.0)]), DAY1)
    assert [(d.amount, d.baseline) for d in ledger.balance_deals(_db(tmp_path), ACC_ID)] == [(500.0, True)]


def test_the_snapshot_is_stamped_with_the_read_time_and_keyed_by_the_slot_day(tmp_path):
    late = DAY1 + pd.Timedelta(hours=3)                                # the slot's job ran after midnight
    _run(tmp_path, FakeBroker(1000.0, []), DAY1, read_at=late)
    _run(tmp_path, FakeBroker(1000.0, []), DAY1, read_at=late + pd.Timedelta(minutes=5))   # a re-run of that slot
    snaps = ledger.snapshots(_db(tmp_path), ACC_ID)
    assert [s.ts_utc for s in snaps] == [late + pd.Timedelta(minutes=5)]
    assert ledger.latest_report(tmp_path, ACC_ID).ts_utc == late + pd.Timedelta(minutes=5)   # type: ignore[union-attr]


def test_an_unexplained_balance_is_read_again_before_it_fails(tmp_path):
    _baseline(tmp_path)

    class Settling(FakeBroker):
        """The balance is updated before its deal shows in the history (seen on the first read only)."""
        def deals_since(self, since_utc: pd.Timestamp) -> pd.DataFrame:
            if self.deal_reads >= 2 and not any(d["ticket"] == 80 for d in self.deals):
                self.deals.append(_deal(80, "2026-10-07 23:00", 2, profit=25.0))
            return super().deals_since(since_utc)
    sleeps: list[float] = []
    rep = _run(tmp_path, Settling(1025.0, []), sleeps=sleeps)
    assert rep.status == "ok" and sleeps == [ledger.RECHECK_S]
    sleeps.clear()
    assert _run(tmp_path, FakeBroker(1099.0, []), sleeps=sleeps).status == "fail" and sleeps == [ledger.RECHECK_S]


def test_a_missing_ledger_table_is_no_report_but_any_other_database_error_warns(tmp_path):
    with sqlite3.connect(tmp_path / "aux.db") as c:                    # an aux.db from before migration 7
        c.execute("CREATE TABLE other (x INTEGER)")
    assert ledger.latest_report(tmp_path, ACC_ID) is None and ledger.ledger_status(_health(tmp_path), ACC_ID) is None
    (tmp_path / "aux.db").write_bytes(b"not a database" * 100)
    assert ledger.ledger_status(_health(tmp_path), ACC_ID) == ("warn", "ledger report unreadable: DatabaseError")


def test_show_opens_aux_db_read_only(tmp_path, capsys, monkeypatch):
    _run(tmp_path, FakeBroker(1000.0, []), DAY1)

    def no_writer(*a: Any, **k: Any) -> Any:
        raise AssertionError("show must not open aux.db for writing")
    monkeypatch.setattr(ledger, "Database", no_writer)
    assert ledger.main(["show", ACC_ID, "--state", str(tmp_path), "--days", "3650"]) == 0
    assert len(json.loads(capsys.readouterr().out)["snapshots"]) == 1


def test_balance_operations_after_a_cancelled_deal_are_tagged_and_warn(tmp_path):
    """MT5 turns a cancelled deal's type into BUY_CANCELED/SELL_CANCELED (13/14) with zero profit and books the P&L
    reversal as a separate balance operation: that operation is not a deposit or withdrawal of the owner's."""
    _baseline(tmp_path)
    deals = [_deal(90, "2026-10-07 10:00", 13, entry=0, pid=5, volume=0.1, price=2400.0, magic=MAGIC),
             _deal(91, "2026-10-07 12:00", 2, profit=-40.0)]
    rep = _run(tmp_path, FakeBroker(960.0, deals))
    assert rep.status == "warn" and "cancelled" in rep.note
    assert [(d.kind, d.follows_cancel) for d in ledger.balance_deals(_db(tmp_path), ACC_ID)] == [("withdrawal", True)]
