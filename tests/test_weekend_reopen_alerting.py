"""SRE re-verify of BACKLOG 59 (412fd0d, 6287d10): what the Telegram alert path SAYS across a weekend close and the
Sunday reopen, minute by minute, as the Telegram service runs health (HEALTH_EVERY_S = 60) and dedupes with
`alert_text`. The engine runs on the production zone (Europe/Athens, config/accounts.yaml), so the reopen is the
static table's 01:02 server time = 22:02 UTC on Sun 18 Oct (EEST), the engine's first live start.

Pinned here: no page while the market is closed (bridge up), a dead bridge pages at once whatever the market, a dead
engine pages, and a reopen whose first quote is a few minutes late does not page before MAX_VERDICT_AGE_S of open
market without a quote (that one is the frozen-feed alert the review asked for)."""
import json
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import pytest

from goldbot.engine import runner
from goldbot.execution.bridge import API_VERSION, MIN_COMPATIBLE
from goldbot.execution.paper import PaperBroker
from goldbot.ops import health
from goldbot.ops.bridge_compat import MAX_VERDICT_AGE_S, REQUIRED_METHODS, BridgeInfo, assess
from goldbot.ops.run import apply_bridge_compat, engine_pass, expire_bridge_compat
from goldbot.telegram.bot import HEALTH_EVERY_S
from tests.test_closed_market_state_cadence import _HostTime
from tests.test_engine_clock import _engine, _tick

TZ = "Europe/Athens"
SHA = "a" * 40
INFO = BridgeInfo(reachable=True, server_tz=TZ, api_version=API_VERSION, min_compatible=MIN_COMPATIBLE,
                  methods=sorted(REQUIRED_METHODS), commit=SHA)
DOWN = BridgeInfo(reachable=False, problem="/health unreadable (ConnectError)")
FRI_LAST = pd.Timestamp("2026-10-16 20:58:30", tz="UTC")     # 23:58:30 Athens: the last quote before the close
SUN_OPEN = pd.Timestamp("2026-10-18 22:02", tz="UTC")        # 01:02 Athens Monday: the table opens


class Box:
    """The engine process, the bridge and the brain's health poll on one host clock."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: pd.Timestamp,
                 last_quote: pd.Timestamp | None = None):
        """`last_quote`: a fresh engine process started at `start` while the terminal still shows that quote."""
        self.tmp = tmp_path
        pb = None
        if last_quote is not None:
            pb = PaperBroker(equity=10_000)
            pb.on_tick(_tick(last_quote))
        self.eng, self.pb, self.host = _engine(tmp_path, start=start, tz=TZ, pb=pb, host=[start])
        monkeypatch.setattr(runner, "time", _HostTime(self.host))
        self.bridge_up = True
        self.engine_up = True
        self.quotes: Callable[[pd.Timestamp], bool] = lambda now: False   # does the broker quote at `now`?
        self.watch: dict[str, health.Status] = {}
        self.pages: list[tuple[pd.Timestamp, str]] = []
        self._last_check = self.host[0]
        self._last_health = self.host[0]
        apply_bridge_compat(self.eng, assess(INFO, TZ, SHA, now=self.host[0]), now=self.host[0])
        assert engine_pass(self.eng, self.pb, "XAUUSD")

    def last_tick(self, symbol: str) -> Any:
        if not self.bridge_up:
            raise ConnectionError("bridge down")
        return self.pb.last_tick(symbol)

    def run(self, until: pd.Timestamp, step_s: int = 2) -> None:
        while self.host[0] < until:
            self.host[0] += pd.Timedelta(seconds=step_s)
            now = self.host[0]
            if self.engine_up:
                if self.bridge_up and self.quotes(now):
                    self.pb.on_tick(_tick(now))
                if (now - self._last_check).total_seconds() >= 60:           # the engine's bridge check thread
                    self._last_check = now
                    apply_bridge_compat(self.eng, assess(INFO if self.bridge_up else DOWN, TZ, SHA, now=now), now=now)
                expire_bridge_compat(self.eng, now)
                broker = self.pb if self.bridge_up else self
                engine_pass(self.eng, broker, "XAUUSD")
            if (now - self._last_health).total_seconds() >= HEALTH_EVERY_S:
                self._last_health = now
                self.poll(now)

    def jump(self, to: pd.Timestamp) -> None:
        """Skip quiet hours without simulating each pass (the heartbeat writes on the next pass)."""
        self.host[0] = to
        self._last_check = self._last_health = to - pd.Timedelta(seconds=1)

    def poll(self, now: pd.Timestamp) -> None:
        up = self.bridge_up
        ctx = health.HealthContext(state_dir=self.tmp, now=now, get_secret={"mt5-bridge-url-icm-demo": "http://x"}.get,
                                   accounts=[health.AccountRef(account_id="icm-demo", server_tz=TZ)],
                                   bridge_info=lambda url: INFO if up else DOWN, commit=SHA,
                                   bridge_probe=lambda url: None if up else "connection refused")
        checks = [health.check_engine(ctx, "icm-demo"), health.check_bridge(ctx, "icm-demo"),
                  health.check_bridge_compat(ctx, ctx.accounts[0])]
        kept = [c for c in checks if c is not None]
        worst: health.Status = "fail" if any(c.status == "fail" for c in kept) else \
            "warn" if any(c.status == "warn" for c in kept) else "ok"
        report = health.HealthReport(ts=now, status=worst, checks=kept)
        text = health.alert_text(self.watch, report)
        if text is not None:
            self.pages.append((now, text))
        self.watch = {c.name: c.status for c in report.checks}

    def failing_pages(self) -> list[tuple[pd.Timestamp, str]]:
        return [(t, x) for t, x in self.pages if "new failure" in x]


def _open(now: pd.Timestamp) -> bool:
    return bool(health.DEFAULT_SESSIONS.is_open(pd.DatetimeIndex([now]))[0])


def test_the_production_table_opens_at_2202_utc_on_sunday_18_oct():
    assert not _open(SUN_OPEN - pd.Timedelta(seconds=1)) and _open(SUN_OPEN)
    assert not _open(FRI_LAST + pd.Timedelta(seconds=30))


def test_a_whole_weekend_with_the_bridge_up_pages_nothing(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FRI_LAST)
    box.run(FRI_LAST + pd.Timedelta(hours=1))                   # the close and the first hour of the weekend
    box.jump(pd.Timestamp("2026-10-17 12:00", tz="UTC"))
    box.run(pd.Timestamp("2026-10-17 12:30", tz="UTC"))         # Saturday
    box.jump(SUN_OPEN - pd.Timedelta(minutes=20))
    box.quotes = lambda now: now >= SUN_OPEN + pd.Timedelta(seconds=5)
    box.run(SUN_OPEN + pd.Timedelta(minutes=30))
    assert box.pages == [], box.pages
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert st["market_closed"] is False and st["last_tick_age_s"] < 15


@pytest.mark.parametrize("late_min", [1, 2, 3])
def test_a_first_quote_a_few_minutes_after_the_reopen_pages_nothing(tmp_path, monkeypatch, late_min):
    box = Box(tmp_path, monkeypatch, FRI_LAST)
    box.jump(SUN_OPEN - pd.Timedelta(minutes=20))
    box.quotes = lambda now: now >= SUN_OPEN + pd.Timedelta(minutes=late_min)
    box.run(SUN_OPEN + pd.Timedelta(minutes=30))
    assert box.pages == [], box.pages


def test_no_quote_for_over_the_verdict_age_after_the_reopen_is_the_frozen_feed_page(tmp_path, monkeypatch):
    """Review HIGH kept: an open market with no quote is alerted (bridge_compat, by the verdict age), and it recovers
    once quotes flow. The reason names the engine's bridge confirmation, not the feed (sre finding, LOW)."""
    box = Box(tmp_path, monkeypatch, FRI_LAST)
    box.jump(SUN_OPEN - pd.Timedelta(minutes=20))
    first = SUN_OPEN + pd.Timedelta(minutes=10)
    box.quotes = lambda now: now >= first
    box.run(SUN_OPEN + pd.Timedelta(minutes=30))
    fails = box.failing_pages()
    assert len(fails) == 1 and "bridge_compat:icm-demo" in fails[0][1]
    paged_after_open = (fails[0][0] - SUN_OPEN).total_seconds()
    assert MAX_VERDICT_AGE_S - 120 <= paged_after_open <= MAX_VERDICT_AGE_S + 120, paged_after_open
    assert any("recovered" in x and t >= first for t, x in box.pages)


def test_a_bridge_that_dies_on_saturday_pages_within_6_min_and_keeps_failing(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FRI_LAST)
    box.jump(pd.Timestamp("2026-10-17 12:00", tz="UTC"))
    box.run(pd.Timestamp("2026-10-17 12:10", tz="UTC"))
    assert box.pages == []
    died = box.host[0]
    box.bridge_up = False
    box.run(died + pd.Timedelta(minutes=10))
    fails = [(t, x) for t, x in box.failing_pages() if "FAIL bridge:icm-demo" in x]
    assert len(fails) == 1                                              # once for the outage (incident 2026-11-01)
    assert (fails[0][0] - died).total_seconds() <= 360                  # within the 6-minute alert objective
    assert box.watch["bridge:icm-demo"] == "fail"
    # the engine's published verdict is no longer confirmed: it goes stale (unchecked) instead of being refreshed
    st = json.loads((tmp_path / "engine_icm-demo.json").read_text())
    assert st["bridge_compat"]["status"] == "unchecked"


def test_a_dead_engine_pages_after_the_reopen(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, FRI_LAST)
    box.jump(SUN_OPEN - pd.Timedelta(minutes=20))
    box.run(SUN_OPEN - pd.Timedelta(minutes=10))
    assert box.pages == []
    box.engine_up = False
    box.run(SUN_OPEN + pd.Timedelta(minutes=50))
    names = " ".join(x for _, x in box.failing_pages())
    assert "bridge_compat:icm-demo" in names                       # the verdict ages out first (5 min)
    assert box.watch["engine:icm-demo"] == "fail"                  # and the state after 45 min of open market


def test_an_engine_started_before_the_reopen_crosses_it_without_a_page(tmp_path, monkeypatch):
    """The quiet way to start the first live week: while the market is closed (sre recommendation for Sun 18 Oct)."""
    box = Box(tmp_path, monkeypatch, SUN_OPEN - pd.Timedelta(minutes=12), last_quote=FRI_LAST)
    box.quotes = lambda now: now >= SUN_OPEN + pd.Timedelta(minutes=4)
    box.run(SUN_OPEN + pd.Timedelta(minutes=30))
    assert box.pages == [], box.pages


def test_an_engine_started_at_the_reopen_before_the_first_quote_pages_the_stale_feed_once(tmp_path, monkeypatch):
    """Pre-existing (not BACKLOG 59): a start in an open session whose latest quote is Friday's refreshes the account
    on its first pass and records a stale-feed data-quality error, so health FAILs `engine:` once and recovers when
    quotes flow. Pinned so the reopen behaviour is known; start before the open, or after the first quote, to avoid
    it."""
    box = Box(tmp_path, monkeypatch, SUN_OPEN, last_quote=FRI_LAST)
    box.quotes = lambda now: now >= SUN_OPEN + pd.Timedelta(minutes=2)
    box.run(SUN_OPEN + pd.Timedelta(minutes=30))
    fails = box.failing_pages()
    assert len(fails) == 1 and "FAIL engine:icm-demo" in fails[0][1] and "stale feed" in fails[0][1]
    assert box.watch["engine:icm-demo"] == "ok"
