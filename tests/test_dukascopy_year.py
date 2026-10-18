import datetime as dt
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

_spec = importlib.util.spec_from_file_location("dukascopy_year", Path(__file__).resolve().parents[1] / "scripts" / "dukascopy_year.py")
assert _spec is not None and _spec.loader is not None
dy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dy)


def test_month_range_end_is_exclusive_first_of_next_month():
    months = list(dy.month_range(2025, today=dt.date(2026, 1, 15)))
    assert len(months) == 12
    assert months[0] == (dt.date(2025, 1, 1), dt.date(2025, 2, 1))
    assert months[-1] == (dt.date(2025, 12, 1), dt.date(2026, 1, 1))


def test_month_range_current_month_stops_before_today():
    # today's file is not published until the day closes
    months = list(dy.month_range(2026, today=dt.date(2026, 3, 10)))
    assert len(months) == 3
    assert months[-1] == (dt.date(2026, 3, 1), dt.date(2026, 3, 10))
    # on the 1st there is no completed day in the new month, so it is not requested (was reported MISSING)
    assert list(dy.month_range(2026, today=dt.date(2026, 10, 1)))[-1] == (dt.date(2026, 9, 1), dt.date(2026, 10, 1))


def test_week_chunks_cover_range_and_skip_weekend_only_chunks():
    # May 2021: 1st is a Saturday, 31st a Monday
    start, end = dt.date(2021, 5, 1), dt.date(2021, 6, 1)
    chunks = list(dy.week_chunks(start, end))
    assert chunks[0][0] == start and chunks[-1][1] == end
    for (a, b), (c, _) in zip(chunks, chunks[1:]):
        assert b == c
    assert all((b - a).days <= 7 for a, b in chunks)
    # a chunk that is only Sat+Sun is dropped
    assert list(dy.week_chunks(dt.date(2021, 5, 1), dt.date(2021, 5, 3))) == []


def _bars(month: str, n: int, flag: str = "") -> pd.DataFrame:
    ts = pd.date_range(f"{month}-01 01:00", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "dq_flag": flag})


def test_months_to_fetch_keeps_complete_months_only():
    months = list(dy.month_range(2022, today=dt.date(2023, 6, 1)))
    existing = pd.concat([
        _bars("2022-01", 20_000),                               # complete -> keep
        _bars("2022-02", 3_000),                                # short -> refetch
        _bars("2022-04", 20_000, "warning:partial_month"),      # flagged partial -> refetch
        _bars("2022-05", 20_000, "warning:ask_imputed"),        # imputed side -> refetch
    ])
    need = dy.months_to_fetch(existing, months, today=dt.date(2023, 6, 1), retry_flagged=True)
    assert "2022-01" not in need
    assert {"2022-02", "2022-03", "2022-04", "2022-05", "2022-12"} <= need


def test_months_to_fetch_keeps_flagged_closed_months_by_default():
    # G5: the Saturday run re-downloaded the three bid-imputed 2026 months for ~4 h a week (run 38042847605);
    # closed months that are flagged imputed/partial are kept unless a dispatch asks for the retry
    months = list(dy.month_range(2026, today=dt.date(2026, 10, 17)))
    existing = pd.concat([
        _bars("2026-01", 20_000),
        _bars("2026-02", 20_000, "warning:bid_imputed"),
        _bars("2026-03", 20_000, "warning:partial_month"),
        _bars("2026-04", 3_000),                                # short and unflagged -> still refetched
        *[_bars(f"2026-{m:02d}", 20_000) for m in range(5, 11)],
    ])
    need = dy.months_to_fetch(existing, months, today=dt.date(2026, 10, 17))
    assert need == {"2026-04", "2026-10"}


def test_months_to_fetch_always_refreshes_open_month():
    today = dt.date(2026, 3, 10)
    months = list(dy.month_range(2026, today=today))
    existing = pd.concat([_bars(m, 20_000) for m in ("2026-01", "2026-02", "2026-03")])
    assert dy.months_to_fetch(existing, months, today=today) == {"2026-03"}
    assert dy.months_to_fetch(None, months, today=today) == {"2026-01", "2026-02", "2026-03"}


def test_fractional_dukascopy_volumes_are_kept(monkeypatch, tmp_path):
    # XAUUSD volumes from Dukascopy are fractional; an int cast once turned most minutes into 0 volume
    ts = pd.date_range("2025-03-04 09:00", periods=5, freq="1min", tz="UTC")
    side = pd.DataFrame({"ts_utc": ts, "open": 2900.0, "high": 2901.0, "low": 2899.0, "close": 2900.5,
                         "volume": [0.12, 0.5, 1.7, 0.0, 0.03]})
    monkeypatch.setattr(dy, "download_side", lambda s, e, which, out: side.assign(close=2900.5 + (which == "ask") * 0.2))
    monkeypatch.setattr(dy, "MIN_BARS_PER_FULL_MONTH", 1)
    monkeypatch.setattr(dy.time, "sleep", lambda s: None)
    bars = dy.month_bars(dt.date(2025, 3, 1), dt.date(2025, 4, 1), tmp_path)
    assert bars is not None and bars["tick_count"].tolist() == [0.12, 0.5, 1.7, 0.0, 0.03]


def _day_bars(day: str, n: int, flag: str = "", price: float = 2000.0) -> pd.DataFrame:
    ts = pd.date_range(f"{day} 01:00", periods=n, freq="1min", tz="UTC")
    return pd.DataFrame({"ts_utc": ts, "bid_close": price, "dq_flag": flag})


def test_merge_never_shrinks_a_published_day():
    published = pd.concat([_day_bars("2026-10-05", 1000), _day_bars("2026-10-06", 1000), _day_bars("2026-10-07", 900)])
    fresh = pd.concat([
        _day_bars("2026-10-05", 400, price=1.0),     # throttled re-download: fewer bars -> published day kept
        _day_bars("2026-10-06", 1000, price=1.0),    # same count, published unflagged -> published kept (no rewrite)
        _day_bars("2026-10-07", 1100, price=1.0),    # more bars -> fresh day replaces it
        _day_bars("2026-10-08", 700, price=1.0),     # new day -> added
    ])
    out = dy.merge_never_shrink(published, fresh)
    day = out["ts_utc"].dt.strftime("%Y-%m-%d")
    counts = day.value_counts().to_dict()
    assert counts == {"2026-10-05": 1000, "2026-10-06": 1000, "2026-10-07": 1100, "2026-10-08": 700}
    assert (out.loc[day == "2026-10-05", "bid_close"] == 2000.0).all()
    assert (out.loc[day == "2026-10-06", "bid_close"] == 2000.0).all()
    assert (out.loc[day == "2026-10-07", "bid_close"] == 1.0).all()
    assert out["ts_utc"].is_monotonic_increasing and not out["ts_utc"].duplicated().any()
    pub_counts = published["ts_utc"].dt.strftime("%Y-%m-%d").value_counts()
    assert all(counts[d] >= n for d, n in pub_counts.items())


def test_merge_prefers_an_unflagged_fresh_day_of_equal_size_over_an_imputed_one():
    published = _day_bars("2026-02-03", 1000, "warning:bid_imputed")
    fresh = _day_bars("2026-02-03", 1000, price=1.0)
    out = dy.merge_never_shrink(published, fresh)
    assert (out["dq_flag"] == "").all() and (out["bid_close"] == 1.0).all()


def test_merge_with_nothing_published_returns_fresh():
    fresh = _day_bars("2026-10-08", 10)
    assert len(dy.merge_never_shrink(fresh.iloc[0:0], fresh)) == 10


def test_main_never_publishes_fewer_bars_for_a_day_than_before(monkeypatch, tmp_path):
    # end to end through main(): the open month comes back short from a throttled download; the published
    # October days must survive (before the never-shrink merge they were replaced wholesale)
    from goldbot.data.resample import BAR_COLUMNS

    def full(ts: pd.DatetimeIndex, price: float) -> pd.DataFrame:
        df = pd.DataFrame({"ts_utc": ts})
        for c in ("bid_open", "bid_high", "bid_low", "bid_close"):
            df[c] = price
        for c in ("ask_open", "ask_high", "ask_low", "ask_close"):
            df[c] = price + 0.3
        df["tick_count"] = 1.0
        df["spread_mean"], df["spread_max"] = 0.3, 0.3
        df["visible_at"] = df["ts_utc"] + pd.Timedelta(minutes=1)
        df["dq_flag"] = ""
        return df[BAR_COLUMNS + ["dq_flag"]]

    # Tue 6 and Wed 7 Oct 2026, 10:00-14:59 UTC (inside the gold session)
    days = [pd.date_range(f"2026-10-0{d} 10:00", periods=300, freq="1min", tz="UTC") for d in (6, 7)]
    published = full(pd.DatetimeIndex(days[0].append(days[1])), 2000.0).assign(source="dukascopy")
    prev = tmp_path / "prev.parquet"
    published.to_parquet(prev, index=False)
    short = full(days[0][:50], 1.0)                    # only 50 of Tuesday's 300 bars came back, no Wednesday

    monkeypatch.setattr(dy, "month_range", lambda year, today=None: iter([(dt.date(2026, 10, 1), dt.date(2026, 10, 17))]))
    monkeypatch.setattr(dy, "month_bars", lambda start, end, tmp: short)
    out = tmp_path / "out.parquet"
    monkeypatch.setattr(dy.sys, "argv", ["dukascopy_year.py", "2026", "--out", str(out), "--existing", str(prev),
                                         "--tmp", str(tmp_path / "raw"), "--report", str(tmp_path / "r.md")])
    assert dy.main() == 0
    got = pd.read_parquet(out)
    assert len(got) == 600
    assert (got["bid_close"] == 2000.0).all()


# --- G1/G2 targeted refetch: a strictly validated month list, one dispatch, never-shrink merge --------------------

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "data-dukascopy.yml"
TODAY = dt.date(2026, 10, 17)
_MONTH_RANGE = dy.month_range


def test_parse_months_normalises_a_valid_list():
    got = dy.parse_months("2026-06,2024-01, 2024-06 ,2024-01", today=TODAY)
    assert got == ["2024-01", "2024-06", "2026-06"]           # sorted, de-duplicated, spaces around commas allowed
    assert dy.parse_months("2026-10", today=TODAY) == ["2026-10"]  # the open month (has completed days) is allowed


@pytest.mark.parametrize("bad", [
    "", " ", ",", "2024-01,", ",2024-01", "2024-01,,2024-02",
    "2024-1", "24-01", "2024-13", "2024-00", "2024/01", "2024-01-05", "202401",
    "2024-01;rm -rf /", "2024-01 2024-02", "2024-01\n", "2024-01\n2024-02", "$(id)", "2024-01`id`",
    "２０２４-01",      # full-width digits: \d would accept them, [0-9] does not
    "2009-12",                           # before the first year Dukascopy is pulled for
    "2026-11", "2027-01",                # not started yet (no completed day to download)
])
def test_parse_months_rejects_anything_but_yyyy_mm(bad):
    with pytest.raises(ValueError):
        dy.parse_months(bad, today=TODAY)


def test_parse_months_caps_the_list_so_a_typo_cannot_book_a_full_refresh():
    many = ",".join(f"{y}-{m:02d}" for y in (2022, 2023) for m in range(1, 13)) + ",2024-01"
    with pytest.raises(ValueError, match="at most"):
        dy.parse_months(many, today=TODAY)
    assert len(dy.parse_months(many.rsplit(",", 1)[0], today=TODAY)) == 24


def test_months_cli_prints_years_and_normalised_months_for_the_plan_job():
    cli = str(ROOT / "scripts" / "dukascopy_months.py")
    r = subprocess.run([sys.executable, cli, "2026-06,2024-01,2024-06"], capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert r.stdout.splitlines() == ["years=[2024, 2026]", "months=2024-01,2024-06,2026-06"]
    bad = subprocess.run([sys.executable, cli, "2024-01;id"], capture_output=True, text=True, check=False)
    assert bad.returncode != 0 and "years=" not in bad.stdout


def _full_bars(ts: pd.DatetimeIndex, price: float, flag: str = "") -> pd.DataFrame:
    from goldbot.data.resample import BAR_COLUMNS
    df = pd.DataFrame({"ts_utc": ts})
    for c in ("bid_open", "bid_high", "bid_low", "bid_close"):
        df[c] = price
    for c in ("ask_open", "ask_high", "ask_low", "ask_close"):
        df[c] = price + 0.3
    df["tick_count"] = 1.0
    df["spread_mean"], df["spread_max"] = 0.3, 0.3
    df["visible_at"] = df["ts_utc"] + pd.Timedelta(minutes=1)
    df["dq_flag"] = flag
    return df[BAR_COLUMNS + ["dq_flag"]]


def _session(day: str, n: int = 300) -> pd.DatetimeIndex:
    return pd.date_range(f"{day} 10:00", periods=n, freq="1min", tz="UTC")


def _run_main(monkeypatch, tmp_path, argv: list[str], fetched: dict[str, pd.DataFrame], calls: list[str]) -> int:
    def fake_month_bars(start, end, tmp):
        calls.append(f"{start:%Y-%m}")
        return fetched.get(f"{start:%Y-%m}")
    monkeypatch.setattr(dy, "month_bars", fake_month_bars)
    monkeypatch.setattr(dy, "month_range", lambda year, today=None: _MONTH_RANGE(year, TODAY))
    monkeypatch.setattr(dy.sys, "argv", ["dukascopy_year.py", *argv, "--tmp", str(tmp_path / "raw"),
                                         "--report", str(tmp_path / "r.md")])
    return dy.main()


def test_main_with_months_refetches_exactly_those_months_and_keeps_the_rest(monkeypatch, tmp_path):
    # G1: 2024-01 lost 01-10..12 yet still cleared 15,000 bars, so no default run reaches it; only a month list does.
    # Here Jan/Feb/Mar are all "complete" and unflagged: without --months nothing would be downloaded.
    jan = pd.DatetimeIndex(_session("2024-01-09").append(_session("2024-01-15")))
    feb, mar = _session("2024-02-06"), _session("2024-03-05")
    published = _full_bars(pd.DatetimeIndex(jan.append(feb).append(mar)), 2000.0).assign(source="dukascopy")
    prev = tmp_path / "prev.parquet"
    published.to_parquet(prev, index=False)
    monkeypatch.setattr(dy, "MIN_BARS_PER_FULL_MONTH", 1)
    # the refetch brings the lost day back, plus a throttled (short) copy of 01-09 that must not win
    fresh_jan = _full_bars(pd.DatetimeIndex(_session("2024-01-09", 40).append(_session("2024-01-10"))), 1.0)
    calls: list[str] = []
    out = tmp_path / "out.parquet"
    rc = _run_main(monkeypatch, tmp_path, ["2024", "--out", str(out), "--existing", str(prev),
                                           "--months", "2024-01,2026-06"], {"2024-01": fresh_jan}, calls)
    assert rc == 0
    assert calls == ["2024-01"]                        # exactly the listed month of this year; 2026-06 is another job's
    got = pd.read_parquet(out)
    day = got["ts_utc"].dt.strftime("%Y-%m-%d")
    counts = day.value_counts().to_dict()
    assert counts == {"2024-01-09": 300, "2024-01-10": 300, "2024-01-15": 300, "2024-02-06": 300, "2024-03-05": 300}
    assert (got.loc[day == "2024-01-09", "bid_close"] == 2000.0).all()   # never-shrink: published day kept bar for bar
    assert (got.loc[day == "2024-01-10", "bid_close"] == 1.0).all()      # the recovered day is added
    assert (got.loc[day.isin(["2024-01-15", "2024-02-06", "2024-03-05"]), "bid_close"] == 2000.0).all()


def test_main_with_months_retries_a_flagged_month_and_repairs_imputed_days(monkeypatch, tmp_path):
    # G2: 2026-02 is bid-imputed on every bar; listing it is the targeted retry (retry_flagged for that month only)
    feb = _session("2026-02-03")
    published = _full_bars(feb, 2000.0, "warning:bid_imputed").assign(source="dukascopy")
    prev = tmp_path / "prev.parquet"
    published.to_parquet(prev, index=False)
    monkeypatch.setattr(dy, "MIN_BARS_PER_FULL_MONTH", 1)
    calls: list[str] = []
    out = tmp_path / "out.parquet"
    rc = _run_main(monkeypatch, tmp_path, ["2026", "--out", str(out), "--existing", str(prev), "--months", "2026-02"],
                   {"2026-02": _full_bars(feb, 1.0)}, calls)
    assert rc == 0 and calls == ["2026-02"]           # the open month 2026-10 is not touched by a targeted run
    got = pd.read_parquet(out)
    assert (got["bid_close"] == 1.0).all() and not got["dq_flag"].fillna("").str.contains("imputed").any()


def test_main_with_months_refuses_without_a_published_year(monkeypatch, tmp_path):
    # a targeted run keeps every unlisted month from the published file; with no file it would publish a year that
    # holds only the listed months, so it stops before any download
    calls: list[str] = []
    out = tmp_path / "out.parquet"
    rc = _run_main(monkeypatch, tmp_path, ["2024", "--out", str(out), "--existing", str(tmp_path / "absent.parquet"),
                                           "--months", "2024-01"], {}, calls)
    assert rc != 0 and calls == [] and not out.exists()


def test_main_with_months_refuses_when_no_listed_month_is_in_the_year(monkeypatch, tmp_path):
    prev = tmp_path / "prev.parquet"
    _full_bars(_session("2024-01-09"), 2000.0).assign(source="dukascopy").to_parquet(prev, index=False)
    calls: list[str] = []
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, ["2024", "--existing", str(prev), "--months", "2026-06"], {}, calls)
    assert calls == []


@pytest.mark.parametrize("flag", ["--refetch-all", "--retry-flagged"])
def test_months_cannot_be_combined_with_the_broad_modes(monkeypatch, tmp_path, flag):
    calls: list[str] = []
    with pytest.raises(SystemExit):
        _run_main(monkeypatch, tmp_path, ["2024", "--months", "2024-01", flag], {}, calls)
    assert calls == []


# --- the workflow: the input exists, reaches shell only through env:, and the plan step validates it -------------

def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


def _step(job: str, step_id: str | None = None, name_prefix: str | None = None) -> dict:
    for s in _wf()["jobs"][job]["steps"]:
        if (step_id and s.get("id") == step_id) or (name_prefix and str(s.get("name", "")).startswith(name_prefix)):
            return s
    raise AssertionError(f"step not found in {job}")


def test_workflow_has_a_months_input_and_never_interpolates_expressions_into_run():
    wf = _wf()
    on = wf["on"] if "on" in wf else wf[True]                  # PyYAML reads the bare key `on` as True
    assert on["workflow_dispatch"]["inputs"]["months"]["default"] == ""
    for job in wf["jobs"].values():
        for s in job["steps"]:
            assert "${{" not in s.get("run", ""), f"expression in run: {s.get('name') or s.get('id')}"
    build = _step("build-year", name_prefix="download and build")
    assert build["env"]["MONTHS"] == "${{ needs.plan.outputs.months }}"   # only the validated, normalised list
    assert '--months "$MONTHS"' in build["run"]


def _run_plan(tmp_path, **env: str) -> tuple[int, dict[str, str], str]:
    step = _step("plan", step_id="years")
    out = tmp_path / "gh_output"
    out.write_text("")
    base = {"EVENT_NAME": "workflow_dispatch", "FROM_YEAR": "2010", "TO_YEAR": "2026", "MONTHS": "",
            "FULL_REFRESH": "false", "RETRY_FLAGGED": "false"}
    assert set(base) <= set(step["env"])
    r = subprocess.run(["bash", "-e", "-c", step["run"]], cwd=ROOT, capture_output=True, text=True, check=False,
                       env={"PATH": os.environ["PATH"], "GITHUB_OUTPUT": str(out), **base, **env})
    lines = dict(line.split("=", 1) for line in out.read_text().splitlines() if "=" in line)
    return r.returncode, lines, r.stdout + r.stderr


def test_plan_step_turns_a_month_list_into_years_and_a_normalised_list(tmp_path):
    rc, got, log = _run_plan(tmp_path, MONTHS="2026-06,2024-01,2024-06")
    assert rc == 0, log
    assert got == {"years": "[2024, 2026]", "months": "2024-01,2024-06,2026-06"}


def test_plan_step_without_months_keeps_the_year_range(tmp_path):
    rc, got, log = _run_plan(tmp_path, FROM_YEAR="2024", TO_YEAR="2025")
    assert rc == 0, log
    assert got == {"years": "[2024, 2025]", "months": ""}


@pytest.mark.parametrize("env", [
    {"MONTHS": "2024-01;id"},
    {"MONTHS": "2024-01\n2024-02"},
    {"MONTHS": "2024-13"},
    {"MONTHS": "2024-01", "FULL_REFRESH": "true"},       # a targeted refetch and a full refresh are exclusive
    {"MONTHS": "2024-01", "RETRY_FLAGGED": "true"},      # listing a month already retries it
])
def test_plan_step_rejects_bad_or_conflicting_month_lists(tmp_path, env):
    rc, got, _ = _run_plan(tmp_path, **env)
    assert rc != 0 and "years" not in got


def _day_frame(days: list[dt.date]) -> pd.DataFrame:
    ts = [pd.Timestamp(d, tz="UTC") + pd.Timedelta(hours=10) for d in days]
    return pd.DataFrame({"ts_utc": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0})


def test_missing_weekdays_lists_only_empty_weekdays():
    have = _day_frame([dt.date(2020, 5, 25), dt.date(2020, 5, 29)])
    miss = dy.missing_weekdays(have, dt.date(2020, 5, 25), dt.date(2020, 6, 1))
    assert miss == [dt.date(2020, 5, 26), dt.date(2020, 5, 27), dt.date(2020, 5, 28)]


def test_download_side_refetches_days_a_salvage_pass_silently_dropped(monkeypatch, tmp_path):
    """dukascopy-node -fr skips a throttled (429) day without failing; those days must be asked for again one by one."""
    week = [dt.date(2020, 5, 25) + dt.timedelta(days=i) for i in range(5)]
    calls: list[tuple[dt.date, dt.date]] = []

    def fake_fetch(a, b, side, outdir, salvage=False):
        calls.append((a, b))
        if (b - a).days > 1:                       # the week call lost the 3 throttled days
            return _day_frame([week[0], week[4]]), ""
        return _day_frame([a]), ""                 # a single-day call succeeds

    monkeypatch.setattr(dy, "_fetch", fake_fetch)
    monkeypatch.setattr(dy.time, "sleep", lambda s: None)
    out = dy.download_side(dt.date(2020, 5, 25), dt.date(2020, 5, 30), "bid", tmp_path)
    assert out is not None
    assert sorted(out["ts_utc"].dt.date.unique()) == week
    one_day = [c for c in calls if (c[1] - c[0]).days == 1]
    assert one_day == [(w, w + dt.timedelta(days=1)) for w in week[1:4]]
