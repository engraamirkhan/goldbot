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

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/registry_release.sh"
WORKFLOWS = [ROOT / ".github/workflows/research.yml", ROOT / ".github/workflows/preregister-quarter.yml"]

FAKE_GH = r"""#!/usr/bin/env bash
# fake gh: behaviour from FAKE_MODE; every call logged to $FAKE_LOG
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "release view")
    if [ "${3:-}" = "research-v1" ] && [ "${4:-}" = "--json" ]; then
      case "$FAKE_MODE" in
        no-release) echo "release not found" >&2; exit 1 ;;
        view-error) echo "HTTP 502: Bad Gateway" >&2; exit 1 ;;
        no-asset) echo "other.txt" ;;
        *) printf 'other.txt\nregistry.jsonl\n' ;;
      esac
      exit 0
    fi
    [ "$FAKE_MODE" = "no-release" ] && exit 1
    exit 0 ;;
  "release download")
    [ "$FAKE_MODE" = "download-fails" ] && { echo "connection reset" >&2; exit 1; }
    out=""; while [ $# -gt 0 ]; do [ "$1" = "-O" ] && out=$2; shift; done
    cp "$FAKE_REMOTE" "$out" ;;
  "release upload") cp "$4" "$FAKE_UPLOADED" ;;
  "release create") ;;
  *) echo "unexpected gh call: $*" >&2; exit 3 ;;
esac
"""


def _row(ts: str, h: str) -> str:
    return json.dumps({"trial": 1, "ts": ts, "agent_id": "a", "config_hash": h, "feature_version": "f-079deacc61",
                       "family": "tsmom", "status": "evaluated", "results": {}}) + "\n"


@pytest.fixture
def gh(tmp_path: Path) -> dict[str, str]:
    if shutil.which("bash") is None:
        pytest.skip("needs bash")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(FAKE_GH)
    (bin_dir / "gh").chmod(0o755)
    py_dir = Path(sys.executable).parent                      # the test interpreter's `python`, with goldbot installed
    path = os.pathsep.join([str(bin_dir), str(py_dir), os.environ["PATH"]])
    env = {**os.environ, "PATH": path, "FAKE_LOG": str(tmp_path / "gh.log"),
           "FAKE_REMOTE": str(tmp_path / "remote.jsonl"), "FAKE_UPLOADED": str(tmp_path / "uploaded.jsonl"),
           "FAKE_MODE": "ok"}
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
