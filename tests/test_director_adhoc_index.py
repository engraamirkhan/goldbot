"""The ad-hoc pre-registration match in `director._Ledger` is indexed by `ts` (CPU attribution 2026-11-10, round 3:
the linear `next()` per trial was O(trials x ad hoc), 12.2 M id() calls at ~5,930 trials). The design rule it keeps:
"a pre-registration is used by its first linked trial only", matched by its link (timestamp and config hash), the
first unused ad-hoc row in registry order, exactly as the linear scan picked it. The old matcher is kept here as the
oracle; every fixture must give the same `adhoc_run` and `slot_use`."""
from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

import goldbot.ops.gates_phase as gates_phase
from goldbot.config import load_settings
from goldbot.research.director import _Ledger
from goldbot.research.registry import PREREGISTERED, is_trial


def _oracle_adhoc_run(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """The pre-index matcher, verbatim: a linear `next()` over the ad-hoc rows per linked trial."""
    adhoc = [r for r in rows if r.get("status") == PREREGISTERED and r.get("queue") is False]
    out: dict[int, dict[str, Any]] = {}
    for t in rows:
        link = t.get("preregistration") if is_trial(t) else None
        if not isinstance(link, dict) or link.get("ts") is None:
            continue
        p = next((a for a in adhoc if id(a) not in out and a.get("ts") == link.get("ts")
                  and link.get("config_hash") in (None, a.get("config_hash"))), None)
        if p is not None:
            out[id(p)] = t
    return out


def _adhoc(ts: Any, h: Any = "h1", **kw: Any) -> dict[str, Any]:
    return {"status": PREREGISTERED, "queue": False, "ts": ts, "config_hash": h, **kw}


def _trial(n: int, ts: Any, h: Any = "h1", tts: str = "2027-01-05T00:00:00+00:00") -> dict[str, Any]:
    return {"status": "evaluated", "trial": n, "family": "f", "ts": tts,
            "preregistration": {"ts": ts, "config_hash": h}}


def _same(rows: list[dict[str, Any]]) -> None:
    got = _Ledger(rows).adhoc_run
    want = _oracle_adhoc_run(rows)
    assert {k: id(v) for k, v in got.items()} == {k: id(v) for k, v in want.items()}


def test_duplicate_ts_rows_are_used_in_registry_order_one_trial_each() -> None:
    a1, a2, a3 = _adhoc("2027-01-01T00:00:00"), _adhoc("2027-01-01T00:00:00"), _adhoc("2027-01-01T00:00:00", "h2")
    t1, t2, t3, t4 = (_trial(1, a1["ts"]), _trial(2, a1["ts"]), _trial(3, a1["ts"], None),
                      _trial(4, a1["ts"], None))
    rows = [a1, a2, a3, t1, t2, t3, t4]
    _same(rows)
    run = _Ledger(rows).adhoc_run
    assert run[id(a1)] is t1 and run[id(a2)] is t2 and run[id(a3)] is t3 and len(run) == 3   # t4: nothing left


def test_missing_none_and_mismatched_links_match_like_the_linear_scan() -> None:
    a1, a2 = _adhoc("2027-01-01T00:00:00"), _adhoc(None)
    rows = [a1, a2, _trial(1, "2027-01-02T00:00:00"), _trial(2, a1["ts"], "other"),
            {"status": "evaluated", "trial": 3, "preregistration": {"config_hash": "h1"}},
            {"status": "evaluated", "trial": 4, "preregistration": "not a dict"},
            {"status": PREREGISTERED, "preregistration": {"ts": a1["ts"]}},        # not a trial: never links
            _trial(5, a1["ts"])]
    _same(rows)
    assert _Ledger(rows).adhoc_run[id(a1)]["trial"] == 5


def test_unhashable_nan_and_numeric_ts_values_match_like_the_linear_scan() -> None:
    nan = float("nan")
    rows = [_adhoc(["2027", "01"]), _adhoc({"d": 1}), _adhoc(nan), _adhoc(1), _adhoc(True), _adhoc(1.0), _adhoc(nan),
            _trial(1, ["2027", "01"]), _trial(2, {"d": 1}), _trial(3, nan), _trial(4, True), _trial(5, 1),
            _trial(6, 1.0), _trial(7, 1), _trial(8, nan)]
    _same(rows)
    # NaN never equals itself by ==, so neither a NaN link nor a NaN row is matched, the same object or not
    shared = _adhoc(nan)
    _same([shared, {"status": "evaluated", "trial": 9, "preregistration": {"ts": shared["ts"]}}])


def test_slot_use_is_unchanged_by_the_index() -> None:
    slot = {"status": PREREGISTERED, "ts": "2026-12-01T00:00:00+00:00", "target_quarter": "2027Q1",
            "slot": {"kind": "survivor", "k": 1, "parent": {"ts": "x"}}, "config_hash": "s"}
    a1 = _adhoc("2027-01-02T00:00:00+00:00", slot_ref={"ts": slot["ts"], "config_hash": "s"})
    a2 = _adhoc("2027-01-02T00:00:00+00:00", slot_ref={"ts": slot["ts"], "config_hash": "s"})
    t1, t2 = _trial(1, a1["ts"]), _trial(2, a1["ts"])
    led = _Ledger([slot, a1, a2, t1, t2])
    assert led.adhoc_run[id(a1)] is t1 and led.adhoc_run[id(a2)] is t2
    assert led.slot_use.get(id(slot)) is t1                      # the first trial uses the slot, exactly once


def test_random_registries_match_the_linear_oracle() -> None:
    rng = random.Random(20261110)
    stamps: list[Any] = ["2027-01-01T00:00:00", "2027-01-02T00:00:00", "2027-01-03T00:00:00", None, 7, ["x"]]
    hashes: list[Any] = ["h1", "h2", None]
    for _ in range(300):
        rows: list[dict[str, Any]] = []
        for n in range(rng.randint(0, 40)):
            if rng.random() < 0.45:
                rows.append(_adhoc(copy.deepcopy(rng.choice(stamps)), rng.choice(hashes),
                                   queue=rng.choice([False, False, True])))
            else:
                rows.append(_trial(n, copy.deepcopy(rng.choice(stamps)), rng.choice(hashes)))
        _same(rows)


class _CountingRow(dict[str, Any]):
    """A registry row that counts reads of its `ts`: the linear scan read one per ad-hoc row per linked trial."""
    reads = 0

    def get(self, key: Any, default: Any = None) -> Any:
        if key == "ts":
            _CountingRow.reads += 1
        return super().get(key, default)


def test_matching_reads_each_row_a_bounded_number_of_times_not_once_per_trial() -> None:
    n = 3000                         # the linear scan read ~n^2 / 2 = 4.5 M stamps here
    stamps = [f"2027-01-01T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}" for i in range(n)]
    rows: list[dict[str, Any]] = [_CountingRow(_adhoc(s)) for s in stamps]
    rows += [_CountingRow(_trial(i, s)) for i, s in reversed(list(enumerate(stamps)))]   # latest first: a full scan each
    _CountingRow.reads = 0
    led = _Ledger(rows)
    assert len(led.adhoc_run) == n
    assert _CountingRow.reads < 20 * n


def test_evaluate_gates_counts_the_trials_once_per_evaluation(monkeypatch: pytest.MonkeyPatch,
                                                               tmp_path: Path) -> None:
    """`n_trials_effective` builds the registry ledger; `evaluate_gates` computed it twice (best_backtest_trial and
    the gate-1 DSR). It is computed once per evaluation and passed down, so both read the same K."""
    seen: list[int] = []
    real = gates_phase.n_trials_effective

    def counting(rows: list[dict[str, Any]], **kw: Any) -> int:
        seen.append(len(rows))
        return real(rows, **kw)

    monkeypatch.setattr(gates_phase, "n_trials_effective", counting)
    reg = tmp_path / "trials.jsonl"
    reg.write_text('{"status": "evaluated", "trial": 1, "family": "f", "ts": "2027-01-01T00:00:00+00:00"}\n')
    gates_phase.evaluate_gates(tmp_path, load_settings(), pd.Timestamp("2027-01-10", tz="UTC"), trials_path=reg)
    assert seen == [1]


def test_best_backtest_trial_reads_a_passed_trial_count_without_recounting(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(rows: list[dict[str, Any]], **kw: Any) -> int:
        raise AssertionError("recounted")

    monkeypatch.setattr(gates_phase, "n_trials_effective", refuse)
    assert gates_phase.best_backtest_trial([], k=3) is None
