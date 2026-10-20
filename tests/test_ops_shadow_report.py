"""`goldbot run shadow-report`: the read-only operator view of the shadow book, the cost-probe ledger and the engine's
history-gap / guard state, with the H-52 evidence cut applied by default."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pandas as pd

from goldbot.db.connection import Database
from goldbot.engine import cost_probe
from goldbot.engine.shadow import ShadowTrade, VersionBook
from goldbot.ops import shadow_report
from goldbot.research.promotion import EVIDENCE_START_UTC
from tests.test_api_home_cards import ACC, LOGIN

OLD = EVIDENCE_START_UTC - pd.Timedelta(hours=5)         # opened before the history-gap fix: quarantined
NEW = EVIDENCE_START_UTC + pd.Timedelta(hours=2)


def _run(tmp_path: Path, *argv: str) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = shadow_report.main(["--state-dir", str(tmp_path), *argv], enabled=[ACC])
    return code, buf.getvalue()


def _trade(agent: str, ts: pd.Timestamp, closed: bool, ret: float | None = 0.002, taken: bool = True) -> ShadowTrade:
    return ShadowTrade(version="v1", agent_id=agent, side=1, entry_ts=ts, entry=2400.0, stop=2395.0, target=2410.0,
                       max_bars=8, p=0.7, taken=taken, timeframe="1h",
                       exit_ts=ts + pd.Timedelta(hours=3) if closed else None, exit=2410.0 if closed else None,
                       barrier="target" if closed else None, ret=ret if closed else None)


def _book(state: Path) -> None:
    b = VersionBook(version="v1", started_utc=OLD, open=[_trade("mfi-g0-1h", NEW + pd.Timedelta(hours=4), False)],
                    closed=[_trade("box-g0-1h", NEW, True), _trade("box-g0-1h", OLD, True),
                            _trade("box-g0-1h", NEW, True, taken=False)])
    (state / "shadow_book.json").write_text(json.dumps({"v1": b.model_dump(mode="json")}))


def _probes(state: Path) -> None:
    db = Database.open(state, "aux")
    with db.write() as conn:
        for cid, ts in (("cp-old", OLD), ("cp-new", NEW)):
            cost_probe.LEDGER.upsert(conn, cost_probe.ProbeRecord(
                client_order_id=cid, account_id=ACC, agent_id="mfi-g0", side=1, lots=0.01, sent_utc=ts,
                requested_price=2400.0, fill_price=2400.3, spread_usd=0.25, commission=0.07, position_id=9,
                exit_utc=ts + pd.Timedelta(hours=1), exit_price=2402.3, exit_reason="target", pnl=1.8))


def _engine(state: Path) -> None:
    e = {"account": ACC, "mode": "demo",
         "history_gap": {"1h": {"from": "2026-10-19T01:00:00+00:00", "to": "2026-10-19T03:00:00+00:00", "open_min": 120,
                                "at": "2026-10-19T04:00:00+00:00"}},
         "history_backfill": {"status": "done"}, "currency": "USD", "lot_terms": {"contract_oz": 100.0},
         "throttle": {"enabled": True, "agents": {"box-g0-1h": {"blocked": ["loss_streak"], "cooldown_until": None},
                                                  "mfi-g0-1h": {"blocked": [], "cooldown_until": None}}}}
    (state / f"engine_{ACC}.json").write_text(json.dumps(e))


def _all(tmp_path: Path) -> None:
    _book(tmp_path)
    _probes(tmp_path)
    _engine(tmp_path)


def test_default_hides_trades_opened_before_the_evidence_start_and_says_how_many(tmp_path):
    _all(tmp_path)
    code, out = _run(tmp_path)
    assert code == 0
    assert "2 taken trade(s), 1 quarantined (history_gap) hidden" in out
    assert "[quarantined:" not in out
    assert "cost probes: 1 row(s), 1 quarantined hidden" in out


def test_include_quarantined_shows_earlier_rows_clearly_marked(tmp_path):
    _all(tmp_path)
    _, out = _run(tmp_path, "--include-quarantined")
    assert sum("[quarantined: history_gap]" in x for x in out.splitlines()) == 2        # one trade, one probe
    assert "3 taken trade(s)" in out and "hidden" not in out


def test_each_shadow_trade_shows_entry_exit_side_r_gross_and_exit_reason_open_and_closed(tmp_path):
    _all(tmp_path)
    _, out = _run(tmp_path)
    closed = next(x for x in out.splitlines() if "closed" in x and "target" in x)
    assert "long" in closed and EVIDENCE_START_UTC.strftime("%Y-%m-%d") in closed
    assert "R +0.96" in closed                                  # 0.002 * 2400 / 5 risk
    opened = next(x for x in out.splitlines() if x.strip().startswith("open"))
    assert "out -" in opened and "R ?" in opened
    assert "untaken" not in out and "box-g0-1h" in out and "mfi-g0-1h" in out


def test_cost_probe_row_shows_requested_vs_fill_spread_commission_exit_and_round_trip(tmp_path):
    _all(tmp_path)
    _, out = _run(tmp_path)
    line = next(x for x in out.splitlines() if "req 2400.00" in x)
    for want in ("fill 2400.30", "spread 0.25", "comm 0.07", "@ 2402.30", "target"):
        assert want in line
    assert "round trip 0.20 USD/oz" in line                       # gross 2.00/oz less net 1.80/oz


def test_engine_history_gap_and_guard_state_are_printed_per_agent(tmp_path):
    _all(tmp_path)
    _, out = _run(tmp_path)
    assert "history_gap: 1h 120 min" in out
    assert "guard box-g0-1h: blocked loss_streak" in out and "guard mfi-g0-1h: clear" in out


def test_since_cuts_older_rows(tmp_path):
    _all(tmp_path)
    _, out = _run(tmp_path, "--since", (NEW + pd.Timedelta(hours=1)).isoformat())
    trades = out.split("cost probes:")[0]
    assert "1 taken trade(s)" in trades and "mfi-g0-1h" in trades and "box-g0-1h" not in trades


def test_account_identifiers_are_masked_in_text_and_json(tmp_path):
    _all(tmp_path)
    _, text = _run(tmp_path)
    _, raw = _run(tmp_path, "--json")
    assert LOGIN not in text and LOGIN not in raw
    assert "icm-demo-…67" in text and json.loads(raw)["engines"][0]["account"] == "icm-demo-…67"


def test_json_output_is_one_document(tmp_path):
    _all(tmp_path)
    code, out = _run(tmp_path, "--json")
    doc = json.loads(out)
    assert code == 0 and set(doc) >= {"shadow", "cost_probes", "engines", "evidence_start_utc"}


def test_an_unreadable_shadow_book_is_named_and_exits_1(tmp_path):
    _all(tmp_path)
    (tmp_path / "shadow_book.json").write_text("{not json")
    code, out = _run(tmp_path)
    assert code == 1 and "shadow book: UNREADABLE" in out


def test_the_report_writes_nothing_and_does_not_migrate_the_ledger(tmp_path):
    _all(tmp_path)
    before = {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir() if p.suffix == ".json"}
    _run(tmp_path)
    assert before == {p.name: p.stat().st_mtime_ns for p in tmp_path.iterdir() if p.suffix == ".json"}


def test_a_bad_since_or_unknown_account_exits_2(tmp_path):
    assert _run(tmp_path, "--since", "yesterday-ish")[0] == 2
    assert _run(tmp_path, "--account", "nope")[0] == 2


def _line(out: str, needle: str) -> str:
    return next(x for x in out.splitlines() if needle in x)


def test_aux_db_is_opened_read_only_journal_mode_and_files_unchanged(tmp_path):
    import sqlite3
    _probes(tmp_path)
    db = tmp_path / "aux.db"
    c = sqlite3.connect(db)
    c.execute("PRAGMA journal_mode=DELETE")                     # rollback mode: a WAL-forcing open would change it
    c.close()
    _engine(tmp_path)
    before = sorted(p.name for p in tmp_path.iterdir()), db.stat().st_mtime_ns, db.stat().st_mode
    code, out = _run(tmp_path)
    assert code == 0 and "cost probes: 1 row(s)" in out
    assert (sorted(p.name for p in tmp_path.iterdir()), db.stat().st_mtime_ns, db.stat().st_mode) == before
    assert sqlite3.connect(db).execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_round_trip_on_a_gbp_account_uses_the_published_fx_and_contract_and_is_marked_approx(tmp_path):
    _probes(tmp_path)
    e = {"account": ACC, "currency": "GBP", "usd_to_account": 0.8, "lot_terms": {"contract_oz": 50.0}}
    (tmp_path / f"engine_{ACC}.json").write_text(json.dumps(e))
    _, out = _run(tmp_path, "--include-quarantined")
    line = _line(out, "req 2400.00")
    # gross 2.00/oz; pnl 1.80 GBP = 2.25 USD over 0.01 lot x 50 oz = 4.50 USD/oz net -> -2.50
    assert "round trip ~-2.50 USD/oz (approx: today's fx)" in line and "comm 0.07 GBP" in line


def test_round_trip_is_unknown_not_guessed_without_contract_or_fx(tmp_path):
    _probes(tmp_path)
    (tmp_path / f"engine_{ACC}.json").write_text(json.dumps({"account": ACC, "currency": "GBP", "lot_terms": {}}))
    _, out = _run(tmp_path)
    assert "round trip ? USD/oz" in out


def test_a_login_inside_engine_file_strings_is_masked(tmp_path):
    e = {"account": ACC, "history_backfill": {"status": f"failed for login {LOGIN}"},
         "history_gap": {f"1h-{LOGIN}": {"from": "2026-10-19T01:00:00.123456+00:00", "to": "bad", "open_min": 5}},
         "throttle": {"enabled": True, "error": f"KeyError: {LOGIN}",
                      "agents": {f"a-{LOGIN}": {"blocked": [f"x{LOGIN}"], "cooldown_until": None}}}}
    (tmp_path / f"engine_{ACC}.json").write_text(json.dumps(e))
    for argv in ((), ("--json",)):
        code, out = _run(tmp_path, *argv)
        assert LOGIN not in out


def test_account_argument_is_validated_and_its_echo_masked(tmp_path):
    code, out = _run(tmp_path, "--account", f"../{LOGIN}/x")
    assert code == 2 and LOGIN not in out and "invalid --account" in out
    code, out = _run(tmp_path, "--account", f"nope-{LOGIN}")
    assert code == 2 and LOGIN not in out
