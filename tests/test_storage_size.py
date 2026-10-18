"""Object Storage size alert (BACKLOG 67): the probe (goldbot/ops/storage_size.py) and the `object_storage` check."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.config import BackupSettings
from goldbot.ops import health, storage_size
from goldbot.ops.health import HealthContext

NOW = pd.Timestamp("2026-10-07 14:00", tz="UTC")
REPO = "s3:https://nsSECRET.compat.objectstorage.eu-frankfurt-1.oraclecloud.com/bucketSECRET/goldbot"


def _ctx(tmp_path: Path) -> HealthContext:
    return HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None)


def _size(tmp_path: Path, gb: float | None, hours_ago: float = 2) -> None:
    ts = (NOW - pd.Timedelta(hours=hours_ago)).isoformat()
    (tmp_path / storage_size.SIZE_FILE).write_text(json.dumps({"ts": ts, "bytes": None if gb is None else gb * 1e9}))


@pytest.mark.parametrize("gb,status", [(0.0, "ok"), (14.9, "ok"), (15.0, "warn"), (18.9, "warn"), (19.0, "fail"),
                                       (25.0, "fail")])
def test_thresholds(tmp_path, gb, status):
    _size(tmp_path, gb)
    c = health.check_object_storage(_ctx(tmp_path))
    assert c.status == status and f"{gb:.1f} GB" in c.reason


@pytest.mark.parametrize("body", ["{not json", json.dumps({"ts": NOW.isoformat(), "bytes": None, "error": "X"}),
                                  json.dumps({"ts": NOW.isoformat(), "bytes": "lots"}), json.dumps({"bytes": 1})])
def test_unreadable_is_unknown_warn_never_ok(tmp_path, body):
    (tmp_path / storage_size.SIZE_FILE).write_text(body)
    c = health.check_object_storage(_ctx(tmp_path))
    assert c.status == "warn" and "unknown" in c.reason


def test_stale_reading_is_unknown(tmp_path):
    _size(tmp_path, 1.0, hours_ago=49)
    c = health.check_object_storage(_ctx(tmp_path))
    assert c.status == "warn" and "unknown" in c.reason


def test_missing_file(tmp_path):
    assert health.check_object_storage(_ctx(tmp_path)).status == "ok"          # no backup yet: nothing to measure
    (tmp_path / "backup_last.json").write_text("{}")
    assert health.check_object_storage(_ctx(tmp_path)).status == "warn"        # backups run, size never read


def test_thresholds_come_from_settings(tmp_path):
    from goldbot.config import load_settings
    s = load_settings()
    s = s.model_copy(update={"backup": s.backup.model_copy(update={"storage_warn_gb": 5.0, "storage_fail_gb": 6.0})})
    ctx = HealthContext(state_dir=tmp_path, now=NOW, get_secret=lambda k: None, settings=s)
    _size(tmp_path, 5.5)
    assert health.check_object_storage(ctx).status == "warn"
    _size(tmp_path, 6.5)
    assert health.check_object_storage(ctx).status == "fail"


def test_default_settings_match_the_free_tier_plan():
    b = BackupSettings()
    assert (b.storage_warn_gb, b.storage_fail_gb) == (15.0, 19.0)


def test_alerts_reach_telegram():
    assert health._alerting("object_storage", "warn") and health._alerting("object_storage", "fail")


# ---------------------------------------------------------------------------------------------------- probe
def _runner(out: Any, rc: int = 0, calls: list | None = None):
    def run(cmd, **kw):
        if calls is not None:
            calls.append(cmd)
        return subprocess.CompletedProcess(cmd, rc, stdout=out if isinstance(out, str) else json.dumps(out), stderr="")
    return run


def test_record_sums_every_version(tmp_path):
    calls: list = []
    data = {"data": [{"size": 5_000_000_000}, {"size": 4_000_000_000}, {"size": None}]}
    rec = storage_size.record_size(tmp_path, lambda k: REPO, runner=_runner(data, calls=calls), now=NOW)
    assert rec["bytes"] == 9_000_000_000
    assert "list-object-versions" in calls[0] and "--all" in calls[0]
    assert json.loads((tmp_path / storage_size.SIZE_FILE).read_text())["bytes"] == 9_000_000_000


@pytest.mark.parametrize("secret,runner", [
    (None, _runner({"data": []})),
    ("s3:https://example.com/bucket", _runner({"data": []})),
    (REPO, _runner("", rc=1)),
    (REPO, _runner("not json")),
])
def test_record_failure_is_unknown_and_leaks_nothing(tmp_path, secret, runner):
    rec = storage_size.record_size(tmp_path, lambda k: secret, runner=runner, now=NOW)
    assert rec["bytes"] is None
    text = (tmp_path / storage_size.SIZE_FILE).read_text()
    assert "SECRET" not in text and "bucket" not in text.lower()
    assert health.check_object_storage(_ctx(tmp_path)).status == "warn"


def test_parse_repository():
    assert storage_size.parse_repository(REPO) == ("nsSECRET", "bucketSECRET")
