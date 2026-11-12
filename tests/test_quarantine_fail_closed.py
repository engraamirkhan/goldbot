"""Backlog 158: a quarantine-span read that fails is unknown, never "no spans".

`quarantine._recorded` caught every exception from its dq_events read and returned no spans (and `_backed` treated a
failing quarantine-table read as "not backed"): a store read that fails (a corrupt part, a duckdb major change) then
silently dropped every span. D12 graded quarantined minutes as feed faults, `record_span` could write a span twice, and
research kept minutes it should leave out. Now the read error surfaces as `QuarantineReadError`, and each caller
treats it as unsafe. A missing table or a schema without the span columns is still no spans.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pandas as pd
import pytest

from goldbot.data import crossfeed as cf
from goldbot.data import quarantine as qr
from goldbot.data.resample import ticks_to_1m
from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.ops.watchdog import loop_heartbeat_path
from goldbot.research import daily_review as dr
from goldbot.research import evidence_exclusions as ev
from goldbot.telegram import labs
from tests import test_daily_review as tdr
from tests.test_crossfeed import FakeBroker, as_broker_m1
from tests.test_store_quarantine import NOW, T0, T1, _ahead, _seed

BROKEN = ("dq_events", "ticks_quarantine", "bars_quarantine")


def _break(monkeypatch: pytest.MonkeyPatch, tables: tuple[str, ...] = BROKEN) -> None:
    """Store.read of `tables` fails as a corrupt part does; every other table reads as before."""
    real = Store.read

    def read(self: Store, table: str, **kw: Any) -> pd.DataFrame:
        if table in tables:
            raise duckdb.IOException(f"corrupt part in {table}")
        return real(self, table, **kw)
    monkeypatch.setattr(Store, "read", read)


def _quarantined(tmp_path: Path) -> Store:
    store, _, _ = _seed(tmp_path)
    qr.move_rows(store, "ticks", source="icm-demo", symbol="XAUUSD", select=_ahead, reason=qr.TZ_MISLABEL, now=NOW,
                 apply=True)
    qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason=qr.TZ_MISLABEL, detail="t",
                   now=NOW)
    return store


# ---------------------------------------------------------------------------------------------- the module
def test_a_failing_dq_events_read_raises_instead_of_returning_no_spans(tmp_path, monkeypatch):
    store = _quarantined(tmp_path)
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1) == [(T0, T1)]
    _break(monkeypatch, ("dq_events",))
    with pytest.raises(qr.QuarantineReadError, match="dq_events of icm-demo/XAUUSD unreadable.*IOException"):
        qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1)


def test_a_failing_quarantine_table_read_raises_instead_of_not_backed(tmp_path, monkeypatch):
    store = _quarantined(tmp_path)
    _break(monkeypatch, ("ticks_quarantine",))
    with pytest.raises(qr.QuarantineReadError, match="ticks_quarantine"):
        qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1)


def test_record_span_writes_nothing_when_the_recorded_spans_cannot_be_read(tmp_path, monkeypatch):
    store = _quarantined(tmp_path)
    _break(monkeypatch, ("dq_events",))
    with pytest.raises(qr.QuarantineReadError):
        qr.record_span(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1, reason=qr.TZ_MISLABEL,
                       detail="again", now=NOW)
    monkeypatch.undo()
    assert len(store.read("dq_events", source="icm-demo")) == 1          # no duplicate span


def test_a_missing_table_or_a_schema_without_span_columns_is_still_no_spans(tmp_path):
    store = Store(tmp_path / "data")
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1) == []          # no dq_events
    store.append("dq_events", pd.DataFrame({"ts_utc": [T0], "check": ["spike"], "severity": ["warning"],
                                            "detail": ["x"]}), source="icm-demo", dedupe=False)
    assert qr.spans(store, source="icm-demo", symbol="XAUUSD", start=T0, end=T1) == []          # no span columns
    assert qr.spans(store, source="icm-probe", symbol="XAUUSD", start=T0, end=T1) == []         # another source


# ---------------------------------------------------------------------------------------------- the callers
def test_d12_reconcile_fails_and_the_job_reports_it_instead_of_grading_the_span_as_a_feed_fault(tmp_path,
                                                                                                monkeypatch):
    from goldbot.ops.jobs import feed_reconcile
    from tests.test_crossfeed import _job_ctx
    store = _quarantined(tmp_path)
    loop_heartbeat_path(tmp_path, "icm-demo").write_text(json.dumps(
        {"phase": "loop", "ts": NOW.timestamp() - 5, "started": (NOW - pd.Timedelta(days=3)).timestamp()}))
    ticks = store.read("ticks", source="icm-demo")
    broker = FakeBroker(as_broker_m1(ticks_to_1m(pd.concat([ticks, store.read("ticks_quarantine",
                                                                                source="icm-demo")]))))
    _break(monkeypatch, ("dq_events",))
    with pytest.raises(qr.QuarantineReadError):
        cf.reconcile_account(store, broker, "icm-demo", "XAUUSD", NOW, state_dir=tmp_path)
    assert not cf.reconcile_file(tmp_path, "icm-demo").exists()        # no report graded without the spans
    with pytest.raises(RuntimeError, match="feed_reconcile failed for icm-demo: QuarantineReadError"):
        feed_reconcile(_job_ctx(tmp_path, lambda acc: broker), NOW)


def test_evidence_exclusions_raise_and_the_nightly_learner_does_not_run(tmp_path, monkeypatch):
    from goldbot.ops import jobs
    from tests.test_exploration_broker import _acc, _ctx
    _break(monkeypatch)
    store = Store(tmp_path / "data")
    with pytest.raises(qr.QuarantineReadError):
        ev.spans_of(store, {"icm-probe": "icm"}, symbol="XAUUSD", now=NOW)
    ctx = _ctx(tmp_path, [_acc("icm-probe", "icm")])
    ctx.store = store
    assert jobs.exploration_learn(ctx, NOW) == {"error": "evidence_exclusions:QuarantineReadError"}


def test_unreadable_spans_never_lower_a_probe_raised_slippage_cell(tmp_path, monkeypatch):
    """Trading-safety S-1: unknown spans must not drop a probe-measured slippage raise back to the prior (a lower
    cost means more entries and bigger size, runner `_cost_atr`). The probes are counted with nothing held out:
    apply_probe_slippage only ever raises a cell, so a possibly-void probe can only overstate the cost."""
    from goldbot.execution.costs import CostTable
    from goldbot.ops.jobs import nightly_costs
    from tests.test_probe_slippage import PRIOR, _ctx, _rows, _write
    _write(tmp_path, _rows(60, 0.30))
    _break(monkeypatch)
    out = nightly_costs(_ctx(tmp_path), NOW)["icm-demo"]
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None and out["probe_fills"] == 60
    cell = table.slippage["london:market"]
    assert not cell.from_prior and cell.mean > PRIOR and table.probe_slippage["london"].raised
    assert any("quarantine spans unreadable" in n for n in table.notes)


def test_unreadable_spans_with_probes_at_the_prior_keep_the_prior(tmp_path, monkeypatch):
    from goldbot.execution.costs import CostTable
    from goldbot.ops.jobs import nightly_costs
    from tests.test_probe_slippage import PRIOR, _ctx, _rows, _write
    _write(tmp_path, _rows(60, -0.07))                     # favourable fills: never credited
    _break(monkeypatch)
    nightly_costs(_ctx(tmp_path), NOW)
    table = CostTable.load(tmp_path / "costs_icm-demo.json")
    assert table is not None
    assert all(c.mean >= PRIOR for c in table.slippage.values())


def _raising_store() -> Any:
    def read(table: str, **_: Any) -> pd.DataFrame:
        if table in BROKEN:
            raise duckdb.IOException(f"corrupt part in {table}")
        return pd.DataFrame()
    return SimpleNamespace(read=read)


def test_the_labs_n_holds_every_candidate_out_when_the_spans_cannot_be_read(tmp_path):
    db = tdr._db(tmp_path)
    tdr._losers(db, "S1A", 30)
    with db.connection() as conn:
        [st] = labs.arm_statuses(conn, ["icm-probe"], store=_raising_store(), now=NOW)
    # n understated, never overstated, until the spans read again; the H-61 stop still counts every trade
    assert st.n == 0 and st.band == "at_stop" and st.stop_at == 30
    with db.connection() as conn:
        [st] = labs.arm_statuses(conn, ["icm-probe"], store=tdr._store(), now=NOW)
    assert st.n == 30


def test_the_daily_review_records_an_unreadable_incident_when_the_spans_cannot_be_read(tmp_path):
    db = tdr._db(tmp_path)
    tdr._losers(db, "S1A", 3)
    review = tdr._run(tmp_path, store=_raising_store())
    hits = [i for i in review.incidents if i.kind == "unreadable" and "quarantine spans" in i.detail]
    assert len(hits) == 1 and review.spans_unresolved == []
    issue = dr._issue(review, False, tdr.S.daily_review)
    assert issue is not None and "- unreadable: quarantine spans unreadable (IOException)" in issue[1]
    assert ev.excluded_ids(Database.open(tmp_path, "aux"), "n") == set()      # nothing voided on an unknown
