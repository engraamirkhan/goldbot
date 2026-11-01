"""Growth sprint weekly read (scripts/growth_sprint_read.py; docs/research/growth-sprint-2026-11-01.md section 3):
net R from the ledger, the matched day-bootstrap difference, real and virtual equity, the top-10 % share, the
small-n warning, a clean empty report and read-only access."""
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pandas as pd
import pytest

SINCE = pd.Timestamp("2026-11-01 22:00", tz="UTC")


def _mod() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "growth_sprint_read.py"
    spec = importlib.util.spec_from_file_location("growth_sprint_read", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                   # dataclasses resolve annotations through the module
    spec.loader.exec_module(mod)
    return mod


gs = _mod()


def _db(tmp_path: Path) -> Path:
    p = tmp_path / "aux.db"
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE exploration_trades (client_order_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
                  "arm_id TEXT NOT NULL, position_id INTEGER, ts_ns INTEGER NOT NULL, closed INTEGER NOT NULL, "
                  "exit_ns INTEGER, body TEXT NOT NULL)")
        c.execute("CREATE TABLE balance_snapshots (key TEXT PRIMARY KEY, account_id TEXT NOT NULL, "
                  "ts_ns INTEGER NOT NULL, body TEXT NOT NULL)")
        c.execute("CREATE TABLE balance_deals (key TEXT PRIMARY KEY, account_id TEXT NOT NULL, ts_ns INTEGER NOT NULL, "
                  "kind TEXT NOT NULL, body TEXT NOT NULL)")
    return p


def _trade(p: Path, cid: str, arm: str, sent: str, pnl: float | None, *, stop_loss: float = 10.0,
           closed: int = 1, account: str = "icm-demo", **extra: object) -> None:
    sent_t = pd.Timestamp(sent, tz="UTC")
    body = {"client_order_id": cid, "account_id": account, "arm_id": arm, "signal_utc": sent_t.isoformat(),
            "sent_utc": sent_t.isoformat(), "exit_utc": (sent_t + pd.Timedelta(minutes=30)).isoformat(),
            "stop_loss": stop_loss, "pnl": pnl, "censored": False, **extra}
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO exploration_trades VALUES (?, ?, ?, NULL, ?, ?, NULL, ?)",
                  (cid, account, arm, sent_t.value, closed, json.dumps(body)))


def _snap(p: Path, ts: str, equity: float) -> None:
    t = pd.Timestamp(ts, tz="UTC")
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO balance_snapshots VALUES (?, 'icm-demo', ?, ?)",
                  (f"icm-demo|{ts}", t.value, json.dumps({"equity": equity})))


def test_empty_report_is_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _db(tmp_path)
    assert gs.main(["--aux", str(p), "--since", "2026-11-01T22:00Z"]) == 0
    out = capsys.readouterr().out
    assert "nothing to read" in out and "n/a" in out and "Traceback" not in out
    assert "cannot separate edge from luck" in out
    assert gs.main(["--aux", str(p), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["arms"][0]["n"] == 0


def test_net_r_ledgers_top_share_and_matched_diff(tmp_path: Path) -> None:
    p = _db(tmp_path)
    for i, (day, r_arm, r_ctl) in enumerate([("02", 2.0, -1.0), ("03", -1.0, -1.0), ("04", 3.0, 0.5)]):
        _trade(p, f"a{i}", "GS1", f"2026-11-{day} 09:00", r_arm * 10)
        _trade(p, f"c{i}", "GS1C", f"2026-11-{day} 10:00", r_ctl * 10)
    _trade(p, "old", "GS1", "2026-10-30 09:00", 50.0)              # before the week start
    _trade(p, "open", "GS1", "2026-11-04 12:00", None, closed=0)    # still open
    _trade(p, "other", "GS1", "2026-11-04 12:00", 10.0, account="icm-probe")
    _trade(p, "nopnl", "GS1C", "2026-11-04 12:00", None)            # closed, P&L unreadable
    rd = gs.build(p, "icm-demo", SINCE)
    a, c = rd.arms
    assert (a.n, c.n, c.unreadable) == (3, 3, 1)
    assert a.mean_r == pytest.approx(4 / 3) and a.total_r == pytest.approx(4.0)
    assert a.top10_share == pytest.approx(3 / 4)                     # ceil(0.3) = 1 trade: the 3 R
    assert c.top10_share is None                                     # total R <= 0
    l10 = a.ledgers[1]
    assert l10.multiple == pytest.approx(1.2 * 0.9 * 1.3)
    assert l10.max_drawdown == pytest.approx(0.1) and not l10.doubled
    assert rd.matched_days == 3 and rd.diff_mean_r == pytest.approx(4 / 3 - (-1.5 / 3))
    assert rd.diff_ci90 is not None and rd.diff_ci90[0] <= rd.diff_mean_r <= rd.diff_ci90[1]
    assert rd.warning                                                # n = 3 < 40


def test_pyramid_adds_fold_into_their_first_unit(tmp_path: Path) -> None:
    p = _db(tmp_path)
    _trade(p, "u1", "GS1", "2026-11-02 09:00", 10.0)
    _trade(p, "u2", "GS1", "2026-11-02 09:30", 5.0, group_id="u1", stop_loss=4.0)
    rd = gs.build(p, "icm-demo", SINCE)
    assert rd.arms[0].n == 1 and rd.arms[0].total_r == pytest.approx(1.5)   # (10 + 5) / the first unit's 10


def test_real_equity_from_the_snapshot_at_the_week_start(tmp_path: Path) -> None:
    p = _db(tmp_path)
    for ts, e in [("2026-10-30 23:25", 9000.0), ("2026-10-31 23:25", 10000.0), ("2026-11-02 23:25", 12000.0),
                  ("2026-11-03 23:25", 9000.0), ("2026-11-04 23:25", 11000.0)]:
        _snap(p, ts, e)
    rd = gs.build(p, "icm-demo", SINCE)
    assert rd.snapshots == 4 and rd.real_multiple == pytest.approx(1.1)
    assert rd.real_max_drawdown == pytest.approx(0.25)


def test_a_deposit_is_not_growth(tmp_path: Path) -> None:
    p = _db(tmp_path)
    _snap(p, "2026-11-01 21:00", 1000.0)
    _snap(p, "2026-11-02 23:25", 11100.0)                          # +100 traded, +10,000 deposited
    with sqlite3.connect(p) as c:
        c.execute("INSERT INTO balance_deals VALUES ('icm-demo|1', 'icm-demo', ?, 'deposit', ?)",
                  (pd.Timestamp("2026-11-02 08:00", tz="UTC").value, json.dumps({"amount": 10000.0})))
    rd = gs.build(p, "icm-demo", SINCE)
    assert rd.real_multiple == pytest.approx(1.1) and rd.real_max_drawdown == 0.0


def test_ledger_ruin_floors_at_zero_and_doubling() -> None:
    assert gs.ledger(np.array([-6.0, 1.0]), 0.2).multiple == 0.0
    big = gs.ledger(np.array([5.0, 5.0]), 0.2)
    assert big.doubled and big.touched_2x and big.multiple == pytest.approx(4.0)


def test_week_start_is_the_sunday_open() -> None:
    assert gs.week_start(pd.Timestamp("2026-11-07 12:00", tz="UTC")) == SINCE
    assert gs.week_start(pd.Timestamp("2026-11-01 21:59", tz="UTC")) == SINCE - pd.Timedelta(days=7)
    assert gs.week_start(SINCE) == SINCE


def test_reads_only(tmp_path: Path) -> None:
    p = _db(tmp_path)
    _trade(p, "a", "GS1", "2026-11-02 09:00", 10.0)
    p.chmod(0o444)
    before = p.read_bytes()
    gs.build(p, "icm-demo", SINCE)
    assert p.read_bytes() == before
