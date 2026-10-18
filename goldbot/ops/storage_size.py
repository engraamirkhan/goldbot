"""Object Storage size of the backup bucket, previous versions included (BACKLOG 67, ADR 0002).

restic's own `stats` is the logical size and misses the previous versions the bucket keeps for 30 days, so the size is
the sum of every object version, listed through the bucket's S3-compatible ListObjectVersions call, signed (SigV4,
httpx) with the restic keys already in the keyring: no new credential, and the bucket and namespace come from the
`restic-repository` secret.
The result goes to state/object_storage_size.json; health (`object_storage`) warns at 15 GB, fails at 19 GB, and
treats a missing or stale reading as unknown (warn), never ok.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from xml.etree import ElementTree

import httpx
import pandas as pd

from goldbot.base import write_atomic

log = logging.getLogger("goldbot.ops.storage_size")
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs request URLs (namespace, bucket) at INFO

SIZE_FILE = "object_storage_size.json"
MAX_BODY_BYTES = 8_000_000            # one page of 1000 versions is ~500 kB
DEADLINE_S = 120.0                    # across all pages: the probe runs inside the backup job
REQUEST_TIMEOUT_S = 20.0
MAX_PAGES = 2000                      # 1000 versions a page: a runaway listing stops at two million
_REPO = re.compile(r"^s3:https://([a-z0-9]+)\.compat\.objectstorage\.([a-z0-9-]+)\.oraclecloud\.com/([^/]+)", re.I)
# fetch(url, headers) -> (status, body); the default is httpx, tests pass a fake
Fetch = Callable[[str, dict[str, str]], "tuple[int, str]"]


def parse_repository(url: str) -> tuple[str, str, str]:
    """(namespace, region, bucket) from the restic repository URL; ValueError (without the URL) when it is not an OCI
    one. Path-style addressing (https://<ns>.compat...<region>.../<bucket>) is what restic itself uses."""
    m = _REPO.match(url.strip())
    if not m:
        raise ValueError("restic-repository is not an Oracle Object Storage s3 URL")
    return m.group(1), m.group(2), m.group(3)


# ------------------------------------------------------------------------------------------------- AWS SigV4
def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def sigv4_authorization(method: str, path: str, query: dict[str, str], headers: dict[str, str], payload_hash: str, *,
                        region: str, service: str, access_key: str, secret_key: str, amz_date: str) -> str:
    """The `Authorization` header value (AWS Signature Version 4). `headers` are all the headers to sign (lower-case
    names, including host and x-amz-date); `path` is already URI-encoded; `amz_date` is YYYYMMDDTHHMMSSZ."""
    enc = lambda v: quote(v, safe="-_.~")   # noqa: E731
    cq = "&".join(f"{enc(k)}={enc(v)}" for k, v in sorted(query.items()))
    names = sorted(headers)
    ch = "".join(f"{n}:{' '.join(headers[n].split())}\n" for n in names)
    signed = ";".join(names)
    creq = "\n".join([method, path, cq, ch, signed, payload_hash])
    scope = f"{amz_date[:8]}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret_key).encode(), amz_date[:8])
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    return f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"


def _httpx_fetch(url: str, headers: dict[str, str]) -> tuple[int, str]:
    r = httpx.get(url, headers=headers, timeout=REQUEST_TIMEOUT_S, follow_redirects=False)
    return r.status_code, r.text


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def bucket_bytes(namespace: str, region: str, bucket: str, access_key: str, secret_key: str, *,
                 fetch: Fetch = _httpx_fetch, now: pd.Timestamp | None = None,
                 monotonic: Callable[[], float] = time.monotonic) -> int:
    """Sum of the size of every object version in the bucket (delete markers count 0), from the S3-compatible
    ListObjectVersions call, paginated. Read-only; the restic keys are used for signing only, never logged."""
    host = f"{namespace}.compat.objectstorage.{region}.oraclecloud.com"
    total, key_marker, ver_marker = 0, "", ""
    start = monotonic()
    for _ in range(MAX_PAGES):
        if monotonic() - start > DEADLINE_S:
            raise RuntimeError("object storage listing too slow")
        query = {"versions": "", "max-keys": "1000"}
        if key_marker:
            query["key-marker"] = key_marker
        if ver_marker:
            query["version-id-marker"] = ver_marker
        t = now if now is not None else pd.Timestamp.now("UTC")
        amz = t.strftime("%Y%m%dT%H%M%SZ")
        empty = hashlib.sha256(b"").hexdigest()
        hdrs = {"host": host, "x-amz-content-sha256": empty, "x-amz-date": amz}
        auth = sigv4_authorization("GET", f"/{bucket}", query, hdrs, empty, region=region, service="s3",
                                   access_key=access_key, secret_key=secret_key, amz_date=amz)
        qs = "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
        status, body = fetch(f"https://{host}/{bucket}?{qs}", {**{k: v for k, v in hdrs.items() if k != "host"},
                                                                 "Authorization": auth})
        if status != 200:
            raise RuntimeError(f"object storage answered HTTP {status}")
        if len(body) > MAX_BODY_BYTES:
            raise RuntimeError("object storage response too large")
        root = ElementTree.fromstring(body)
        truncated, next_key, next_ver = False, "", ""
        for el in root:
            tag = _local(el.tag)
            if tag == "Version":
                total += sum(int(c.text or 0) for c in el if _local(c.tag) == "Size")
            elif tag == "IsTruncated":
                truncated = (el.text or "").strip().lower() == "true"
            elif tag == "NextKeyMarker":
                next_key = el.text or ""
            elif tag == "NextVersionIdMarker":
                next_ver = el.text or ""
        if not truncated:
            return total
        if not (next_key or next_ver) or (next_key, next_ver) == (key_marker, ver_marker):
            raise RuntimeError("object storage listing does not advance")
        key_marker, ver_marker = next_key, next_ver
    raise RuntimeError("object storage listing did not end")


def record_size(state_dir: Path, get_secret: Callable[[str], str | None], *, fetch: Fetch = _httpx_fetch,
                now: pd.Timestamp | None = None, monotonic: Callable[[], float] = time.monotonic) -> dict[str, Any]:
    """Measure and write state/object_storage_size.json. Never raises and never records the bucket, namespace, keys or
    any response text: a failure is recorded as `bytes: null` with the exception class only (health: unknown)."""
    ts = (now if now is not None else pd.Timestamp.now("UTC")).isoformat()
    try:
        url, ak, sk = (get_secret(k) for k in ("restic-repository", "restic-s3-access-key", "restic-s3-secret-key"))
        if not (url and ak and sk):
            raise ValueError("restic secrets not set")
        ns, region, bucket = parse_repository(url)
        rec: dict[str, Any] = {"ts": ts, "bytes": bucket_bytes(ns, region, bucket, ak, sk, fetch=fetch, now=now,
                                                                    monotonic=monotonic)}
    except Exception as exc:
        log.warning("object storage size unreadable: %s", type(exc).__name__)
        rec = {"ts": ts, "bytes": None, "error": type(exc).__name__}
    try:
        write_atomic(Path(state_dir) / SIZE_FILE, json.dumps(rec))
    except Exception as exc:   # never replace the backup's own result
        log.warning("object storage size not saved: %s", type(exc).__name__)
    return rec


def main(argv: list[str]) -> int:  # pragma: no cover - thin wrapper
    from goldbot.ops import accounts
    rec = record_size(Path("state"), accounts.get_secret)
    if rec["bytes"] is None:
        print(f"object storage: unknown ({rec['error']})")
        return 1
    print(f"object storage: {rec['bytes'] / 1e9:.2f} GB")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
