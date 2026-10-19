"""`goldbot run positions`: the read-only operator view of open trades (no dashboard login). It builds on the same
function as GET /api/positions/open (home_cards.open_positions) and reads state files only."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from goldbot.engine.shadow import ShadowBook
from goldbot.ops import positions
from tests.test_api_home_cards import ACC, NOW, _engine, _orders

ACCOUNTS = [ACC]                        # the enabled accounts' ids; this config name embeds a login


def _run(tmp_path: Path, *argv: str, accounts: list[str] | None = None) -> tuple[int, str]:
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = positions.main(["--state-dir", str(tmp_path), *argv], now=NOW, enabled=accounts or ACCOUNTS)
    return code, buf.getvalue()


def _book(state: Path) -> None:
    book = ShadowBook(state, max_open_per_agent=3)
    book.track("v1", NOW - pd.Timedelta(days=1))
    for agent, hours in (("box_break_close-g0-1h", 2), ("mfi-g0-15m", 3)):
        book.open_trade(version="v1", agent_id=agent, side=1, bar_ts=NOW - pd.Timedelta(hours=hours), entry=2400.0,
                        atr_usd=4.0, target_atr=3.0, stop_atr=1.5, max_bars=48, p=1.0, timeframe="1h", threshold=0.5)
    book.save(NOW)


def test_each_open_position_shows_agent_side_lots_stop_target_risk_pnl_and_how_it_entered(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    code, out = _run(tmp_path)
    assert code == 0
    line = next(x for x in out.splitlines() if "box_break_close-g0-1h" in x)
    for want in ("long", "0.05", "2,400.00", "2,396.00", "2,420.00", "demo auto", "GBP"):
        assert want in line
    assert "+40.00" in line                                  # floating P&L at the mid
    assert "risk 16.00" in line                              # open risk: 4 USD x 0.05 lot x 100 oz x 0.8
    probe = next(x for x in out.splitlines() if "cost_probe:mfi-g0" in x)
    assert "probe" in probe and "short" in probe


def test_totals_and_the_shadow_books_open_count_by_agent_are_printed(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    _book(tmp_path)
    _, out = _run(tmp_path)
    assert "2 open" in out and "41.60" in out                # 40.00 + 1.60 floating
    assert "shadow: 2 open" in out and "box_break_close-g0-1h 1" in out and "mfi-g0-15m 1" in out


def test_json_output_is_one_document_per_enabled_account(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    code, out = _run(tmp_path, "--json")
    doc = json.loads(out)
    assert code == 0
    acct = doc["accounts"][0]
    assert acct["account"] == "icm-demo-…67" and len(acct["positions"]) == 2
    assert acct["totals"]["open"] == 2 and acct["totals"]["floating_pnl"] == 41.6
    assert {p["entered"] for p in acct["positions"]} == {"demo auto", "probe"}


def test_account_filter_shows_only_that_account_and_an_unknown_one_is_refused(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    (tmp_path / "orders_other.json").write_text(json.dumps({"open": {}}))
    code, out = _run(tmp_path, "--account", "other", accounts=["other", ACC])
    assert code == 0 and "box_break_close" not in out and "other" in out
    code, out = _run(tmp_path, "--account", "nope")
    assert code == 2 and "unknown account" in out


def test_an_empty_orders_file_means_no_positions_not_a_failure(tmp_path):
    (tmp_path / "orders_icm-demo.json").write_text(json.dumps({"open": {}}))
    code, out = _run(tmp_path, accounts=["icm-demo"])
    assert code == 0 and "no open positions" in out


def test_an_unreadable_orders_file_is_named_not_shown_as_empty(tmp_path):
    _engine(tmp_path)
    (tmp_path / f"orders_{ACC}.json").write_text("{torn")
    code, out = _run(tmp_path)
    assert code == 1 and "UNREADABLE" in out


def test_the_command_reads_state_and_writes_nothing(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    _book(tmp_path)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    _run(tmp_path)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before


def test_positions_is_a_registered_run_command():
    import goldbot.ops.run  # noqa: F401 - registers every command
    from goldbot.ops.commands.registry import find
    assert find("positions") is not None


def test_a_login_number_never_reaches_the_output(tmp_path):
    _engine(tmp_path)
    _orders(tmp_path)
    for argv in ((), ("--json",)):
        _, out = _run(tmp_path, *argv)
        assert "51234567" not in out


@pytest.mark.parametrize("flag", ["--account", "--state-dir"])
def test_flags_need_a_value(flag, tmp_path):
    with pytest.raises(SystemExit):
        positions.main([flag], now=NOW, enabled=ACCOUNTS)


def test_an_enabled_account_with_no_orders_file_says_the_engine_has_not_run(tmp_path):
    code, out = _run(tmp_path, accounts=["icm-demo"])
    assert code == 0 and "no engine order file (engine not run yet?)" in out and "no open positions" not in out
    doc = json.loads(_run(tmp_path, "--json", accounts=["icm-demo"])[1])
    assert doc["accounts"][0]["orders_file"] is False


def test_an_explicit_account_that_is_not_enabled_but_has_a_torn_file_is_reported(tmp_path):
    (tmp_path / "orders_old.json").write_text("{torn")
    code, out = _run(tmp_path, "--account", "old", accounts=["icm-demo"])
    assert code == 1 and "old" in out and "UNREADABLE" in out


def test_totals_from_a_stale_engine_file_are_flagged_stale(tmp_path):
    _engine(tmp_path, ts=(NOW - pd.Timedelta(seconds=90)).timestamp())
    _orders(tmp_path)
    _, out = _run(tmp_path)
    total = next(x for x in out.splitlines() if "total:" in x)
    assert "(stale)" in total
    assert json.loads(_run(tmp_path, "--json")[1])["accounts"][0]["totals"]["stale"] is True


def test_a_position_without_a_stop_says_risk_is_unbounded_in_the_row_and_the_total(tmp_path):
    _engine(tmp_path)
    p = tmp_path / f"orders_{ACC}.json"
    d = {"position_id": 20, "agent_id": "mfi-g0-1h", "side": 1, "lots": 0.01, "entry_bar_ts": NOW.isoformat(),
         "max_bars": 8, "open_price": 2400.0, "opened_utc": NOW.isoformat()}
    p.write_text(json.dumps({"open": {"20": d}}))
    _, out = _run(tmp_path)
    assert "no stop: risk unbounded" in next(x for x in out.splitlines() if "#20" in x)
    assert "no stop: risk unbounded" in next(x for x in out.splitlines() if "total:" in x)
