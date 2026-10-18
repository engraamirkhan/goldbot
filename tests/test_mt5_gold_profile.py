"""MT5 default gold chart: profile files and the apply/rollback script (docs/deploy/mt5-default-gold-chart.md)."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MT5 = ROOT / "goldbot" / "ops" / "linux" / "mt5"
SCRIPT = MT5 / "mt5-gold-profile.sh"
CAPTURE = ROOT / "goldbot" / "ops" / "linux" / "goldbot-chart-capture.sh"

needs_tools = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("iconv")) or os.geteuid() == 0,
                                 reason="needs bash + iconv, non-root")


def _kv(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("<"))


def test_profile_is_one_gold_m15_chart_without_one_click() -> None:
    files = sorted(p.name for p in (MT5 / "profile" / "goldbot").iterdir())
    assert files == ["chart01.chr", "order.wnd"]
    kv = _kv((MT5 / "profile" / "goldbot" / "chart01.chr").read_text())
    assert kv["symbol"] == "XAUUSD"                       # broker's exact name, checked on the box 18 Oct
    assert (kv["period_type"], kv["period_size"]) == ("0", "15")
    assert kv["one_click"] == "0"
    assert kv["window_type"] == "3"                       # maximised
    assert _kv((MT5 / "goldbot.tpl").read_text())["one_click"] == "0"


def _mt5(path: Path, text: str) -> None:
    path.write_bytes(b"\xff\xfe" + text.replace("\n", "\r\n").encode("utf-16-le"))


def _read(path: Path) -> str:
    raw = path.read_bytes()
    assert raw[:2] == b"\xff\xfe"
    return raw[2:].decode("utf-16-le")


@needs_tools
def test_apply_then_rollback(tmp_path: Path) -> None:
    d = tmp_path / "ICMarkets"
    (d / "Config").mkdir(parents=True)
    (d / "Profiles" / "Charts" / "Default").mkdir(parents=True)
    (d / "Profiles" / "Templates").mkdir()
    common = "[Common]\nLogin=1\n[Charts]\nProfileLast=Default\nMaxBars=100000\n[Experts]\nEnabled=1\n"
    terminal = "[Window]\nFullscreen=0\nType=3\n[Toolbars]\nArrange=1\n"
    _mt5(d / "Config" / "common.ini", common)
    _mt5(d / "Config" / "terminal.ini", terminal)
    env = {**os.environ, "MT5_DIR": str(d), "BACKUP_ROOT": str(tmp_path), "SKIP_RUNNING_CHECK": "1"}
    out = subprocess.run(["bash", str(SCRIPT), "apply"], env=env, capture_output=True, text=True, check=True).stdout
    assert "Login" not in out
    c = _read(d / "Config" / "common.ini")
    assert "\r\nProfileLast=goldbot\r\nMaxBars=100000\r\n" in c and "ProfileLast=Default" not in c
    assert "Login=1\r\n" in c and "Enabled=1\r\n" in c
    assert "Fullscreen=1\r\n" in _read(d / "Config" / "terminal.ini")
    chr_ = _read(d / "Profiles" / "Charts" / "goldbot" / "chart01.chr")
    assert "symbol=XAUUSD\r\n" in chr_
    assert (d / "Profiles" / "Templates" / "goldbot.tpl").is_file()
    backup = out.strip().rsplit(" ", 1)[-1]
    subprocess.run(["bash", str(SCRIPT), "rollback", backup], env=env, capture_output=True, text=True, check=True)
    assert _read(d / "Config" / "common.ini") == common.replace("\n", "\r\n")
    assert _read(d / "Config" / "terminal.ini") == terminal.replace("\n", "\r\n")
    assert not (d / "Profiles" / "Charts" / "goldbot").exists()


@needs_tools
def test_refuses_while_terminal_runs_and_without_data_dir(tmp_path: Path) -> None:
    env = {**os.environ, "MT5_DIR": str(tmp_path / "none")}
    r = subprocess.run(["bash", str(SCRIPT), "apply"], env=env, capture_output=True, text=True)
    assert r.returncode == 2 and "refused" in r.stderr


def test_capture_crop_is_validated() -> None:
    s = CAPTURE.read_text()
    assert "CHART_CROP" in s
    assert "^[0-9]+x[0-9]+[+][0-9]+[+][0-9]+$" in s
