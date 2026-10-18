"""Object Storage size alert (BACKLOG 67): the probe (goldbot/ops/storage_size.py) and the `object_storage` check."""
from __future__ import annotations

import json
from pathlib import Path

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
KEYS = {"restic-repository": REPO, "restic-s3-access-key": "AKSECRET0123", "restic-s3-secret-key": "sk/SECRET+abc"}
NS = "http://s3.amazonaws.com/doc/2006-03-01/"


def _page(versions: list[int | None], markers: tuple[str, str] | None = None) -> str:
    body = "".join(f"<Version><Key>k{i}</Key><Size>{n}</Size></Version>" if n is not None
                   else f"<DeleteMarker><Key>k{i}</Key></DeleteMarker>" for i, n in enumerate(versions))
    more = (f"<IsTruncated>true</IsTruncated><NextKeyMarker>{markers[0]}</NextKeyMarker>"
            f"<NextVersionIdMarker>{markers[1]}</NextVersionIdMarker>") if markers else "<IsTruncated>false</IsTruncated>"
    return f'<ListVersionsResult xmlns="{NS}"><Name>b</Name>{more}{body}</ListVersionsResult>'


def _fetcher(pages: list[tuple[int, str]], calls: list | None = None):
    it = iter(pages)

    def fetch(url, headers):
        if calls is not None:
            calls.append((url, headers))
        return next(it)
    return fetch


def test_sigv4_matches_aws_published_vectors():
    """AWS Signature V4 test suite: get-vanilla and get-vanilla-query-order-key-case."""
    kw = dict(region="us-east-1", service="service", access_key="AKIDEXAMPLE",
              secret_key="wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", amz_date="20150830T123600Z")
    h = {"host": "example.amazonaws.com", "x-amz-date": "20150830T123600Z"}
    empty = storage_size.hashlib.sha256(b"").hexdigest()
    a = storage_size.sigv4_authorization("GET", "/", {}, h, empty, **kw)
    assert a == ("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
                 "SignedHeaders=host;x-amz-date, Signature="
                 "5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31")
    q = storage_size.sigv4_authorization("GET", "/", {"Param2": "value2", "Param1": "value1"}, h, empty, **kw)
    assert q.endswith("Signature=b97d918cfa904a5beff61c982a1b6f458b799221646efd99d3219ec94cdf2500")


def test_record_sums_every_version_over_pages(tmp_path):
    calls: list = []
    pages = [(200, _page([5_000_000_000, None], ("k1", "v9"))), (200, _page([4_000_000_000]))]
    rec = storage_size.record_size(tmp_path, lambda k: KEYS[k], fetch=_fetcher(pages, calls), now=NOW)
    assert rec["bytes"] == 9_000_000_000
    assert json.loads((tmp_path / storage_size.SIZE_FILE).read_text())["bytes"] == 9_000_000_000
    (u1, h1), (u2, _) = calls
    assert "versions=" in u1 and "key-marker" not in u1
    assert "key-marker=k1" in u2 and "version-id-marker=v9" in u2
    assert h1["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKSECRET0123/20261007/eu-frankfurt-1/s3/")
    assert "SECRET+abc" not in str(h1)                      # the secret key itself is never sent


@pytest.mark.parametrize("secrets,pages", [
    ({**KEYS, "restic-s3-secret-key": None}, [(200, _page([1]))]),
    ({**KEYS, "restic-repository": "s3:https://example.com/bucket"}, [(200, _page([1]))]),
    (KEYS, [(403, "<Error>SECRET bucketSECRET</Error>")]),
    (KEYS, [(200, "not xml")]),
])
def test_record_failure_is_unknown_and_leaks_nothing(tmp_path, secrets, pages):
    rec = storage_size.record_size(tmp_path, lambda k: secrets[k], fetch=_fetcher(pages), now=NOW)
    assert rec["bytes"] is None
    text = (tmp_path / storage_size.SIZE_FILE).read_text()
    assert "SECRET" not in text and "bucket" not in text.lower()
    assert health.check_object_storage(_ctx(tmp_path)).status == "warn"


def test_fetch_exception_is_masked(tmp_path, caplog):
    def boom(url, headers):
        raise OSError(f"cannot reach {url} {headers}")
    rec = storage_size.record_size(tmp_path, lambda k: KEYS[k], fetch=boom, now=NOW)
    assert rec["bytes"] is None and rec["error"] == "OSError"
    assert "SECRET" not in caplog.text and "AWS4" not in caplog.text


def test_parse_repository():
    assert storage_size.parse_repository(REPO) == ("nsSECRET", "eu-frankfurt-1", "bucketSECRET")


def test_truncated_with_unchanged_or_missing_markers_is_unknown_fast(tmp_path):
    calls: list = []
    same = (200, _page([1], ("k1", "v1")))
    rec = storage_size.record_size(tmp_path, lambda k: KEYS[k], fetch=_fetcher([same] * 50, calls), now=NOW)
    assert rec["bytes"] is None and len(calls) == 2           # the second page repeats the markers: stop
    calls.clear()
    nomark = (200, '<ListVersionsResult><IsTruncated>true</IsTruncated></ListVersionsResult>')
    rec = storage_size.record_size(tmp_path, lambda k: KEYS[k], fetch=_fetcher([nomark] * 50, calls), now=NOW)
    assert rec["bytes"] is None and len(calls) == 1


def test_deadline_across_pages_is_unknown(tmp_path):
    t = iter(range(0, 10_000, 100))                            # each clock read is 100 s later
    pages = [(200, _page([1], (f"k{i}", f"v{i}"))) for i in range(50)]
    calls: list = []
    rec = storage_size.record_size(tmp_path, lambda k: KEYS[k], fetch=_fetcher(pages, calls), now=NOW,
                                   monotonic=lambda: float(next(t)))
    assert rec["bytes"] is None and len(calls) < 5


def test_oversized_body_is_refused(tmp_path):
    big = (200, "<a>" + "x" * (storage_size.MAX_BODY_BYTES + 1) + "</a>")
    rec = storage_size.record_size(tmp_path, lambda k: KEYS[k], fetch=_fetcher([big]), now=NOW)
    assert rec["bytes"] is None


def test_record_size_never_raises_on_write_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")                                    # state_dir is a file: the write fails
    rec = storage_size.record_size(blocker, lambda k: KEYS[k], fetch=_fetcher([(200, _page([1]))]), now=NOW)
    assert rec["bytes"] == 1


def test_httpx_logger_is_quiet():
    import logging
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_backup_error_survives_a_failing_probe(monkeypatch):
    from types import SimpleNamespace

    from goldbot.ops import backup as backup_mod
    from goldbot.ops import jobs

    class Boom(Exception):
        pass

    def bad_backup(*a, **k):
        raise backup_mod.BackupError("backup itself failed")

    def bad_probe(*a, **k):
        raise OSError("probe failed")
    ctx = SimpleNamespace(settings=SimpleNamespace(backup=SimpleNamespace(host="h", timeout_s=60)), state_dir=Path("."))
    monkeypatch.setattr(jobs, "_backup_args", lambda c: (None, lambda k: None))
    monkeypatch.setattr(backup_mod, "backup_job", bad_backup)
    monkeypatch.setattr(storage_size, "record_size", bad_probe)
    with pytest.raises(backup_mod.BackupError, match="backup itself failed"):
        jobs.backup(ctx, NOW)
