"""The date-shift framework (tests/clockshift.py) and the quarantine marker (tests/conftest.py)."""
import datetime as dt
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests import clockshift

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str, shift: str | None = None) -> str:
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    env.pop(clockshift.ENV, None)
    if shift is not None:
        env[clockshift.ENV] = shift
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(code)], capture_output=True, text=True, env=env, cwd=ROOT)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


@pytest.mark.guard
@pytest.mark.parametrize("days", [7, 30, 45])
def test_every_clock_reader_moves_by_exactly_n_days(days: int) -> None:
    out = _run(f"""
        import datetime, time
        from datetime import datetime as D, date
        import pandas as pd
        from tests import clockshift
        real_t = time.time()
        clockshift.install({days})
        day = 86400
        print(round((time.time() - real_t) / day, 3))
        print(round((time.time_ns() / 1e9 - real_t) / day, 3))
        print(round((D.now() - D.fromtimestamp(real_t)).total_seconds() / day, 3))
        utc0 = datetime.datetime.fromtimestamp(real_t, datetime.timezone.utc).replace(tzinfo=None)
        print(round((datetime.datetime.utcnow() - utc0).total_seconds() / day, 3))
        print((date.today() - date.fromtimestamp(real_t)).days)
        print(round((pd.Timestamp.now() - pd.Timestamp.fromtimestamp(real_t)).total_seconds() / day, 3))
        print(round((pd.Timestamp.now(tz="UTC") - pd.Timestamp.fromtimestamp(real_t, tz="UTC")).total_seconds() / day, 3))
        print(isinstance(datetime.datetime(2020, 1, 1), D), isinstance(D.now(), datetime.datetime))
        """)
    lines = out.splitlines()
    assert [float(x) for x in lines[:7]] == [float(days)] * 7
    assert lines[7] == "True True"


def test_unset_or_zero_shift_moves_nothing() -> None:
    assert clockshift.shift_days_from_env({}) == 0
    assert clockshift.shift_days_from_env({clockshift.ENV: "0"}) == 0
    assert _run("""
        import datetime, time
        import tests.conftest
        from tests import clockshift
        real = clockshift._REAL["time"]()
        print(clockshift.offset(), abs(time.time() - real) < 1, isinstance(datetime.datetime.now(), datetime.datetime),
              abs((datetime.datetime.now() - datetime.datetime.fromtimestamp(real)).total_seconds()) < 1)
        """) == "0.0 True True True"


@pytest.mark.parametrize("bad", ["abc", "-1", "366", "1.5"])
def test_a_bad_shift_is_refused_not_ignored(bad: str) -> None:
    with pytest.raises(ValueError, match="whole number of days"):
        clockshift.shift_days_from_env({clockshift.ENV: bad})


def test_the_shift_is_removed_cleanly() -> None:
    out = _run("""
        import datetime, time
        from datetime import datetime as D
        from tests import clockshift
        real = (datetime.datetime, D, time.time)
        clockshift.install(30)
        clockshift.uninstall()
        print((datetime.datetime, D, time.time) == real)
        """)
    assert out == "True"


QUARANTINE_FILE = '''
import pytest

@pytest.mark.quarantine(issue="#1", until="{until}")
def test_known_bad():
    assert False
'''


def _pytest_in(tmp_path: Path, body: str, shift: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run pytest on `body` in a throwaway project carrying this repo's conftest and clockshift."""
    (tmp_path / "test_q.py").write_text(body)
    shutil.copy(ROOT / "tests" / "conftest.py", tmp_path / "conftest.py")
    pkg = tmp_path / "tests"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    shutil.copy(ROOT / "tests" / "clockshift.py", pkg / "clockshift.py")
    (tmp_path / "pytest.ini").write_text("[pytest]\naddopts = --strict-markers -p no:randomly -p no:cacheprovider\n")
    env = {**os.environ, "PYTHONPATH": f"{tmp_path}{os.pathsep}{ROOT}"}
    env.pop(clockshift.ENV, None)
    if shift is not None:
        env[clockshift.ENV] = shift
    return subprocess.run([sys.executable, "-m", "pytest", "-rs", str(tmp_path / "test_q.py")],
                          capture_output=True, text=True, env=env, cwd=tmp_path)


def test_a_quarantined_test_is_skipped_until_its_date(tmp_path: Path) -> None:
    until = (clockshift.real_today() + dt.timedelta(days=1)).isoformat()          # the real calendar, not a shifted one
    r = _pytest_in(tmp_path, QUARANTINE_FILE.format(until=until))
    assert r.returncode == 0, r.stdout
    assert "1 skipped" in r.stdout and f"quarantined until {until} (#1)" in r.stdout


def test_the_last_day_of_a_quarantine_still_skips_even_under_a_shift(tmp_path: Path) -> None:
    until = clockshift.real_today().isoformat()                                   # boundary: until == today is not expired
    assert _pytest_in(tmp_path, QUARANTINE_FILE.format(until=until), shift="30").returncode == 0


def test_an_expired_quarantine_fails_the_run(tmp_path: Path) -> None:
    until = (clockshift.real_today() - dt.timedelta(days=1)).isoformat()
    r = _pytest_in(tmp_path, 'import pytest\n\n@pytest.mark.quarantine(issue="#1", until="%s")\n'
                             'def test_would_pass():\n    assert True\n' % until)
    assert r.returncode == 1 and "expired" in r.stdout and "1 error" in r.stdout


def test_a_quarantine_without_a_date_is_refused(tmp_path: Path) -> None:
    r = _pytest_in(tmp_path, 'import pytest\n\n@pytest.mark.quarantine(issue="#1")\ndef test_x():\n    pass\n')
    assert r.returncode != 0 and "until" in (r.stdout + r.stderr)


def test_the_run_header_names_an_active_shift(tmp_path: Path) -> None:
    r = _pytest_in(tmp_path, "def test_ok():\n    pass\n", shift="9")
    assert "clock shifted +9 days" in r.stdout


@pytest.mark.clock_at("2031-03-30T23:30:00Z")
def test_a_pinned_test_starts_at_the_instant_and_keeps_ticking() -> None:
    import time

    import pandas as pd
    now = pd.Timestamp.now(tz="UTC")
    assert now.date() == dt.date(2031, 3, 30) and now.hour == 23
    t0 = time.time()
    time.sleep(0.02)
    assert 0.015 < time.time() - t0 < 5
    assert dt.datetime.now(dt.timezone.utc).year == 2031 and dt.date.today().year == 2031
    assert isinstance(dt.datetime.now(), dt.datetime)


def test_a_pin_ends_with_its_test() -> None:
    assert clockshift.offset() == float(clockshift.shift_days_from_env() * 86400)
    assert dt.date.today().year < 2031


@pytest.mark.real_clock(reason="compares file mtimes with now")
def test_real_clock_ignores_the_shift(tmp_path: Path) -> None:
    f = tmp_path / "x"
    f.write_text("x")
    import time
    assert abs(time.time() - f.stat().st_mtime) < 60


def test_real_clock_without_a_reason_fails(tmp_path: Path) -> None:
    r = _pytest_in(tmp_path, "import pytest\n\n@pytest.mark.real_clock\ndef test_x():\n    pass\n")
    assert r.returncode == 1 and "needs a reason" in r.stdout


def test_a_quarantine_dated_more_than_two_days_out_is_refused(tmp_path: Path) -> None:
    until = (clockshift.real_today() + dt.timedelta(days=400)).isoformat()
    r = _pytest_in(tmp_path, QUARANTINE_FILE.format(until=until))
    assert r.returncode != 0 and "at most 2 days" in (r.stdout + r.stderr)
    ok = (clockshift.real_today() + dt.timedelta(days=2)).isoformat()
    (tmp_path / "ok").mkdir()
    assert _pytest_in(tmp_path / "ok", QUARANTINE_FILE.format(until=ok)).returncode == 0   # the boundary: today + 2
