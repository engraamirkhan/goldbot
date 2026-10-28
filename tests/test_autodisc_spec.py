"""Autodisc foundation: spec canonical hash, search ledger (hash chain, logged-before-result), explore-window guard."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from goldbot.research.autodisc import (
    EXPLORE_CUTOFF,
    ExitTemplate,
    ExploreFrame,
    Group,
    Leaf,
    LedgerError,
    LedgerTamperError,
    SearchLedger,
    StrategySpec,
    WindowViolation,
    canonical_json,
    canonicalise,
    guard_times,
    spec_hash,
    truncate_to_explore,
)

A = Leaf(kind="oscillator", name="rsi_bucket", params={"low": 30, "high": 70})
B = Leaf(kind="regime", name="rv_quantile", params={"q": 0.5})
C = Leaf(kind="time", name="minutes_since_open", params={"max": 60})
EXIT = ExitTemplate(stop="atr_1.5", target="2R")


def mk(entry: Leaf | Group = A, **kw: object) -> StrategySpec:
    args: dict[str, object] = {"timeframe": "1h", "side_rule": "long", "entry": entry, "exit": EXIT}
    args.update(kw)
    return StrategySpec(**args)  # type: ignore[arg-type]


# ---- spec ----------------------------------------------------------------------------------------------------

def test_hash_stable_and_hex() -> None:
    assert spec_hash(mk()) == spec_hash(mk())
    assert len(spec_hash(mk())) == 64


def test_commutative_order_and_duplicates_share_a_hash() -> None:
    s1 = mk(Group(op="and", children=(A, B)))
    s2 = mk(Group(op="and", children=(B, A, A)))
    s3 = mk(Group(op="and", children=(Group(op="and", children=(B,)), A)))
    assert spec_hash(s1) == spec_hash(s2) == spec_hash(s3)
    assert canonical_json(s1) == canonical_json(s2)


def test_param_spelling_canonicalised() -> None:
    a2 = Leaf(kind="oscillator", name="rsi_bucket", params={"high": 70.0, "low": 30})
    assert spec_hash(mk(a2)) == spec_hash(mk(A))


def test_single_child_group_collapses() -> None:
    assert spec_hash(mk(Group(op="or", children=(A,)))) == spec_hash(mk(A))


def test_different_rules_differ() -> None:
    base = spec_hash(mk())
    assert spec_hash(mk(B)) != base
    assert spec_hash(mk(side_rule="short")) != base
    assert spec_hash(mk(timeframe="4h")) != base
    assert spec_hash(mk(Group(op="and", children=(A, B)))) != spec_hash(mk(Group(op="or", children=(A, B))))
    assert spec_hash(mk(exit=ExitTemplate(stop="atr_1.5", target="1R"))) != base


def test_canonicalise_idempotent() -> None:
    s = mk(Group(op="or", children=(Group(op="or", children=(C, A)), B)))
    assert canonicalise(canonicalise(s)) == canonicalise(s)


def test_validation() -> None:
    with pytest.raises(ValidationError):
        ExitTemplate(stop="atr_1.0", target="time")                       # time_bars missing
    with pytest.raises(ValidationError):
        ExitTemplate(stop="atr_1.0", target="1R", time_bars=4)
    with pytest.raises(ValidationError):
        mk(side_rule="with")                                              # needs side_primitive
    with pytest.raises(ValidationError):
        mk(side_primitive=A)                                              # forbidden for long
    leaves5 = tuple(Leaf(kind="time", name="t", params={"i": i}) for i in range(5))
    with pytest.raises(ValidationError):
        mk(Group(op="and", children=leaves5))                             # > 4 leaves
    deep = Group(op="and", children=(Group(op="or", children=(Group(op="and", children=(A, B)), C)), A))
    with pytest.raises(ValidationError):
        mk(deep)                                                          # depth 4
    mk(Group(op="and", children=(Group(op="or", children=(A, B)), C)))   # depth 3 fine
    with pytest.raises(ValidationError):
        Leaf(kind="oscillator", name="x", surprise=1)  # type: ignore[call-arg]


def test_json_roundtrip() -> None:
    s = canonicalise(mk(Group(op="and", children=(A, B)), filter=C, side_rule="with", side_primitive=A))
    assert StrategySpec.model_validate_json(s.model_dump_json()) == s


# ---- ledger --------------------------------------------------------------------------------------------------

def _ledger(tmp_path: Path) -> SearchLedger:
    return SearchLedger(tmp_path / "state" / "discovery.db")


def test_spec_logged_before_result(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    s = mk()
    h = spec_hash(s)
    assert not led.is_logged(h)
    with pytest.raises(LedgerError):
        led.record_result(h, {"t": 1.0})
    with pytest.raises(LedgerError):
        led.require_logged(h)
    e = led.log_spec(s, "combine", "1")
    r = led.record_result(h, {"t": 1.0})
    assert r is not None and r.seq > e.seq
    assert led.result(h) == {"t": 1.0}
    assert led.get_spec(h) == canonicalise(s)
    led.require_logged(h)
    assert led.verify() == 2


def test_k_search_counts_distinct_hashes_and_null_separately(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    s1, s2 = mk(Group(op="and", children=(A, B))), mk(Group(op="and", children=(B, A)))     # one rule, two spellings
    e1 = led.log_spec(s1, "combine", "1")
    e2 = led.log_spec(s2, "combine", "1")
    assert e1.seq == e2.seq and led.k_search() == 1
    led.log_spec(mk(C), "combine", "1", parents=[spec_hash(s1)])
    assert led.k_search() == 2 and led.k_null() == 0
    led.log_spec(s1, "g0", "1", null=True)
    assert led.k_search() == 2 and led.k_null() == 1
    with pytest.raises(LedgerError):
        led.record_result(spec_hash(mk(B)), {}, null=False)
    # re-evaluating adds nothing: the first result stands
    h = spec_hash(s1)
    assert led.record_result(h, {"v": 1}) is not None
    assert led.record_result(h, {"v": 2}) is None
    assert led.result(h) == {"v": 1} and led.k_search() == 2
    assert led.verify() == len(led.entries())


def test_persists_across_instances(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    led.log_spec(mk(), "combine", "1")
    again = _ledger(tmp_path)
    assert again.k_search() == 1 and again.verify() == 1
    assert again.head() == led.head()


def test_empty_ledger_verifies(tmp_path: Path) -> None:
    assert _ledger(tmp_path).verify() == 0


def _tamper(led: SearchLedger, sql: str, args: tuple[object, ...] = ()) -> None:
    con = sqlite3.connect(led.path)
    con.execute(sql, args)
    con.commit()
    con.close()


def _filled(tmp_path: Path) -> SearchLedger:
    led = _ledger(tmp_path)
    for leaf in (A, B, C):
        s = mk(leaf)
        led.log_spec(s, "combine", "1")
        led.record_result(spec_hash(s), {"t": 2.5})
    assert led.verify() == 6
    return led


def test_tamper_payload_detected(tmp_path: Path) -> None:
    led = _filled(tmp_path)
    _tamper(led, "UPDATE entries SET payload = replace(payload, '2.5', '9.9') WHERE seq = 2")
    with pytest.raises(LedgerTamperError, match="entry 2"):
        led.verify()


def test_tamper_deleted_row_detected(tmp_path: Path) -> None:
    led = _filled(tmp_path)
    _tamper(led, "DELETE FROM entries WHERE seq = 3")
    with pytest.raises(LedgerTamperError):
        led.verify()


def test_tamper_rewritten_timestamp_and_rechained_row_detected(tmp_path: Path) -> None:
    led = _filled(tmp_path)
    _tamper(led, "UPDATE entries SET written_utc = '2000-01-01T00:00:00+00:00' WHERE seq = 4")
    with pytest.raises(LedgerTamperError, match="entry 4"):
        led.verify()


def test_tamper_swapped_order_detected(tmp_path: Path) -> None:
    led = _filled(tmp_path)
    _tamper(led, "UPDATE entries SET seq = 99 WHERE seq = 1")
    with pytest.raises(LedgerTamperError):
        led.verify()


def test_tail_truncation_caught_by_saved_head(tmp_path: Path) -> None:
    led = _filled(tmp_path)
    saved = led.head()
    _tamper(led, "DELETE FROM entries WHERE seq = 6")
    assert led.verify() == 5                       # chain alone is intact ...
    assert led.head() != saved                     # ... the saved head exposes the cut


def test_wal_mode(tmp_path: Path) -> None:
    led = _ledger(tmp_path)
    con = sqlite3.connect(led.path)
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()


# ---- windows -------------------------------------------------------------------------------------------------

def _bars(start: str, n: int, freq: str = "15min") -> pd.DataFrame:
    idx = pd.date_range(start, periods=n, freq=freq, tz="UTC")
    return pd.DataFrame({"close": np.linspace(1000.0, 1100.0, n)}, index=idx)


def test_cutoff_constant() -> None:
    from goldbot.research.autodisc import windows
    assert EXPLORE_CUTOFF == pd.Timestamp("2018-01-01", tz="UTC")
    assert windows._CUTOFF_NS == EXPLORE_CUTOFF.value
    assert windows._START_NS == windows.EXPLORE_START.value


def test_2018_bar_refused() -> None:
    with pytest.raises(WindowViolation):
        ExploreFrame.from_frame(_bars("2017-12-31 23:30", 4))     # 23:30 .. 00:15 (2018)
    with pytest.raises(WindowViolation):
        guard_times(pd.DatetimeIndex([pd.Timestamp("2018-01-01 00:00", tz="UTC")]))
    with pytest.raises(WindowViolation):
        guard_times(pd.DatetimeIndex(["2026-01-01"]))             # naive is read as UTC
    with pytest.raises(WindowViolation):
        ExploreFrame(_bars("2019-05-01", 3))


def test_last_explore_bar_allowed() -> None:
    f = ExploreFrame.from_frame(_bars("2017-12-31 23:00", 4)[:-1])
    assert len(f) == 3


def test_time_col_and_datetime_columns_guarded() -> None:
    df = _bars("2017-12-29", 3, "1h").reset_index(names="ts")
    df["t1"] = df["ts"] + pd.Timedelta(days=10)                  # label end time spills into 2018
    with pytest.raises(WindowViolation, match="t1"):
        ExploreFrame.from_frame(df, time_col="ts")
    with pytest.raises(WindowViolation):
        ExploreFrame.from_frame(df.drop(columns="t1").assign(ts=lambda d: d["ts"] + pd.Timedelta(days=10)),
                                time_col="ts")


def test_truncate_then_guard_digest_equal() -> None:
    wide = pd.concat([_bars("2009-12-31 23:00", 3, "1h"), _bars("2017-12-31 22:00", 6, "1h")])
    with pytest.raises(WindowViolation):
        ExploreFrame.from_frame(wide)
    cut = truncate_to_explore(wide)
    assert cut.index.min() >= pd.Timestamp("2010-01-01", tz="UTC") and cut.index.max() < EXPLORE_CUTOFF
    assert len(cut) == 4          # 2 of the 2010 frame (01-01 00:00 and 01:00) + 2 of the 2017 frame
    a, b = ExploreFrame.from_frame(cut), ExploreFrame.from_frame(cut.copy())
    assert a.digest == b.digest
    assert ExploreFrame.from_frame(cut.iloc[:-1]).digest != a.digest


def test_unit_independent_index() -> None:
    idx = pd.DatetimeIndex(["2017-12-31 23:45"], tz="UTC").as_unit("s")
    guard_times(idx)
    with pytest.raises(WindowViolation):
        guard_times(pd.DatetimeIndex(["2018-01-01"], tz="UTC").as_unit("ms"))
