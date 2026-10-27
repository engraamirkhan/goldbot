"""The pilot account's server clock is measured before it is unlocked (docs/deploy/live-readiness-2026-10-30.md, L4:
"server_tz set from a measurement on the live server"): health compares a configured pilot account's server_tz with
its live terminal as soon as a bridge for it answers, whether or not the account is enabled yet. Read-only."""
from __future__ import annotations

import pandas as pd

from goldbot.data.timeutil import NY_PLUS_7, server_to_utc, server_wall
from goldbot.execution.broker import Tick
from goldbot.ops import health
from goldbot.ops.health import AccountRef, HealthContext

NOW = pd.Timestamp("2026-10-28 12:00", tz="UTC")          # inside the US-EU gap week: NY+7 and Athens differ by 1 h
ATHENS = "Europe/Athens"


def _tick(broker_tz: str, configured_tz: str) -> Tick:
    raw = server_wall(pd.DatetimeIndex([NOW]), broker_tz)
    return Tick(ts_utc=server_to_utc(raw, configured_tz)[0], bid=4000.0, ask=4000.3)


def _ctx(tmp_path, reader, tz: str = NY_PLUS_7, enabled: bool = False) -> HealthContext:
    ref = AccountRef(account_id="icm-live", server_tz=tz)
    return HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None, tick_reader=reader,
                         accounts=[ref] if enabled else [], pilot_accounts=[] if enabled else [ref])


def test_a_disabled_pilot_account_with_a_connected_terminal_gets_its_server_clock_measured(tmp_path):
    ok = _ctx(tmp_path, lambda a, s: _tick(NY_PLUS_7, NY_PLUS_7))
    checks = {c.name: c for c in health.run_checks(ok).checks}
    assert checks["server_clock:icm-live"].status == "ok"


def test_a_wrong_pilot_server_tz_fails_health_before_the_unlock(tmp_path):
    bad = _ctx(tmp_path, lambda a, s: _tick(NY_PLUS_7, ATHENS), tz=ATHENS)
    c = [x for x in health.check_pilot_server_clock(bad) if x.name == "server_clock:icm-live"]
    assert c and c[0].status == "fail" and "server_tz is wrong" in c[0].reason


def test_no_bridge_for_the_pilot_account_adds_no_check_and_an_enabled_one_is_measured_once(tmp_path):
    assert health.check_pilot_server_clock(_ctx(tmp_path, lambda a, s: None)) == []
    on = _ctx(tmp_path, lambda a, s: _tick(NY_PLUS_7, NY_PLUS_7), enabled=True)
    names = [c.name for c in health.run_checks(on).checks]
    assert names.count("server_clock:icm-live") == 1
