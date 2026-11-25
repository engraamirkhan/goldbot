"""Evidence reads on the unlimited demo epoch (ADR 0097 §7; quant review of #454, F1/F2/F3/F5).

A fill sent on a demo account without limits in a market state a capped account refuses entries in (a news shock, a
spread over the capped ceiling, the reopen window, a thin holiday, a tier-2 blackout) is a fill no capped or live account
could have had. `evidence_exclusions.unlimited_market_state` names it. Cost reads (cost cells, r2 legs, fill-to-bar,
realised costs), stop units (H-61 n, H-81), the edge cap and the kill reads (learning cap and 300, GD-1, the lifecycle
ledger) leave it out and count it. Money reads keep it. Every other unlimited-epoch fill stays in the kill reads, and the
judged reads never see the epoch (they read r2 replay labels of candidates)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import ExplorationRecord
from goldbot.research import arm_stops
from goldbot.research import cost_of_record as C
from goldbot.research import exploration_data as ex
from goldbot.research.evidence_exclusions import MARKET_STATE_COUNTER, unlimited_market_state

T0 = pd.Timestamp("2026-11-30 10:00", tz="UTC")
HOSTS = {"icm-probe": "icm"}
ARM = "IEX1H"
SHOCK = dict(limits_epoch="unlimited", in_blackout=True, blackout_kind="news_shock", capped_reasons=["news_blackout"])


def _fill(db: Database, cid: str, *, minutes: int, **tags: Any) -> None:
    at = T0 + pd.Timedelta(minutes=minutes)
    with db.write() as conn:
        exploration.LEDGER.upsert(conn, ExplorationRecord(
            client_order_id=cid, account_id="icm-probe", arm_id=ARM, family="f", tf="15m", side=1, lots=0.01,
            signal_utc=at, sent_utc=at, atr_usd=3.0, stop_usd=3.0, target_usd=3.0, hold_s=3600, position_id=1,
            requested_price=2000.0, fill_price=2000.2, exit_utc=at + pd.Timedelta(minutes=20), exit_price=1997.0,
            exit_reason="stop", pnl=-3.2, stop_loss=3.0, cost=0.2, fx_acct_per_usd=0.8, **tags))


def _db(tmp_path: Path) -> Database:
    db = Database.open(tmp_path, "aux")
    _fill(db, "plain", minutes=0)
    _fill(db, "capped-epoch-ok", minutes=60, limits_epoch="unlimited", capped_reasons=[])
    _fill(db, "shock", minutes=120, **SHOCK)
    return db


@pytest.mark.parametrize("tags, out", [
    (dict(limits_epoch="unlimited", blackout_kind="news_shock"), True),
    (dict(limits_epoch="unlimited", capped_reasons=["spread_too_wide"]), True),
    (dict(limits_epoch="unlimited", capped_reasons=["unread"]), True),           # fail closed
    (dict(limits_epoch="unlimited", capped_reasons=[]), False),
    (dict(limits_epoch=None, blackout_kind="news_shock"), False),                # a capped account's own shock rule
])
def test_the_predicate_names_only_unlimited_epoch_market_state_fills(tags, out) -> None:
    assert unlimited_market_state(tags) is out
    assert unlimited_market_state(SimpleNamespace(**{"capped_reasons": None, "blackout_kind": None, **tags})) is out


def test_a_news_shock_fill_on_an_unlimited_account_enters_no_cost_cell_no_stop_unit_and_no_edge_cap(tmp_path,
                                                                                                   monkeypatch):
    db = _db(tmp_path)
    legs, counts = ex.ledger_legs(db, broker_of=HOSTS, as_of=T0 + pd.Timedelta(days=1))
    assert set(legs["client_order_id"]) == {"plain", "capped-epoch-ok"} and counts[MARKET_STATE_COUNTER] == 1
    conn = sqlite3.connect(f"file:{tmp_path / 'aux.db'}?mode=ro", uri=True)
    try:
        fr = C.read_frames(conn, {}, HOSTS, T0 - pd.Timedelta(hours=1), T0 + pd.Timedelta(days=1))
        assert len(fr.entries) == 2 and len(fr.closes) == 2 and fr.n_market_state == 1
        fills, _ = C.read_fill_to_bar(conn, HOSTS, T0 - pd.Timedelta(hours=1), T0 + pd.Timedelta(days=1))
        assert {t.client_order_id for t in fills} == {"plain", "capped-epoch-ok"}
        from goldbot.research.realised_costs import read_rows
        trades, _, _ = read_rows(conn)
        assert {r["client_order_id"] for r in trades} == {"plain", "capped-epoch-ok"}
        seen: list[Any] = []
        monkeypatch.setattr(arm_stops, "cost_state", lambda rows, *a, **k: seen.extend(rows))
        rules = SimpleNamespace(cost_stop=SimpleNamespace(arms=[ARM]))
        arm_stops.cost_states(conn, [ARM], rules=rules)                     # type: ignore[arg-type]
        assert {t.client_order_id for t in seen} == {"plain", "capped-epoch-ok"}
        got: list[Any] = []

        def trade_costs(rows: Any, *a: Any, **k: Any) -> pd.DataFrame:
            got.extend(rows)
            return pd.DataFrame({"stop_usd": [], "arm_id": []})
        monkeypatch.setattr("goldbot.research.realised_costs.trade_costs", trade_costs)
        record = SimpleNamespace(brokers=HOSTS, probe_exit_slip_usd={}, exit_prior_usd=0.1)
        arm_stops.edge_cap_states(conn, [ARM], record=record, rules=SimpleNamespace(edge_cap=object()))  # type: ignore[arg-type]
        assert {r["client_order_id"] for r in got} == {"plain", "capped-epoch-ok"}
        from goldbot.engine.exploration_learning import read
        assert read(conn, ARM, 3.0).fills == 2                              # F5: the cap and the 300 leave it out
    finally:
        conn.close()
    from goldbot.telegram.labs import counts_toward_reads
    with db.connection() as c2:
        recs = {t.client_order_id: t for t in exploration.LEDGER.select(c2, "", ())}
    assert not counts_toward_reads(recs["shock"])


def test_a_fill_sent_on_the_unlimited_epoch_outside_any_market_state_stays_in_every_kill_read(tmp_path) -> None:
    """ADR 0097 §7 (b): the epoch alone never removes a fill from a kill read."""
    db = _db(tmp_path)
    from goldbot.research.arm_lifecycle import _ledger
    with db.connection() as conn:
        n, _ = _ledger(conn, ARM, ["icm-probe"], None)
    assert n == 2


def test_the_arm_lifecycle_read_is_identical_with_and_without_demo_unlimited_on_the_same_candidates(tmp_path):
    """§7 (a): judged reads use the r2 replay labels of every candidate; the epoch never enters them. The candidates
    (`lifecycle_obs`) read the same whatever the ledger's fills are tagged."""
    from goldbot.research.arm_lifecycle import lifecycle_obs
    from tests.test_replay_parity import _pair
    a, b = Database.open(tmp_path / "a", "aux"), Database.open(tmp_path / "b", "aux")
    for db in (a, b):
        for i in range(5):
            _pair(db, f"c{i}", at=T0 + pd.Timedelta(minutes=15 * i), fill_r=0.5, sim_r=0.4)
    with b.write() as conn:                 # the same fills, now tagged as the unlimited epoch in a news shock
        for t in exploration.LEDGER.select(conn, "", ()):
            exploration.LEDGER.upsert(conn, t.model_copy(update=SHOCK))
    oa, ob = lifecycle_obs(a, "icm"), lifecycle_obs(b, "icm")
    wall = [c for c in oa.columns if c.endswith("recorded_ns")]          # when the test wrote the row: not a read
    assert len(oa) == 5
    pd.testing.assert_frame_equal(oa.drop(columns=wall), ob.drop(columns=wall))


def test_a_cost_probe_on_an_unlimited_account_carries_the_epoch_and_blackout_tags(tmp_path) -> None:
    from goldbot.engine import cost_probe
    from tests.test_exit_first import _paper
    eng = _paper(tmp_path)
    eng.state.in_blackout, eng.state.blackout_kind = True, "news_shock"
    tags = cost_probe.evidence_tags(eng)
    assert tags["limits_epoch"] == "unlimited" and tags["in_blackout"] is True
    assert tags["blackout_kind"] == "news_shock" and "news_blackout" in tags["capped_reasons"]
    rec = cost_probe.ProbeRecord(client_order_id="p", account_id="icm-probe", agent_id="a", side=1, lots=0.01,
                                 signal_utc=T0, sent_utc=T0, **tags)
    assert unlimited_market_state(rec)
    capped = _paper(tmp_path / "capped", free=False)
    assert cost_probe.evidence_tags(capped) == {}


def test_a_news_shock_loss_counts_toward_the_learning_loss_cap_but_not_toward_the_300_fills(tmp_path) -> None:
    """ADR 0097 §7 (b), quant R1: money bounds count every closed fill of every epoch; only n and fill counts leave
    market-state fills out."""
    db = Database.open(tmp_path, "aux")
    for i in range(3):
        _fill(db, f"clean{i}", minutes=30 * i)
    _fill(db, "shock", minutes=200, **SHOCK)
    with db.write() as conn:
        t = exploration.LEDGER.get(conn, "shock")
        assert t is not None
        exploration.LEDGER.upsert(conn, t.model_copy(update={"pnl": -50.0}))
    from goldbot.engine.exploration_learning import read
    from goldbot.research.arm_lifecycle import _ledger
    with db.connection() as conn:
        r = read(conn, ARM, 3.0)
        n, pnl = _ledger(conn, ARM, ["icm-probe"], None)
    assert r.fills == 3 and r.pnl == pytest.approx(3 * -3.2 - 50.0)
    assert n == 3 and pnl == pytest.approx(3 * -3.2 - 50.0)
