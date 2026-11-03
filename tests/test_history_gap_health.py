"""history_gap:<account> (vantage-demo 2026-10-26..11-03): an account whose agents were refused `history_gap` at every
decision close for hours took no specialist entry at all, and no health check said so. The check warns, and the owner
is told, when the engine publishes a hole now and the decisions journal shows the refused agents decided nothing else
over the last HISTORY_GAP_WINDOW_H hours."""
import json
from pathlib import Path

import pandas as pd

from goldbot.data.store import Store
from goldbot.ops import health
from goldbot.ops.health import AccountRef
from tests.test_health import NOW, SETTINGS, make_ctx, write

ACC = AccountRef(account_id="vantage-demo", symbol="XAUUSD+")
GAP = {"1h": {"from": "2026-10-02T20:57:00+00:00", "to": "2026-10-05T00:00:00+00:00", "open_min": 118,
              "at": "2026-10-07T13:00:00+00:00"}}


def _ctx(tmp_path: Path, rows: list[tuple[pd.Timestamp, str, str]], gaps: dict | None = None, symbol: str = "XAUUSD+"):
    s = SETTINGS.model_copy(update={"data_root": str(tmp_path / "data")})
    write(tmp_path / "engine_vantage-demo.json", {"history_gap": GAP if gaps is None else gaps})
    if rows:
        journal = pd.DataFrame([{"ts_utc": ts, "account_id": ACC.account_id, "agent_id": agent, "action": action,
                                 "p": 0.0, "mult": 0.0, "proposal_id": None, "detail": json.dumps({})}
                                for ts, agent, action in rows])
        Store(s.data_root).append("decisions", journal, source=ACC.account_id, symbol=symbol, dedupe=False)
    return make_ctx(tmp_path, settings=s, accounts=[ACC])


def _hourly(n: int, agents: tuple[str, ...] = ("trend-g0-a", "tsmom-g0-b"), action: str = "history_gap:1h",
            end: pd.Timestamp = NOW) -> list[tuple[pd.Timestamp, str, str]]:
    return [(end.floor("h") - pd.Timedelta(hours=h), a, action) for h in range(n) for a in agents]


def test_every_close_refused_by_history_gap_for_hours_warns_and_the_owner_is_told(tmp_path):
    c = health.check_history_gap(_ctx(tmp_path, _hourly(6)), ACC)
    assert c is not None and c.status == "warn" and c.name == "history_gap:vantage-demo"
    assert "6 closes" in c.reason and "1h" in c.reason and "2026-10-02T20:57" in c.reason
    assert c.name.startswith(health.NOTIFY_ON_WARN)


def test_an_agent_that_also_decided_otherwise_is_not_starved(tmp_path):
    rows = _hourly(6) + [(NOW.floor("h") - pd.Timedelta(minutes=30), "trend-g0-a", "flat")]
    c = health.check_history_gap(_ctx(tmp_path, rows), ACC)
    assert c is not None and c.status == "ok"


def test_a_short_burst_of_refusals_while_the_fill_runs_is_ok(tmp_path):
    c = health.check_history_gap(_ctx(tmp_path, _hourly(2)), ACC)
    assert c is not None and c.status == "ok"


def test_a_hole_the_engine_no_longer_publishes_is_ok_so_the_warning_recovers(tmp_path):
    c = health.check_history_gap(_ctx(tmp_path, _hourly(6), gaps={}), ACC)
    assert c is not None and c.status == "ok"


def test_no_hole_and_no_refusal_adds_no_line(tmp_path):
    assert health.check_history_gap(_ctx(tmp_path, [], gaps={}), ACC) is None


def test_refusals_older_than_the_window_do_not_count(tmp_path):
    old = _hourly(6, end=NOW - pd.Timedelta(hours=health.HISTORY_GAP_WINDOW_H))
    c = health.check_history_gap(_ctx(tmp_path, old), ACC)
    assert c is not None and c.status == "ok"


def test_the_journal_is_read_under_the_account_symbol(tmp_path):
    """vantage-demo's gold decisions live under symbol=XAUUSD+, not the settings' XAUUSD."""
    c = health.check_history_gap(_ctx(tmp_path, _hourly(6), symbol="XAUUSD"), ACC)
    assert c is not None and c.status == "ok"
    c = health.check_history_gap(_ctx(tmp_path, _hourly(6)), ACC)
    assert c is not None and c.status == "warn"


def test_no_settings_or_no_engine_report_skips_the_check(tmp_path):
    assert health.check_history_gap(make_ctx(tmp_path, settings=None, accounts=[ACC]), ACC) is None
    s = SETTINGS.model_copy(update={"data_root": str(tmp_path / "data")})
    assert health.check_history_gap(make_ctx(tmp_path, settings=s, accounts=[ACC]), ACC) is None
