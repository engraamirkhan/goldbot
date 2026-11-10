"""goldbot.research.registry `stale_preregistrations` / `exempting_withdrawal`: the withdrawals and the trial reads
are indexed once per call, not scanned once per pre-registration. The scan per row was O(n^2) (5.4 M steps per
health pass on the brain, telegram CPU attribution 2026-11-10). The quant ruling of 2026-11-09 (item 3(b)) is
unchanged: the indexed answer is the same, row for row, as the scan's on a registry with every kind of withdrawal."""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from goldbot.research import registry
from goldbot.research.registry import (
    EXEMPT_WITHDRAWAL_RE,
    PREREGISTERED,
    STALE_PREREG_DAYS,
    WITHDRAWN,
    _row_when,
    evidence_ok,
    is_trial,
    refers_to,
    row_hash,
    row_ref,
    withdrawal_evidence,
)

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
SHA = "ab" * 32


def _scan_exempting_withdrawal(rows: list[dict[str, Any]], prereg: dict[str, Any]) -> dict[str, Any] | None:
    """The implementation before the index (origin/main 6785b282), kept here as the reference."""
    start = _row_when(prereg)
    stale_point = start + timedelta(days=STALE_PREREG_DAYS)
    unreadable = datetime.min.replace(tzinfo=timezone.utc)
    for w in rows:
        if w.get("status") != WITHDRAWN or not refers_to(w.get("withdraws"), prereg):
            continue
        if not EXEMPT_WITHDRAWAL_RE.match(str(w.get("reason") or "")) or not evidence_ok(withdrawal_evidence(w)):
            continue
        end = _row_when(w)
        if end == unreadable or end >= stale_point:
            continue
        if any(is_trial(t) and t.get("family") == prereg.get("family") and row_hash(t) == prereg.get("config_hash")
               and start <= _row_when(t) <= end for t in rows):
            continue
        return w
    return None


def _fixture(seed: int = 7, n_prereg: int = 260) -> list[dict[str, Any]]:
    """~800 rows: stale and fresh ad-hoc pre-registrations, linked results, and withdrawals of every kind (exempting,
    late, unstamped, bad reason, no evidence, a read between, a second withdrawal after a bad first, a stray ref)."""
    rnd = random.Random(seed)
    rows: list[dict[str, Any]] = []
    families = ["tsmom", "box", "learning_class"]
    for i in range(n_prereg):
        ts = NOW - timedelta(days=rnd.uniform(1, 40), minutes=i)
        fam, h = rnd.choice(families), f"{i:012x}"
        p = {"trial": len(rows) + 1, "ts": ts.isoformat(), "family": fam, "config_hash": h, "status": PREREGISTERED,
             "queue": False, "results": {}}
        rows.append(p)
        kind = rnd.choice(["none", "none", "result", "ok", "late", "unstamped", "bad_reason", "no_evidence",
                           "read_between", "bad_then_ok", "stray", "other_family_read"])
        w_ts = ts + timedelta(days=rnd.uniform(0.1, 6.5))

        def withdrawal(when: Any, reason: str = "unevaluable: no bars", evidence: Any = None,
                       ref: dict[str, Any] | None = None) -> dict[str, Any]:
            return {"trial": len(rows) + 1, "ts": when, "family": fam, "config_hash": h, "status": WITHDRAWN,
                    "withdraws": ref or row_ref(p), "reason": reason,
                    "evidence": [{"path": "out.json", "sha256": SHA}] if evidence is None else evidence,
                    "results": {}}
        if kind == "result":
            rows.append({"trial": len(rows) + 1, "ts": (ts + timedelta(hours=1)).isoformat(), "family": fam,
                         "config_hash": h, "status": "evaluated", "preregistration": row_ref(p), "results": {}})
        elif kind == "ok":
            rows.append(withdrawal(w_ts.isoformat()))
        elif kind == "late":
            rows.append(withdrawal((ts + timedelta(days=STALE_PREREG_DAYS, hours=1)).isoformat()))
        elif kind == "unstamped":
            rows.append(withdrawal("not a time"))
        elif kind == "bad_reason":
            rows.append(withdrawal(w_ts.isoformat(), reason="not a trial"))
        elif kind == "no_evidence":
            rows.append(withdrawal(w_ts.isoformat(), evidence=[]))
        elif kind == "read_between":
            rows.append({"trial": len(rows) + 1, "ts": (ts + timedelta(minutes=5)).isoformat(), "family": fam,
                         "config_hash": h, "status": "screened", "results": {}})
            rows.append(withdrawal(w_ts.isoformat()))
        elif kind == "bad_then_ok":
            rows.append(withdrawal(w_ts.isoformat(), reason="changed my mind"))
            rows.append(withdrawal((w_ts + timedelta(minutes=1)).isoformat(), reason="never formed: empty set"))
        elif kind == "stray":
            rows.append(withdrawal(w_ts.isoformat(), ref={"ts": p["ts"], "config_hash": "f" * 12}))
        elif kind == "other_family_read":
            rows.append({"trial": len(rows) + 1, "ts": (ts + timedelta(minutes=5)).isoformat(), "family": "other",
                         "config_hash": h, "status": "screened", "results": {}})
            rows.append(withdrawal(w_ts.isoformat()))
    while len(rows) < 800:
        rows.append({"trial": len(rows) + 1, "ts": (NOW - timedelta(days=rnd.uniform(1, 40))).isoformat(),
                     "family": rnd.choice(families), "config_hash": f"{rnd.getrandbits(48):012x}",
                     "status": "evaluated", "results": {}})
    rnd.shuffle(rows)
    return rows


def test_the_fixture_covers_exempted_and_counted_preregistrations():
    rows = _fixture()
    assert len(rows) >= 800
    preregs = [r for r in rows if r.get("status") == PREREGISTERED]
    got = [_scan_exempting_withdrawal(rows, p) for p in preregs]
    assert sum(w is not None for w in got) > 20 and sum(w is None for w in got) > 100


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_the_indexed_exempting_withdrawal_matches_the_scan_row_for_row(seed: int):
    rows = _fixture(seed)
    for p in (r for r in rows if r.get("status") == PREREGISTERED):
        assert registry.exempting_withdrawal(rows, p) is _scan_exempting_withdrawal(rows, p)


@pytest.mark.parametrize("seed", [1, 7, 42])
def test_stale_preregistrations_and_the_counts_are_unchanged_by_the_index(seed: int, monkeypatch: pytest.MonkeyPatch):
    rows = _fixture(seed)
    indexed = [id(r) for r in registry.stale_preregistrations(rows, NOW)]
    k_eff = registry.n_trials_effective(rows, now=NOW)
    monkeypatch.setattr(registry, "exempting_withdrawal", _scan_exempting_withdrawal)
    scanned = [id(r) for r in registry.stale_preregistrations(rows, NOW)]
    assert indexed == scanned and indexed
    assert k_eff == registry.n_trials_effective(rows, now=NOW)


def test_stale_preregistrations_reads_each_row_a_bounded_number_of_times(monkeypatch: pytest.MonkeyPatch):
    """Linear, not quadratic: the trial test runs at most twice per row, however many pre-registrations there are."""
    rows = _fixture(3, n_prereg=400)
    calls = 0
    real = registry.is_trial

    def counted(row: dict[str, Any]) -> bool:
        nonlocal calls
        calls += 1
        return real(row)
    monkeypatch.setattr(registry, "is_trial", counted)
    registry.stale_preregistrations(rows, NOW)
    assert calls <= 2 * len(rows)
