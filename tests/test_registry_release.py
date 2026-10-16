"""The workflows that write the shared trial registry (research.yml, preregister-quarter.yml) go through
scripts/registry_release.sh, which runs goldbot.research.registry_sync's one protocol through gh. It never overwrites
the registry after a failed read (security review of ab24d87): fetch fails the job when research-v1/registry.jsonl
exists but cannot be read, and starts empty only when the release does not exist (or with --bootstrap). It never
overwrites a concurrent writer (BACKLOG 51): save publishes by compare-and-swap through a pending asset and merges and
retries when the VPS monthly loop lands meanwhile. It never leaves the release without a registry after a delete,
and refuses a union shorter than the published copy. Exercised with a stateful fake `gh` on PATH; no network."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/registry_release.sh"
WORKFLOWS = [ROOT / ".github/workflows/research.yml", ROOT / ".github/workflows/preregister-quarter.yml"]

FAKE_GH = r'''
# fake gh: research-v1 as GitHub serves it, state in $FAKE_STATE (seeded from $FAKE_REMOTE), behaviour from
# $FAKE_MODE, every call logged to $FAKE_LOG. Asset names are unique (an upload onto a taken name fails with
# HTTP 422), a deleted id is a 404, every write gets a new id and updated_at. The race modes play a concurrent writer
# (the VPS monthly loop): race-once / race-always land a row right after the pending upload (before the re-check),
# race-at-delete replaces the asset just before the delete of the pulled id. patch-fail answers every rename with a
# 502, patch-fail-once the first only; recheck-fails answers the first release read after an upload with a 502.
import json, os, sys, tempfile
from pathlib import Path

args, mode = sys.argv[1:], os.environ["FAKE_MODE"]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(args) + "\n")
state_path = Path(os.environ["FAKE_STATE"])
if state_path.exists():
    st = json.loads(state_path.read_text())
else:
    st = {"exists": mode != "no-release", "next": 100, "clock": 0, "uploads": 0, "deletes": 0, "patches": 0,
          "reads_after_upload": 0, "assets": []}
    st["assets"].append({"id": 1, "name": "other.txt", "updated_at": "t0", "size": 1, "body": "x"})
    if mode not in ("no-release", "no-asset"):
        body = Path(os.environ["FAKE_REMOTE"]).read_text()
        st["assets"].append({"id": 2, "name": "registry.jsonl", "updated_at": "t0", "size": len(body), "body": body})


def save():
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(state_path)


def die(msg, code=1):
    save()
    print(msg, file=sys.stderr)
    sys.exit(code)


def find(name):
    return next((a for a in st["assets"] if a["name"] == name), None)


def put(name, body):
    st["assets"] = [a for a in st["assets"] if a["name"] != name]
    st["next"] += 1
    st["clock"] += 1
    st["assets"].append({"id": st["next"], "name": name, "updated_at": f"t{st['clock']}", "size": len(body),
                         "body": body})


def concurrent_write():
    cur = find("registry.jsonl")
    n = st["clock"] + 1
    row = {"trial": 1, "ts": f"2026-10-{10 + n:02d}T00:00:00+00:00", "agent_id": "vps", "config_hash": f"vps{n}",
           "feature_version": "f-079deacc61", "family": "tsmom", "status": "evaluated", "results": {}}
    put("registry.jsonl", (cur["body"] if cur else "") + json.dumps(row) + "\n")


def public(a):
    return {k: v for k, v in a.items() if k != "body"}


cmd = args[:2]
if cmd == ["release", "create"]:
    st["exists"] = True
elif cmd == ["release", "upload"]:
    if "--clobber" in args:
        die("this fake refuses --clobber: the protocol swaps by id")
    src = Path(args[3])
    if find(src.name) is not None:
        die("HTTP 422: asset already exists")
    put(src.name, src.read_text())
    st["uploads"] += 1
    if mode == "race-always" or (mode == "race-once" and st["uploads"] == 1):
        concurrent_write()
elif args[0] == "api":
    method = args[args.index("-X") + 1] if "-X" in args else "GET"
    path = next(x for x in args[1:] if x.startswith("repos/"))
    assert path.startswith("repos/{owner}/{repo}/"), path
    if path.endswith("/releases/tags/research-v1") and method == "GET":
        if mode == "view-error":
            die("gh: Bad Gateway (HTTP 502)")
        if st["uploads"] and mode == "recheck-fails" and not st["reads_after_upload"]:
            st["reads_after_upload"] += 1
            die("gh: Bad Gateway (HTTP 502)")
        if not st["exists"]:
            print('{"message": "Not Found"}')
            die("gh: Not Found (HTTP 404)")
        print(json.dumps({"upload_url": "https://uploads.example/{?name}",
                          "assets": [public(a) for a in st["assets"]]}))
    elif "/releases/assets/" in path:
        aid = int(path.rsplit("/", 1)[1])
        a = next((x for x in st["assets"] if x["id"] == aid), None)
        if method == "GET" and "Accept: application/octet-stream" in args:     # a download, by id
            st["downloads"] = st.get("downloads", 0) + 1
            if mode == "download-fails":
                die("connection reset")
            if a is None:
                die("gh: Not Found (HTTP 404)")
            sys.stdout.write(a["body"])
        elif method == "DELETE":
            st["deletes"] += 1
            if mode == "race-at-delete" and st["deletes"] == 1:
                concurrent_write()
                a = next((x for x in st["assets"] if x["id"] == aid), None)
            if a is None:
                die("gh: Not Found (HTTP 404)")
            st["assets"].remove(a)
        elif method == "PATCH":
            st["patches"] += 1
            if mode == "patch-fail" or (mode == "patch-fail-once" and st["patches"] == 1):
                die("gh: Bad Gateway (HTTP 502)")
            name = next(x.split("=", 1)[1] for x in args if x.startswith("name="))
            if a is None:
                die("gh: Not Found (HTTP 404)")
            if find(name) is not None:
                die("gh: Validation Failed (HTTP 422)")
            st["clock"] += 1
            a.update(name=name, updated_at=f"t{st['clock']}")
            if name == "registry.jsonl":
                Path(os.environ["FAKE_UPLOADED"]).write_text(a["body"])
            print(json.dumps(public(a)))
        else:
            die(f"unexpected api call: {args}", 3)
    else:
        die(f"unexpected api call: {args}", 3)
else:
    die(f"unexpected gh call: {args}", 3)
save()
'''


def _row(ts: str, h: str) -> str:
    return json.dumps({"trial": 1, "ts": ts, "agent_id": "a", "config_hash": h, "feature_version": "f-079deacc61",
                       "family": "tsmom", "status": "evaluated", "results": {}}) + "\n"


def _state(env: dict[str, str]) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(Path(env["FAKE_STATE"]).read_text())
    return state


def _hashes_of(env: dict[str, str], name: str) -> list[str]:
    a = next(x for x in _state(env)["assets"] if x["name"] == name)
    return [json.loads(line)["config_hash"] for line in a["body"].splitlines()]


def _published(env: dict[str, str]) -> list[str]:
    return _hashes_of(env, "registry.jsonl")


def _names(env: dict[str, str]) -> list[str]:
    return sorted(a["name"] for a in _state(env)["assets"])


def _pending(env: dict[str, str]) -> list[str]:
    return [n for n in _names(env) if n.startswith("registry.jsonl.pending-")]


def _log(env: dict[str, str]) -> str:
    p = Path(env["FAKE_LOG"])
    return p.read_text() if p.exists() else ""


@pytest.fixture
def gh(tmp_path: Path) -> dict[str, str]:
    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!{sys.executable}\n" + FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    py_dir = Path(sys.executable).parent                      # the test interpreter's `python`, with goldbot installed
    path = os.pathsep.join([str(bin_dir), str(py_dir), os.environ["PATH"]])
    env = {**os.environ, "PATH": path, "FAKE_LOG": str(tmp_path / "gh.log"),
           "FAKE_REMOTE": str(tmp_path / "remote.jsonl"), "FAKE_UPLOADED": str(tmp_path / "uploaded.jsonl"),
           "FAKE_MODE": "ok", "FAKE_STATE": str(tmp_path / "gh_state.json"), "PYTHONPATH": str(ROOT)}
    Path(env["FAKE_REMOTE"]).write_text(_row("2026-10-01T00:00:00+00:00", "aaa") + _row("2026-10-02T00:00:00+00:00", "bbb"))
    return env


def _run(env: dict[str, str], cwd: Path, *args: str, mode: str = "ok") -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, env={**env, "FAKE_MODE": mode},
                          capture_output=True, text=True, timeout=120)


def _local(tmp_path: Path, *hashes: str) -> None:
    (tmp_path / "registry.jsonl").write_text("".join(_row(f"2026-10-0{3 + i}T00:00:00+00:00", h)
                                                     for i, h in enumerate(hashes)))


def _local_hashes(tmp_path: Path) -> list[str]:
    return [json.loads(x)["config_hash"] for x in (tmp_path / "registry.jsonl").read_text().splitlines()]


# ---------------------------------------------------------------- fetch
def test_fetch_fails_the_job_when_the_published_registry_exists_but_its_download_fails(gh: dict[str, str],
                                                                                        tmp_path: Path) -> None:
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode="download-fails")
    assert r.returncode != 0 and not (tmp_path / "registry.jsonl").exists()


def test_fetch_fails_the_job_when_the_release_cannot_be_read(gh: dict[str, str], tmp_path: Path) -> None:
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode="view-error")
    assert r.returncode != 0 and "HTTP 502" in r.stderr and not (tmp_path / "registry.jsonl").exists()


def test_fetch_starts_empty_only_when_the_release_does_not_exist(gh: dict[str, str], tmp_path: Path) -> None:
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode="no-release")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "registry.jsonl").read_text() == "" and "octet-stream" not in _log(gh)


def test_fetch_refuses_a_release_without_a_registry_unless_bootstrapping(gh: dict[str, str], tmp_path: Path) -> None:
    """Defence in depth (HIGH-1): a release that exists without registry.jsonl had it deleted, by a stranded swap or
    by hand; starting empty would shrink the deflated-Sharpe N and restart the numbering."""
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode="no-asset")
    assert r.returncode != 0 and "--bootstrap" in r.stderr and not (tmp_path / "registry.jsonl").exists()
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", "--bootstrap", mode="no-asset")
    assert r.returncode == 0 and (tmp_path / "registry.jsonl").read_text() == ""


def test_fetch_downloads_the_published_registry(gh: dict[str, str], tmp_path: Path) -> None:
    assert _run(gh, tmp_path, "fetch", "registry.jsonl").returncode == 0
    assert (tmp_path / "registry.jsonl").read_text() == Path(gh["FAKE_REMOTE"]).read_text()


# ---------------------------------------------------------------- save
def test_save_publishes_the_union_through_a_pending_asset_and_leaves_one_copy(gh: dict[str, str],
                                                                              tmp_path: Path) -> None:
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["aaa", "bbb", "ccc"] and _names(gh) == ["other.txt", "registry.jsonl"]
    assert [json.loads(x)["config_hash"] for x in Path(gh["FAKE_UPLOADED"]).read_text().splitlines()] == \
        ["aaa", "bbb", "ccc"]
    log = _log(gh)
    assert "registry.jsonl.pending-" in log and "--clobber" not in log


def test_save_never_uploads_after_a_failed_download(gh: dict[str, str], tmp_path: Path) -> None:
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="download-fails")
    assert r.returncode != 0 and not Path(gh["FAKE_UPLOADED"]).exists()
    assert "release upload" not in _log(gh)


def test_save_refuses_a_merged_registry_shorter_than_the_published_one(gh: dict[str, str], tmp_path: Path) -> None:
    dup = _row("2026-10-01T00:00:00+00:00", "aaa")
    Path(gh["FAKE_REMOTE"]).write_text(dup * 3)              # the union keeps one: 1 line < 3 published
    (tmp_path / "registry.jsonl").write_text("")
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode != 0 and "refusing to upload" in r.stderr and not Path(gh["FAKE_UPLOADED"]).exists()
    assert "release upload" not in _log(gh)


@pytest.mark.parametrize("mode", ["race-once", "race-at-delete"])
def test_save_merges_a_concurrent_vps_push_instead_of_overwriting_it(gh: dict[str, str], tmp_path: Path,
                                                                      mode: str) -> None:
    """The VPS monthly loop pushes outside `concurrency: research-registry` (BACKLOG 51): its row lands after this
    job's pull (race-once: before the re-check; race-at-delete: between the re-check and the delete)."""
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode=mode)
    assert r.returncode == 0, r.stderr
    published = _published(gh)
    assert published[:3] == ["aaa", "bbb", "ccc"] and len(published) == 4 and published[3].startswith("vps")
    assert len(set(published)) == len(published)                 # each trial once
    assert _names(gh) == ["other.txt", "registry.jsonl"]
    assert "lost a race" in r.stderr


def test_save_fails_loudly_after_bounded_retries_when_the_registry_keeps_changing(gh: dict[str, str],
                                                                                  tmp_path: Path) -> None:
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="race-always")
    assert r.returncode != 0 and "changed during each of 3" in r.stderr
    assert _state(gh)["uploads"] == 3
    assert not Path(gh["FAKE_UPLOADED"]).exists()                 # never published over the other writer
    published = _published(gh)
    assert published[:2] == ["aaa", "bbb"] and "ccc" not in published and len(published) == 5
    assert _names(gh) == ["other.txt", "registry.jsonl"]          # every pending upload cleaned up
    assert "ccc" in _local_hashes(tmp_path) and set(published[:4]) <= set(_local_hashes(tmp_path))


def test_save_creates_the_release_and_publishes_the_first_registry(gh: dict[str, str], tmp_path: Path) -> None:
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="no-release")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["ccc"] and "release create" in _log(gh)


def test_save_drops_its_pending_upload_when_the_recheck_fails(gh: dict[str, str], tmp_path: Path) -> None:
    """MEDIUM-1: a failed read between the upload and the swap removes our pending asset; the published copy is
    untouched."""
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="recheck-fails")
    assert r.returncode != 0 and "HTTP 502" in r.stderr
    assert _names(gh) == ["other.txt", "registry.jsonl"] and _published(gh) == ["aaa", "bbb"]


def test_save_retries_a_rename_that_fails_once_after_the_delete(gh: dict[str, str], tmp_path: Path) -> None:
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="patch-fail-once")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["aaa", "bbb", "ccc"] and _names(gh) == ["other.txt", "registry.jsonl"]


def test_a_rename_that_keeps_failing_strands_the_merge_and_recover_publishes_it(gh: dict[str, str],
                                                                               tmp_path: Path) -> None:
    """HIGH-1 (reviewer's exp_bash.py): PATCH 502 after the delete. The pending upload is the only full copy: it
    stays; fetch and save refuse rather than start empty; `recover` (named in the error) publishes it."""
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="patch-fail")
    assert r.returncode != 0 and "registry_release.sh recover" in r.stderr
    assert "registry.jsonl" not in _names(gh) and len(_pending(gh)) == 1
    assert _hashes_of(gh, _pending(gh)[0]) == ["aaa", "bbb", "ccc"]
    r = _run(gh, tmp_path, "fetch", "out.jsonl")
    assert r.returncode != 0 and "in flight" in r.stderr and "recover" in r.stderr
    assert not (tmp_path / "out.jsonl").exists()
    r = _run(gh, tmp_path, "recover")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["aaa", "bbb", "ccc"] and _names(gh) == ["other.txt", "registry.jsonl"]


def test_recover_unions_the_published_copy_with_every_pending_one(gh: dict[str, str], tmp_path: Path) -> None:
    st: dict[str, Any] = {"exists": True, "next": 100, "clock": 0, "uploads": 0, "deletes": 0, "patches": 0,
                          "reads_after_upload": 0, "assets": []}
    for i, (name, body) in enumerate([("registry.jsonl", _row("2026-10-01T00:00:00+00:00", "aaa")),
                                      ("registry.jsonl.pending-x", _row("2026-10-02T00:00:00+00:00", "bbb")),
                                      ("registry.jsonl.pending-y", _row("2026-10-03T00:00:00+00:00", "ccc"))]):
        st["assets"].append({"id": 10 + i, "name": name, "updated_at": "t0", "size": len(body), "body": body})
    Path(gh["FAKE_STATE"]).write_text(json.dumps(st))
    r = _run(gh, tmp_path, "recover")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["aaa", "bbb", "ccc"] and _names(gh) == ["registry.jsonl"]


def test_fetch_and_save_never_read_a_pending_upload_without_a_published_copy_as_empty(gh: dict[str, str],
                                                                                     tmp_path: Path) -> None:
    """Another writer is between its delete and its rename, or stranded there."""
    Path(gh["FAKE_STATE"]).write_text(json.dumps({
        "exists": True, "next": 100, "clock": 0, "uploads": 0, "deletes": 0, "patches": 0, "reads_after_upload": 0,
        "assets": [{"id": 9, "name": "registry.jsonl.pending-x", "updated_at": "t0", "size": 1,
                    "body": _row("2026-10-01T00:00:00+00:00", "aaa")}]}))
    r = _run(gh, tmp_path, "fetch", "out.jsonl")
    assert r.returncode != 0 and "in flight" in r.stderr and not (tmp_path / "out.jsonl").exists()
    _local(tmp_path, "ccc")
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode != 0 and "release upload" not in _log(gh)
    assert _names(gh) == ["registry.jsonl.pending-x"]


def test_the_release_notes_name_every_writer_of_the_registry() -> None:
    from goldbot.research import registry_sync as rs
    assert "research and preregister-quarter workflows and the VPS monthly loop" in rs.RELEASE_NOTES
    assert "registry_sync" in SCRIPT.read_text()
    for wf in WORKFLOWS:
        assert "gh release create" not in wf.read_text()                     # created only by registry_sync


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_registry_workflows_read_and_save_the_registry_only_through_the_guarded_script(path: Path) -> None:
    raw = path.read_text()
    assert not re.search(r"release download research-v1[^\n]*\|\|", raw)     # no fallback after a failed download
    assert "release download research-v1" not in raw and "release upload research-v1" not in raw
    assert "scripts/registry_release.sh fetch registry.jsonl" in raw
    assert "scripts/registry_release.sh save registry.jsonl" in raw
    wf = yaml.safe_load(raw)
    assert "concurrency" not in wf                                           # the lock is on the writing job only
    writers = [name for name, job in wf["jobs"].items()
               if "registry_release.sh save" in "\n".join(s.get("run", "") for s in job["steps"])]
    assert writers and all(wf["jobs"][j]["concurrency"]["group"] == "research-registry" for j in writers)
    readers = [name for name in wf["jobs"] if name not in writers]
    assert all("concurrency" not in wf["jobs"][j] for j in readers)          # a dry run never waits on a writer


def test_research_yml_passes_discovery_values_only_in_equals_form_after_a_families_guard() -> None:
    """A value given as a separate argument after `--families` (nargs="?") can be an option itself, e.g.
    families=--registry=/tmp/x would move where the trial row is written: values go in the equals form only."""
    wf = yaml.safe_load((ROOT / ".github/workflows/research.yml").read_text())
    run = "\n".join(s.get("run", "") for s in wf["jobs"]["walk-forward"]["steps"])
    assert '--families="$FAMILIES"' in run and '--discover-config="$DISCOVER_CONFIG"' in run
    assert '--rule-ref="$RULE_REF"' in run
    assert not re.search(r'--(families|discover-config|rule-ref) "\$', run)        # never a separate argument
    guard = 'case "$FAMILIES" in ""|column|feature|family|cluster) ;;'
    assert guard in run and run.index(guard) < run.index('--families="$FAMILIES"')


def test_research_yml_writes_the_registry_only_from_main() -> None:
    wf = yaml.safe_load((ROOT / ".github/workflows/research.yml").read_text())
    assert "github.ref == 'refs/heads/main'" in wf["jobs"]["walk-forward"]["if"]
