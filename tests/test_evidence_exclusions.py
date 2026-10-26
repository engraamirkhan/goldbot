"""Evidence exclusions (quant ruling 2026-10-26, blind; prereg-2026-10-25-high-volume.md Amendment 4): "Trades whose
decision window overlaps a recorded dq quarantine span are void as evidence (n, posterior, cost cells, graduation);
they still count toward H-61 stops and P&L. Decided blind, before any read; criterion v1; adds 0 to K."
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
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
CORRECTED = pd.Timestamp("2026-10-26 15:07:59", tz="UTC")       # C: the corrected bars became visible
RELOAD = pd.Timestamp("2026-10-26 15:09:35", tz="UTC")          # R: the engines' first warm start after C
NOW = pd.Timestamp("2026-10-27 12:00", tz="UTC")
RELOADS = {("icm-probe", REF): (CORRECTED, RELOAD)}


def _cands(*rows: tuple[str, pd.Timestamp], entry: pd.Timestamp | None = None, label_exit: pd.Timestamp | None = None,
           labelled: pd.Timestamp | None = None) -> pd.DataFrame:
    return pd.DataFrame([{"candidate_id": c, "account_id": "icm-probe", "decision_utc": t, "entry_utc": entry,
                          "label_exit_utc": label_exit, "labelled_utc": labelled} for c, t in rows],
                        columns=["candidate_id", "account_id", "decision_utc", "entry_utc", "label_exit_utc",
                                 "labelled_utc"])


def _probe(cid: str, at: pd.Timestamp,
           purpose: Literal["cost_probe", "cost_measurement"] = "cost_measurement") -> cost_probe.ProbeRecord:
    return cost_probe.ProbeRecord(client_order_id=cid, account_id="icm-probe", purpose=purpose, agent_id="scheduled",
                                  side=1, lots=0.01, sent_utc=at, requested_price=2000.0, fill_price=2000.3,
                                  spread_usd=0.2)


def _decide(cands: pd.DataFrame, probes: list[Any] = (),  # type: ignore[assignment]
            reloads: dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]] | None = None) -> dict[str, ev.Exclusion]:
    out = ev.decide(candidates=cands, probes=list(probes), spans_by_account={"icm-probe": [(*SPAN, REF)]},
                    reloads=RELOADS if reloads is None else reloads, now=NOW, reason="tz mislabel")
    return {e.client_order_id: e for e in out}


# ---------------------------------------------------------------------------------------------- the tables
def test_migration_19_adds_append_only_exclusion_and_reload_tables_and_is_additive(tmp_path):
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
    assert ev.record_reload(db, account_id="icm-probe", span=REF, corrected=CORRECTED, reloaded=RELOAD, now=NOW)
    assert not ev.record_reload(db, account_id="icm-probe", span=REF, corrected=CORRECTED, reloaded=RELOAD, now=NOW)
    assert ev.reloads(db) == RELOADS
    with pytest.raises(ValueError, match="before the correction"):
        ev.record_reload(db, account_id="icm-demo", span=REF, corrected=CORRECTED,
                         reloaded=CORRECTED - pd.Timedelta(seconds=1), now=NOW)
    for sql in ("UPDATE evidence_exclusions SET reason = 'x'", "DELETE FROM evidence_exclusions",
                "UPDATE evidence_reloads SET reloaded_ns = 0", "DELETE FROM evidence_reloads"):
        with pytest.raises(sqlite3.IntegrityError, match="append-only"), db.write() as conn:
            conn.execute(sql)
    with sqlite3.connect(tmp_path / "aux.db") as conn:
        assert migrate(conn, "aux", migrations=AUX[:18]) == []           # the older code still opens the file


# ---------------------------------------------------------------------------------------------- criterion v1
def test_a_decision_from_the_span_start_until_the_engine_reloaded_the_corrected_bars_is_void_and_no_later_one():
    """Quant ruling: indicators are recomputed from the stored bars at every decision, so there is no lookback term."""
    got = _decide(_cands(("in", SPAN[0] + pd.Timedelta(hours=1)),
                         ("after-span", SPAN[1] + pd.Timedelta(hours=8)),          # before R: stale bars in memory
                         ("at-reload", RELOAD),
                         ("weeks-later", RELOAD + pd.Timedelta(days=20)),          # a 4h arm too: no lookback
                         ("before", SPAN[0] - pd.Timedelta(minutes=1))))
    assert set(got) == {"in", "after-span"}
    assert got["in"].scope == ev.EXPLORATION_SCOPE and got["in"].span_ref == REF and got["in"].blind


def test_a_label_written_before_the_correction_from_a_path_through_the_span_is_void():
    early = SPAN[0] - pd.Timedelta(minutes=30)
    path = {"entry": early, "label_exit": SPAN[0] + pd.Timedelta(minutes=10)}
    assert set(_decide(_cands(("x", early), **path, labelled=CORRECTED - pd.Timedelta(hours=1)))) == {"x"}
    assert _decide(_cands(("x", early), **path, labelled=CORRECTED)) == {}
    assert _decide(_cands(("x", early), entry=early, label_exit=SPAN[0] - pd.Timedelta(minutes=1),
                          labelled=CORRECTED - pd.Timedelta(hours=1))) == {}


def test_a_span_without_a_recorded_reload_voids_nothing_and_its_later_decisions_are_held_out_in_memory():
    cands = _cands(("in", SPAN[0] + pd.Timedelta(hours=1)), ("late", RELOAD + pd.Timedelta(days=3)),
                   ("before", SPAN[0] - pd.Timedelta(minutes=1)))
    assert _decide(cands, probes=[_probe("p", SPAN[0] + pd.Timedelta(hours=2))], reloads={}) == {}
    assert ev.held_out(cands, {"icm-probe": [(*SPAN, REF)]}, {}) == {"in", "late"}
    assert ev.held_out(cands, {"icm-probe": [(*SPAN, REF)]}, RELOADS) == set()


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


def _script_args(tmp_path: Path, *extra: str) -> list[str]:
    return ["--state-dir", str(tmp_path), "--hosts", "icm-probe=icm", "--now", NOW.isoformat(), *extra]


def test_the_script_is_a_dry_run_by_default_idempotent_with_apply_and_prints_no_r_or_pnl(tmp_path, monkeypatch,
                                                                                      capsys):
    db = tdr._db(tmp_path)
    tdr._cand(db, "x", at=SPAN[0] + pd.Timedelta(hours=1))
    tdr._trade(db, "x", sent=SPAN[0] + pd.Timedelta(hours=1), pnl=-3.0)
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)] if "icm-probe" in sources
                        else [])
    mod = _script()
    monkeypatch.setattr(mod, "_store", lambda a: object())
    args = _script_args(tmp_path, "--corrected", CORRECTED.isoformat(), "--reloaded",
                        f"icm-probe={RELOAD.isoformat()}")
    assert mod.main(args) == 0
    assert ev.excluded_ids(db, "posterior") == set() and ev.reloads(db) == {}      # dry run: nothing written
    assert mod.main([*args, "--apply"]) == 0 and ev.excluded_ids(db, "posterior") == {"x"}
    assert ev.reloads(db) == RELOADS
    assert mod.main([*args, "--apply"]) == 0
    out = capsys.readouterr().out
    assert '"inserted": 0' in out and "pnl" not in out.lower() and "-3" not in out
    tdr._blinded(out)


@pytest.mark.parametrize("extra,why", [((), "reloaded"),
                                       (("--reloaded", f"icm-probe={RELOAD.isoformat()}"), "corrected"),
                                       (("--corrected", CORRECTED.isoformat(), "--reloaded",
                                         "icm-probe=2026-10-26T07:23:00Z"), "before the correction")])
def test_the_script_refuses_a_span_without_a_reload_time_or_a_reload_before_the_correction(tmp_path, monkeypatch,
                                                                                            extra, why):
    tdr._db(tmp_path)
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)])
    mod = _script()
    monkeypatch.setattr(mod, "_store", lambda a: object())
    with pytest.raises(SystemExit, match=why):
        mod.main(_script_args(tmp_path, *extra, "--apply"))
    assert ev.reloads(tdr._db(tmp_path)) == {}


# ---------------------------------------------------------------------------------------------- the nightly learner
def _obs(db: Database, cid: str, at: pd.Timestamp) -> None:
    ex.record_candidate(db, candidate_id=cid, account_id="icm-probe", broker="icm", arm_id="S1A", family="f",
                        style="scalp", tf="1m", side=1, decision_utc=at, stop_dist=2.0, target_dist=2.0,
                        max_hold_s=600, taken=True, mu=0.5, branch="random", u=0.5)
    with db.write() as conn:
        conn.execute("UPDATE exploration_obs SET label_sim_r = 1.0, label_mid_r = 1.0, label_exit_ns = ?, "
                     "labelled_ns = ? WHERE candidate_id = ?",
                     ((at + pd.Timedelta(minutes=10)).value, NOW.value, cid))


def test_the_nightly_learner_never_writes_exclusions_without_a_recorded_reload_and_holds_the_span_out_in_memory(
        tmp_path, monkeypatch):
    from goldbot.ops import jobs
    from tests.test_exploration_broker import _acc, _ctx
    db = Database.open(tmp_path, "aux")
    _obs(db, "before", SPAN[0] - pd.Timedelta(hours=1))
    _obs(db, "in", SPAN[0] + pd.Timedelta(hours=1))
    _obs(db, "late", RELOAD + pd.Timedelta(hours=1))
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)] if "icm-probe" in sources
                        else [])
    arms: Any = {"S1A": SimpleNamespace(style="scalp")}
    ctx = _ctx(tmp_path, [_acc("icm-probe", "icm", arms=arms)])
    rep = jobs.exploration_learn(ctx, NOW)
    assert rep["evidence_exclusions"] == {"inserted": 0, "held_out": 2, "unresolved": ["icm-probe"]}
    assert ev.excluded_ids(db, "posterior") == set()                   # append-only: nothing voided on a guess
    with db.connection() as conn:
        detail = json.loads(conn.execute("SELECT detail FROM exploration_broker_weights").fetchone()[0])
    assert detail["n"] == 1                                            # only the decision before the span learnt
    ev.record_reload(db, account_id="icm-probe", span=REF, corrected=CORRECTED, reloaded=RELOAD, now=NOW)
    rep = jobs.exploration_learn(ctx, NOW + pd.Timedelta(days=1))
    assert rep["evidence_exclusions"] == {"inserted": 1, "held_out": 0, "unresolved": []}
    assert ev.excluded_ids(db, "posterior") == {"in"}


def test_the_nightly_learner_learns_nothing_when_it_cannot_screen_the_evidence(tmp_path, monkeypatch):
    from goldbot.ops import jobs
    from tests.test_exploration_broker import _acc, _ctx

    def boom(*a: Any, **k: Any) -> Any:
        raise OSError("store unreadable")
    monkeypatch.setattr(ev, "screen", boom)
    assert jobs.exploration_learn(_ctx(tmp_path, [_acc("icm-probe", "icm")]), NOW) == \
        {"error": "evidence_exclusions:OSError"}


def test_the_daily_review_alerts_on_a_span_with_no_recorded_reload(tmp_path, monkeypatch):
    tdr._db(tmp_path)
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)] if "icm-probe" in sources
                        else [])
    review = tdr._run(tmp_path)
    assert review.spans_unresolved == ["icm-probe"]
    lines = "\n".join(dr.report_lines(review))
    assert "Review: ALERT quarantine span with no recorded engine restart (Scalp lab): its decisions are held out " \
           "of learning, not voided" in lines
    tdr._blinded(lines)


def test_the_nightly_cost_table_holds_out_scheduled_probes_inside_a_span_with_no_recorded_reload(tmp_path):
    from goldbot.ops import jobs
    db = Database.open(tmp_path, "aux")
    with db.write() as conn:
        for i, at in enumerate((SPAN[0] + pd.Timedelta(hours=1), SPAN[1] + pd.Timedelta(minutes=5))):
            cost_probe.LEDGER.upsert(conn, _probe(f"p{i}", at))
    rows = jobs.cost_probe_rows(tmp_path, "icm-probe", SPAN[0] - pd.Timedelta(days=1), NOW,
                                held=[(*SPAN, REF)])
    assert [p.client_order_id for p in rows] == ["p1"]


# ---------------------------------------------------------------------------------------------- held out, no R yet
def test_a_candidate_decided_before_the_span_whose_label_path_crosses_it_is_held_out_until_r_is_recorded():
    early = SPAN[0] - pd.Timedelta(minutes=30)
    spans = {"icm-probe": [(*SPAN, REF)]}
    crosses = _cands(("x", early), entry=early, label_exit=SPAN[0] + pd.Timedelta(minutes=10), labelled=NOW)
    clear = _cands(("y", early), entry=early, label_exit=SPAN[0] - pd.Timedelta(minutes=1), labelled=NOW)
    assert ev.held_out(crosses, spans, {}) == {"x"}                   # C unknown: any label on that path is held out
    assert ev.held_out(clear, spans, {}) == set()
    assert ev.held_out(crosses, spans, RELOADS) == set()              # recorded: the criterion decides, not memory


def test_the_n_toward_the_300_read_holds_out_a_span_with_no_recorded_r_and_the_h61_stop_still_counts_it(
        tmp_path, monkeypatch):
    db = tdr._db(tmp_path)
    tdr._losers(db, "S1A", 30, start=SPAN[0] - pd.Timedelta(minutes=10))  # a minute apart: 10 before, 20 in the span
    monkeypatch.setattr(ev, "recorded_spans", lambda store, sources, **k: [(*SPAN, REF)] if "icm-probe" in sources
                        else [])
    with db.connection() as conn:
        [st] = labs.arm_statuses(conn, ["icm-probe"], store=object(), now=NOW)
    # decided from the span's start on: 20 held out of n in memory (nothing voided), all 30 still in the stop
    assert ev.excluded_ids(db, "n") == set()
    assert st.n == 10 and st.band == "at_stop" and st.stop_at == 30
    with db.connection() as conn:
        [st] = labs.arm_statuses(conn, ["icm-probe"], store=object(), now=NOW, held_out=set())
    assert st.n == 30
