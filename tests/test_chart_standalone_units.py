"""MT5-chart pieces install standalone: MT5-box unit and forced command use /usr/local/libexec/goldbot, never /opt/goldbot."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "goldbot" / "ops" / "linux"
LIBEXEC = "/usr/local/libexec/goldbot"


def test_capture_unit_runs_from_libexec() -> None:
    t = (LINUX / "systemd" / "goldbot-chart-capture.service").read_text()
    assert f"ExecStart={LIBEXEC}/goldbot-chart-capture.sh" in t
    assert "/opt/goldbot" not in t


def test_runbook_retires_the_chart_capture_and_never_installs_it_again() -> None:
    """Owner 2026-11-02: no chart display, so the RUNBOOK only turns the capture off (both timers, the forced-command
    key, the libexec scripts) and no longer carries an install recipe."""
    s = (ROOT / "docs" / "RUNBOOK.md").read_text()
    sec = s[s.index("### MT5 terminal chart: retired") :]
    nxt = sec.find("\n### ", 10)
    sec = sec if nxt < 0 else sec[:nxt]
    for step in ("disable --now goldbot-chart-fetch.timer", "disable --now goldbot-chart-capture.timer",
                 "sed -i '/goldbot-chart/d' /home/mt5/.ssh/authorized_keys", f"rm -f {LIBEXEC}/goldbot-chart-*.sh"):
        assert step in sec, step
    assert "enable --now goldbot-chart" not in s and "### MT5 terminal chart on the dashboard" not in s


def test_chart_scripts_exist() -> None:
    for f in ("goldbot-chart-capture.sh", "goldbot-chart-serve.sh"):
        assert (LINUX / f).is_file()
