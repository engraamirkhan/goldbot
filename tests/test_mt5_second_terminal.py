"""ADR 0062 item 4: a second MT5 terminal and bridge on one box (vantage-247 beside vantage-demo on the Vantage box).

Design rule: "the shared unit templates stay byte for byte; a second instance is a set of per-instance systemd drop-ins
(its install dir, its one-terminal guard, its bridge wait, its watchdog port), and its sibling's guards are narrowed to
the sibling's own install dir, so neither terminal's guard ever refuses the other's restart". Every pattern stays a
literal (no shell variable), and no guard's own text matches its pattern (incident 2026-10-19)."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LINUX = REPO / "goldbot" / "ops" / "linux"
INSTANCE = LINUX / "mt5-instance.sh"
BOOT = LINUX / "mt5_bootstrap.sh"
EXPERTS = LINUX / "mt5" / "mt5-experts-enable.sh"
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def _instance(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {"PATH": "/usr/bin:/bin", "GOLDBOT_TEST": "1", "GOLDBOT_DROPIN_ROOT": str(tmp_path)}
    assert BASH is not None
    return subprocess.run([BASH, str(INSTANCE), *args], capture_output=True, text=True, env=env, timeout=20)


def _drop(tmp_path: Path, unit: str) -> str:
    return (tmp_path / f"{unit}.d" / "goldbot-instance.conf").read_text(encoding="utf-8")


def test_the_second_instance_gets_its_own_dir_guard_bridge_wait_and_watchdog_port(tmp_path):
    r = _instance(tmp_path, "vantage-247", "Vantage247", "8766", "--beside", "vantage-demo", "ICMarkets")
    assert r.returncode == 0, r.stdout + r.stderr
    term = _drop(tmp_path, "goldbot-mt5-terminal@vantage-247.service")
    assert "ExecStart=\nExecStart=/usr/bin/wine /home/mt5/.wine/drive_c/MT5/Vantage247/terminal64.exe /profile:goldbot" \
        in term
    assert "ExecStartPre=\n" in term and "-/usr/local/libexec/goldbot/mt5-experts-enable.sh Vantage247" in term
    assert 'pgrep -u mt5 -f "MT5.Vantage247.[t]erminal64[.]exe"' in term
    bridge = _drop(tmp_path, "goldbot-bridge@vantage-247.service")
    assert 'pgrep -u mt5 -f "MT5.Vantage247.[t]erminal64[.]exe"' in bridge and "ExecStartPre=\n" in bridge
    dog = _drop(tmp_path, "goldbot-bridge-watchdog@vantage-247.service")
    assert "ExecStart=\nExecStart=/usr/local/sbin/goldbot-bridge-watchdog vantage-247 8766" in dog
    # the sibling keeps its dir, its port and its unit; only its guards are narrowed to its own install
    sib = _drop(tmp_path, "goldbot-mt5-terminal@vantage-demo.service")
    assert 'pgrep -u mt5 -f "MT5.ICMarkets.[t]erminal64[.]exe"' in sib
    assert "/home/mt5/.wine/drive_c/MT5/ICMarkets/terminal64.exe /profile:goldbot" in sib
    assert 'MT5.ICMarkets.[t]erminal64[.]exe' in _drop(tmp_path, "goldbot-bridge@vantage-demo.service")
    assert not (tmp_path / "goldbot-bridge-watchdog@vantage-demo.service.d").exists()     # 8765 as before


def test_no_drop_in_uses_a_variable_and_no_guard_matches_its_own_text(tmp_path):
    assert _instance(tmp_path, "vantage-247", "Vantage247", "8766", "--beside", "vantage-demo",
                     "ICMarkets").returncode == 0
    for f in tmp_path.rglob("goldbot-instance.conf"):
        text = f.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("ExecStartPre=") and "pgrep" in line:
                assert "$" not in line
                pat = re.search(r'pgrep[^"]*"([^"]+)"', line)
                assert pat and not re.search(pat.group(1), line), line
                assert re.search(pat.group(1), r"C:\MT5" + "\\" + pat.group(1).split(".")[1] + r"\terminal64.exe")


@pytest.mark.parametrize("args", [
    ["vantage-247", "Vantage247", "8765", "--beside", "vantage-demo", "ICMarkets"],    # the sibling's port
    ["vantage-247", "ICMarkets", "8766", "--beside", "vantage-demo", "ICMarkets"],     # the sibling's dir
    ["Vantage", "Vantage247", "8766", "--beside", "vantage-demo", "ICMarkets"],
    ["vantage-247", "Van tage", "8766", "--beside", "vantage-demo", "ICMarkets"],
    ["vantage-247", "Vantage247", "80", "--beside", "vantage-demo", "ICMarkets"],
    ["vantage-247", "Vantage247", "8766"],                                            # the sibling is required
    ["vantage-247", "Va.*", "8766", "--beside", "vantage-demo", "ICMarkets"],          # never a regex
])
def test_bad_instance_arguments_write_nothing(tmp_path, args):
    r = _instance(tmp_path, *args)
    assert r.returncode == 2 and not list(tmp_path.rglob("*.conf"))


def test_the_shared_templates_are_unchanged_for_every_other_box():
    term = (LINUX / "systemd" / "goldbot-mt5-terminal@.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/bin/wine /home/mt5/.wine/drive_c/MT5/ICMarkets/terminal64.exe /profile:goldbot" in term
    assert "ExecStartPre=-/usr/local/libexec/goldbot/mt5-experts-enable.sh\n" in term
    dog = (LINUX / "systemd" / "goldbot-bridge-watchdog@.service").read_text(encoding="utf-8")
    assert "ExecStart=/usr/local/sbin/goldbot-bridge-watchdog %i 8765\n" in dog


def test_the_experts_script_scopes_its_dir_and_running_check_to_a_named_install(tmp_path):
    d = tmp_path / ".wine" / "drive_c" / "MT5" / "Vantage247" / "Config"
    d.mkdir(parents=True)
    ini = d / "common.ini"
    ini.write_bytes(b"\xff\xfe" + "[Common]\r\nLogin=1\r\n".encode("utf-16-le"))
    fake = tmp_path / "bin"
    fake.mkdir()
    # a terminal of ANOTHER install runs: pgrep matches only when asked for any terminal
    (fake / "pgrep").write_text('#!/bin/sh\ncase "$*" in *Vantage247*) exit 1 ;; *) echo 4242; exit 0 ;; esac\n')
    (fake / "pgrep").chmod(0o755)
    env = {**os.environ, "HOME": str(tmp_path), "PATH": f"{fake}:/usr/bin:/bin"}
    env.pop("GOLDBOT_TEST", None)
    r = subprocess.run([str(BASH), str(EXPERTS), "Vantage247"], capture_output=True, text=True, env=env, timeout=30)
    if r.returncode != 0 and "iconv" in r.stderr:
        pytest.skip("needs iconv")
    assert r.returncode == 0, r.stderr
    assert "Enabled=1" in ini.read_bytes().decode("utf-16")
    bad = subprocess.run([str(BASH), str(EXPERTS), "../x"], capture_output=True, text=True, env=env, timeout=30)
    assert bad.returncode == 2


def test_the_tunnel_key_may_forward_to_every_bridge_port_of_its_box_on_one_line():
    s = BOOT.read_text(encoding="utf-8")
    m = re.search(r"cat > /usr/local/bin/goldbot-mt5-authorize <<'WRAP'\n(.*?)\nWRAP\n", s, re.S)
    assert m and "permitopen" in m.group(1) and "grep -vF" in m.group(1)


def test_the_bootstrap_takes_an_instance_beside_an_account_and_never_reinstalls_wine_for_it():
    s = BOOT.read_text(encoding="utf-8")
    assert "--instance" in s and "--beside" in s and "--port" in s
    inst = s[s.index("# ADR 0062: a second terminal"):s.index("# ADR 0062: end of the second terminal")]
    assert 'bash "$OPS/mt5-second-terminal.sh"' in inst and "exit 0" in inst
    assert s.index("# ADR 0062: end of the second terminal") < s.index("swapon") < s.index("apt-get update")
    second = (LINUX / "mt5-second-terminal.sh").read_text(encoding="utf-8")
    assert "apt-get" not in second and "wineboot" not in second and "python-installer" not in second
    assert "mt5setup.exe /auto" in second and "systemctl restart" not in second and "systemctl start" not in second
    # the drop-ins (which validate every argument) are written before anything is installed
    assert second.index("mt5-instance.sh") < second.index("install -m 755") < second.index("mt5setup.exe")


def test_the_installers_stop_of_its_own_terminal_never_matches_the_shell_running_it():
    """SRE review of 43ad25ee (HIGH): `pkill -f 'MT5.<dir>.[t]erminal64[.]exe'` ran inside a `bash -lc "..."` whose argv
    held the expanded path, so it killed the installing shell itself. The install dir now travels as "$1"."""
    second = (LINUX / "mt5-second-terminal.sh").read_text(encoding="utf-8")
    m = re.search(r"sudo -u mt5 -H bash -c '(.*?)' _ \"\$dir\"", second, re.S)
    assert m, "the mt5 step passes the install dir as an argument"
    script = m.group(1)
    assert '$1' in script and '$dir' not in script
    pk = re.search(r'pkill -u mt5 -f "MT5\.\$1\.\[t\]erminal64\[\.\]exe"', script)
    assert pk
    pattern = "MT5.Vantage247.[t]erminal64[.]exe"                    # what pkill runs with, $1 = Vantage247
    argv = " ".join(["sudo", "-u", "mt5", "-H", "bash", "-c", script, "_", "Vantage247"])
    assert not re.search(pattern, argv) and not re.search(pattern, " ".join(["bash", "-c", script, "_", "Vantage247"]))
    assert re.search(pattern, r"C:\MT5\Vantage247\terminal64.exe /profile:goldbot")


def test_the_instance_path_installs_the_authorize_wrapper_that_takes_the_new_port():
    """SRE review of 43ad25ee (HIGH): --instance exited before the wrapper was written, so the box kept the old
    8765-only, appending one and the 8766 forward was refused."""
    s = BOOT.read_text(encoding="utf-8")
    inst = s[s.index("# ADR 0062: a second terminal"):s.index("# ADR 0062: end of the second terminal")]
    assert "write_authorize" in inst
    assert s.index("write_authorize() {") < s.index("# ADR 0062: a second terminal")
    assert s.count("cat > /usr/local/bin/goldbot-mt5-authorize <<'WRAP'") == 1


@pytest.mark.parametrize("args", [
    ["--instance", "Vantage247", "--port", "8766"],                                 # no sibling
    ["--instance", "ICMarkets", "--port", "8766", "--beside", "vantage-demo"],      # the sibling's dir
    ["--instance", "Va/x", "--port", "8766", "--beside", "vantage-demo"],
    ["--account", "vantage-demo", "--instance", "V247", "--port", "8766", "--beside", "vantage-demo"],
])
def test_bad_instance_arguments_stop_the_bootstrap_before_anything(args):
    assert BASH is not None
    out = subprocess.run([BASH, str(BOOT), "--account", "vantage-247", *args], capture_output=True, text=True,
                         timeout=20, env={"PATH": "/usr/bin:/bin"})
    assert out.returncode == 2 and "usage:" in out.stdout


def test_the_second_terminal_always_takes_the_siblings_servers_dat():
    """2026-10-30: a generic servers.dat from the installer's first start gave login -10005 IPC timeouts. The sibling's
    broker list is copied over it, unconditionally."""
    second = (LINUX / "mt5-second-terminal.sh").read_text(encoding="utf-8")
    assert 'cp -f "$m/ICMarkets/Config/servers.dat" "$m/$1/Config/servers.dat"' in second
    assert '[ ! -f "$m/$1/Config/servers.dat" ]' not in second
