"""The workflows that write the shared trial registry (research.yml, preregister-quarter.yml) never overwrite it after a
failed read (security review of ab24d87): scripts/registry_release.sh fails the job when research-v1/registry.jsonl
exists but cannot be read, starts empty only when the release or the asset does not exist, and refuses an upload whose
merged registry has fewer lines than the published copy. Exercised with a fake `gh` on PATH; no network."""
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
# $FAKE_MODE, every call logged to $FAKE_LOG. Asset names are unique (an upload or a rename onto a taken name fails
# with HTTP 422), a deleted id is a 404, every write gets a new id and updated_at. The race modes play a concurrent
# writer (the VPS monthly loop): race-once / race-always land a row right after the pending upload (before the
# re-check), race-at-delete replaces the asset just before the delete of the pulled id.
import json, os, shutil, sys
from pathlib import Path

args, mode = sys.argv[1:], os.environ["FAKE_MODE"]
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(" ".join(args) + "\n")
state_path = Path(os.environ["FAKE_STATE"])
if state_path.exists():
    st = json.loads(state_path.read_text())
else:
    st = {"exists": mode != "no-release", "next": 100, "clock": 0, "uploads": 0, "deletes": 0, "assets": []}
    st["assets"].append({"id": 1, "name": "other.txt", "updated_at": "t0", "size": 1, "body": "x"})
    if mode not in ("no-release", "no-asset"):
        body = Path(os.environ["FAKE_REMOTE"]).read_text()
        st["assets"].append({"id": 2, "name": "registry.jsonl", "updated_at": "t0", "size": len(body), "body": body})


def save():
    state_path.write_text(json.dumps(st))


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


cmd = args[:2]
if cmd == ["release", "view"]:
    if "--json" in args:
        if mode == "view-error":
            die("HTTP 502: Bad Gateway")
        if not st["exists"]:
            die("release not found")
        print("\n".join(a["name"] for a in st["assets"]))
    elif not st["exists"]:
        die("release not found")
elif cmd == ["release", "download"]:
    if mode == "download-fails":
        die("connection reset")
    name, out = args[args.index("-p") + 1], args[args.index("-O") + 1]
    a = find(name)
    if a is None:
        die(f"no asset {name}")
    Path(out).write_text(a["body"])
elif cmd == ["release", "create"]:
    st["exists"] = True
elif cmd == ["release", "upload"]:
    if "--clobber" in args:
        die("this fake refuses --clobber: the script must swap by id")
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
        if not st["exists"]:
            print('{"message": "Not Found"}')
            die("gh: Not Found (HTTP 404)")
        print(json.dumps({"assets": [{k: v for k, v in a.items() if k != "body"} for a in st["assets"]]}))
    elif "/releases/assets/" in path:
        aid = int(path.rsplit("/", 1)[1])
        a = next((x for x in st["assets"] if x["id"] == aid), None)
        if method == "DELETE":
            st["deletes"] += 1
            if mode == "race-at-delete" and st["deletes"] == 1:
                concurrent_write()
                a = next((x for x in st["assets"] if x["id"] == aid), None)
            if a is None:
                die("gh: Not Found (HTTP 404)")
            st["assets"].remove(a)
        elif method == "PATCH":
            name = next(x.split("=", 1)[1] for x in args if x.startswith("name="))
            if a is None:
                die("gh: Not Found (HTTP 404)")
            if find(name) is not None:
                die("gh: Validation Failed (HTTP 422)")
            st["clock"] += 1
            a.update(name=name, updated_at=f"t{st['clock']}")
            if name == "registry.jsonl":
                Path(os.environ["FAKE_UPLOADED"]).write_text(a["body"])
            print(json.dumps({k: v for k, v in a.items() if k != "body"}))
        else:
            die(f"unexpected api call: {args}", 3)
    else:
        die(f"unexpected api call: {args}", 3)
else:
    die(f"unexpected gh call: {args}", 3)
save()
'''


def _state(env: dict[str, str]) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(Path(env["FAKE_STATE"]).read_text())
    return state


def _published(env: dict[str, str]) -> list[str]:
    a = next(x for x in _state(env)["assets"] if x["name"] == "registry.jsonl")
    return [json.loads(line)["config_hash"] for line in a["body"].splitlines()]


def _names(env: dict[str, str]) -> list[str]:
    return sorted(a["name"] for a in _state(env)["assets"])


def _row(ts: str, h: str) -> str:
    return json.dumps({"trial": 1, "ts": ts, "agent_id": "a", "config_hash": h, "feature_version": "f-079deacc61",
                       "family": "tsmom", "status": "evaluated", "results": {}}) + "\n"


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
           "FAKE_MODE": "ok", "FAKE_STATE": str(tmp_path / "gh_state.json")}
    Path(env["FAKE_REMOTE"]).write_text(_row("2026-10-01T00:00:00+00:00", "aaa") + _row("2026-10-02T00:00:00+00:00", "bbb"))
    return env


def _run(env: dict[str, str], cwd: Path, *args: str, mode: str = "ok") -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT), *args], cwd=cwd, env={**env, "FAKE_MODE": mode},
                          capture_output=True, text=True, timeout=120)


def test_fetch_fails_the_job_when_the_published_registry_exists_but_its_download_fails(gh: dict[str, str],
                                                                                        tmp_path: Path) -> None:
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode="download-fails")
    assert r.returncode != 0 and not (tmp_path / "registry.jsonl").exists()


def test_fetch_fails_the_job_when_the_release_cannot_be_read(gh: dict[str, str], tmp_path: Path) -> None:
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode="view-error")
    assert r.returncode != 0 and "refusing" in r.stderr and not (tmp_path / "registry.jsonl").exists()


@pytest.mark.parametrize("mode", ["no-release", "no-asset"])
def test_fetch_starts_empty_only_when_the_release_or_its_registry_does_not_exist(gh: dict[str, str], tmp_path: Path,
                                                                                mode: str) -> None:
    r = _run(gh, tmp_path, "fetch", "registry.jsonl", mode=mode)
    assert r.returncode == 0 and (tmp_path / "registry.jsonl").read_text() == ""
    assert "release download" not in Path(gh["FAKE_LOG"]).read_text()


def test_fetch_downloads_the_published_registry(gh: dict[str, str], tmp_path: Path) -> None:
    assert _run(gh, tmp_path, "fetch", "registry.jsonl").returncode == 0
    assert (tmp_path / "registry.jsonl").read_text() == Path(gh["FAKE_REMOTE"]).read_text()


def test_save_uploads_the_union_of_the_local_and_published_registries(gh: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode == 0, r.stderr
    uploaded = Path(gh["FAKE_UPLOADED"]).read_text().splitlines()
    assert [json.loads(x)["config_hash"] for x in uploaded] == ["aaa", "bbb", "ccc"]


def test_save_never_uploads_after_a_failed_download(gh: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="download-fails")
    assert r.returncode != 0 and not Path(gh["FAKE_UPLOADED"]).exists()
    assert "release upload" not in Path(gh["FAKE_LOG"]).read_text()


def test_save_refuses_a_merged_registry_shorter_than_the_published_one(gh: dict[str, str], tmp_path: Path) -> None:
    dup = _row("2026-10-01T00:00:00+00:00", "aaa")
    Path(gh["FAKE_REMOTE"]).write_text(dup * 3)              # the union keeps one: 1 line < 3 published
    (tmp_path / "registry.jsonl").write_text("")
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode != 0 and "refusing to upload" in r.stderr and not Path(gh["FAKE_UPLOADED"]).exists()


def test_save_swaps_through_a_pending_asset_and_leaves_one_published_copy(gh: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["aaa", "bbb", "ccc"] and _names(gh) == ["other.txt", "registry.jsonl"]
    log = Path(gh["FAKE_LOG"]).read_text()
    assert "registry.jsonl.pending-" in log and "--clobber" not in log


@pytest.mark.parametrize("mode", ["race-once", "race-at-delete"])
def test_save_merges_a_concurrent_vps_push_instead_of_overwriting_it(gh: dict[str, str], tmp_path: Path,
                                                                      mode: str) -> None:
    """The VPS monthly loop pushes outside `concurrency: research-registry` (BACKLOG 51): its row lands after this
    job's pull (race-once: before the re-check; race-at-delete: between the re-check and the delete)."""
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode=mode)
    assert r.returncode == 0, r.stderr
    published = _published(gh)
    assert published[:3] == ["aaa", "bbb", "ccc"] and len(published) == 4 and published[3].startswith("vps")
    assert len(set(published)) == len(published)                 # each trial once
    assert _names(gh) == ["other.txt", "registry.jsonl"]
    assert "retrying" in r.stderr


def test_save_fails_loudly_after_bounded_retries_when_the_registry_keeps_changing(gh: dict[str, str],
                                                                                  tmp_path: Path) -> None:
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="race-always")
    assert r.returncode != 0 and "changed during each of 3" in r.stderr
    assert _state(gh)["uploads"] == 3
    assert not Path(gh["FAKE_UPLOADED"]).exists()                 # never published over the other writer
    published = _published(gh)
    assert published[:2] == ["aaa", "bbb"] and "ccc" not in published and len(published) == 5
    assert _names(gh) == ["other.txt", "registry.jsonl"]          # every pending upload cleaned up
    local = [json.loads(x)["config_hash"] for x in (tmp_path / "registry.jsonl").read_text().splitlines()]
    assert "ccc" in local and set(published[:4]) <= set(local)    # the job's row kept for a re-run


def test_save_creates_the_release_and_publishes_the_first_registry(gh: dict[str, str], tmp_path: Path) -> None:
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl", mode="no-release")
    assert r.returncode == 0, r.stderr
    assert _published(gh) == ["ccc"] and "release create" in Path(gh["FAKE_LOG"]).read_text()


def test_fetch_and_save_never_read_a_pending_upload_without_a_published_copy_as_empty(gh: dict[str, str],
                                                                                     tmp_path: Path) -> None:
    """Another writer is between its delete and its rename, or crashed there: starting empty would drop every row."""
    Path(gh["FAKE_STATE"]).write_text(json.dumps({"exists": True, "next": 100, "clock": 0, "uploads": 0,
                                                  "deletes": 0, "assets": [{"id": 9, "name": "registry.jsonl.pending-x",
                                                                            "updated_at": "t0", "size": 1,
                                                                            "body": _row("2026-10-01T00:00:00+00:00",
                                                                                         "aaa")}]}))
    r = _run(gh, tmp_path, "fetch", "out.jsonl")
    assert r.returncode != 0 and "in flight" in r.stderr and not (tmp_path / "out.jsonl").exists()
    (tmp_path / "registry.jsonl").write_text(_row("2026-10-03T00:00:00+00:00", "ccc"))
    r = _run(gh, tmp_path, "save", "registry.jsonl")
    assert r.returncode != 0 and "release upload" not in Path(gh["FAKE_LOG"]).read_text()
    assert _names(gh) == ["registry.jsonl.pending-x"]


def test_the_release_notes_name_every_writer_of_the_registry() -> None:
    text = SCRIPT.read_text()
    assert "research and preregister-quarter workflows and the VPS monthly loop" in text
    for wf in WORKFLOWS:
        assert "gh release create" not in wf.read_text()                     # created only by the script


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
