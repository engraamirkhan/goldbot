"""Two ops regressions kept closed.

* The deploy timer must run, not only be enabled (2026-11-02: brain_bootstrap.sh ran `systemctl enable` without
  `--now` after boot, so goldbot-deploy.timer stayed inactive and a Telegram-approved deploy would never be applied).
  The bootstrap starts it, and health FAILs on an approval nothing has picked up.
* The TradingView services retired on 2026-11-02 (tv-pull, webhook) stay retired: no unit, no bootstrap service, no
  heartbeat expected by the deploy script. The authors' ideas feed (goldbot-tv-ideas) is kept (owner, 2026-11-02).
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from goldbot.ops import health
from goldbot.ops.checks.services import DEPLOY_APPROVAL_STUCK_S
from tests.test_health import NOW, make_ctx, write

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / "goldbot" / "ops"
RETIRED = ("tv-pull", "webhook")


def _approval(tmp_path: Path, age_s: float) -> None:
    f = tmp_path / "deploy" / "approved.json"
    write(f, {"sha": "0" * 40})
    t = NOW.timestamp() - age_s
    os.utime(f, (t, t))


def test_the_bootstrap_starts_the_deploy_timer_not_only_enables_it():
    text = (OPS / "linux" / "brain_bootstrap.sh").read_text(encoding="utf-8")
    lines = [ln.strip() for ln in text.splitlines() if re.search(r"systemctl\s+enable\b.*goldbot-deploy\.timer", ln)]
    assert lines and all("--now" in ln for ln in lines), lines


def test_no_waiting_approval_adds_no_check(tmp_path):
    assert health.check_deploy_approval(make_ctx(tmp_path)) is None
    assert "deploy_approval" not in {c.name for c in health.run_checks(make_ctx(tmp_path)).checks}


def test_a_fresh_approval_is_ok(tmp_path):
    _approval(tmp_path, 90)
    c = health.check_deploy_approval(make_ctx(tmp_path))
    assert c is not None and c.status == "ok"


def test_an_approval_nothing_applied_fails_and_names_the_timer(tmp_path):
    _approval(tmp_path, DEPLOY_APPROVAL_STUCK_S + 60)
    c = health.check_deploy_approval(make_ctx(tmp_path))
    assert c is not None and c.status == "fail"
    assert "sudo systemctl enable --now goldbot-deploy.timer" in c.reason
    assert health._alerting(c.name, c.status)
    assert c in health.run_checks(make_ctx(tmp_path)).checks


def test_retired_tradingview_units_are_gone():
    units = {p.name for p in (OPS / "linux" / "systemd").iterdir()}
    assert not {f"goldbot-{s}.service" for s in RETIRED} & units
    assert "goldbot-tv-ideas.service" in units                     # kept: the followed authors' ideas feed research


def test_no_bootstrap_or_deploy_script_runs_a_retired_service():
    deploy = (OPS / "linux" / "goldbot-deploy.sh").read_text(encoding="utf-8")
    beats = next(ln for ln in deploy.splitlines() if ln.startswith("BEAT_SERVICES=")).split('"')[1].split()
    assert not set(RETIRED) & set(beats)
    assert "goldbot-tv-pull" not in (OPS / "linux" / "brain_bootstrap.sh").read_text(encoding="utf-8")
    assert '"goldbot-webhook"' not in (OPS / "vps_bootstrap.ps1").read_text(encoding="utf-8")
    assert "goldbot-webhook" not in (OPS / "vps_update.ps1").read_text(encoding="utf-8")
