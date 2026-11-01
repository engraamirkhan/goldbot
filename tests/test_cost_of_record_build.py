"""goldbot.research.cost_of_record: a fixture aux.db reproduces every cell of the table, void fills are dropped from
entries, closes, stop exits and exit legs, moved stops are counted apart, and the interval resamples whole minutes."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from goldbot.db.connection import Database
from goldbot.engine import cost_probe
from goldbot.engine.exploration import LEDGER, ExplorationRecord
from goldbot.research import arms
from goldbot.research import cost_of_record as C
from goldbot.research import evidence_exclusions as EV

HOSTS = {"acct-a": "brk", "acct-v": "vbrk"}
LO, HI = pd.Timestamp("2026-10-26 22:00", tz="UTC"), pd.Timestamp("2026-10-27 22:00", tz="UTC")
LONDON = pd.Timestamp("2026-10-27 09:00", tz="UTC")       # inside the London window
NY = pd.Timestamp("2026-10-27 15:00", tz="UTC")


def _trade(cid: str, acct: str, sent: pd.Timestamp, *, side: int = 1, spread: float = 0.08, slip: float = 0.0,
           reason: str = "time_exit", over: float = 0.0, arm: str = "S0", comm_gbp: float = 0.06,
           stop: float = 3.0) -> ExplorationRecord:
    req = 2400.0
    fill = req + side * slip
    exit_px = (req - side * stop) - side * over if reason == "stop" else req + side
    return ExplorationRecord(client_order_id=cid, account_id=acct, arm_id=arm, family="f", tf="1m", side=side, lots=0.01,
                             signal_utc=sent, sent_utc=sent, atr_usd=stop, stop_usd=stop, target_usd=2 * stop, hold_s=60,
                             requested_price=req, spread_usd=spread, fill_price=fill, exit_utc=sent + pd.Timedelta(minutes=5),
                             exit_price=exit_px, exit_reason=reason, commission=-comm_gbp, swap=0.0, fx_acct_per_usd=0.75)


def _seed(tmp_path: Path) -> dict[str, pd.DataFrame]:
    db = Database.open(tmp_path, "aux")
    rows = [
        _trade("a1", "acct-a", LONDON, spread=0.08, slip=0.10, reason="stop", over=0.20),
        _trade("a2", "acct-a", LONDON, side=-1, spread=0.10, slip=-0.04, reason="stop", over=0.10),   # same minute as a1
        _trade("a3", "acct-a", LONDON + pd.Timedelta(minutes=1), spread=0.06, slip=0.0),
        _trade("a4", "acct-a", NY, spread=0.12, slip=0.30, reason="stop", over=-0.90),                 # moved stop
        _trade("a5", "acct-a", NY, arm="BXR15", spread=0.10, slip=0.10, reason="stop", over=0.05),     # trailing arm
        _trade("void", "acct-a", NY, spread=9.0, slip=9.0, reason="stop", over=9.0),                   # voided
        _trade("v1", "acct-v", LONDON, spread=0.10, slip=0.20, reason="stop", over=0.0, comm_gbp=0.04),
    ]
    with db.write() as conn:
        for r in rows:
            LEDGER.upsert(conn, r)
        cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
            client_order_id="p1", account_id="acct-v", agent_id="sched", side=1, lots=0.01, sent_utc=LONDON,
            requested_price=2400.0, fill_price=2400.0, exit_price=2399.95, exit_bid=2400.0, exit_ask=2400.1,
            purpose="cost_measurement"))
    EV.record(db, [EV.Exclusion(client_order_id="void", kind="exploration", account_id="acct-a", scope=("cost_cells",),
                                reason="test", span_ref="dq_events:x", decided_utc=LO)])
    legs = pd.DataFrame({"ts_utc": [LONDON + pd.Timedelta(minutes=5), NY + pd.Timedelta(minutes=5),
                                    NY + pd.Timedelta(minutes=5)],
                         "client_order_id": ["a3", "a4", "void"], "agent_id": ["explore:S0"] * 3, "side": [1, 1, 1],
                         "requested": [2401.0, 2401.0, 2401.0], "filled": [2400.98, 2401.01, 2390.0]})
    return {"acct-a": legs}


def _frames(tmp_path: Path) -> C.Frames:
    legs = _seed(tmp_path)
    import sqlite3
    conn = sqlite3.connect(f"file:{tmp_path / 'aux.db'}?mode=ro", uri=True)
    try:
        return C.read_frames(conn, legs, HOSTS, LO, HI)
    finally:
        conn.close()


def test_a_target_leg_never_offsets_a_stops_cost(tmp_path: Path) -> None:
    import sqlite3
    _seed(tmp_path)
    at = LONDON + pd.Timedelta(minutes=5)
    legs = pd.DataFrame({"ts_utc": [at, at], "client_order_id": ["t1", "t2"], "agent_id": ["explore:S0"] * 2,
                         "side": [1, 1], "kind": ["stop", "target"], "requested": [2401.0, 2401.0],
                         "filled": [2400.9, 2402.0]})
    conn = sqlite3.connect(f"file:{tmp_path / 'aux.db'}?mode=ro", uri=True)
    try:
        fr = C.read_frames(conn, {"acct-a": legs}, HOSTS, LO, HI)
    finally:
        conn.close()
    assert list(fr.legs["xslip"]) == [pytest.approx(0.1)]


def test_a_fixture_aux_db_reproduces_each_cell(tmp_path: Path) -> None:
    fr = _frames(tmp_path)
    assert fr.n_void == 1
    t = C.build_table(fr, version="cor-test", window_utc=(LO.isoformat(), HI.isoformat()), resamples=200)
    a = t["cells"]["brk"]
    lon, ny, al = a["london"], a["newyork"], a["all"]
    comm = 0.06 / (0.01 * 100 * 0.75)
    assert lon["n_fills"] == 3 and lon["n_minutes"] == 2
    assert lon["spread_usd"] == pytest.approx(0.08) and lon["entry_slip_usd"] == pytest.approx(0.02)
    assert lon["exit_slip_usd"] == pytest.approx(0.02) and lon["exit_slip_source"] == "strategy_exit_legs"
    assert lon["commission_usd"] == pytest.approx(comm)
    assert lon["round_trip_usd"] == pytest.approx(0.08 + 0.02 + 0.02 + comm)
    assert lon["n_stop_exits"] == 2 and lon["overshoot_usd"] == pytest.approx(0.15) and lon["n_moved_stop_exits"] == 0
    # New York: the void fill is gone everywhere; the moved stop and the trailing arm are counted apart
    assert ny["n_fills"] == 2 and ny["spread_usd"] == pytest.approx(0.11) and ny["entry_slip_usd"] == pytest.approx(0.20)
    assert ny["n_exit_legs"] == 1 and ny["exit_slip_usd"] == pytest.approx(-0.01)
    assert ny["n_stop_exits"] == 0 and ny["n_moved_stop_exits"] == 2
    assert al["n_fills"] == 5 and al["n_stop_exits"] == 2 and al["n_moved_stop_exits"] == 2
    for cell in a.values():
        lo, hi = cell["round_trip_ci90"]
        assert lo <= cell["round_trip_usd"] <= hi
    # a broker with no exit leg reads its own scheduled probe: long, exit at 2399.95 under the 2400.00 bid
    v = t["cells"]["vbrk"]["london"]
    assert v["exit_slip_source"] == "scheduled_probes" and v["exit_slip_usd"] == pytest.approx(0.05)
    assert v["commission_usd"] == pytest.approx(0.04 / 0.75)


def test_the_rendered_yaml_loads_as_the_cost_of_record(tmp_path: Path) -> None:
    t = C.build_table(_frames(tmp_path), version="cor-test", window_utc=(LO.isoformat(), HI.isoformat()), resamples=200)
    text = C.render_yaml(t)
    rec = arms.CostOfRecord.model_validate(yaml.safe_load(text))
    assert rec.version == "cor-test" and rec.cells["brk"]["london"].n_fills == 3
    assert "acct-a" not in text and "acct-v" not in text          # aggregates only: no account in the table


def test_exits_at_the_exact_stop_price_mark_the_overshoot_untrusted() -> None:
    n = C.MIN_STOPS_FOR_TRUST
    ts = list(range(n))
    fr = C.Frames(entries=pd.DataFrame({"broker": ["b"] * n, "session": ["london"] * n, "minute": ts,
                                        "spread": [0.1] * n, "slip": [0.0] * n}),
                  closes=pd.DataFrame({"broker": ["b"] * n, "session": ["london"] * n, "minute": ts, "comm": [0.05] * n,
                                       "stop": [True] * n, "over": [0.0] * n, "moved": [False] * n}),
                  legs=pd.DataFrame(columns=C.COLUMNS["legs"]), probes=pd.DataFrame(columns=C.COLUMNS["probes"]),
                  n_void=0)
    t = C.build_table(fr, version="v", window_utc=("a", "b"), resamples=100)
    assert t["cells"]["b"]["london"]["overshoot_trusted"] is False
    assert t["cells"]["b"]["all"]["overshoot_trusted"] is False       # decided on the broker's pooled stop exits


def test_the_interval_resamples_whole_minutes() -> None:
    rng = np.random.default_rng(1)
    v = rng.normal(0.0, 0.5, 200)
    one = C.clustered_means(v, np.arange(200), np.random.default_rng(7), 2000)
    # every fill copied into its own minute: twice the fills, the same clusters, so the same interval
    twice = C.clustered_means(np.repeat(v, 2), np.repeat(np.arange(200), 2), np.random.default_rng(7), 2000)
    assert np.percentile(twice, 95) == pytest.approx(np.percentile(one, 95))
    # treated as independent fills the copies would shrink it by about 1/sqrt(2)
    naive = C.clustered_means(np.repeat(v, 2), np.arange(400), np.random.default_rng(7), 2000)
    assert np.percentile(naive, 95) - np.mean(v) < 0.8 * (np.percentile(one, 95) - np.mean(v))


def test_the_committed_table_is_the_builders_output() -> None:
    """The repo table carries the builder's header and the clustered interval (regenerate it, never hand-edit)."""
    text = (Path(__file__).resolve().parents[1] / "config" / "cost_of_record.yaml").read_text()
    assert "GENERATED by `python -m goldbot.research.cost_of_record`" in text
    assert "clustered by minute" in yaml.safe_load(text)["interval"]
