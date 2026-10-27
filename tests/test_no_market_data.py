"""ADR 0048: no market data may enter this public repository (scripts/check_no_market_data.py, also a pre-commit hook)."""
import gzip
import importlib.util
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_no_market_data", ROOT / "scripts" / "check_no_market_data.py")
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_no_market_data"] = guard
_spec.loader.exec_module(guard)


def _bars() -> pd.DataFrame:
    ts = pd.date_range("2026-01-02", periods=5, freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5})


def test_no_tracked_file_is_market_data():
    files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True).stdout.decode().split("\0")
    bad = [(f, r) for f in files if f and (r := guard.reason(ROOT / f, f))]
    assert not bad, bad


def test_parquet_outside_fixtures_is_refused_even_renamed(tmp_path):
    p = tmp_path / "bars.bin"
    _bars().to_parquet(p)
    assert guard.reason(p, "raw/bars.bin")                               # magic bytes, not the name
    assert guard.reason(p, "notes/xauusd_2026.parquet")
    assert guard.reason(p, "tests/data/small_golden.parquet") is None    # small synthetic fixture


def test_large_fixture_is_refused(tmp_path, monkeypatch):
    p = tmp_path / "x.parquet"
    _bars().to_parquet(p)
    monkeypatch.setattr(guard, "MAX_FIXTURE_BYTES", 10)
    assert guard.reason(p, "tests/data/x.parquet")


def test_price_csv_is_refused_plain_and_gzipped(tmp_path):
    p = tmp_path / "x.csv"
    _bars().to_csv(p, index=False)
    assert guard.reason(p, "docs/x.csv")
    q = tmp_path / "ticks.csv.gz"
    q.write_bytes(gzip.compress(b"\xef\xbb\xbfGmt time;Bid;Ask;Volume\n02.01.2026 00:00:00.123;2000.1;2000.4;1\n"))
    assert guard.reason(q, "ticks.csv.gz")
    r = tmp_path / "dk.csv"
    r.write_text("timestamp,bid_open,bid_high,bid_low,bid_close\n1,2,3,4,5\n")
    assert guard.reason(r, "dk.csv")


def test_ordinary_files_pass(tmp_path):
    p = tmp_path / "evidence.csv"
    p.write_text("event_id,check,evidence\ncpi:2010-02-17,bls_schedule,none\n")
    assert guard.reason(p, "docs/evidence.csv") is None
    s = tmp_path / "x.py"
    s.write_text("def close(open, high, low):\n    return open\n")
    assert guard.reason(s, "goldbot/x.py") is None


def test_main_exit_codes(tmp_path, capsys):
    p = tmp_path / "x.parquet"
    _bars().to_parquet(p)
    assert guard.main([str(p)]) == 1 and "ADR 0048" in capsys.readouterr().out
    ok = tmp_path / "ok.md"
    ok.write_text("# notes\n")
    assert guard.main([str(ok)]) == 0


def test_gitignore_keeps_local_data_out():
    """The local copy (raw/, raw-macro/, raw-calendar/) and the store (data/) are ignored, so `git add .` skips them."""
    paths = ["raw/xauusd_1m_dukascopy_2026.parquet", "data/bars_1m/source=dukascopy/part.parquet",
             "raw-macro/fred.parquet", "raw-calendar/us_calendar.parquet", "raw/dukascopy/2026-01.csv"]
    out = subprocess.run(["git", "check-ignore", "--no-index", *paths], cwd=ROOT, capture_output=True, text=True).stdout
    assert sorted(out.split()) == sorted(paths)
