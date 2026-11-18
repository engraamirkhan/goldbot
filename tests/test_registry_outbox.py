"""A brain token without `Contents: write` cannot publish the registry: the pending payload goes to a local outbox
(state/registry_outbox/) for the Mac to publish with its own gh (scripts/registry_publish.sh), logged at INFO, not
WARNING; the brain clears an entry once the release holds its rows (read-only from the Mac)."""
import logging
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from goldbot.research import registry_sync as rs
from goldbot.research.registry import TrialRegistry


def _local(base: Path, specs: list[tuple[str, int]]) -> Path:
    reg = TrialRegistry(base / "state" / "research_registry.jsonl")
    for agent, x in specs:
        reg.record(agent_id=agent, family="session_open", config={"x": x}, feature_version="f1", rationale="t",
                   results={})
    return base / "state" / "research_registry.jsonl"


def _forbidden_push(monkeypatch: pytest.MonkeyPatch, remote: list[dict]) -> None:
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: (remote, (1, "t", 1, "d")))

    def refuse(t, rows, *, expected, strict=True):
        raise rs.RegistryForbidden("HTTP 403 on POST")
    monkeypatch.setattr(rs, "push_release", refuse)


def test_a_forbidden_upload_writes_the_pending_registry_to_the_outbox_and_logs_info(tmp_path, monkeypatch, caplog):
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    _forbidden_push(monkeypatch, [])
    with caplog.at_level(logging.INFO, logger=rs.log.name):
        n = rs.sync(local, "tok")
    entries = list((local.parent / rs.OUTBOX).glob("*.jsonl"))
    assert n == 2 and len(entries) == 1
    assert rs.read_rows(entries[0]) == rs.read_rows(local)
    assert caplog.records and all(r.levelno <= logging.INFO for r in caplog.records)


def test_syncing_again_with_the_same_rows_leaves_exactly_one_identical_outbox_entry(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    first = {p.name: p.read_bytes() for p in (local.parent / rs.OUTBOX).iterdir()}
    rs.sync(local, "tok")
    assert {p.name: p.read_bytes() for p in (local.parent / rs.OUTBOX).iterdir()} == first


def test_a_grown_registry_replaces_the_older_outbox_entry_and_no_partial_file_is_left(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    TrialRegistry(local).record(agent_id="b", family="session_open", config={"x": 2}, feature_version="f1",
                                rationale="t", results={})
    rs.sync(local, "tok")
    names = [p.name for p in (local.parent / rs.OUTBOX).iterdir() if p.name != rs.OUTBOX_SINCE]
    assert len(names) == 1 and names[0].endswith(".jsonl")
    assert len(rs.read_rows(local.parent / rs.OUTBOX / names[0])) == 2


def test_an_outbox_entry_whose_rows_are_all_on_the_release_is_cleared_on_the_next_sync(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    assert list((local.parent / rs.OUTBOX).iterdir())
    _forbidden_push(monkeypatch, rs.read_rows(local))         # the Mac published them
    rs.sync(local, "tok")
    assert not list((local.parent / rs.OUTBOX).iterdir())


def test_a_successful_upload_clears_the_outbox(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: ([], None))
    monkeypatch.setattr(rs, "push_release", lambda t, rows, *, expected, strict=True: None)
    rs.sync(local, "tok")
    assert not list((local.parent / rs.OUTBOX).iterdir())


def test_other_errors_still_raise_and_write_no_outbox(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])

    def down(t, bootstrap=False):
        raise OSError("net down")
    monkeypatch.setattr(rs, "pull_stamped", down)
    with pytest.raises(OSError):
        rs.sync(local, "tok")
    assert not (local.parent / rs.OUTBOX).exists()


def test_the_publish_command_merges_the_outbox_with_the_release_through_the_same_union(tmp_path, monkeypatch):
    """The Mac side: outbox files in, the release copy merged exactly as `sync` would (same rows, same numbers)."""
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    outbox = tmp_path / "ob"
    outbox.mkdir()
    (outbox / "x.jsonl").write_text(local.read_text())
    (outbox / "y.jsonl").write_text(local.read_text())                       # a duplicate entry: no duplicate rows
    release = _local(tmp_path / "rel", [("w", 9)])
    seen: dict[str, list] = {}
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: (rs.read_rows(release), (1, "t", 1, "d")))
    monkeypatch.setattr(rs, "push_release", lambda t, rows, *, expected, strict=True: seen.setdefault("rows", rows))
    n = rs.publish_outbox(object(), outbox, tmp_path / "work.jsonl")  # type: ignore[arg-type]
    assert n == 3 and len(seen["rows"]) == 3
    assert seen["rows"] == rs.merge_rows(rs.read_rows(local), rs.read_rows(release))


# ---------------------------------------------------------------------- incident 2026-11-18 (SRE): the outbox never cleared
# The outbox is a snapshot taken at sync time; rows the brain added after it (the six learning-class K rows of 15 Nov,
# scripts/record_learning_k_rows.py) never reached the Mac publish, and the brain cleared only when the release held
# every LOCAL row, so health's feedback_loop failed on the entry's age for up to a week after the publish.
def _add(local: Path, agent: str, x: int) -> None:
    TrialRegistry(local).record(agent_id=agent, family="session_open", config={"x": x}, feature_version="f1",
                                rationale="t", results={})


class _Release:
    """The release copy as `pull_stamped` reads it, counting the reads (the daily check must be cheap)."""
    def __init__(self, rows: list[dict] | None = None, fail: Exception | None = None) -> None:
        self.rows, self.fail, self.reads = rows or [], fail, 0

    def pull(self, t, bootstrap=False):
        self.reads += 1
        if self.fail is not None:
            raise self.fail
        return self.rows, (1, "t", 1, "d")


def test_a_row_the_brain_added_after_the_outbox_snapshot_is_published_with_the_brain_registry(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")                                          # the outbox snapshot: row a
    _add(local, "k", 2)                                           # a row written between syncs
    seen: dict[str, list] = {}
    monkeypatch.setattr(rs, "pull_stamped", lambda t, bootstrap=False: ([], (1, "t", 1, "d")))
    monkeypatch.setattr(rs, "push_release", lambda t, rows, *, expected, strict=True: seen.setdefault("rows", rows))
    rs.publish_outbox(object(), local.parent / rs.OUTBOX, tmp_path / "work.jsonl", [local])  # type: ignore[arg-type]
    assert len(seen["rows"]) == 2 and rs._keys(seen["rows"]) == rs._keys(rs.read_rows(local))


def _publish_repo(tmp: Path, registry: str | None, outbox: dict[str, str]) -> tuple[Path, dict[str, str]]:
    """A copy of scripts/registry_publish.sh with a fake `ssh` (the brain: its state dir is `tmp/brain`, `sudo` is
    dropped) and a fake venv python that copies every file it was asked to publish into `tmp/calls/`."""
    repo = tmp / "repo"
    (repo / "scripts").mkdir(parents=True)
    script = repo / "scripts" / "registry_publish.sh"
    script.write_text((Path(__file__).resolve().parents[1] / "scripts" / "registry_publish.sh").read_text())
    brain = tmp / "brain"
    (brain / "registry_outbox").mkdir(parents=True)
    for name, text in outbox.items():
        (brain / "registry_outbox" / name).write_text(text)
    if registry is not None:
        (brain / "research_registry.jsonl").write_text(registry)
    bin_ = tmp / "bin"
    bin_.mkdir()
    (bin_ / "ssh").write_text('#!/bin/bash\ncmd="${@: -1}"\neval "${cmd//sudo /}"\n')
    (bin_ / "ssh").chmod(0o755)
    venv = repo / ".venv" / "bin"
    venv.mkdir(parents=True)
    calls = tmp / "calls"
    calls.mkdir()
    # argv: -m goldbot.research.registry_sync publish-outbox OUTBOX WORK [--extra FILE ...]
    (venv / "python").write_text(f'#!/bin/bash\necho "$@" > {calls}/argv\nshift 3\ncp "$1"/*.jsonl {calls}/ '
                                 f'2>/dev/null\nshift 2\nwhile [ $# -gt 0 ]; do cp "$2" {calls}/extra-$(basename "$2"); '
                                 'shift 2; done\n')
    (venv / "python").chmod(0o755)
    return script, {"PATH": f"{bin_}:/usr/bin:/bin", "HOME": str(tmp), "BRAIN_STATE": str(brain), "TMPDIR": str(tmp)}


def test_the_publish_script_reads_the_brains_whole_registry_and_publishes_it_with_the_outbox(tmp_path):
    script, env = _publish_repo(tmp_path, '{"ts": "late"}\n', {"0123456789abcdef.jsonl": '{"ts": "snap"}\n'})
    out = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    files = {p.name: p.read_text() for p in (tmp_path / "calls").iterdir() if p.name != "argv"}
    assert files["0123456789abcdef.jsonl"] == '{"ts": "snap"}\n'
    assert '{"ts": "late"}\n' in [v for k, v in files.items() if k.startswith("extra-")]
    assert "brain registry rows read: 1" in out.stdout


def test_the_publish_script_stops_when_the_brain_registry_cannot_be_read(tmp_path):
    script, env = _publish_repo(tmp_path, None, {"0123456789abcdef.jsonl": '{"ts": "snap"}\n'})
    out = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True)
    assert out.returncode != 0 and "research_registry.jsonl" in out.stderr
    assert not (tmp_path / "calls" / "argv").exists()              # a publish without the registry is not run


def test_the_daily_check_clears_the_outbox_once_the_release_holds_its_rows_even_with_newer_local_rows(tmp_path,
                                                                                                         monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    published = rs.read_rows(local)
    _add(local, "k", 2)                                           # written after the snapshot, not yet published
    monkeypatch.setattr(rs, "pull_stamped", _Release(published).pull)
    assert rs.check_outbox(object(), local) == {"outbox": "cleared"}  # type: ignore[arg-type]
    assert not rs._outbox_files(local.parent / rs.OUTBOX)
    assert not (local.parent / rs.OUTBOX / rs.OUTBOX_SINCE).exists()


def test_the_daily_check_keeps_the_outbox_and_its_wait_while_rows_are_missing_from_the_release(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1), ("b", 2)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    since = (local.parent / rs.OUTBOX / rs.OUTBOX_SINCE).read_text()
    monkeypatch.setattr(rs, "pull_stamped", _Release(rs.read_rows(local)[:1]).pull)
    assert rs.check_outbox(object(), local) == {"outbox": "waiting", "rows": 1}  # type: ignore[arg-type]
    assert rs._outbox_files(local.parent / rs.OUTBOX)
    assert (local.parent / rs.OUTBOX / rs.OUTBOX_SINCE).read_text() == since


def test_the_daily_check_reads_nothing_when_the_outbox_is_empty(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    rel = _Release([])
    monkeypatch.setattr(rs, "pull_stamped", rel.pull)
    assert rs.check_outbox(object(), local) == {"outbox": "empty"}  # type: ignore[arg-type]
    assert rel.reads == 0


def test_a_release_the_daily_check_cannot_read_keeps_the_outbox_for_the_age_fallback(tmp_path, monkeypatch):
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    monkeypatch.setattr(rs, "pull_stamped", _Release(fail=OSError("net down")).pull)
    out = rs.check_outbox(object(), local)  # type: ignore[arg-type]
    assert out["outbox"] == "cant_check" and "net down" in out["error"]
    assert rs._outbox_files(local.parent / rs.OUTBOX)


def test_a_sync_after_the_entry_was_published_restarts_the_wait_for_the_newer_rows(tmp_path, monkeypatch):
    """The age health judges is that of the oldest UNPUBLISHED payload: once the Mac published an entry, rows written
    after it start a new wait instead of inheriting the old stamp."""
    local = _local(tmp_path, [("a", 1)])
    _forbidden_push(monkeypatch, [])
    rs.sync(local, "tok")
    since = local.parent / rs.OUTBOX / rs.OUTBOX_SINCE
    since.write_text("2026-11-01T00:00:00+00:00\n")
    published = rs.read_rows(local)
    _add(local, "k", 2)
    _forbidden_push(monkeypatch, published)                       # the Mac published the first entry only
    rs.sync(local, "tok")
    entries = rs._outbox_files(local.parent / rs.OUTBOX)
    assert len(entries) == 1 and len(rs.read_rows(entries[0])) == 2
    assert since.read_text() != "2026-11-01T00:00:00+00:00\n"


def test_the_registry_outbox_job_runs_daily_after_the_mac_publish_and_skips_without_a_token(tmp_path):
    from goldbot.config import load_settings
    from goldbot.data.store import Store
    from goldbot.ops.jobs import JOBS, JobContext, registry_outbox
    from goldbot.research.model_registry import ModelRegistry
    from goldbot.research.population import Population
    s = load_settings().scheduler.registry_outbox
    assert JOBS["registry_outbox"] is registry_outbox and s.kind == "daily" and s.run_if_never_run
    assert "10:00" <= s.at < "12:00"       # after the Mac publish (launchd 09:15 Mac time, 08:15-09:15 UTC)

    def ctx(check):
        return JobContext(settings=load_settings(), store=Store(tmp_path / "data"), state_dir=tmp_path,
                          models=ModelRegistry(tmp_path / "models"), trials=TrialRegistry(tmp_path / "r.jsonl"),
                          accounts=[], population=Population(tmp_path / "population.json"), check_outbox=check)
    slot = pd.Timestamp("2026-11-18 10:30", tz="UTC")
    assert "skipped" in registry_outbox(ctx(None), slot)
    assert registry_outbox(ctx(lambda p: {"outbox": "cleared", "path": str(p)}), slot) == \
        {"outbox": "cleared", "path": str(tmp_path / "r.jsonl")}
