"""Object Storage size of the backup bucket, previous versions included (BACKLOG 67, ADR 0002).

restic's own `stats` is the logical size and misses the previous versions the bucket keeps for 30 days, so the size is
the sum of every object version, listed with the OCI CLI (credentials: the CLI's own config on the box; the bucket and
namespace come from the `restic-repository` secret, so nothing new is stored and nothing is written to the repo).
The result goes to state/object_storage_size.json; health (`object_storage`) warns at 15 GB, fails at 19 GB, and
treats a missing or stale reading as unknown (warn), never ok.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from goldbot.base import write_atomic

log = logging.getLogger("goldbot.ops.storage_size")

SIZE_FILE = "object_storage_size.json"
_REPO = re.compile(r"^s3:https?://([a-z0-9]+)\.compat\.objectstorage\.[a-z0-9-]+\.oraclecloud\.com/([^/]+)", re.I)
Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def parse_repository(url: str) -> tuple[str, str]:
    """(namespace, bucket) from the restic repository URL; ValueError (without the URL) when it is not an OCI one."""
    m = _REPO.match(url.strip())
    if not m:
        raise ValueError("restic-repository is not an Oracle Object Storage s3 URL")
    return m.group(1), m.group(2)


def bucket_bytes(namespace: str, bucket: str, runner: Runner = subprocess.run, timeout_s: float = 300) -> int:
    """Sum of the size of every object version (and delete marker) in the bucket."""
    cmd = ["oci", "os", "object", "list-object-versions", "--namespace-name", namespace, "--bucket-name", bucket,
           "--fields", "size", "--all", "--output", "json"]
    proc = runner(cmd, capture_output=True, text=True, timeout=timeout_s, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"oci exited {proc.returncode}")
    data = json.loads(proc.stdout or "{}").get("data") or []
    return int(sum(int(o.get("size") or 0) for o in data))


def record_size(state_dir: Path, get_secret: Callable[[str], str | None], *, runner: Runner = subprocess.run,
                now: pd.Timestamp | None = None) -> dict[str, Any]:
    """Measure and write state/object_storage_size.json. Never raises and never records the bucket, namespace or the
    tool's output: a failure is recorded as `bytes: null` with the exception class only (health: unknown)."""
    ts = (now if now is not None else pd.Timestamp.now("UTC")).isoformat()
    try:
        url = get_secret("restic-repository")
        if not url:
            raise ValueError("restic-repository not set")
        ns, bucket = parse_repository(url)
        rec: dict[str, Any] = {"ts": ts, "bytes": bucket_bytes(ns, bucket, runner)}
    except Exception as exc:
        log.warning("object storage size unreadable: %s", type(exc).__name__)
        rec = {"ts": ts, "bytes": None, "error": type(exc).__name__}
    write_atomic(Path(state_dir) / SIZE_FILE, json.dumps(rec))
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
