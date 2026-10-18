"""MT5-chart pieces install standalone: MT5-box unit and forced command use /usr/local/libexec/goldbot, never /opt/goldbot."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "goldbot" / "ops" / "linux"
LIBEXEC = "/usr/local/libexec/goldbot"


def test_capture_unit_runs_from_libexec() -> None:
    t = (LINUX / "systemd" / "goldbot-chart-capture.service").read_text()
    assert f"ExecStart={LIBEXEC}/goldbot-chart-capture.sh" in t
    assert "/opt/goldbot" not in t


def test_runbook_installs_scripts_from_root_clone_and_forces_libexec_command() -> None:
    s = (ROOT / "docs" / "RUNBOOK.md").read_text()
    sec = s[s.index("### MT5 terminal chart on the dashboard") :]
    nxt = sec.find("\n### ", 10)
    sec = sec if nxt < 0 else sec[:nxt]
    assert f'command="{LIBEXEC}/goldbot-chart-serve.sh"' in sec
    assert "merge-base --is-ancestor" in sec
    assert f"install -m 755 -o root -g root $d/r/goldbot/ops/linux/$f {LIBEXEC}/" in sec
    assert "/opt/goldbot/goldbot/ops/linux/goldbot-chart-serve.sh" not in sec
    assert "/opt/goldbot/goldbot/ops/linux/goldbot-chart-capture.sh" not in sec


def test_chart_scripts_exist() -> None:
    for f in ("goldbot-chart-capture.sh", "goldbot-chart-serve.sh"):
        assert (LINUX / f).is_file()
