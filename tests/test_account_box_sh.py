"""One MT5 box per account (docs/ops/icm-probe-account-plan.md Option B, step B3): mt5_bootstrap.sh takes the account
(`--account`, or ACCOUNT, default icm-demo) and refuses an id that is not a plain lowercase id before anything else;
for icm-demo it runs the same unit commands as before. The brain gets a templated tunnel per further account box
whose env file names the box and the local port, and which refuses icm-demo's 8765 for any other account."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
LINUX = REPO / "goldbot" / "ops" / "linux"
BOOT = LINUX / "mt5_bootstrap.sh"
TUNNEL = LINUX / "systemd" / "goldbot-bridge-tunnel@.service"
LEGACY = LINUX / "systemd" / "goldbot-bridge-tunnel.service"
BASH = shutil.which("bash") or ""
SHA = "0" * 40


def _boot(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    e = {"PATH": "/usr/bin:/bin", **env}
    return subprocess.run([BASH, str(BOOT), *args], capture_output=True, text=True, timeout=20, env=e)


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("args", [
    ["--account", "ICM"], ["--account", "icm demo"], ["--account", "../x"], ["--account", "a;id"],
    ["--account", ""], ["--account"], [SHA, SHA], [SHA, "--account", "-x"], ["--account=Bad"],
])
def test_bad_arguments_are_refused_before_anything(args):
    out = _boot(*args)
    assert out.returncode == 2 and "usage:" in out.stdout


@pytest.mark.skipif(not BASH, reason="needs bash")
@pytest.mark.parametrize("args,env", [
    ([SHA, "--account", "icm-probe"], {}), (["--account", "icm-probe", SHA], {}), ([SHA, "--account=icm-probe"], {}),
    ([SHA], {"ACCOUNT": "icm-probe"}), ([SHA], {}), ([], {}),
])
def test_good_arguments_pass_parsing(args, env):
    """Not root here, so a valid call stops at the root check (after the parsing)."""
    if subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip() == "0":
        pytest.skip("must not run the bootstrap as root")
    out = _boot(*args, **env)
    assert out.returncode == 1 and "run with sudo" in out.stdout, out.stdout


def test_units_are_enabled_for_the_account_and_icm_demo_is_the_default():
    s = BOOT.read_text(encoding="utf-8")
    assert re.search(r"^ACCOUNT=\$\{ACCOUNT:-icm-demo\}$", s, re.M)
    for line in ('systemctl enable "goldbot-mt5-terminal@$ACCOUNT"', 'systemctl enable "goldbot-bridge@$ACCOUNT"',
                 'systemctl enable --now "goldbot-bridge-watchdog@$ACCOUNT.timer"'):
        assert line in s
    code = [x for x in s.splitlines() if not x.lstrip().startswith("#")]
    assert not [x for x in code if "systemctl" in x and "icm-demo" in x]    # no unit hardcoded to one account
    # the template with Restart= is installed on every box built by this script
    assert '"$OPS"/systemd/goldbot-mt5-terminal@.service' in s
    term = (LINUX / "systemd" / "goldbot-mt5-terminal@.service").read_text()
    assert "Restart=always" in term
    # the box layout is the same for every account: one prefix, one install path, one bridge port
    assert 'permitopen=\\"127.0.0.1:8765\\"' in s and "C:\\\\MT5\\\\ICMarkets" in s


def _directive(text: str, key: str) -> str:
    joined = text.replace("\\\n", " ")
    m = re.search(rf"^{key}=(.*)$", joined, re.M)
    assert m, key
    return re.sub(r"\s+", " ", m.group(1)).strip()


def test_the_tunnel_template_reads_its_env_file_and_forwards_to_the_box_bridge():
    t = TUNNEL.read_text()
    assert _directive(t, "EnvironmentFile") == "/etc/goldbot/bridge-%i.env"
    assert _directive(t, "User") == "goldbot"
    ex = _directive(t, "ExecStart")
    assert "-L 127.0.0.1:${LOCAL_PORT}:127.0.0.1:8765 mt5@${MT5_HOST}" in ex
    assert "-o ExitOnForwardFailure=yes" in ex and ex.startswith("/usr/bin/ssh -N ")
    assert _directive(t, "Restart") == "always" and _directive(t, "StartLimitIntervalSec") == "0"
    assert _directive(t, "WantedBy") == "multi-user.target"


def test_the_icm_demo_tunnel_is_untouched():
    t = LEGACY.read_text()
    assert _directive(t, "EnvironmentFile") == "/etc/goldbot/bridge.env"
    assert "-L 127.0.0.1:8765:127.0.0.1:8765 mt5@${MT5_HOST}" in _directive(t, "ExecStart")


def test_the_brain_installs_the_template_without_enabling_it():
    s = (LINUX / "brain_bootstrap.sh").read_text()
    assert re.search(r"^for u in .* bridge-tunnel bridge-tunnel@; do install", s, re.M)
    code = "\n".join(x for x in s.splitlines() if not x.lstrip().startswith("#"))
    assert "goldbot-bridge-tunnel@" not in code


def _pre(account: str) -> str:
    """The ExecStartPre shell as systemd hands it to /bin/sh: %i expanded, $$ unescaped."""
    pre = _directive(TUNNEL.read_text(), "ExecStartPre")
    m = re.fullmatch(r"/bin/sh -c '(.*)'", pre)
    assert m, pre
    return m.group(1).replace("%i", account).replace("$$", "$")


@pytest.mark.skipif(not shutil.which("sh"), reason="needs sh")
@pytest.mark.parametrize("account,env,ok", [
    ("icm-probe", {"MT5_HOST": "h", "LOCAL_PORT": "8766"}, True),
    ("icm-probe", {"MT5_HOST": "h", "LOCAL_PORT": "8765"}, False),
    ("icm-demo", {"MT5_HOST": "h", "LOCAL_PORT": "8765"}, True),
    ("icm-probe", {"MT5_HOST": "h"}, False),
    ("icm-probe", {"MT5_HOST": "h", "LOCAL_PORT": "87a6"}, False),
    ("icm-probe", {"MT5_HOST": "h", "LOCAL_PORT": ""}, False),
    ("icm-probe", {"LOCAL_PORT": "8766"}, False),
])
def test_the_tunnel_refuses_a_bad_env_file(account, env, ok):
    p = subprocess.run(["sh", "-c", _pre(account)], capture_output=True, text=True,
                       env={"PATH": "/usr/bin:/bin", **env})
    assert (p.returncode == 0) is ok, p.stdout


def test_runbook_names_aliases_only():
    text = (REPO / "docs" / "RUNBOOK.md").read_text()
    sec = text[text.index("### 0.3b Adding an account box"):text.index("### 0.4 The brain")]
    for step in ("B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11"):
        assert f"| {step} |" in sec
    assert "sudo bash mt5_bootstrap.sh <SHA> --account icm-probe" in sec
    assert "goldbot-bridge-tunnel@icm-probe" in sec and "LOCAL_PORT=8766" in sec
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", sec.replace("127.0.0.1", ""))   # no IP address
    assert not re.search(r"\b\d{6,}\b", sec)                                          # no login number
