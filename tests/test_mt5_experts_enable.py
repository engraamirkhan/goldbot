"""mt5-experts-enable (goldbot/ops/linux/mt5/mt5-experts-enable.sh): incident 2026-10-20, the probe terminal ran with
AutoTrading off (no [Experts] section in its common.ini) and refused every order. The terminal unit now sets the seven
[Experts] keys the demo terminal has before every start, with the terminal stopped (it rewrites common.ini on exit),
keeping MT5's UTF-16LE + BOM + CRLF, backing the file up first, and changing nothing when they are already set."""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "goldbot" / "ops" / "linux"
SCRIPT = LINUX / "mt5" / "mt5-experts-enable.sh"
EXPERTS = "[Experts]\r\nAllowDllImport=0\r\nEnabled=1\r\nAccount=1\r\nProfile=1\r\nChart=0\r\nApi=0\r\nWebRequest=0\r\n"
LIBEXEC = "/usr/local/libexec/goldbot"

needs_tools = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("iconv")) or os.geteuid() == 0,
                                 reason="needs bash + iconv, non-root")


def _mt5(path: Path, text: str) -> None:
    path.write_bytes(b"\xff\xfe" + text.replace("\n", "\r\n").encode("utf-16-le"))


def _read(path: Path) -> str:
    raw = path.read_bytes()
    assert raw[:2] == b"\xff\xfe", "BOM lost"
    text = raw[2:].decode("utf-16-le")
    assert "\n" not in text.replace("\r\n", ""), "a bare LF: not CRLF"
    return text


def _box(tmp_path: Path, common: str | None) -> tuple[Path, dict[str, str]]:
    d = tmp_path / "ICMarkets"
    (d / "Config").mkdir(parents=True)
    if common is not None:
        _mt5(d / "Config" / "common.ini", common)
    env = {**os.environ, "MT5_DIR": str(d), "BACKUP_ROOT": str(tmp_path), "SKIP_RUNNING_CHECK": "1",
           "GOLDBOT_TEST": "1"}
    return d / "Config" / "common.ini", env


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def _backups(tmp_path: Path) -> list[Path]:
    return sorted(tmp_path.glob("mt5-common-backup-*.ini"))


@needs_tools
def test_a_common_ini_without_an_experts_section_gets_the_seven_keys_in_mt5_encoding(tmp_path):
    common = "[Common]\nLogin=1\nProxyEnable=0\n[Charts]\nProfileLast=goldbot\n"
    ini, env = _box(tmp_path, common)
    r = _run(env)
    assert r.returncode == 0, r.stderr
    text = _read(ini)
    assert text == common.replace("\n", "\r\n") + EXPERTS
    assert "Login" not in r.stdout + r.stderr                       # never prints ini contents (it holds the login)
    [b] = _backups(tmp_path)
    assert b.read_bytes()[2:].decode("utf-16-le") == common.replace("\n", "\r\n")      # the untouched original
    assert b.stat().st_mode & 0o077 == 0                                                # private: it holds the login


@needs_tools
def test_existing_experts_keys_are_overwritten_in_place_and_others_kept(tmp_path):
    common = "[Experts]\nEnabled=0\nAllowDllImport=1\nNews=1\n[Charts]\nProfileLast=goldbot\n"
    ini, env = _box(tmp_path, common)
    assert _run(env).returncode == 0
    text = _read(ini)
    sec = text.split("[Charts]")[0]
    assert sec.startswith("[Experts]\r\nEnabled=1\r\nAllowDllImport=0\r\nNews=1\r\n")
    for kv in ("Account=1", "Profile=1", "Chart=0", "Api=0", "WebRequest=0"):
        assert f"\r\n{kv}\r\n" in sec and text.count(kv.split("=")[0] + "=") == 1, kv
    assert text.endswith("[Charts]\r\nProfileLast=goldbot\r\n")


@needs_tools
def test_a_second_run_changes_no_byte_and_makes_no_backup(tmp_path):
    ini, env = _box(tmp_path, "[Common]\nLogin=1\n")
    assert _run(env).returncode == 0
    first = ini.read_bytes()
    r = _run(env)
    assert r.returncode == 0 and "already" in r.stdout
    assert ini.read_bytes() == first and len(_backups(tmp_path)) == 1


@needs_tools
def test_the_demo_terminals_own_section_is_left_exactly_as_it_is(tmp_path):
    ini, env = _box(tmp_path, "[Common]\nLogin=1\n" + EXPERTS.replace("\r\n", "\n"))
    before = ini.read_bytes()
    assert _run(env).returncode == 0
    assert ini.read_bytes() == before and _backups(tmp_path) == []


@needs_tools
@pytest.mark.parametrize("common", [None], ids=["no_common_ini"])
def test_refuses_without_common_ini_or_data_dir(tmp_path, common):
    _, env = _box(tmp_path, common)
    r = _run(env)
    assert r.returncode == 2 and "refused" in r.stderr
    r = _run({**env, "MT5_DIR": str(tmp_path / "none")})
    assert r.returncode == 2 and "refused" in r.stderr


@needs_tools
def test_refuses_while_a_terminal_runs(tmp_path):
    ini, env = _box(tmp_path, "[Common]\nLogin=1\n")
    env.pop("SKIP_RUNNING_CHECK")
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "pgrep").write_text("#!/bin/sh\necho 4242\nexit 0\n")
    (fake / "pgrep").chmod(0o755)
    before = ini.read_bytes()
    r = _run({**env, "PATH": f"{fake}:{env['PATH']}"})
    assert r.returncode == 2 and "running" in r.stderr and ini.read_bytes() == before


def test_the_terminal_unit_enables_autotrading_after_the_one_terminal_guard_as_mt5():
    unit = (LINUX / "systemd" / "goldbot-mt5-terminal@.service").read_text()
    pres = [ln.split("=", 1)[1] for ln in unit.splitlines() if ln.startswith("ExecStartPre=")]
    assert len(pres) == 2 and "[t]erminal64" in pres[0]               # the guard first: no terminal runs after it
    # '-': a failed edit never keeps the terminal down (no terminal = no exits); /health then says autotrading_off
    assert pres[1] == f"-{LIBEXEC}/mt5-experts-enable.sh"
    assert "\nUser=mt5\n" in unit


def test_bootstrap_and_box_upgrade_install_the_script_and_its_helper_root_owned():
    boot = (LINUX / "mt5_bootstrap.sh").read_text()
    up = (LINUX / "mt5-box-upgrade.sh").read_text()
    for f in ("mt5-experts-enable.sh", "mt5-ini.sh"):
        assert f'install -m 755 -o root -g root "$OPS/mt5/{f}" {LIBEXEC}/{f}' in boot, f
        assert f'install -m 755 -o root -g root "$ops/mt5/{f}" "$LIBEXEC/{f}"' in up, f
    assert f"LIBEXEC={LIBEXEC}\n" in up
    assert boot.index("mt5-experts-enable.sh") < boot.index("systemctl daemon-reload")


def test_shellcheck_clean():
    sc = shutil.which("shellcheck")
    if sc is None:
        pytest.skip("shellcheck not installed")
    for f in ("mt5-experts-enable.sh", "mt5-ini.sh", "mt5-gold-profile.sh"):
        r = subprocess.run([sc, "-x", "--severity=warning", str(LINUX / "mt5" / f)], capture_output=True, text=True, cwd=LINUX / "mt5")
        assert r.returncode == 0, r.stdout


@needs_tools
def test_the_path_and_check_overrides_are_ignored_outside_tests(tmp_path):
    """Security LOW: the unit's environment never redirects the edit (MT5_DIR, BACKUP_ROOT) or skips the running check;
    only GOLDBOT_TEST=1 honours them, so without it the script works on $HOME's own MT5 tree."""
    ini, env = _box(tmp_path, "[Common]\nLogin=1\n")
    env.pop("GOLDBOT_TEST")
    home = tmp_path / "home"
    home.mkdir()
    before = ini.read_bytes()
    r = _run({**env, "HOME": str(home)})
    assert r.returncode == 2 and str(home) in r.stderr and ini.read_bytes() == before


@needs_tools
def test_ini_set_never_follows_a_planted_symlink(tmp_path):
    """Security LOW: ini_set writes through a fresh temp file beside the target (mktemp), never `> $f.new`."""
    target = tmp_path / "elsewhere"
    target.write_text("keep\n")
    f = tmp_path / "x.ini"
    f.write_text("[A]\nk=1\n")
    (tmp_path / "x.ini.new").symlink_to(target)
    helper = LINUX / "mt5" / "mt5-ini.sh"
    subprocess.run(["bash", "-c", f'. "{helper}"; ini_set "{f}" A k 2'], check=True)
    assert target.read_text() == "keep\n" and f.read_text() == "[A]\nk=2\n"
