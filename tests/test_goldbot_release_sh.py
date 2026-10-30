"""scripts/goldbot_release.sh: the owner-approvable brain deploy wrapper (ROADMAP 5a FR-9, deploy-approval runbook
option B). It deploys only a 40-hex commit on GitHub's main with green backend + frontend CI, runs exactly the
documented remote commands (D2 deploy, D3 record, D4 health) and nothing else, and its exit code says what happened.
`gh` and `ssh` are fakes on PATH that log every call."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "goldbot_release.sh"
SHA = "a" * 40
HEAD = "b" * 40

pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("python3") is None,
                                reason="needs bash and python3")

FAKE_GH = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$FAKE_LOG.gh"
url=$2
case "$url" in
  */compare/*) [ -n "$FAKE_COMPARE" ] || exit 1; echo "$FAKE_COMPARE" ;;
  */check-runs*) case " $FAKE_GREEN " in *" $(echo "$url" | sed 's#.*/commits/\([0-9a-f]*\)/.*#\1#') "*) echo 2 ;; *) echo 1 ;; esac ;;
  */git/commits/*) case "$url" in *"$FAKE_ODD_TREE"*) echo othertree ;; *) echo sametree ;; esac ;;
  */commits/*) echo "$FAKE_PARENT2" ;;
  *) exit 9 ;;
esac
"""

FAKE_SSH = r"""#!/usr/bin/env bash
cmd=${!#}
printf '%s\n' "$*" >> "$FAKE_LOG.ssh"
case "$cmd" in
  *systemd-run*) exit "${FAKE_D2_RC:-0}" ;;
  *deploys.jsonl*) n=$(grep -c 'deploys.jsonl' "$FAKE_LOG.ssh"); if [ "$n" -ge 2 ] && [ -n "$FAKE_RECORD2" ]; then echo "$FAKE_RECORD2"; else echo "$FAKE_RECORD"; fi ;;
  *health*) echo "$FAKE_HEALTH"; exit "${FAKE_HEALTH_RC:-0}" ;;
  *) exit 99 ;;
esac
"""


def record(result: str = "deployed", to: str = SHA) -> str:
    return json.dumps({"ts": "2026-10-30T10:00:00Z", "result": result, "from": "c" * 40, "to": to})


def health(entry: str = "ok", reconcile: str = "ok", other_fail: bool = False) -> str:
    checks = [{"name": "entry_status:icm-demo", "status": entry, "reason": "open"},
              {"name": "engine:icm-demo", "status": "ok", "reason": "fresh"},
              {"name": "reconcile:icm-demo", "status": reconcile, "reason": "positions match the broker"}]
    if other_fail:
        checks.append({"name": "tv_pull", "status": "fail", "reason": "stale"})
    return json.dumps({"ts": "2026-10-30T10:01:00Z", "status": "ok", "checks": checks})


@pytest.fixture
def fakes(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for name, body in (("gh", FAKE_GH), ("ssh", FAKE_SSH)):
        (bin_ / name).write_text(body, encoding="utf-8")
        (bin_ / name).chmod(0o755)
    env = {"PATH": f"{bin_}:/usr/bin:/bin:/opt/homebrew/bin:/usr/local/bin", "HOME": str(tmp_path),
           "FAKE_LOG": str(tmp_path / "log"), "FAKE_COMPARE": "ahead", "FAKE_GREEN": SHA, "FAKE_PARENT2": "",
           "FAKE_ODD_TREE": "none", "FAKE_RECORD": record(), "FAKE_RECORD2": "", "FAKE_HEALTH": health(),
           "GOLDBOT_RELEASE_WATCH_S": "0"}
    return tmp_path, env


def run(fakes, *args: str, **over: str) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    tmp, env = fakes
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env={**env, **over})
    log = tmp / "log.ssh"
    return r, log.read_text(encoding="utf-8").splitlines() if log.exists() else []


D2 = (f"sudo systemctl reset-failed 'goldbot-deploy-manual-*'; sudo systemd-run --wait --unit "
      f"goldbot-deploy-manual-$(date +%s) /usr/local/sbin/goldbot-deploy {SHA}")
D3 = "sudo tail -1 /var/lib/goldbot-deploy/deploys.jsonl"
D4 = 'bash -lc "goldbot run health --json"'


def test_a_ci_green_main_commit_runs_exactly_the_documented_commands_and_succeeds(fakes):
    tmp, _ = fakes
    r, ssh = run(fakes, SHA)
    assert r.returncode == 0, r.stdout + r.stderr
    prefix = f"-F {tmp}/.ssh/goldbot.conf -o BatchMode=yes goldbot-brain "
    assert ssh == [prefix + D2, prefix + D3, prefix + D3, prefix + D4]
    assert '"result": "deployed"' in r.stdout and "DEPLOYED " + SHA in r.stdout
    assert "entry_status:icm-demo" in r.stdout and "reconcile:icm-demo" in r.stdout


@pytest.mark.parametrize("bad", ["", SHA[:39], SHA.upper(), SHA + "0", "main", f"{SHA[:39]};", "$(reboot)" + SHA[:31]])
def test_anything_but_a_full_lowercase_sha_is_refused_before_any_call(fakes, bad):
    tmp, _ = fakes
    r, ssh = run(fakes, bad)
    assert r.returncode == 2 and "REFUSED" in r.stdout
    assert ssh == [] and not (tmp / "log.gh").exists()


def test_extra_arguments_are_refused(fakes):
    r, ssh = run(fakes, SHA, "--rollback")
    assert r.returncode == 2 and ssh == []


@pytest.mark.parametrize("compare", ["behind", "diverged", ""])
def test_a_commit_not_on_main_is_refused_without_touching_the_box(fakes, compare):
    r, ssh = run(fakes, SHA, FAKE_COMPARE=compare)
    assert r.returncode == 2 and ssh == []


def test_a_commit_without_green_ci_is_refused(fakes):
    r, ssh = run(fakes, SHA, FAKE_GREEN="")
    assert r.returncode == 2 and "no green" in r.stdout and ssh == []


def test_a_merge_commit_with_its_pr_heads_tree_uses_the_pr_heads_green_ci(fakes):
    r, ssh = run(fakes, SHA, FAKE_GREEN=HEAD, FAKE_PARENT2=HEAD)
    assert r.returncode == 0, r.stdout
    assert "PR head " + HEAD in r.stdout and len(ssh) == 4


def test_a_merge_commit_whose_tree_differs_from_the_green_pr_head_is_refused(fakes):
    r, ssh = run(fakes, SHA, FAKE_GREEN=HEAD, FAKE_PARENT2=HEAD, FAKE_ODD_TREE=SHA)
    assert r.returncode == 2 and "tree differs" in r.stdout and ssh == []


@pytest.mark.parametrize("rec", [record("refused"), record("rolled_back"), record("deployed", "c" * 40), "garbage", ""])
def test_a_record_that_does_not_say_deployed_this_sha_fails_with_3_and_skips_the_checks(fakes, rec):
    r, ssh = run(fakes, SHA, FAKE_RECORD=rec, FAKE_D2_RC="1")
    assert r.returncode == 3, r.stdout
    assert len(ssh) == 2 and not any("health" in line for line in ssh)


def test_a_rollback_by_the_on_box_watch_after_the_switch_fails_with_3(fakes):
    r, ssh = run(fakes, SHA, FAKE_RECORD2=record("rolled_back"))
    assert r.returncode == 3 and "rolled back" in r.stdout and len(ssh) == 3


def test_a_degraded_deploy_counts_as_deployed(fakes):
    r, _ = run(fakes, SHA, FAKE_RECORD=record("deployed_degraded"))
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize("over", [{"FAKE_HEALTH": health(entry="fail"), "FAKE_HEALTH_RC": "1"},
                                  {"FAKE_HEALTH": health(reconcile="fail"), "FAKE_HEALTH_RC": "1"},
                                  {"FAKE_HEALTH": "not json"},
                                  {"FAKE_HEALTH": json.dumps({"status": "ok", "checks": []})}])
def test_a_failing_entry_status_or_reconcile_or_an_unreadable_report_fails_with_4(fakes, over):
    r, _ = run(fakes, SHA, **over)
    assert r.returncode == 4, r.stdout


def test_an_unrelated_failing_check_is_shown_but_does_not_fail_the_deploy(fakes):
    r, _ = run(fakes, SHA, FAKE_HEALTH=health(other_fail=True), FAKE_HEALTH_RC="1")
    assert r.returncode == 0 and "tv_pull" in r.stdout


def test_remote_output_is_printed_without_terminal_escapes(fakes):
    r, _ = run(fakes, SHA, FAKE_RECORD=json.dumps({"result": "deployed", "to": SHA, "detail": "\x1b]0;pwned\x07"}))
    assert r.returncode == 0 and "\x1b" not in r.stdout and "\x07" not in r.stdout


def test_the_only_remote_commands_in_the_script_are_d2_d3_d4():
    """No rollback, stage, MT5 box or free-form remote command can be reached through the wrapper."""
    text = SCRIPT.read_text(encoding="utf-8")
    code = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert sum('"${SSH[@]}"' in line for line in code) == 4
    assert all(any(f'"${{SSH[@]}}" "{c}"' in line for c in ("$D2", "$D3", "$D4"))
               for line in code if '"${SSH[@]}"' in line)
    assert "--rollback" not in "\n".join(code) and "--stage" not in "\n".join(code) and "goldbot-mt5" not in text


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed (CI runs it in pre-commit)")
def test_the_script_is_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
