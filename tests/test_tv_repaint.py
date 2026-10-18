"""Repaint check: the CSV history is trusted only where it matches what the live alerts recorded for the same bars."""
import pandas as pd

from goldbot.data import tv_csv
from goldbot.data.store import Store
from goldbot.features import tradingview as tvf
from goldbot.research import tv_repaint
from goldbot.webhook.app import Alert, to_row
from goldbot.webhook.inbox import TABLE, TvInbox, sync_to_store

T0 = pd.Timestamp("2026-10-12T08:00:00Z")
NOW = pd.Timestamp("2026-10-18T00:00:00Z")


def seed(root, n, *, bad=0, version="2.02", shift=5):
    """n CSV bars (plot_0 = i, plot_1 = 2i); the live alerts agree on every bar except the first `bad`, whose plot_0
    is off by `shift`."""
    root.mkdir(parents=True, exist_ok=True)
    rows = ["time,open,high,low,close,A,B"]
    for i in range(n):
        rows.append(f"{int((T0 + pd.Timedelta(minutes=15 * i)).timestamp())},1,2,0.5,1.5,{i},{2 * i}")
    p = root / "e.csv"
    p.write_text("\n".join(rows) + "\n")
    store = Store(root / "data")
    tv_csv.import_tv_csv(p, store=store, indicator="K-Indi", version=version, tf="15", now=NOW)
    box = TvInbox(root / "tv.db")
    for i in range(n):
        bar = T0 + pd.Timedelta(minutes=15 * i)
        v = i + shift if i < bad else i
        a = Alert.model_validate({"indicator": "K-Indi", "version": version, "symbol": "XAUUSD", "tf": "15",
                                  "bar_time": bar.isoformat(), "fired_at": (bar + pd.Timedelta(minutes=15)).isoformat(),
                                  "secret": "x", "plots": {"plot_0": str(v), "plot_1": str(2 * i)}})
        box.put(to_row(a, (bar + pd.Timedelta(minutes=16)).to_pydatetime()))
    sync_to_store(box, store)
    return store


def check(store, version="2.02", **kw):
    return tv_repaint.validate(store, indicator="K-Indi", version=version, tf="15", **kw)


def flags(store, version="2.02"):
    df = store.read(TABLE, source=f"K-Indi@{version}")
    return set(df[df["origin"] == "csv"]["repaint_validated"])


def test_99_percent_agreement_over_200_bars_verifies_and_flips_the_flag(tmp_path):
    store = seed(tmp_path, 300, bad=3)                   # 297 / 300 = 99.0 %
    r = check(store)
    assert r.overlap_bars == 300 and r.match_pct == 99.0 and r.validated is True
    assert flags(store) == {True}
    assert "tv_label" not in tvf.tv_features(store, "K-Indi", "15").attrs


def test_below_99_percent_marks_repaints_and_leaves_the_flag_false(tmp_path):
    store = seed(tmp_path, 300, bad=4)
    r = check(store)
    assert r.match_pct < 99 and r.validated is False and "repaint" in r.reason
    assert flags(store) == {False}
    assert r.per_plot["plot_0"] < 100 and r.per_plot["plot_1"] == 100.0


def test_199_overlapping_bars_stay_unvalidated_however_good(tmp_path):
    store = seed(tmp_path, 199)
    r = check(store)
    assert r.match_pct == 100.0 and r.validated is False and "200" in r.reason and flags(store) == {False}


def test_a_failing_recheck_flips_the_flag_back(tmp_path):
    store = seed(tmp_path, 300)
    assert check(store).validated and flags(store) == {True}
    assert check(store, min_bars=301).validated is False and flags(store) == {False}


def test_a_new_version_starts_unvalidated_whatever_the_old_one_earned(tmp_path):
    store = seed(tmp_path / "a", 300)
    check(store)
    other = seed(tmp_path / "b", 300, version="2.03")
    assert flags(store) == {True} and flags(other, "2.03") == {False}


def test_tolerance_is_absolute(tmp_path):
    store = seed(tmp_path, 300, bad=300)                 # every plot_0 differs by 5
    assert check(store, abs_tol=4.9).validated is False
    assert check(store, abs_tol=5.0).validated is True


def test_no_overlap_reports_zero_and_changes_nothing(tmp_path):
    p = tmp_path / "e.csv"
    p.write_text(f"time,open,high,low,close,A\n{int(T0.timestamp())},1,2,1,1,3\n")
    store = Store(tmp_path / "data")
    tv_csv.import_tv_csv(p, store=store, indicator="K-Indi", version="2.02", tf="15", now=NOW)
    r = check(store)
    assert r.overlap_bars == 0 and r.validated is False and flags(store) == {False}


def test_cli_prints_the_report(tmp_path, capsys):
    seed(tmp_path, 300)
    rc = tv_repaint.main(["--indicator", "K-Indi", "--version", "2.02", "--tf", "15",
                          "--data-root", str(tmp_path / "data")])
    out = capsys.readouterr().out
    assert rc == 0 and "100.0%" in out and "VALIDATED" in out


def test_only_plots_the_alerts_carried_are_flagged_validated_and_the_report_states_the_limit(tmp_path):
    seed(tmp_path, 300)
    store = Store(tmp_path / "d2")
    tv_csv.import_tv_csv(tmp_path / "e.csv", store=store, indicator="K-Indi", version="2.02", tf="15", now=NOW)
    box = TvInbox(tmp_path / "tv2.db")                    # alerts that carry plot_0 only
    for i in range(300):
        bar = T0 + pd.Timedelta(minutes=15 * i)
        a = Alert.model_validate({"indicator": "K-Indi", "version": "2.02", "symbol": "XAUUSD", "tf": "15",
                                  "bar_time": bar.isoformat(), "fired_at": (bar + pd.Timedelta(minutes=15)).isoformat(),
                                  "secret": "x", "plots": {"plot_0": str(i)}})
        box.put(to_row(a, (bar + pd.Timedelta(minutes=16)).to_pydatetime()))
    sync_to_store(box, store)
    r = check(store)
    df = store.read(TABLE, source="K-Indi@2.02")
    by_plot = df[df["origin"] == "csv"].groupby("plot")["repaint_validated"].all().to_dict()
    assert by_plot == {"plot_0": True, "plot_1": False}
    assert "plot_1" in r.text() and "older than the overlap" in r.text()
