"""Ledger sync: the Mac's program.db rows reach the brain's (Mac -> brain over ssh, append-only).

Design rules:
* "A row is never updated or deleted": sync only appends. Each row travels as one JSON line (table, key, body, source,
  privacy, own fields, base64 blob); the line's sha256 is its identity, so a line already acknowledged is never sent
  again and a replay is a no-op (Ledger.add returns the existing row for identical content).
* "Secrets and account identifiers never enter the ledger": `ingest` runs every line through NewEntry and
  Ledger.add, which scan it (fail closed). A bad line is rejected and counted, never stored, and never aborts the rest.
* Private rows (owner_notes, `--private`) are stored on the brain and stay private there; export never writes them.
* Only an acknowledged batch is marked sent: a short ack leaves every line pending for the next run.
The brain end is goldbot/ops/linux/goldbot-ledger-ingest.sh, the only command the sync key may run."""
from __future__ import annotations

import base64
import hashlib
import json
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from goldbot.ledger.models import MAX_BLOB_BYTES, TABLE_FIELDS, NewEntry
from goldbot.ledger.store import Ledger, LedgerRefused

SENT_FILE = "ledger_sync_sent.txt"           # in the state dir: one sha256 per acknowledged line
MAX_LINE_BYTES = 2 * MAX_BLOB_BYTES          # a longer line is rejected before it is parsed (blob is base64: +1/3)
Runner = Callable[[bytes], tuple[int, str]]  # payload -> (exit code, stdout)


def line_sha(line: str) -> str:
    return hashlib.sha256(line.encode("utf-8")).hexdigest()


def serialise(row: dict[str, object], table: str) -> str | None:
    """The wire line of one stored row, or None when it cannot travel (an artifact whose content was too large to
    store has no blob to rebuild its sha256 from)."""
    blob = row.get("blob")
    if table == "artifacts" and not isinstance(blob, bytes):
        return None
    msg: dict[str, object] = {"table": table, "key": row["key"], "body": row["body"], "source": row["source"],
                              "privacy": row["privacy"], "fields": {k: str(row[k]) for k in TABLE_FIELDS[table]}}
    if isinstance(blob, bytes):
        msg["blob_b64"] = base64.b64encode(blob).decode("ascii")
    return json.dumps(msg, sort_keys=True, separators=(",", ":"))


def _sent(state: Path) -> set[str]:
    p = state / SENT_FILE
    return set(p.read_text().split()) if p.exists() else set()


def pending(ledger: Ledger, state: Path) -> list[tuple[str, str]]:
    """(sha256, line) of every stored row version not yet acknowledged, oldest first, one per distinct line."""
    sent: set[str] = _sent(state)
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for table in TABLE_FIELDS:
        with ledger.db.read() as c:
            cur = c.execute(f"SELECT * FROM {table} ORDER BY id")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
        for row in rows:
            line = serialise(row, table)
            if line is None:
                continue
            sha = line_sha(line)
            if sha not in sent and sha not in seen:
                seen.add(sha)
                out.append((sha, line))
    return out


@dataclass(frozen=True)
class IngestResult:
    acked: int
    rejected: int
    reasons: tuple[str, ...] = ()


def ingest(ledger: Ledger, lines: Iterable[str]) -> IngestResult:
    """Append each valid line; `acked` counts lines now stored (new or already there), `rejected` the bad ones."""
    acked = 0
    reasons: list[str] = []
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        try:
            if len(line.encode("utf-8")) > MAX_LINE_BYTES:
                raise ValueError("line too long")
            msg = json.loads(line)
            if not isinstance(msg, dict):
                raise ValueError("not an object")
            blob = base64.b64decode(msg["blob_b64"], validate=True) if "blob_b64" in msg else None
            entry = NewEntry(table=msg["table"], key=msg["key"], body=msg["body"], source=msg["source"],
                             privacy=msg.get("privacy"), fields=msg["fields"], blob=blob)
            ledger.add(entry)
            acked += 1
        except LedgerRefused as exc:
            reasons.append(f"line {n}: {exc}")
        except (ValueError, KeyError, TypeError) as exc:       # pydantic's ValidationError is a ValueError
            reasons.append(f"line {n}: bad row ({type(exc).__name__})")
    return IngestResult(acked, len(reasons), tuple(reasons))


def ssh_runner(remote: str, key: str | None) -> Runner:
    """Run the brain's forced command over ssh; the remote command word is ignored by the key's `command=`."""
    def run(payload: bytes) -> tuple[int, str]:
        cmd = ["/usr/bin/ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "ConnectTimeout=10"]
        if key:
            cmd += ["-i", key]
        p = subprocess.run([*cmd, remote, "ingest"], input=payload, capture_output=True, timeout=300, check=False)
        return p.returncode, p.stdout.decode("utf-8", errors="replace")
    return run


def _parse_ack(out: str) -> int:
    for ln in reversed(out.splitlines()):
        if ln.startswith("ack "):
            try:
                return int(ln[4:])
            except ValueError:
                return -1
    return -1


def sync(ledger: Ledger, state: Path, run: Runner, batch: int = 200) -> tuple[int, int]:
    """Send pending lines in batches; returns (acknowledged, still_pending). A batch is marked sent only when the
    brain acknowledged every line in it."""
    todo = pending(ledger, state)
    done = 0
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        code, out = run(("\n".join(line for _, line in chunk) + "\n").encode("utf-8"))
        if code != 0 or _parse_ack(out) != len(chunk):
            break
        with (state / SENT_FILE).open("a") as f:
            f.write("".join(f"{sha}\n" for sha, _ in chunk))
        done += len(chunk)
    return done, len(todo) - done
