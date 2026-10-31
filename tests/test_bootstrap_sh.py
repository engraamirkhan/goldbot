"""The Linux bootstrap scripts (goldbot/ops/linux/brain_bootstrap.sh, mt5_bootstrap.sh) are run by hand as root over
SSH for the first deploy (docs/deploy/first-deploy.md). They must:

* take an optional commit id, refuse anything that is not 40 lowercase hex before doing anything (no root needed to
  see the refusal), and put root's own ops clone and the service user's tree on exactly that commit, so both VMs run
  the code the owner checked on GitHub's main, whatever merges in between;
* ignore SIGHUP and SIGPIPE before the first change (docs/AGENT_STANDARDS.md "Root-run tooling") and keep a root-only
  log, so a dropped session never leaves a half-installed box with no record;
* give the 1 GB MT5 box 4 GB of swap, and a brain on a fallback box under 4 GB the same
  (docs/deploy/free-hosting-options.md)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

LINUX = Path(__file__).resolve().parents[1] / "goldbot" / "ops" / "linux"
SCRIPTS = {"brain": LINUX / "brain_bootstrap.sh", "mt5": LINUX / "mt5_bootstrap.sh"}
BASH = shutil.which("bash") or ""
FIRST_CHANGE = re.compile(r"^\s*(?:apt-get|dpkg|fallocate|useradd|install|systemctl|git clone|mkdir)\b", re.M)


def _text(role: str) -> str:
    return SCRIPTS[role].read_text(encoding="utf-8")


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("role", sorted(SCRIPTS))
@pytest.mark.parametrize("ref", ["main", "abc123", "A" * 40, "0" * 39, "0" * 40 + ";id", "../" + "0" * 37])
def test_a_ref_that_is_not_a_full_commit_id_is_refused_before_anything(role, ref):
    out = subprocess.run([BASH, str(SCRIPTS[role]), ref], capture_output=True, text=True, timeout=20)
    assert out.returncode == 2
    assert "usage:" in out.stdout and "40-hex" in out.stdout


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_a_valid_ref_reaches_the_root_check(role):
    out = subprocess.run([BASH, str(SCRIPTS[role]), "0123456789abcdef0123456789abcdef01234567"],
                         capture_output=True, text=True, timeout=20)
    if out.returncode == 0:      # pragma: no cover - only if the suite ever runs as root, which it must not
        pytest.fail("bootstrap ran as root in a test")
    assert "usage:" not in out.stdout
    assert "run with sudo" in out.stdout or "x86_64" in out.stdout


@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_hangup_and_pipe_are_ignored_before_the_first_change(role):
    s = _text(role)
    trap = s.index("trap '' PIPE HUP")
    first = FIRST_CHANGE.search(s)
    assert first is not None and trap < first.start()
    assert 'tee -a "$LOG" --output-error=warn' in s and "LOG=/root/goldbot-bootstrap.log" in s


@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_both_clones_land_on_the_pinned_commit(role):
    s = _text(role)
    user = "goldbot" if role == "brain" else "mt5"
    assert 'fetch -q --depth 1 "$REPO" "$REF"' in s and "checkout -q --detach FETCH_HEAD" in s
    assert '[ "$(git -C "$OPS_SRC/src" rev-parse HEAD)" = "$REF" ]' in s
    assert f'[ -z "$REF" ] || sudo -u {user} -H git -C "$ROOT" checkout -q --detach "$REF"' in s


def test_the_mt5_box_gets_4_gb_of_swap():
    assert "fallocate -l 4G /swapfile" in _text("mt5")


def test_a_small_fallback_brain_gets_swap_and_the_a1_shape_none():
    s = _text("brain")
    assert "-lt 4000000 ] && ! swapon --show | grep -q /swapfile" in s and "fallocate -l 4G /swapfile" in s
    assert s.index("fallocate -l 4G") < s.index("npm ci")


def test_m1_the_brain_key_on_the_mt5_box_can_run_no_command():
    s = _text("mt5")
    # ADR 0062: the options are composed per bridge port (default 8765) and always end in command="/bin/false"
    assert 'opts="restrict,port-forwarding,permitlisten=\\"127.0.0.1:1\\""' in s and '[ $# -gt 0 ] || set -- 8765' in s
    assert 'opts="$opts,permitopen=\\"127.0.0.1:$p\\""' in s
    assert '_ "$opts,command=\\"/bin/false\\" $key" "$body"' in s
    assert "(no shell, no other ports)" not in s                    # the old comment claimed what the line lacked


@pytest.mark.parametrize("role", sorted(SCRIPTS))
def test_l2_an_existing_tree_on_another_commit_is_called_out(role):
    assert 'WARNING: $ROOT is at $head, not $REF' in _text(role)


def test_l1_the_brain_installs_python_packages_from_the_lockfile():
    s = _text("brain")
    assert "$UV sync --frozen --extra live --python 3.11" in s and "pip install" not in s


def test_switching_the_tunnel_target_restarts_a_running_tunnel():
    """`enable --now` leaves a running tunnel on the old host (first-deploy.md 6B: switching to option B)."""
    s = _text("brain")
    assert "systemctl enable goldbot-bridge-tunnel && systemctl restart goldbot-bridge-tunnel" in s
    assert "enable --now goldbot-bridge-tunnel" not in s


def test_deploys_install_from_the_lockfile_and_react_to_it():
    s = (LINUX / "goldbot-deploy.sh").read_text(encoding="utf-8")
    assert "UV=/var/lib/goldbot/.local/bin/uv" in s and "uv pip install" not in s
    assert "$UV sync -q --frozen --extra live --python 3.11" in s   # through the staged-build cgroup (build_run)
    assert "grep -qE '^(pyproject.toml|uv.lock)$'" in s


# --- the MT5 box as fixed by hand on 2026-10-16 (docs/incidents/2026-10-16-mt5-debugger-found-under-wine.md) ---

UNIT = LINUX / "systemd" / "goldbot-bridge@.service"
DEPLOY = LINUX / "goldbot-deploy.sh"
WINPY_RE = re.compile(r"^WINPY=(\S+)$", re.M)


def _winpy() -> str:
    m = WINPY_RE.search(_text("mt5"))
    assert m, "mt5_bootstrap.sh must define WINPY"
    return m.group(1)


def _code(text: str) -> list[str]:
    return [x for x in text.splitlines() if not x.lstrip().startswith("#")]


def test_wine_is_devel_not_stable_and_not_pinned_by_an_agent():
    """WineHQ bug 58043: wine-stable 11.0 makes MT5 report a debugger. Pinning is the owner's call (ADR 0019)."""
    s = _text("mt5")
    code = "\n".join(_code(s))
    assert "apt-get install -y --install-recommends winehq-devel" in code
    assert "winehq-stable" not in code
    assert "apt-mark hold" not in code and "winehq-devel=" not in code and "/etc/apt/preferences" not in code
    assert "58043" in s and "OWNER DECISION" in s and "0019-wine-version-for-the-mt5-box.md" in s
    assert (LINUX.parents[2] / "docs" / "decisions" / "0019-wine-version-for-the-mt5-box.md").is_file()


def test_wineboot_runs_without_the_mono_dialog():
    assert "WINEDLLOVERRIDES=mscoree= wineboot -i" in _text("mt5")


def test_the_python_installer_is_not_named_python_exe():
    """Windows looks in the current directory first: an installer saved as python.exe answers `wine python`."""
    s = _text("mt5")
    assert "curl -fsSLo python-installer.exe" in s and "wine python-installer.exe /quiet" in s
    assert "-fsSLo python.exe" not in s and "wine python.exe" not in s


def test_mt5setup_exit_code_is_ignored_but_the_terminal_must_exist():
    s = _text("mt5")
    setup = s.index("wine mt5setup.exe /auto /path:'C:\\\\MT5\\\\ICMarkets' || true")
    check = s.index("test -f ~/.wine/drive_c/MT5/ICMarkets/terminal64.exe || {")
    assert setup < check < s.index("-m pip install")
    assert "exit 1; }" in s[check:check + 200]


def test_winpy_is_absolute_and_matches_pyver():
    s = _text("mt5")
    pyver = re.search(r"^PYVER=(\d+)\.(\d+)\.\d+$", s, re.M)
    assert pyver, "PYVER must be X.Y.Z"
    assert _winpy() == ("/home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/"
                        f"Python{pyver.group(1)}{pyver.group(2)}/python.exe")
    assert '[[ "$WINPY" == */Python"$(echo "$PYVER" | cut -d. -f1,2 | tr -d .)"/python.exe ]]' in s


@pytest.mark.skipif(not BASH, reason="needs bash")
def test_the_runtime_winpy_check_fires_on_a_mismatch(tmp_path):
    """The bootstrap's own guard (before the root check) refuses a WINPY that a PYVER bump left behind."""
    broken = tmp_path / "mt5_bootstrap.sh"
    broken.write_text(_text("mt5").replace("PYVER=3.11.9", "PYVER=3.12.7", 1), encoding="utf-8")
    out = subprocess.run([BASH, str(broken)], capture_output=True, text=True, timeout=20)
    assert out.returncode == 1 and "WINPY does not match PYVER" in out.stdout


def test_every_windows_python_call_uses_the_absolute_path():
    """No bare `wine python` in code: the bridge unit and goldbot-mt5 run with cwd /opt/goldbot (mt5-writable), where
    a planted python.exe would run first. The unit, the wrapper and goldbot-deploy carry the same path as WINPY."""
    winpy = _winpy()
    texts = {"bootstrap": _text("mt5"), "unit": UNIT.read_text(encoding="utf-8"),
             "deploy": DEPLOY.read_text(encoding="utf-8")}
    for name, text in texts.items():
        bare = [x for x in _code(text) if re.search(r"\bwine\s+['\"]?python(\s|['\"]|$)", x)]
        assert bare == [], f"{name}: {bare}"
    assert f"ExecStart=/usr/bin/wine {winpy} -m goldbot.ops.run bridge %i" in texts["unit"]
    wrapper = texts["bootstrap"].split("cat > /usr/local/bin/goldbot-mt5 <<'WRAP'", 1)[1].split("\nWRAP\n", 1)[0]
    assert f'wine {winpy} -m "goldbot.ops.$mod"' in wrapper
    assert f"WINPY={winpy}\n" in texts["deploy"]
    assert 'wine "$WINPY" -m pip install' in texts["deploy"] and 'wine $WINPY"' in texts["deploy"]
    assert "wine '$WINPY' -m pip install" in texts["bootstrap"] and "wine '$WINPY' -c" in texts["bootstrap"]


def test_the_installer_is_the_generic_metaquotes_one_and_the_login_flow_is_named():
    """ADR 0020: the generic mt5setup ships no IC Markets servers, so File > Login to ICMarketsSC-Demo never connects
    (incident 2026-10-16-mt5-login-generic-installer). The installer stays MetaQuotes' own host; the bootstrap and the
    RUNBOOK name the flow that adds the servers (File > Open an Account > Raw Trading Ltd)."""
    s = _text("mt5")
    code = "\n".join(_code(s))
    urls = re.findall(r"https://\S+mt5setup\.exe|https://\S+setup\.exe", code)
    assert urls == ["https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe"], urls
    assert "0020-mt5-installer-generic-plus-company-search.md" in s
    assert (LINUX.parents[2] / "docs" / "decisions" / "0020-mt5-installer-generic-plus-company-search.md").is_file()
    tail = s.rsplit("cat > /usr/local/bin/goldbot-mt5 <<'WRAP'", 1)[1]
    assert "File > Open an Account > ICMarkets > Raw Trading Ltd" in tail
    runbook = (LINUX.parents[2] / "docs" / "RUNBOOK.md").read_text(encoding="utf-8")
    section = runbook.split("### 0.3 The MT5 box", 1)[1].split("### 0.4", 1)[0].replace("**", "")
    section = " ".join(section.split())
    for step in ("File -> Open an Account", "Raw Trading Ltd", "Connect with an existing trade account",
                 "Config/servers.dat", "-10005"):
        assert step in section, step
