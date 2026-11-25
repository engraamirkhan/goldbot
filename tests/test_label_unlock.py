"""Label unlock (lane W1-D; red-team gap 1; docs/research/prereg-amendment-2026-11-25-sr-v3-snapshot-admission.md):
the vantage audit reads the account's own symbol, per-timeframe snapshot quotas with a time budget, decision-bar
dedupe, the sr-v3 spec path (early nominal admission, OOS only for judged pairs, horizon embargo) and per-cell
usable-row counts for all nine timeframes. sr-v2 stays as signed."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from goldbot.data.store import Store
from goldbot.db.connection import Database
from goldbot.research import feature_snapshots as fs
from goldbot.research import self_retrain as sr
from tests.test_feature_snapshots import DECISIONS, _cand, _minutes
from tests.test_feature_snapshots import START as FS_START
from tests.test_self_retrain import DAYS, START, _ctx, _feats, _populate, _Trials, _write

pytestmark = pytest.mark.usefixtures("unknown_arms_are_gold", "_test_arms_prenamed")


@pytest.fixture
def _test_arms_prenamed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sr, "PRENAMED", (*sr.PRENAMED, "A", "N"))


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return Database.open(tmp_path, "aux")


# ---------------------------------------------------------------------------------------------- vantage symbol
def test_every_gold_read_of_the_jobs_uses_the_accounts_own_store_symbol_never_a_default():
    from goldbot.ops import jobs
    accs = [SimpleNamespace(account_id="icm-demo", broker="icm", symbol="XAUUSD"),
            SimpleNamespace(account_id="vantage-demo", broker="vantage", symbol="XAUUSD+")]
    assert jobs.gold_symbols(accs) == {"icm-demo": "XAUUSD", "vantage-demo": "XAUUSD+"}
    src = Path(jobs.__file__).read_text()
    assert 'getattr(acc, "symbol", "XAUUSD")' not in src and src.count("gold_symbols(ctx.accounts)") >= 3


def test_the_weekly_audit_reads_vantage_minutes_under_its_own_symbol(tmp_path, db, monkeypatch):
    from goldbot.ops import jobs
    from goldbot.research import snapshot_audit as sa
    seen: dict[str, Any] = {}

    def audit(_db: Any, _store: Any, *, since: Any, symbol_of: Any = None) -> dict[str, Any]:
        seen["symbol_of"] = symbol_of
        return {"clean": set(), "excluded": set(), "unread": set(), "counts": {}}
    monkeypatch.setattr(sa, "audit", audit)
    ctx = _ctx(tmp_path, _Trials())
    ctx.accounts = [SimpleNamespace(account_id="vantage-demo", broker="vantage", symbol="XAUUSD+")]
    slot = pd.Timestamp("2026-11-29 15:00", tz="UTC")
    jobs.self_retrain(ctx, slot)
    assert seen["symbol_of"] == {"vantage-demo": "XAUUSD+"}


# ---------------------------------------------------------------------------------------------- snapshot quotas
def _keys(rows: list[tuple[str, str, int]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["account_id", "tf", "decision_ns"])


def test_per_timeframe_quotas_serve_the_slow_timeframes_first_and_share_each_fairly_between_accounts():
    rows = [("a", "1m", i) for i in range(100)] + [("b", "1m", 1000 + i) for i in range(100)]
    rows += [("a", "4h", 5000 + i) for i in range(3)] + [("b", "1h", 6000 + i) for i in range(4)]
    got = fs.select_keys(_keys(rows), quotas={"1m": 10, "1h": 2, "4h": 5}, limit=1000)
    assert list(got["tf"].iloc[:5]) == ["4h", "4h", "4h", "1h", "1h"]
    one = got[got["tf"] == "1m"]
    assert len(one) == 10 and (one["account_id"] == "a").sum() == 5
    assert set(fs.TF_QUOTAS) == set(sr.CELL_TFS) - {"1d", "1w"} and len(sr.CELL_TFS) == 9   # 1d/1w: no history
    small = fs.scaled_quotas(fs.TF_QUOTAS, 600)
    assert sum(small.values()) <= 600 and all(v >= 1 for v in small.values()) and set(small) == set(fs.TF_QUOTAS)
    assert fs.scaled_quotas(fs.TF_QUOTAS, 5000) == fs.TF_QUOTAS
    assert len(fs.select_keys(_keys(rows), quotas={"1m": 10, "1h": 2, "4h": 5}, limit=4)) == 4   # the night's cap


def test_two_candidates_on_one_decision_bar_are_snapshotted_once_at_the_earlier_decision(tmp_path):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _minutes(), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    at = DECISIONS[0]
    _cand(db, "early", at)
    _cand(db, "late", at + pd.Timedelta(seconds=2))
    rep = fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm"}, symbol_of={"icm-probe": "XAUUSD"},
                              as_of=FS_START + pd.Timedelta(days=40), quotas=fs.TF_QUOTAS)
    assert rep["decisions"] == 1 and rep["written"] == 2
    snaps = fs.read_snapshots(db)
    assert set(snaps.index) == {"early", "late"} and snaps.loc[["early"]].reset_index(drop=True).equals(snaps.loc[["late"]].reset_index(drop=True))


def test_the_snapshot_night_stops_at_its_deadline_and_never_runs_in_the_research_window(tmp_path):
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _minutes(), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    for i, at in enumerate(DECISIONS[:6]):
        _cand(db, f"c{i}", at)
    t = iter(pd.Timestamp("2026-11-25 23:20", tz="UTC") + pd.Timedelta(minutes=m) for m in range(100))
    rep = fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm"}, symbol_of={"icm-probe": "XAUUSD"},
                              as_of=FS_START + pd.Timedelta(days=40), quotas=fs.TF_QUOTAS,
                              deadline=pd.Timestamp("2026-11-25 23:22", tz="UTC"), clock=lambda: next(t))
    assert rep["stopped"] == "deadline" and 0 < rep["decisions"] < 6
    night = pd.Timestamp("2026-11-25 23:10", tz="UTC")
    assert fs.snapshot_deadline(night) == night + fs.SNAPSHOT_BUDGET
    late = pd.Timestamp("2026-11-26 00:30", tz="UTC")
    assert fs.snapshot_deadline(late) == pd.Timestamp("2026-11-26 00:45", tz="UTC")       # never into 00:45-04:05
    assert fs.snapshot_deadline(pd.Timestamp("2026-11-26 01:30", tz="UTC")) is None
    assert fs.snapshot_deadline(pd.Timestamp("2026-11-26 04:10", tz="UTC")) is not None


# ---------------------------------------------------------------------------------------------- sr-v3 admission
def test_sr_v2_is_unchanged_and_sr_v3_admits_early_nominal_rows_only_for_a_passed_broker_and_a_live_fed_day(db):
    assert sr.SR_V2.version == sr.SPEC_VERSION == "sr-v2" and sr.SR_V2.nominal_from == sr.NOMINAL_FROM
    early = _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal")
    other = _write(db, arm="A", day=0, k=1, r=1.0, x=_feats(np.random.default_rng(1)), pit="nominal")
    cut = START + pd.Timedelta(days=3)
    rows, _ = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, nominal_ok={early, other})
    assert rows.empty                                                          # sr-v2: before NOMINAL_FROM, never
    v3 = sr.SR_V3.model_copy(update={"nominal_early_brokers": ("icm",)})
    rows, counts = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, spec=v3, nominal_ok={early})
    assert set(rows["candidate_id"]) == {early} and counts["nominal_not_audited_clean"] == 1
    failed = sr.SR_V3.model_copy(update={"nominal_early_brokers": ()})
    rows, _ = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, spec=failed, nominal_ok={early})
    assert rows.empty                                                          # a broker that failed the audit


def test_the_live_feed_audit_admits_an_early_nominal_row_only_when_its_last_day_is_all_live_minutes(db, monkeypatch):
    from goldbot.research import snapshot_audit as sa
    at = START + pd.Timedelta(days=2)
    a = _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal", start=at)
    b = _write(db, arm="A", day=0, k=1, r=1.0, x=_feats(np.random.default_rng(1)), pit="nominal", start=at)
    ts = pd.date_range(at - pd.Timedelta(days=5), at + pd.Timedelta(hours=1), freq="1min", tz="UTC")
    rank = np.zeros(len(ts), dtype=int)
    rank[(ts > at + pd.Timedelta(minutes=5)) & (ts <= at + pd.Timedelta(minutes=20))] = 1   # a back-fill b reads
    v = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "written": False, "rank": rank})
    seen: dict[str, Any] = {}

    def versions(store: Any, **kw: Any) -> pd.DataFrame:
        seen.update(kw)
        return v
    monkeypatch.setattr(sa, "minute_versions", versions)
    got = sa.live_feed_audit(db, object(), before=sr.NOMINAL_FROM, brokers={"icm"},
                             symbol_of={"icm-probe": "XAUUSD"})
    assert got["clean"] == {a} and got["excluded"] == {b} and seen["symbol"] == "XAUUSD"
    assert sa.live_feed_audit(db, object(), before=sr.NOMINAL_FROM, brokers=set(), symbol_of={})["clean"] == set()


# ---------------------------------------------------------------------------------------------- quant Q2
def test_sr_v3_computes_out_of_sample_outcomes_only_for_judged_pairs(db, monkeypatch):
    _populate(db, arms=("A", "L"))
    cutoff = START + pd.Timedelta(days=DAYS + 1)
    calls: list[int] = []
    real = sr.oos_metrics

    def counted(oos: pd.DataFrame, control: pd.DataFrame, **kw: Any) -> dict[str, Any]:
        calls.append(len(oos))
        return real(oos, control, **kw)
    monkeypatch.setattr(sr, "oos_metrics", counted)
    read = sr.compute_broker(db, broker="icm", cutoff=cutoff, holdout=None, named_controls={}, rules={},
                             spec=sr.SR_V3)
    assert calls == []                                                         # nothing read before the judged set
    for v in read["arms"].values():
        assert set(v["metrics"]) <= set(sr.COUNT_KEYS)
    judged = sr.complete_judged([read])
    assert judged == [("icm", "A")] and len(calls) == 1
    assert read["arms"]["A"]["metrics"]["edge_vs_nomodel"] is not None
    assert set(read["arms"]["L"]["metrics"]) <= set(sr.COUNT_KEYS)          # L is live, fitted, never read
    assert sr.decide([read], serve=True)[("icm", "L")]["judged"] is False


def test_sr_v3_embargoes_a_training_row_for_the_arms_longest_horizon():
    rows = pd.DataFrame({"max_hold_s": [3 * 86400, 3600]})
    assert sr.embargo_of(rows, sr.SR_V2) == pd.Timedelta(days=1)
    assert sr.embargo_of(rows, sr.SR_V3) == pd.Timedelta(days=3) + sr.MAX_SEND_DELAY


# ---------------------------------------------------------------------------------------------- per-cell counts
def test_every_run_records_usable_row_counts_per_broker_class_and_all_nine_timeframes_counts_only(db):
    _populate(db, days=12)
    cutoff = START + pd.Timedelta(days=13)
    _, counts = sr.load_rows(db, broker="icm", cutoff=cutoff, holdout=None)
    cells = sr.cells(db, broker="icm", by_tf=counts["by_tf"], since=START)
    gold = [c for c in cells if c["class"] == "metal"]
    assert sorted(c["tf"] for c in gold) == sorted(sr.CELL_TFS)
    m15 = next(c for c in gold if c["tf"] == "15m")
    assert m15["usable"] == counts["with_features"] > 0 and m15["labels"] == counts["labels"]
    assert all(c["usable"] == 0 for c in gold if c["tf"] != "15m")
    banned = {"label_sim_r", "label_mid_r", "r", "mean", "edge", "sim_r", "mid_r"}
    assert not any(k in banned for c in cells for k in c)


def test_the_job_records_the_cells_for_sr_v2_and_the_sr_v3_census_in_the_run_detail(tmp_path, db):
    from goldbot.ops.jobs import self_retrain
    _populate(db, days=12)
    slot = START + pd.Timedelta(days=20, hours=7)
    self_retrain(_ctx(tmp_path, _Trials(), clock=lambda: slot), slot)
    from tests.test_self_retrain import _detail
    d = _detail(db)
    assert {c["tf"] for c in d["cells"]} == set(sr.CELL_TFS)
    assert {c["tf"] for c in d["cells_sr_v3"]} == set(sr.CELL_TFS)
    assert all(set(c) <= set(sr.CELL_KEYS) for c in d["cells"] + d["cells_sr_v3"])


def test_live_feed_audit_excludes_a_backfill_three_days_before_the_decision(db, monkeypatch):
    """Quant M1 (review of #451): every minute the snapshot read (ENGINE_MINUTES), not only the last day."""
    from goldbot.research import snapshot_audit as sa
    at = START + pd.Timedelta(days=4)
    cid = _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal", start=at)
    ts = pd.date_range(at - pd.Timedelta(days=5), at, freq="1min", tz="UTC")
    rank = np.zeros(len(ts), dtype=int)
    rank[(ts >= at - pd.Timedelta(days=3)) & (ts < at - pd.Timedelta(days=3) + pd.Timedelta(minutes=10))] = 1
    v = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "written": False, "rank": rank})
    monkeypatch.setattr(sa, "minute_versions", lambda store, **kw: v)
    got = sa.live_feed_audit(db, object(), before=sr.NOMINAL_FROM, brokers={"icm"}, symbol_of={"icm-probe": "XAUUSD"})
    assert got["excluded"] == {cid} and got["clean"] == set()


def test_a_reselection_never_snapshots_a_decision_outside_its_accounts_read(tmp_path):
    """Code review HIGH of #451 (its repro): another account dropping out must not hand a loaded account a decision
    its minutes were never read for (a stale frame stored ok)."""
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", _minutes(), source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    t0, t1 = FS_START + pd.Timedelta(days=20), DECISIONS[-1]
    _cand(db, "a4h", t0, tf="4h")
    _cand(db, "b4h", t0, account="vantage-x", tf="4h")
    _cand(db, "a1h", t1, tf="1h")
    fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm", "vantage-x": "vantage"},
                        symbol_of={"icm-probe": "XAUUSD", "vantage-x": "XAUUSD+"},
                        as_of=FS_START + pd.Timedelta(days=40), quotas={"4h": 2, "1h": 5}, max_decisions=2)
    with db.connection() as conn:
        got = conn.execute("SELECT candidate_id, decision_ns, available_utc_max_ns FROM exploration_feature_snapshots "
                           "WHERE status = 'ok'").fetchall()
    assert got
    for _cid, dec, avail in got:          # the frame's last bar is at most one 4h bar before its decision
        assert int(dec) - int(avail) <= pd.Timedelta(hours=4).value


def test_a_snapshot_decided_inside_the_holdout_is_refused_and_one_after_it_reads_the_engines_full_memory(tmp_path):
    """ADR 0100 (quant ruling on #451): holdout minutes only as the engine's lookback of a post-holdout decision; the
    stored row equals engine.frames.frame on the same minutes; a decision inside the holdout is refused."""
    from goldbot.data import holdout_guard
    from tests.test_feature_snapshots_pit import _engine_row
    shift = pd.Timedelta(days=30)
    m = _minutes()
    m["ts_utc"], m["visible_at"] = m["ts_utc"] - shift, m["visible_at"] - shift
    assert holdout_guard.in_holdout(m).any()
    store = Store(tmp_path / "data")
    store.append("bars_1m_live", m, source="icm", symbol="XAUUSD")
    db = Database.open(tmp_path, "aux")
    after = DECISIONS[0] - shift
    inside = holdout_guard.HOLDOUT_END - pd.Timedelta(days=2)
    _cand(db, "after", after)
    _cand(db, "inside", inside)
    fs.snapshot_pending(db, store, broker_of={"icm-probe": "icm"}, symbol_of={"icm-probe": "XAUUSD"},
                        as_of=after + pd.Timedelta(days=1), quotas=fs.TF_QUOTAS)
    with db.connection() as conn:
        st = dict(conn.execute("SELECT candidate_id, status || ':' || COALESCE(detail, '') "
                               "FROM exploration_feature_snapshots").fetchall())
    assert st["inside"] == "error:HoldoutSealed" and st["after"].startswith("ok")
    row: dict[Any, Any] = fs.read_snapshots(db).loc["after"].astype(float).to_dict()
    engine = _engine_row(m, "1h", after)
    assert set(engine) <= set(row)
    for k, v in engine.items():
        assert (v is None and pd.isna(row[k])) or np.isclose(float(row[k]), v, rtol=1e-9, atol=1e-12), k


def test_icm_admitted_ids_are_identical_with_and_without_symbol_of(db, monkeypatch):
    """Quant ruling (1) on #451: the vantage symbol fix is a bug fix inside sr-v2; icm's admission is unchanged."""
    from goldbot.research import snapshot_audit as sa
    at = sr.NOMINAL_FROM + pd.Timedelta(days=2)
    ids = [_write(db, arm="A", day=0, k=k, r=1.0, x=_feats(np.random.default_rng(k)), pit="nominal", start=at)
           for k in range(3)]
    ts = pd.date_range(at - pd.Timedelta(days=3), at + pd.Timedelta(hours=2), freq="1min", tz="UTC")
    v = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "written": True, "rank": 0})
    seen: list[str] = []

    def versions(store: Any, **kw: Any) -> pd.DataFrame:
        seen.append(kw["symbol"])
        return v
    monkeypatch.setattr(sa, "minute_versions", versions)
    a = sa.audit(db, object(), since=sr.NOMINAL_FROM)
    b = sa.audit(db, object(), since=sr.NOMINAL_FROM, symbol_of={"icm-probe": "XAUUSD", "vantage-demo": "XAUUSD+"})
    assert a["clean"] == b["clean"] == set(ids) and seen == ["XAUUSD", "XAUUSD"]


def test_reference_minutes_older_than_a_day_are_counted_in_sr_v2_and_excluded_from_sr_v3(db, monkeypatch):
    """Quant H2 of #451: sr-v2's remaining exposure is counted every run (nominal_rank3_beyond_24h)."""
    from goldbot.research import snapshot_audit as sa
    at = sr.NOMINAL_FROM + pd.Timedelta(days=2)
    cid = _write(db, arm="A", day=0, k=0, r=1.0, x=_feats(np.random.default_rng(0)), pit="nominal", start=at)
    ts = pd.date_range(sa.STAMPS_FROM, at, freq="1min", tz="UTC")
    rank = np.where((ts >= at - pd.Timedelta(days=2)) & (ts < at - pd.Timedelta(days=2) + pd.Timedelta(minutes=5)),
                    3, 0)
    v = pd.DataFrame({"ts_utc": ts, "visible_at": ts + pd.Timedelta(minutes=1), "written": rank == 0, "rank": rank})
    monkeypatch.setattr(sa, "minute_versions", lambda store, **kw: v)
    got = sa.audit(db, object(), since=sr.NOMINAL_FROM)
    assert got["clean"] == {cid} and got["rank3_beyond_24h"] == {cid}
    cut = at + pd.Timedelta(days=2)
    rows, c = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, nominal_ok=got["clean"],
                           rank3_beyond_24h=got["rank3_beyond_24h"])
    assert len(rows) == 1 and c["nominal_rank3_beyond_24h"] == 1
    rows, c = sr.load_rows(db, broker="icm", cutoff=cut, holdout=None, nominal_ok=got["clean"], spec=sr.SR_V3,
                           rank3_beyond_24h=got["rank3_beyond_24h"])
    assert rows.empty and c["nominal_rank3_beyond_24h"] == 1


def test_the_duplicate_bar_share_counts_an_arms_bars_decided_on_two_accounts():
    at = pd.Timestamp("2026-11-20 10:00", tz="UTC")
    rows = pd.DataFrame({"tf": ["15m"] * 4, "account_id": ["icm-demo", "icm-probe", "icm-demo", "icm-demo"],
                         "decision_utc": [at, at + pd.Timedelta(seconds=3), at + pd.Timedelta(minutes=15),
                                          at + pd.Timedelta(minutes=30)]})
    assert sr.dup_bar_share(rows) == 0.5
