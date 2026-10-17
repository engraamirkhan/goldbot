"""aux.db bookkeeping: staff-agent run log, monthly agent spend, scheduler job state and the Telegram outbox
(docs/proposals/2026-10-state-store.md, step 3).

Design rules quoted from the proposal:
* "Dual-read: read the DB row; if it is missing, read the JSON file, upsert it, and count a `state_fallback` hit."
  Each file is imported once, on first open, into an empty table (`imports` records it). Afterwards a file whose
  SHA-256 is not the one this code last exported was written by someone else, in practice an older release after
  vps_update.ps1 rolled back: it is merged back once (`dual_read`: the larger spend per month, the newer slot per
  job, the outbox's progress) and counted in `state_fallback`.
* "Use isolation_level=None and start every write with BEGIN IMMEDIATE, so the write lock is taken up front."
  Every function below that writes runs inside the caller's `Database.write()` transaction.
* Budget check and spend "commit together under BEGIN IMMEDIATE": `reserve_spend` grants at most what is left of
  the monthly cap and books it in the same transaction, so concurrent runs can never be granted more than the cap.
* "dual-write the JSON after the DB commit for one release, so rolling back the code is safe."

Compatibility exports. Readers outside step 3 still open the old files: the dashboard (`api/app.py`: scheduler.json,
agent_runs.jsonl), health (`ops/health.py`: agent_spend.json; scheduler.json only when aux.db has no heartbeat), the
agents' `read_state` tool (scheduler.json), the VPS updater (fallback to scheduler.json) and the API's owner notices,
which append to agent_runs.jsonl; and a rolled-back older release reads all four. So the DB is the record and the
files are regenerated from it after each commit (`export_latest`), outside the transaction: a failed export (a
Windows sharing violation on the rename) is logged and never undoes the booking. The runner still appends each run's
line to agent_runs.jsonl (existing lines are never rewritten). The original of a rewritten file is copied once to
`state/pre-db/` (`import_state.keep_backup`). Step 8 deletes the exports once those readers query the DB.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from goldbot.db.connection import Database, db_path, now_ns
from goldbot.db.import_state import ImportSpec, Loader, file_sha256, import_file

log = logging.getLogger(__name__)

RUNS_FILE = "agent_runs.jsonl"
SPEND_FILE = "agent_spend.json"
SCHEDULER_FILE = "scheduler.json"
OUTBOX_FILE = "telegram_outbox.json"


# ---------------------------------------------------------------------------------------------------- helpers
def export_json(path: Path, text: str) -> None:
    """Atomically replace `path` with `text` (unique temp name per process, fsync before the rename), so a reader
    never sees a torn file and two writers never share a temp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def table_empty(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is None      # noqa: S608 - fixed names only


def count_fallback(conn: sqlite3.Connection, source: str) -> None:
    conn.execute("INSERT INTO state_fallback (source, hits, last_ns) VALUES (?, 1, ?) ON CONFLICT(source) DO UPDATE "
                 "SET hits = hits + 1, last_ns = excluded.last_ns", (source, now_ns()))


def fallback_hits(conn: sqlite3.Connection) -> dict[str, int]:
    return {s: int(h) for s, h in conn.execute("SELECT source, hits FROM state_fallback ORDER BY source")}


def bump_export(conn: sqlite3.Connection, source: str) -> None:
    """In the write transaction that changed what `source` exports: a new export sequence number."""
    conn.execute("INSERT INTO exports (source, seq) VALUES (?, 1) ON CONFLICT(source) DO UPDATE SET seq = seq + 1",
                 (source,))


KEEP_HASHES = 16                 # own exports remembered per file (any of them on disk is never "foreign")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def remember_hash(conn: sqlite3.Connection, source: str, sha: str) -> None:
    """Record `sha` as written by this code; the newest KEEP_HASHES are kept. Called in the write transaction that
    writes the file, so no reader can pair this file with an older hash list (see `export_latest`)."""
    r = conn.execute("SELECT hashes FROM exports WHERE source = ?", (source,)).fetchone()
    hashes = [h for h in (json.loads(r[0]) if r else []) if h != sha] + [sha]
    conn.execute("INSERT INTO exports (source, seq, hashes) VALUES (?, 0, ?) ON CONFLICT(source) "
                 "DO UPDATE SET hashes = excluded.hashes", (source, json.dumps(hashes[-KEEP_HASHES:])))


def export_latest(db: Database, source: str, path: Path, render: Callable[[sqlite3.Connection], str]) -> bool:
    """Write the latest committed state of `source` to `path`, after the booking's own commit.

    The render, the hash record and the file write happen in ONE write transaction (BEGIN IMMEDIATE), and
    `foreign_write` checks under the same lock. So exporters and checkers are serialised by aux.db's write lock:
    the file on disk is always the newest snapshot (no exporter can overwrite a newer file with an older render)
    and its hash is always the newest one recorded (never evicted by concurrent exporters), and a checker never sees
    a file whose hash it read before the export. Under load (2026-10-11) the previous lock-free version let a
    checker read the hash list, then the file an exporter wrote in between, and merge our own export as foreign.

    The lock is held only for a local write and fsync of a small file. Never raises: the booking is already
    committed; a failed export rolls back only its hash record and the next commit writes the file again. An error
    after the rename (the file is ours) still commits the hash; a process killed between the rename and the commit
    leaves the file unrecorded, but it equals what the DB renders, which `dual_read` recognises as ours."""
    try:
        with db.write() as conn:
            text = render(conn)
            sha = _sha(text)
            remember_hash(conn, source, sha)
            try:
                export_json(path, text)
            except Exception:
                if not _holds(path, sha):
                    raise                               # nothing written: roll the hash record back
                log.warning("%s was written but reported an error afterwards; kept as ours", path.name)
        return True
    except Exception as exc:                            # noqa: BLE001 - an export must never undo or block a booking
        log.warning("could not export %s (the database has the data; retried on the next write): %s", path.name, exc)
        return False


def ensure_imported(db: Database, spec: ImportSpec, state_dir: Path) -> None:
    """Dual-read on open: import `spec.source` once. A file missing at the first open is recorded as imported with
    no rows: the DB starts empty and owns the data from then on, so a file that appears later (this code's own
    export, or an older release's after a rollback) is never imported over it, only merged by `dual_read`."""
    with db.connection() as conn:
        done = conn.execute("SELECT 1 FROM imports WHERE source = ?", (spec.source,)).fetchone()
    if done is not None:
        return
    path = state_dir / spec.source
    if path.exists():
        import_file(db, spec, path)
        return
    with db.write() as conn:
        conn.execute("INSERT INTO imports (source, sha256, rows, imported_ns) VALUES (?, '', 0, ?) "
                     "ON CONFLICT(source) DO NOTHING", (spec.source, now_ns()))


def _holds(path: Path, sha: str) -> bool:
    try:
        return path.exists() and file_sha256(path) == sha
    except OSError:
        return False


Render = Callable[[sqlite3.Connection], str]


def _foreign_in(conn: sqlite3.Connection, source: str, path: Path, render: Render | None = None) -> bool:
    """Inside a write transaction (exporters hold the same lock): does `path` hold content this code neither imported
    nor exported recently? A file equal to what the DB renders now is ours (an export killed before its commit)."""
    if not path.exists():
        return False
    exp = conn.execute("SELECT hashes FROM exports WHERE source = ?", (source,)).fetchone()
    imp = conn.execute("SELECT sha256 FROM imports WHERE source = ?", (source,)).fetchone()
    if imp is None:
        return False                                    # never imported: ensure_imported handles it
    known = set(json.loads(exp[0]) if exp else []) | {imp[0]}
    try:
        sha = file_sha256(path)
    except OSError:
        return False
    if sha in known:
        return False
    if render is not None and sha == _sha(render(conn)):
        remember_hash(conn, source, sha)
        return False
    return True


def foreign_write(db: Database, source: str, path: Path, render: Render | None = None) -> bool:
    """True when `path` holds content this code neither imported nor exported recently (an older release wrote it).
    Checked under the write lock, so an export cannot land between reading the hashes and reading the file."""
    if not path.exists():
        return False
    with db.write() as conn:
        return _foreign_in(conn, source, path, render)


def dual_read(db: Database, spec: ImportSpec, state_dir: Path, merge: Loader, render: Render | None = None) -> bool:
    """Import once; afterwards merge a file another writer changed (rollback, then roll forward), count the
    fallback and adopt its hash so it is merged only once. The check and the merge are one write transaction, so
    our own concurrent exports are never mistaken for foreign writes. Returns True when a merge happened."""
    ensure_imported(db, spec, state_dir)
    path = state_dir / spec.source
    if not path.exists():
        return False
    try:
        with db.write() as conn:
            if not _foreign_in(conn, spec.source, path, render):
                return False
            merge(path, conn)
            count_fallback(conn, spec.source)
            bump_export(conn, spec.source)
            remember_hash(conn, spec.source, file_sha256(path))
    except (OSError, ValueError) as exc:
        log.warning("%s changed outside this code but could not be merged: %s", spec.source, exc)
        return False
    log.warning("%s was written by another release (rollback?): merged into aux.db", spec.source)
    return True


def complete_lines(path: Path) -> list[str]:
    """Lines of an append-only log that end in a newline: a line another process is still writing is left for the
    next read instead of being counted (and skipped) half-written."""
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").split("\n")[:-1]


# ---------------------------------------------------------------------------------------------------- agent runs
def _run_columns(row: dict[str, Any]) -> tuple[str, str, float]:
    return str(row.get("role") or "agent"), str(row.get("status") or ""), float(row.get("cost_usd") or 0.0)


def _insert_lines(conn: sqlite3.Connection, lines: list[str], start: int, *, skip_notices: bool) -> int:
    n = 0
    for i, line in enumerate(lines[start:], start):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict) or (skip_notices and row.get("status") == "notice"):
            continue
        if skip_notices and conn.execute("SELECT 1 FROM agent_runs WHERE body = ?", (line,)).fetchone():
            continue                                    # our own mirror line, shifted by a concurrent notice
        role, status, cost = _run_columns(row)
        conn.execute("INSERT INTO agent_runs (role, status, cost_usd, jsonl_line, body) VALUES (?, ?, ?, ?, ?)",
                     (role, status, cost, i, line if skip_notices else json.dumps(row)))
        n += 1
    return n


def import_agent_runs_jsonl(path: Path, conn: sqlite3.Connection) -> int:
    """agent_runs.jsonl -> agent_runs, in file order, keeping each line's number; unreadable lines are skipped (the
    old readers skipped them too). Only into an empty table: rows the runner already wrote are never duplicated."""
    if not table_empty(conn, "agent_runs"):
        return 0
    return _insert_lines(conn, complete_lines(path), 0, skip_notices=False)


AGENT_RUNS = ImportSpec(source=RUNS_FILE, kind="aux", loader=import_agent_runs_jsonl, once=True)


class AgentRunLog:
    """The staff-agent run log in aux.db, with agent_runs.jsonl kept as an append-only mirror for the dashboard, the
    API's owner notices and an older release after a rollback (see the module docstring)."""

    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.db = Database.open(self.dir, "aux")
        ensure_imported(self.db, AGENT_RUNS, self.dir)
        self._adopt_foreign_runs()

    def _adopt_foreign_runs(self) -> None:
        """Runs an older release appended to the file only (rollback, then roll forward) are copied into the table
        once; owner notices stay in the file (the outbox relays them from there)."""
        lines = complete_lines(self.dir / RUNS_FILE)
        with self.db.write() as conn:
            last = conn.execute("SELECT MAX(jsonl_line) FROM agent_runs").fetchone()[0]
            if last is None or len(lines) <= int(last) + 1:
                return
            if _insert_lines(conn, lines, int(last) + 1, skip_notices=True):
                count_fallback(conn, RUNS_FILE)

    def append(self, body: str) -> int:
        """Record one run (its JSON line) and mirror it to agent_runs.jsonl. Returns the row id. The DB commit comes
        first; a failed mirror write loses only the dashboard line, never the record."""
        row = json.loads(body)
        role, status, cost = _run_columns(row)
        with self.db.write() as conn:
            line = len(complete_lines(self.dir / RUNS_FILE))
            cur = conn.execute("INSERT INTO agent_runs (role, status, cost_usd, jsonl_line, body) "
                               "VALUES (?, ?, ?, ?, ?)", (role, status, cost, line, body))
            rid = int(cur.lastrowid or 0)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / RUNS_FILE, "a", encoding="utf-8") as fh:
                fh.write(body + "\n")
        except OSError as exc:
            log.warning("could not mirror run %s to %s: %s", rid, RUNS_FILE, exc)
        return rid

    def recent(self, limit: int = 30) -> list[dict[str, Any]]:
        """The newest `limit` rows, oldest first (the order of the old file reader)."""
        with self.db.connection() as conn:
            rows = conn.execute("SELECT body FROM agent_runs ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [json.loads(b) for (b,) in reversed(rows)]


# ---------------------------------------------------------------------------------------------------- agent spend
def _spend_file(path: Path) -> dict[str, float]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path.name}: expected an object of month -> USD")
    return {str(m): float(u) for m, u in raw.items()}


def import_agent_spend_json(path: Path, conn: sqlite3.Connection) -> int:
    """agent_spend.json ({"YYYY-MM": usd}) -> agent_spend, only into an empty table."""
    if not table_empty(conn, "agent_spend"):
        return 0
    raw = _spend_file(path)
    for month, usd in raw.items():
        conn.execute("INSERT INTO agent_spend (month, usd) VALUES (?, ?)", (month, usd))
    return len(raw)


def merge_agent_spend_json(path: Path, conn: sqlite3.Connection) -> int:
    """A file an older release kept writing: the larger amount per month wins (spend is never lost, so the cap
    errs towards stopping)."""
    raw = _spend_file(path)
    for month, usd in raw.items():
        conn.execute("INSERT INTO agent_spend (month, usd) VALUES (?, ?) ON CONFLICT(month) DO UPDATE "
                     "SET usd = MAX(usd, excluded.usd)", (month, usd))
    return len(raw)


AGENT_SPEND = ImportSpec(source=SPEND_FILE, kind="aux", loader=import_agent_spend_json, once=True, backup=True)


def month_spent(conn: sqlite3.Connection, month: str) -> float:
    r = conn.execute("SELECT usd FROM agent_spend WHERE month = ?", (month,)).fetchone()
    return float(r[0]) if r else 0.0


def add_spend(conn: sqlite3.Connection, month: str, usd: float) -> float:
    """Book `usd` (may be negative: a released reservation) for `month`; returns the new month total (never < 0)."""
    total = max(round(month_spent(conn, month) + usd, 6), 0.0)
    conn.execute("INSERT INTO agent_spend (month, usd) VALUES (?, ?) "
                 "ON CONFLICT(month) DO UPDATE SET usd = excluded.usd", (month, total))
    bump_export(conn, SPEND_FILE)
    return total


def reserve_spend(conn: sqlite3.Connection, month: str, want: float, cap: float) -> float:
    """Grant min(want, what is left of `cap`) and book it, in the caller's write transaction (check-and-add is one
    step, so concurrent runs are never granted more than the cap between them)."""
    granted = max(min(want, cap - month_spent(conn, month)), 0.0)
    if granted > 0:
        add_spend(conn, month, granted)
    return granted


def all_spend(conn: sqlite3.Connection) -> dict[str, float]:
    return {m: float(u) for m, u in conn.execute("SELECT month, usd FROM agent_spend ORDER BY month")}


def export_spend(db: Database, state_dir: Path) -> bool:
    """Regenerate agent_spend.json (ops/health.py, an older release) after a commit; never raises."""
    return export_latest(db, SPEND_FILE, state_dir / SPEND_FILE, render_spend)


def render_spend(conn: sqlite3.Connection) -> str:
    return json.dumps(all_spend(conn), indent=1)


# ---------------------------------------------------------------------------------------------------- scheduler
def load_jobs(conn: sqlite3.Connection) -> dict[str, str]:
    """Job name -> JobState JSON."""
    return {n: b for n, b in conn.execute("SELECT name, body FROM scheduler_jobs ORDER BY name")}


def save_jobs(conn: sqlite3.Connection, jobs: dict[str, str], heartbeat_ns: int | None = None,
              status: str | None = None) -> None:
    """Job states, plus the heartbeat with the whole status document (`status`, the scheduler.json shape)."""
    for name, body in jobs.items():
        conn.execute("INSERT INTO scheduler_jobs (name, body) VALUES (?, ?) "
                     "ON CONFLICT(name) DO UPDATE SET body = excluded.body", (name, body))
    conn.execute("INSERT INTO scheduler_heartbeat (id, ts_ns, body) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE "
                 "SET ts_ns = excluded.ts_ns, body = excluded.body",
                 (heartbeat_ns or now_ns(), status or json.dumps({"jobs": {}})))
    bump_export(conn, SCHEDULER_FILE)


def scheduler_document(conn: sqlite3.Connection) -> str | None:
    r = conn.execute("SELECT body FROM scheduler_heartbeat WHERE id = 1").fetchone()
    return str(r[0]) if r else None


def scheduler_heartbeat(state_dir: str | Path) -> tuple[int, dict[str, Any]] | None:
    """(heartbeat ns, status document) from one row of aux.db, read-only; None without aux.db or a heartbeat."""
    p = db_path(state_dir, "aux")
    if not p.exists():
        return None
    # a plain read-only connection (never `immutable`: a writer may be mid-commit); it takes the WAL read lock, so
    # it may create the -wal/-shm files, which SQLite removes again when the last connection closes
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=5)
        try:
            r = conn.execute("SELECT ts_ns, body FROM scheduler_heartbeat WHERE id = 1").fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    if r is None:
        return None
    out = json.loads(r[1])
    return (int(r[0]), out) if isinstance(out, dict) else None


def scheduler_status(state_dir: str | Path) -> dict[str, Any] | None:
    """The scheduler's status document ({"ts", "jobs"}) from aux.db, read-only (never creates or migrates the file);
    None when there is no aux.db or no heartbeat yet (callers then read scheduler.json)."""
    hb = scheduler_heartbeat(state_dir)
    return None if hb is None else hb[1]


# ---------------------------------------------------------------------------------------------------- outbox
def outbox_sent(conn: sqlite3.Connection) -> dict[str, list[tuple[int, int]]]:
    return {pid: [(int(c), int(m)) for c, m in json.loads(msgs)]
            for pid, msgs in conn.execute("SELECT proposal_id, messages FROM outbox_sent ORDER BY rowid")}


def outbox_mark_sent(conn: sqlite3.Connection, proposal_id: str, messages: list[tuple[int, int]]) -> None:
    conn.execute("INSERT INTO outbox_sent (proposal_id, messages) VALUES (?, ?) "
                 "ON CONFLICT(proposal_id) DO UPDATE SET messages = excluded.messages",
                 (proposal_id, json.dumps([list(m) for m in messages])))
    bump_export(conn, OUTBOX_FILE)


def outbox_forget(conn: sqlite3.Connection, proposal_id: str) -> bool:
    """True if this call removed the row: of two concurrent callers only one reports the outcome."""
    gone = conn.execute("DELETE FROM outbox_sent WHERE proposal_id = ?", (proposal_id,)).rowcount == 1
    if gone:
        bump_export(conn, OUTBOX_FILE)
    return gone


def outbox_watched(conn: sqlite3.Connection) -> dict[str, tuple[list[tuple[int, int]], str, int]]:
    """proposal id -> (NOT CONFIRMED reply messages, the outcome they show, since_ns), oldest first (BACKLOG 87)."""
    return {pid: ([(int(c), int(m)) for c, m in json.loads(msgs)], shown, int(since))
            for pid, msgs, shown, since in
            conn.execute("SELECT proposal_id, messages, shown, since_ns FROM outbox_watch ORDER BY rowid")}


def outbox_watch(conn: sqlite3.Connection, proposal_id: str, messages: list[tuple[int, int]], shown: str,
                 since_ns: int) -> None:
    """Start watching a card's replies; a card already watched keeps what it shows (a repeat never resets it)."""
    conn.execute("INSERT INTO outbox_watch (proposal_id, messages, shown, since_ns) VALUES (?, ?, ?, ?) "
                 "ON CONFLICT(proposal_id) DO NOTHING",
                 (proposal_id, json.dumps([list(m) for m in messages]), shown, since_ns))


def outbox_set_shown(conn: sqlite3.Connection, proposal_id: str, shown: str) -> None:
    conn.execute("UPDATE outbox_watch SET shown = ? WHERE proposal_id = ?", (shown, proposal_id))


def outbox_unwatch(conn: sqlite3.Connection, proposal_id: str) -> None:
    conn.execute("DELETE FROM outbox_watch WHERE proposal_id = ?", (proposal_id,))


def outbox_cursor(conn: sqlite3.Connection) -> tuple[int, int] | None:
    """(last agent_runs id relayed, agent_runs.jsonl lines scanned for notices); None before the first start."""
    r = conn.execute("SELECT reports_id, notices_line FROM outbox_cursor WHERE id = 1").fetchone()
    return (int(r[0]), int(r[1])) if r else None


def set_outbox_cursor(conn: sqlite3.Connection, reports_id: int, notices_line: int) -> None:
    conn.execute("INSERT INTO outbox_cursor (id, reports_id, notices_line) VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE "
                 "SET reports_id = excluded.reports_id, notices_line = excluded.notices_line",
                 (reports_id, notices_line))
    bump_export(conn, OUTBOX_FILE)


def max_run_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM agent_runs").fetchone()[0])


def runs_after(conn: sqlite3.Connection, run_id: int) -> list[tuple[int, dict[str, Any]]]:
    """Staff-agent runs newer than `run_id`, oldest first. Owner notices (status 'notice') are relayed from
    agent_runs.jsonl, where the API writes them, so they are left out here (never sent twice)."""
    return [(int(i), json.loads(b)) for i, b in
            conn.execute("SELECT id, body FROM agent_runs WHERE id > ? AND status != 'notice' ORDER BY id", (run_id,))]


def cursor_from_line_offset(conn: sqlite3.Connection, offset: int) -> int:
    """The agent_runs id cursor matching the old outbox's line offset into agent_runs.jsonl: the last row from a
    line already handled. Without rows that carry a line the log was empty: start at the end."""
    if conn.execute("SELECT 1 FROM agent_runs WHERE jsonl_line IS NOT NULL LIMIT 1").fetchone() is None:
        return max_run_id(conn)
    r = conn.execute("SELECT MAX(id) FROM agent_runs WHERE jsonl_line < ?", (offset,)).fetchone()[0]
    return int(r or 0)


def line_offset_from_cursor(conn: sqlite3.Connection, reports_id: int, notices_line: int) -> int:
    """The old outbox's line offset for an older release after a rollback: the first line not yet relayed (the
    first unrelayed run's line, else the notice cursor). Errs early: a repeat beats a lost report."""
    r = conn.execute("SELECT MIN(jsonl_line) FROM agent_runs WHERE id > ? AND status != 'notice'",
                     (reports_id,)).fetchone()[0]
    return notices_line if r is None else min(int(r), notices_line)


def outbox_document(conn: sqlite3.Connection) -> str:
    """telegram_outbox.json in the pre-database format ({"sent", "reports_offset"}), for a rollback."""
    cur = outbox_cursor(conn)
    offset = None if cur is None else line_offset_from_cursor(conn, *cur)
    return json.dumps({"sent": {k: [list(m) for m in v] for k, v in outbox_sent(conn).items()},
                       "reports_offset": offset})


__all__ = ["AGENT_RUNS", "AGENT_SPEND", "AgentRunLog", "OUTBOX_FILE", "RUNS_FILE", "SCHEDULER_FILE", "SPEND_FILE",
           "add_spend", "all_spend", "bump_export", "complete_lines", "count_fallback", "cursor_from_line_offset",
           "dual_read", "ensure_imported", "export_json", "export_latest", "export_spend", "fallback_hits",
           "foreign_write", "line_offset_from_cursor", "load_jobs", "max_run_id", "merge_agent_spend_json",
           "month_spent", "outbox_cursor", "outbox_document", "outbox_forget", "outbox_mark_sent", "outbox_sent",
           "outbox_set_shown", "outbox_unwatch", "outbox_watch", "outbox_watched",
           "render_spend", "reserve_spend", "runs_after", "remember_hash", "save_jobs", "scheduler_document", "scheduler_heartbeat",
           "scheduler_status", "set_outbox_cursor",
           "table_empty"]
