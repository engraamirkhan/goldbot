"""A specialist arm's exploration rows carry the specialist's own name as their family (box_break, session_open, ...),
not the rule word "specialist" every wave-1 arm shares; rows written before the change read the same through the
ledger, which labels an arm from its registry row. The RiskGate's `Intent.family` stays the arm's rule (no gate
behaviour moves with a label)."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from goldbot.api import trades_ledger as tl
from goldbot.api.strategy_labels import arm_family, load_catalog
from goldbot.db import Database
from goldbot.engine import exploration
from goldbot.engine.exploration import Arm, ExplorationRecord
from goldbot.research import arms

SPEC = Path(__file__).resolve().parents[1] / "config" / "exploration_arms.yaml"
NOW = pd.Timestamp("2026-10-27 12:00", tz="UTC")


def _spec() -> dict[str, arms.Arm]:
    return {a.id: a for a in arms.load_arms(SPEC)}


def test_a_specialist_arm_records_its_specialists_name_and_any_other_arm_its_rule() -> None:
    spec = _spec()
    xbb = Arm(spec=spec["XBB15"], style="intraday")
    assert xbb.ledger_family == "box_break" and xbb.family == "specialist"      # the gate's family is unchanged
    s1a = Arm(spec=spec["S1A"], style="scalp")
    assert s1a.ledger_family == s1a.family == "session_vwap_fade"
    for a in spec.values():
        assert a.family == arm_family(a.rule, a.params)                       # one definition, the ledger's


def _row(state: Path, cid: str, family: str) -> None:
    rec = ExplorationRecord(
        client_order_id=cid, account_id="icm-demo", arm_id="XBB15", family=family, tf="15m", side=1, lots=0.01,
        signal_utc=NOW - pd.Timedelta(hours=1), sent_utc=NOW - pd.Timedelta(hours=1), atr_usd=3.0, stop_usd=3.0,
        target_usd=6.0, hold_s=3600, requested_price=2400.0, spread_usd=0.2, fill_price=2400.1, stop_loss=2.4,
        exit_utc=NOW - pd.Timedelta(minutes=30), exit_price=2406.1, exit_reason="target", swap=0.0, commission=0.07,
        pnl=1.2, censored=False, fx_acct_per_usd=0.8)
    with Database.open(state, "aux").write() as conn:
        exploration.LEDGER.upsert(conn, rec)


def test_old_rows_labelled_specialist_and_new_rows_read_as_the_same_family(tmp_path, enabled_accounts) -> None:
    enabled_accounts("icm-demo")
    _row(tmp_path, "old", "specialist")
    _row(tmp_path, "new", "box_break")
    rows = tl.build_ledger(tmp_path, NOW, cat=load_catalog()).rows
    fam = {r.trade_id.rsplit(":", 1)[-1]: r.family for r in rows if r.kind == "exploration"}
    assert fam == {"old": "box_break", "new": "box_break"}
