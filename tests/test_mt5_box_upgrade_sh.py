"""mt5-box-upgrade (goldbot/ops/linux/mt5-box-upgrade.sh): the attended MT5 box upgrade, with fake systemctl, sudo,
install, pgrep and a local git repository standing in for GitHub. It refuses when the market may be open, a flat book or a
stopped engine is not attested, or an engine unit is active; backs up Config/ once; installs the four units and two
watchdog scripts from a clone at exactly the sha; leaves the watchdog timer off until post-reboot passes; sets the
rollback floor only after the cold restart passes; each phase can be run again; and prints no secrets.

The script is sourced (its `main` runs only when executed), as in tests/test_goldbot_deploy_sh.py."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LINUX = REPO / "goldbot" / "ops" / "linux"
SCRIPT = LINUX / "mt5-box-upgrade.sh"
UNITS = [
    "goldbot-mt5-terminal@.service", "goldbot-bridge@.service",
    "goldbot-bridge-watchdog@.service", "goldbot-bridge-watchdog@.timer",
]
SCRIPTS = {"goldbot-bridge-watchdog.sh": "goldbot-bridge-watchdog", "goldbot-bridge-probe.sh": "goldbot-bridge-probe"}
# run as mt5 by the terminal unit before each start (AutoTrading on; incident 2026-10-20), root-owned in LIBEXEC
LIBEXEC_SCRIPTS = ("mt5/mt5-experts-enable.sh", "mt5/mt5-ini.sh")
# needrestart never restarts goldbot units (incident 2026-10-22), installed in NEEDRESTART_DIR
NEEDRESTART_CONF = "needrestart/goldbot.conf"
SECRET = "SECRETLOGIN-12345"
OLD = "a" * 40
FLAGS = ("--book-flat", "--engine-stopped")

pytestmark = pytest.mark.skipif(shutil.which("git") is None or shutil.which("bash") is None,
                                reason="needs git and bash")

HARNESS = r"""
set -euo pipefail
source "$SCRIPT"
REPO=$T/remote; STATE_DIR=$T/state; ROLLBACK_FLOOR=$T/etc/rollback-floor; ROLE_FILE=$T/etc/role
UNIT_DIR=$T/units; SBIN=$T/sbin; LIBEXEC=$T/libexec; NEEDRESTART_DIR=$T/needrestart; DEPLOY=$T/deploy; CONFIG_DIR=$T/mt5/Config; BACKUP_DIR=$T/mt5/config-backup
mkdir -p "$UNIT_DIR" "$T/en" "$T/act"
now_dowhm() { echo "${NOW:-60900}"; }                     # Saturday 09:00 UTC
boot_id() { cat "$T/bootid"; }
is_root() { [ -z "${NOT_ROOT:-}" ]; }
mktemp() { mkdir -p "$T/ops"; echo "$T/ops"; }
df() { printf 'Filesystem 1M-blocks Used Available Capacity Mounted\n/dev/x 9999 1 %s 1%% /\n' "${FREE_MB:-5000}"; }
sleep() { :; }
mt5_head() { cat "$T/mt5head"; }
sudo() {
  while [ $# -gt 0 ]; do case "$1" in -u) shift 2 ;; -H) shift ;; *) break ;; esac; done
  if [ "${1-}" = sh ]; then
    [ "${BACKUP_RC:-0}" = 0 ] || return 1
    mkdir -p "$T/mt5/config-backup" && cp -a "$CONFIG_DIR" "$T/mt5/config-backup/"; return 0
  fi
  "$@"
}
install() {
  local a=() d=0
  while [ $# -gt 0 ]; do
    case "$1" in -m|-o|-g) shift 2 ;; -d) d=1; shift ;; *) a+=("$1"); shift ;; esac
  done
  if [ "$d" = 1 ]; then mkdir -p "${a[@]}"; else cp -f "${a[0]}" "${a[1]}"; fi
}
pkill() { echo "pkill $*" >> "$T/calls.log"; }
pgrep() { echo "${TERMINALS:-1}"; }
chmod() { :; }
unit() { local n=$1; [[ $n == *.* ]] || n=$n.service; echo "$n"; }
systemctl() {
  echo "$*" >> "$T/calls.log"
  case "$1" in
    list-units) [ -z "${ENGINE_ACTIVE:-}" ] || echo "goldbot-engine@icm-demo.service loaded active running x" ;;
    is-active) [ -e "$T/act/$(unit "${@: -1}")" ] || return 3 ;;
    is-enabled) [ -e "$T/en/$(unit "${@: -1}")" ] || return 1 ;;
    enable) shift; [ "${1-}" != "--now" ] || shift; touch "$T/en/$(unit "$1")" ;;
    disable) rm -f "$T/en/$(unit "${@: -1}")" ;;
    show) echo "${NRESTARTS:-0}" ;;
  esac
  return 0
}
touch "$T/en/goldbot-bridge@icm-demo.service"
main "$@"
"""


def _remote(tmp: Path) -> str:
    r = tmp / "remote"
    ops = r / "goldbot" / "ops" / "linux"
    (ops / "systemd").mkdir(parents=True)
    for u in UNITS:
        shutil.copy(LINUX / "systemd" / u, ops / "systemd" / u)
    for s in SCRIPTS:
        shutil.copy(LINUX / s, ops / s)
    (ops / "mt5").mkdir()
    for s in LIBEXEC_SCRIPTS:
        shutil.copy(LINUX / s, ops / s)
    (ops / "needrestart").mkdir()
    shutil.copy(LINUX / NEEDRESTART_CONF, ops / NEEDRESTART_CONF)
    for c in (["init", "-q"], ["add", "-A"],
              ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "x"]):
        subprocess.run(["git", "-C", str(r), *c], check=True, capture_output=True)
    return subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture()
def box(tmp_path: Path):
    sha = _remote(tmp_path)
    (tmp_path / "mt5" / "Config").mkdir(parents=True)
    (tmp_path / "mt5" / "Config" / "accounts.ini").write_text(SECRET)
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc" / "role").write_text("mt5\n")
    (tmp_path / "deploy").write_text('#!/bin/sh\necho "deployed $*" >> "$T/calls.log"\nexit ${DEPLOY_RC:-0}\n')
    (tmp_path / "deploy").chmod(0o755)
    (tmp_path / "bootid").write_text("boot-1\n")
    (tmp_path / "mt5head").write_text(OLD + "\n")
    (tmp_path / "sbin").mkdir()
    probe = tmp_path / "sbin" / "goldbot-bridge-probe"
    probe.write_text('#!/bin/sh\necho "${PROBE:-ok}"\n')
    probe.chmod(0o755)
    return tmp_path, sha


def run(box, *args, **env):
    tmp, _ = box
    e = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "SCRIPT": str(SCRIPT), "T": str(tmp), **env}
    p = subprocess.run(["bash", "-c", HARNESS, "h", *args], capture_output=True, text=True, env=e)
    return p.returncode, p.stdout + p.stderr


def calls(tmp: Path) -> str:
    f = tmp / "calls.log"
    return f.read_text() if f.exists() else ""


def test_shellcheck_clean():
    sc = shutil.which("shellcheck")
    if sc is None:
        pytest.skip("shellcheck not installed")
    assert subprocess.run([sc, str(SCRIPT)], capture_output=True).returncode == 0


def test_bad_arguments_refused(box):
    _, sha = box
    assert run(box, "nonsense", sha)[0] == 2
    assert run(box, "precheck", "abc")[0] == 2
    assert run(box, "precheck", sha, "--force")[0] == 2


@pytest.mark.parametrize("now", ["11000", "11200", "50100", "52229", "72100", "72359", "10900"])
def test_market_open_refuses(box, now):
    tmp, sha = box
    rc, out = run(box, "precheck", sha, *FLAGS, NOW=now)
    assert rc == 1 and "FAIL   market closed" in out
    assert not (tmp / "mt5" / "config-backup").exists()


@pytest.mark.parametrize("now", ["60000", "62359", "52230", "70000", "72059"])
def test_market_closed_passes(box, now):
    assert run(box, "precheck", box[1], *FLAGS, NOW=now)[0] == 0


def test_attestations_and_active_engine_refuse(box):
    tmp, sha = box
    rc, out = run(box, "precheck", sha, "--engine-stopped")
    assert rc == 1 and "FAIL   book flat" in out
    rc, out = run(box, "precheck", sha, "--book-flat")
    assert rc == 1 and "FAIL   engine stopped" in out
    rc, out = run(box, "precheck", sha, *FLAGS, ENGINE_ACTIVE="1")
    assert rc == 1 and "FAIL   no engine unit active" in out
    assert not (tmp / "mt5" / "config-backup").exists()


def test_not_root_and_wrong_role_and_low_disk_refuse(box):
    tmp, sha = box
    assert "FAIL   running as root" in run(box, "precheck", sha, *FLAGS, NOT_ROOT="1")[1]
    assert "FAIL   free disk" in run(box, "precheck", sha, *FLAGS, FREE_MB="10")[1]
    (tmp / "etc" / "role").write_text("brain\n")
    assert "FAIL   goldbot-deploy installed" in run(box, "precheck", sha, *FLAGS)[1]


def test_precheck_backs_up_once_and_prints_no_secret(box):
    tmp, sha = box
    rc, out = run(box, "precheck", sha, *FLAGS)
    assert rc == 0 and "ALL PASS" in out and "made" in out
    assert (tmp / "mt5" / "config-backup" / "Config" / "accounts.ini").read_text() == SECRET
    (tmp / "mt5" / "Config" / "accounts.ini").write_text("changed")
    rc, out2 = run(box, "precheck", sha, *FLAGS)
    assert rc == 0 and "exists, kept" in out2
    assert (tmp / "mt5" / "config-backup" / "Config" / "accounts.ini").read_text() == SECRET
    assert SECRET not in out + out2


def test_install_order_and_results(box):
    tmp, sha = box
    rc, out = run(box, "install", sha, *FLAGS)
    assert rc == 0, out
    c = calls(tmp)
    assert f"deployed {sha}" in c
    assert (c.index("deployed") < c.index("daemon-reload") < c.index("pkill")
            < c.index("enable goldbot-mt5-terminal@icm-demo"))
    assert "stop goldbot-mt5-terminal-check.service" in c
    assert "pkill -u mt5 -f terminal64[.]exe" in c
    for u in UNITS:
        assert (tmp / "units" / u).read_text() == (LINUX / "systemd" / u).read_text()
    for src, dst in SCRIPTS.items():
        assert (tmp / "sbin" / dst).read_text() == (LINUX / src).read_text()
    for src in LIBEXEC_SCRIPTS:
        assert (tmp / "libexec" / Path(src).name).read_text() == (LINUX / src).read_text()
    assert (tmp / "needrestart" / "goldbot.conf").read_text() == (LINUX / NEEDRESTART_CONF).read_text()
    assert "enable --now goldbot-bridge-watchdog" not in c          # the timer stays off until post-reboot
    assert (tmp / "state" / "previous-sha").read_text().strip() == OLD
    assert "sudo reboot" in out and SECRET not in out


def test_install_twice_keeps_previous_sha(box):
    tmp, sha = box
    assert run(box, "install", sha, *FLAGS)[0] == 0
    (tmp / "mt5head").write_text(sha + "\n")
    assert run(box, "install", sha, *FLAGS)[0] == 0
    assert (tmp / "state" / "previous-sha").read_text().strip() == OLD


def test_install_refusal_changes_nothing(box):
    tmp, sha = box
    assert run(box, "install", sha, "--book-flat")[0] == 1
    assert "deployed" not in calls(tmp)
    assert not (tmp / "units" / "goldbot-bridge@.service").exists()


def test_deploy_failure_stops_before_units(box):
    tmp, sha = box
    rc, out = run(box, "install", sha, *FLAGS, DEPLOY_RC="1")
    assert rc == 1 and "FAIL   goldbot-deploy" in out
    assert not (tmp / "units" / "goldbot-bridge@.service").exists()


def test_install_refuses_a_sha_the_remote_lacks(box):
    tmp, _ = box
    rc, out = run(box, "install", "b" * 40, *FLAGS)
    assert rc == 1 and "FAIL   root clone at sha" in out
    assert not (tmp / "units" / "goldbot-bridge@.service").exists()


def _installed(box):
    tmp, sha = box
    assert run(box, "install", sha, *FLAGS)[0] == 0
    probe = tmp / "sbin" / "goldbot-bridge-probe"          # install put the real probe there; the fake answers instead
    probe.write_text('#!/bin/sh\necho "${PROBE:-ok}"\n')
    for u in ("goldbot-xvfb", "goldbot-mt5-terminal@icm-demo", "goldbot-bridge@icm-demo"):
        (tmp / "act" / f"{u}.service").touch()
    (tmp / "mt5head").write_text(sha + "\n")
    (tmp / "bootid").write_text("boot-2\n")


def test_post_reboot_needs_install_and_a_reboot(box):
    tmp, sha = box
    rc, out = run(box, "post-reboot", sha)
    assert rc == 1 and "FAIL   install phase recorded" in out
    assert not (tmp / "etc" / "rollback-floor").exists()
    assert run(box, "install", sha, *FLAGS)[0] == 0
    rc, out = run(box, "post-reboot", sha)
    assert rc == 1 and "FAIL   machine rebooted" in out
    assert not (tmp / "etc" / "rollback-floor").exists()


def test_post_reboot_pass_enables_timer_and_sets_floor(box):
    tmp, sha = box
    _installed(box)
    rc, out = run(box, "post-reboot", sha)
    assert rc == 0, out
    assert (tmp / "etc" / "rollback-floor").read_text().strip() == sha
    assert "enable --now goldbot-bridge-watchdog@icm-demo.timer" in calls(tmp)
    rc, out = run(box, "post-reboot", sha)                                   # idempotent
    assert rc == 0 and "already set" in out


@pytest.mark.parametrize("env,row", [
    ({"TERMINALS": "2"}, "exactly one terminal64.exe"),
    ({"TERMINALS": "0"}, "exactly one terminal64.exe"),
    ({"PROBE": "not_logged_in"}, "bridge /health"),
])
def test_post_reboot_failure_changes_nothing(box, env, row):
    tmp, sha = box
    _installed(box)
    rc, out = run(box, "post-reboot", sha, **env)
    assert rc == 1 and f"FAIL   {row}" in out
    assert not (tmp / "etc" / "rollback-floor").exists()
    assert "enable --now goldbot-bridge-watchdog" not in calls(tmp)


def test_post_reboot_inactive_unit_fails(box):
    tmp, sha = box
    _installed(box)
    (tmp / "act" / "goldbot-bridge@icm-demo.service").unlink()
    rc, out = run(box, "post-reboot", sha)
    assert rc == 1 and "FAIL   active: goldbot-bridge@icm-demo" in out
    assert not (tmp / "etc" / "rollback-floor").exists()


def test_conflicting_floor_leaves_watchdog_off(box):
    tmp, sha = box
    _installed(box)
    (tmp / "etc" / "rollback-floor").write_text("c" * 40 + "\n")
    run(box, "post-reboot", sha)
    assert "enable --now goldbot-bridge-watchdog" not in calls(tmp)


def test_successive_upgrades_renew_previous_sha(box):
    tmp, sha_b = box
    assert run(box, "install", sha_b, *FLAGS)[0] == 0           # A (OLD) -> B
    assert (tmp / "state" / "previous-sha").read_text().strip() == OLD
    (tmp / "mt5head").write_text(sha_b + "\n")
    remote = str(tmp / "remote")
    (tmp / "remote" / "x").write_text("2")
    for c in (["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "y"]):
        subprocess.run(["git", "-C", remote, *c], check=True, capture_output=True)
    sha_c = subprocess.run(["git", "-C", remote, "rev-parse", "HEAD"], check=True, capture_output=True,
                           text=True).stdout.strip()
    assert run(box, "install", sha_c, *FLAGS)[0] == 0           # B -> C
    assert (tmp / "state" / "previous-sha").read_text().strip() == sha_b


def test_existing_different_floor_is_not_overwritten(box):
    tmp, sha = box
    _installed(box)
    (tmp / "etc" / "rollback-floor").write_text("c" * 40 + "\n")
    rc, out = run(box, "post-reboot", sha)
    assert rc == 1 and "FAIL   rollback floor" in out
    assert (tmp / "etc" / "rollback-floor").read_text().strip() == "c" * 40


# --- one box per account (docs/ops/icm-probe-account-plan.md Option B, step B3) ---

def test_bad_account_refused(box):
    _, sha = box
    for bad in ("ICM", "icm demo", "../x", "-x", "a;id", ""):
        rc, out = run(box, "precheck", sha, *FLAGS, "--account", bad)
        assert rc == 2 and "account id" in out, bad
    assert run(box, "precheck", sha, *FLAGS, "--account")[0] == 2


def test_default_account_is_icm_demo_and_env_is_honoured(box):
    tmp, sha = box
    assert run(box, "install", sha, *FLAGS)[0] == 0
    assert "enable goldbot-mt5-terminal@icm-demo.service" in calls(tmp)
    (tmp / "calls.log").unlink()
    (tmp / "en").mkdir(exist_ok=True)
    (tmp / "en" / "goldbot-bridge@icm-probe.service").touch()
    rc, out = run(box, "install", sha, *FLAGS, ACCOUNT="icm-probe")
    assert rc == 0, out
    assert "enable goldbot-mt5-terminal@icm-probe.service" in calls(tmp)


def test_probe_box_upgrade_checks_the_probe_units(box):
    tmp, sha = box
    (tmp / "en").mkdir(exist_ok=True)
    (tmp / "en" / "goldbot-bridge@icm-probe.service").touch()
    rc, out = run(box, "install", sha, *FLAGS, "--account", "icm-probe")
    assert rc == 0, out
    c = calls(tmp)
    assert "enable goldbot-mt5-terminal@icm-probe.service" in c and "icm-demo" not in c
    assert "disable --now goldbot-bridge-watchdog@icm-probe.timer" in c
    probe = tmp / "sbin" / "goldbot-bridge-probe"
    probe.write_text('#!/bin/sh\necho "${PROBE:-ok}"\n')
    for u in ("goldbot-xvfb", "goldbot-mt5-terminal@icm-probe"):
        (tmp / "act" / f"{u}.service").touch()
    (tmp / "mt5head").write_text(sha + "\n")
    (tmp / "bootid").write_text("boot-2\n")
    rc, out = run(box, "post-reboot", sha, "--account", "icm-probe")
    assert rc == 1 and "FAIL   active: goldbot-bridge@icm-probe" in out
    (tmp / "act" / "goldbot-bridge@icm-probe.service").touch()
    rc, out = run(box, "post-reboot", sha, "--account", "icm-probe")
    assert rc == 0, out
    assert "enable --now goldbot-bridge-watchdog@icm-probe.timer" in calls(tmp)


def test_probe_box_without_its_bridge_unit_enabled_fails(box):
    tmp, sha = box
    rc, out = run(box, "install", sha, *FLAGS, "--account", "icm-probe")
    assert rc == 1 and "FAIL   bridge unit enabled" in out and "icm-probe" in out


def test_the_terminal_units_guard_never_matches_its_own_command_line():
    """Live 2026-10-19 (icm-probe box): the guard's own error text contained "terminal64.exe", so `pgrep -f` matched the
    guarding shell itself and the unit always refused to start. No ExecStartPre may contain what its pattern matches."""
    import re
    from pathlib import Path
    unit = (Path(__file__).resolve().parents[1] / "goldbot/ops/linux/systemd/goldbot-mt5-terminal@.service").read_text()
    for line in (ln for ln in unit.splitlines() if ln.startswith("ExecStartPre=") and "pgrep" in ln):
        m = re.search(r'pgrep[^"]*"([^"]+)"', line)
        assert m is not None, line
        assert not re.search(m.group(1), line), line


def test_install_refuses_a_sha_without_the_autotrading_script_and_installs_no_unit(box):
    """The terminal unit's ExecStartPre names /usr/local/libexec/goldbot/mt5-experts-enable.sh: a unit is never put in
    place without the script it runs."""
    tmp, _ = box
    r = tmp / "remote"
    subprocess.run(["git", "-C", str(r), "rm", "-q", "goldbot/ops/linux/mt5/mt5-experts-enable.sh"], check=True)
    subprocess.run(["git", "-C", str(r), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "y"], check=True)
    sha = subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"], check=True, capture_output=True,
                         text=True).stdout.strip()
    rc, out = run(box, "install", sha, *FLAGS)
    assert rc == 1 and "FAIL   watchdog scripts" in out
    assert not (tmp / "units" / "goldbot-mt5-terminal@.service").exists()


def test_needrestart_never_restarts_a_goldbot_unit():
    """Incident 2026-10-22: unattended-upgrades + needrestart restarted the terminal and bridge mid-session. The drop-in
    keeps every goldbot-* unit out of its automatic restarts, and every installer puts it in place."""
    conf = (LINUX / NEEDRESTART_CONF).read_text()
    code = [ln.strip() for ln in conf.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert code == ["$nrconf{override_rc}{qr(^goldbot-)} = 0;"]
    for f in ("mt5_bootstrap.sh", "brain_bootstrap.sh"):
        assert ('install -m 644 -o root -g root "$OPS/needrestart/goldbot.conf" /etc/needrestart/conf.d/goldbot.conf'
                in (LINUX / f).read_text()), f


def test_the_terminal_count_ignores_the_wine_start_launcher():
    """Live 2026-10-25 (demo box): the unit's main process is wine's `start.exe /exec ...terminal64.exe`, which names the
    terminal too, so post-reboot counted 2. The count anchors on the terminal's own Windows path."""
    import re
    script = (Path(__file__).resolve().parents[1] / "goldbot" / "ops" / "linux" / "mt5-box-upgrade.sh").read_text()
    m = re.search(r"pgrep -c -u mt5 -f '([^']+)'", script)
    assert m is not None
    pat = re.compile(m.group(1).replace("[\\\\]", "\\\\"))
    assert pat.search(r"C:\MT5\ICMarkets\terminal64.exe /profile:goldbot")
    assert not pat.search("start.exe /exec /home/mt5/.wine/drive_c/MT5/ICMarkets/terminal64.exe")


@pytest.mark.parametrize("phase", ["precheck", "install", "post-reboot"])
def test_a_box_with_a_second_terminal_is_refused_before_anything(box, phase):
    """SRE review of 43ad25ee (ADR 0062): this script's pkill, its one-terminal count and its one watchdog assume one
    terminal per box; on a box with per-instance drop-ins (vantage-247 beside vantage-demo) it would stop both
    terminals and FAIL at two. It refuses every phase there, before any call."""
    tmp, sha = box
    d = tmp / "units" / "goldbot-mt5-terminal@vantage-247.service.d"
    d.mkdir(parents=True)
    (d / "goldbot-instance.conf").write_text("[Service]\n")
    rc, out = run(box, phase, sha, *FLAGS)
    assert rc == 2 and "more than one MT5 terminal" in out
    assert "pkill" not in calls(tmp) and "stop" not in calls(tmp)
