"""Evidence exclusions (quant ruling 2026-10-26, blind; prereg-2026-10-25-high-volume.md Amendment 4): "Trades whose
decision window overlaps a recorded dq quarantine span are void as evidence (n, posterior, cost cells, graduation);
they still count toward H-61 stops and P&L. Decided blind, before any read; criterion v1; adds 0 to K."
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import pytest

from goldbot.db.connection import Database
from goldbot.db.migrations import AUX, compat_back_to, migrate
from goldbot.engine import cost_probe
from goldbot.research import daily_review as dr
from goldbot.research import evidence_exclusions as ev
from goldbot.research import exploration_data as ex
from goldbot.telegram import labs
from tests import test_daily_review as tdr

SPAN = (pd.Timestamp("2026-10-25 22:02", tz="UTC"), pd.Timestamp("2026-10-26 06:17", tz="UTC"))
REF = ev.span_ref("icm-probe", *SPAN)
RELOAD = pd.Timestamp("2026-10-26 07:23", tz="UTC")
NOW = pd.Timestamp("2026-10-27 12:00", tz="UTC")


def _cands(*rows: tuple[str, str, str, pd.Timestamp]) -> pd.DataFrame:
    return pd.DataFrame([{"candidate_id": c, "account_id": "icm-probe", "arm_id": a, "tf": tf, "decision_utc": t}
                         for c, a, tf, t in rows])


def _probe(cid: str, at: pd.Timestamp,
           purpose: Literal["cost_probe", "cost_measurement"] = "cost_measurement") -> cost_probe.ProbeRecord:
    return cost_probe.ProbeRecord(client_order_id=cid, account_id="icm-probe", purpose=purpose, agent_id="scheduled",
                                  side=1, lots=0.01, sent_utc=at, requested_price=2000.0, fill_price=2000.3,
                                  spread_usd=0.2)


def _decide(cands: pd.DataFrame, probes: list[Any] = (), reloaded: dict[str, pd.Timestamp] | None = None,  # type: ignore[assignment]
            timeframes: dict[str, str] | None = None) -> dict[str, ev.Exclusion]:
    out = ev.decide(candidates=cands, probes=list(probes), spans_by_account={"icm-probe": [(*SPAN, REF)]},
                    timeframes=timeframes or {}, reloaded=reloaded or {}, now=NOW, reason="tz mislabel")
    return {e.client_order_id: e for e in out}


# ---------------------------------------------------------------------------------------------- the table
def test_migration_19_adds_an_append_only_exclusions_table_and_is_additive(tmp_path):
    m = AUX[18]
    assert (m.version, m.name, m.additive) == (19, "evidence_exclusions", True)
    assert compat_back_to(AUX, 19) == compat_back_to(AUX, 18)
    db = Database.open(tmp_path, "aux")
    e = ev.Exclusion(client_order_id="c1", kind="exploration", account_id="icm-probe", scope=ev.EXPLORATION_SCOPE,
                     reason="r", span_ref=REF, decided_utc=NOW)
    assert ev.record(db, [e]) == 1 and ev.record(db, [e]) == 0            # idempotent: the first row stands
    with db.connection() as conn:
        row = conn.execute("SELECT kind, scope, criterion_version, blind FROM evidence_exclusions").fetchone()
    assert row == ("exploration", json.dumps(list(ev.SCOPES)), "v1", 1)
    for sql in ("UPDATE evidence_exclusions SET reason = 'x'", "DELETE FROM evidence_exclusions"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"), db.write() as conn:
            conn.execute(sql)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        assert migrate(conn, "aux", migrations=AUX[:18]) == []           # the older code still opens the file


# ---------------------------------------------------------------------------------------------- criterion v1
def test_the_lookback_is_the_window_the_engine_feeds_the_arms_timeframe():
    assert ev.lookback("15m") == pd.Timedelta(minutes=15 * 201)            # 200 bars + the one closing
    assert ev.lookback("1m") == pd.Timedelta(minutes=1000)                # at least LOOKBACK_1M minutes
    assert ev.lookback("4h") == pd.Timedelta(hours=4 * 201)


def test_a_candidate_whose_decision_window_overlaps_the_span_is_void_for_every_evidence_scope():
    end = SPAN[1]
    got = _decide(_cands(("in", "S1A", "1m", SPAN[0] + pd.Timedelta(hours=1)),
                         ("15m-near", "I2A", "15m", end + pd.Timedelta(hours=40)),     # 50.25 h lookback reaches it
                         ("15m-far", "I2A", "15m", end + pd.Timedelta(hours=51)),
                         ("1m-late", "S1A", "1m", end + pd.Timedelta(hours=20)),       # 1,000 minutes do not
                         ("before", "S1A", "1m", SPAN[0] - pd.Timedelta(minutes=1))))
    assert set(got) == {"in", "15m-near"}
    assert got["in"].scope == ev.EXPLORATION_SCOPE and got["in"].span_ref == REF and got["in"].blind


def test_the_lookback_comes_from_the_arm_spec_timeframe_not_the_rows_own():
    late = SPAN[1] + pd.Timedelta(hours=40)
    assert set(_decide(_cands(("x", "W1A", "1m", late)), timeframes={"W1A": "4h"})) == {"x"}
    assert _decide(_cands(("x", "W1A", "1m", late)), timeframes={}) == {}


def test_a_decision_before_the_engine_reloaded_corrected_bars_is_void_even_outside_its_lookback():
    at = SPAN[1] + pd.Timedelta(minutes=1020)                              # past a 1m arm's 1,000-minute window
    assert _decide(_cands(("x", "S1A", "1m", at))) == {}
    got = _decide(_cands(("x", "S1A", "1m", at)), reloaded={"icm-probe": at + pd.Timedelta(minutes=1)})
    assert "reloaded" in got["x"].reason
    assert _decide(_cands(("x", "S1A", "1m", at)), reloaded={"icm-probe": at}) == {}


def test_only_scheduled_probes_entered_inside_the_span_are_void_and_only_for_the_cost_cells():
    got = _decide(_cands(), probes=[_probe("p-in", SPAN[0] + pd.Timedelta(hours=2)),
                                    _probe("p-out", SPAN[1] + pd.Timedelta(minutes=1)),
                                    _probe("mirror", SPAN[0] + pd.Timedelta(hours=2), purpose="cost_probe")])
    assert set(got) == {"p-in"} and got["p-in"].scope == ("cost_cells",) and got["p-in"].kind == "cost_probe"


# ---------------------------------------------------------------------------------------------- the readers
def _void(db: Database, *cids: str, scope: tuple[ev.Scope, ...] = ev.EXPLORATION_SCOPE) -> None:
    ev.record(db, [ev.Exclusion(client_order_id=c, kind="exploration", account_id="icm-probe", scope=scope,
                                reason="r", span_ref=REF, decided_utc=NOW) for c in cids])


def test_the_nightly_posterior_never_reads_a_void_candidate(tmp_path):
    db = Database.open(tmp_path, "aux")
    t0 = pd.Timestamp("2026-10-26 08:00", tz="UTC")
    for i in range(6):
        ex.record_candidate(db, candidate_id=f"c{i}", account_id="icm-probe", broker="icm", arm_id="S1A", family="f",
                            style="scalp", tf="1m", side=1, decision_utc=t0 + pd.Timedelta(minutes=i), stop_dist=2.0,
                            target_dist=2.0, max_hold_s=600, taken=True, mu=0.5, branch="random", u=0.5)
        with db.write() as conn:
            conn.execute("UPDATE exploration_obs SET label_sim_r = 1.0, label_mid_r = 1.0, label_exit_ns = ? "
                         "WHERE candidate_id = ?", ((t0 + pd.Timedelta(hours=1)).value, f"c{i}"))
    _void(db, "c0", "c1")
    out = ex.learn(db, broker="icm", as_of=t0 + pd.Timedelta(days=1), arms={"S1A": "scalp"})
    with db.connection() as conn:
        detail = json.loads(conn.execute("SELECT detail FROM exploration_broker_weights").fetchone()[0])
    assert out["weights"] and detail["n"] == 4
    assert len(ex.read_obs(db, broker="icm", evidence="posterior")) == 4 and len(ex.read_obs(db, broker="icm")) == 6


def test_the_n_toward_the_300_read_leaves_void_trades_out_and_the_h61_stop_still_counts_them(tmp_path):
    db = tdr._db(tmp_path)
    tdr._losers(db, "S1A", 30)                                             # -30 R: the scalp stop is reached at 30
    _void(db, *(f"S1A-{i}" for i in range(10)))
    with db.connection() as conn:
        [st] = labs.arm_statuses(conn, ["icm-probe"])
    assert st.n == 20 and st.band == "at_stop" and st.stop_at == 30


def test_the_refit_cells_leave_void_trades_out(tmp_path):
    db = tdr._db(tmp_path)
    for i in range(200):
        tdr._trade(db, f"f{i}", sent=tdr.LONDON + pd.Timedelta(seconds=10 * i), requested=2000.0, fill=2000.4)
    _void(db, "f0", scope=("cost_cells",))
    assert tdr._run(tmp_path).refits == []                                 # 199 admissible fills: not yet


def test_the_daily_review_shows_the_void_trades_as_a_count_line(tmp_path):
    db = tdr._db(tmp_path)
    tdr._trade(db, "a", sent=tdr.LONDON)
    tdr._trade(db, "b", sent=tdr.LONDON + pd.Timedelta(minutes=1))
    _void(db, "a")
    review = tdr._run(tmp_path)
    assert review.evidence_void == 1
    lines = "\n".join(dr.report_lines(review))
    assert "1 trade void as evidence (dq quarantine; H-61 stops and P&L keep it)" in lines
    tdr._blinded(lines)


def test_the_nightly_cost_table_leaves_void_scheduled_probe_fills_out(tmp_path):
    from goldbot.ops import jobs
    db = Database.open(tmp_path, "aux")
    at = pd.Timestamp("2026-10-26 09:00", tz="UTC")
    with db.write() as conn:
        for i in range(3):
            cost_probe.LEDGER.upsert(conn, _probe(f"p{i}", at + pd.Timedelta(minutes=15 * i)))
    _void(db, "p0", scope=ev.PROBE_SCOPE)
    rows = jobs.cost_probe_rows(tmp_path, "icm-probe", at - pd.Timedelta(days=1), at + pd.Timedelta(days=1))
    assert [p.client_order_id for p in rows] == ["p1", "p2"]


# ---------------------------------------------------------------------------------------------- the script
def _script() -> Any:
    path = Path(__file__).resolve().parents[1] / "scripts" / "evidence_exclusions.py"
    spec = importlib.util.spec_from_file_location("evidence_exclusions_script", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_script_is_a_dry_run_by_default_idempotent_with_apply_and_prints_no_r_or_pnl(tmp_path, monkeypatch,
                                                                                      capsys):
    db = tdr._db(tmp_path)
    tdr._cand(db, "x", at=SPAN[0] + pd.Timedelta(hours=1))
    tdr._trade(db, "x", sent=SPAN[0] + pd.Timedelta(hours=1), pnl=-3.0)
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)] if "icm-probe" in sources
                        else [])
    mod = _script()
    args = ["--state-dir", str(tmp_path), "--hosts", "icm-probe=icm", "--reloaded", f"icm-probe={RELOAD.isoformat()}",
            "--now", NOW.isoformat()]
    monkeypatch.setattr(mod, "_store", lambda a: object())
    assert mod.main(args) == 0
    assert ev.excluded_ids(db, "posterior") == set()                        # dry run: nothing written
    assert mod.main([*args, "--apply"]) == 0 and ev.excluded_ids(db, "posterior") == {"x"}
    assert mod.main([*args, "--apply"]) == 0
    out = capsys.readouterr().out
    assert '"inserted": 0' in out and "pnl" not in out.lower() and "-3" not in out
    tdr._blinded(out)


def test_the_script_refuses_an_account_with_a_span_but_no_reload_time(tmp_path, monkeypatch):
    tdr._db(tmp_path)
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)])
    mod = _script()
    monkeypatch.setattr(mod, "_store", lambda a: object())
    with pytest.raises(SystemExit, match="reloaded"):
        mod.main(["--state-dir", str(tmp_path), "--hosts", "icm-probe=icm", "--now", NOW.isoformat()])


# ---------------------------------------------------------------------------------------------- the nightly learner
def test_the_nightly_learner_records_new_exclusions_before_it_learns_and_learns_nothing_if_it_cannot(tmp_path,
                                                                                                  monkeypatch):
    """A 4h arm decided weeks after the span still reads it: the exclusions grow nightly, before the posterior."""
    from goldbot.ops import jobs
    from tests.test_exploration_broker import _acc, _ctx
    db = Database.open(tmp_path, "aux")
    at = SPAN[1] + pd.Timedelta(days=20)
    ex.record_candidate(db, candidate_id="w", account_id="icm-probe", broker="icm", arm_id="W1A", family="f",
                        style="swing", tf="4h", side=1, decision_utc=at, stop_dist=5.0, target_dist=5.0,
                        max_hold_s=3600, taken=True, mu=0.5, branch="random", u=0.5)
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)] if "icm-probe" in sources
                        else [])
    slot = at + pd.Timedelta(days=1)
    rep = jobs.exploration_learn(_ctx(tmp_path, [_acc("icm-probe", "icm")]), slot)
    assert rep["evidence_exclusions"] == {"inserted": 1} and ev.excluded_ids(db, "posterior") == {"w"}

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("store unreadable")
    monkeypatch.setattr(ev, "compute", boom)
    rep = jobs.exploration_learn(_ctx(tmp_path, [_acc("icm-probe", "icm")]), slot)
    assert rep == {"error": "evidence_exclusions:OSError"}
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM exploration_broker_weights").fetchone()[0] <= 1
