"""BACKLOG 60: the owner's 90 s approval window never trusts the wall clock alone. `created + window_s > time.time()`
was the `now - ts > limit` shape BACKLOG 58 closed for heartbeats: a backward clock step of S seconds stretched the
window by S, and a pending file stamped in the future (a restored backup, a bad write) stayed approvable for longer
than 90 s. Now a proposal stamped more than CLOCK_AHEAD_TOLERANCE_S ahead, or with a `created` that is not a finite
number, counts as expired (fail closed), and the engine's ApprovalCenter holds a monotonic deadline per proposal it
raised. The deadline is in memory only: after a restart the engine holds no proposal (the pending entries died with
the process), so an on-disk proposal can never be executed and falls back to the wall-clock rule plus the guard."""
from __future__ import annotations

import json
import time
from typing import Any

import pandas as pd
import pytest

from goldbot.data.timeutil import CLOCK_AHEAD_TOLERANCE_S
from goldbot.engine import Engine, EngineConfig
from goldbot.execution.paper import PaperBroker
from goldbot.risk import Intent
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal, window_over
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import proposals_line
from tests.test_approval_bus import _p

TWO_DAYS = 2 * 86_400.0


class _Mono:
    """A monotonic clock the test moves by hand (the wall clock stays real, so `created` checks see no time pass)."""

    def __init__(self) -> None:
        self.t = 1_000.0

    def __call__(self) -> float:
        return self.t


def _raw_pending(bus: ApprovalBus, pid: str, **over: Any) -> None:
    """A pending file as a bad write or a restored backup leaves it (json.dumps writes NaN as a bare token)."""
    row = {**json.loads(_p(pid).model_dump_json()), **over}
    (bus.pending_dir / f"{pid}.json").write_text(json.dumps(row))


# ------------------------------------------------------------------------------- the wall-clock rule and its guard
def test_a_proposal_stamped_more_than_the_tolerance_ahead_is_expired():
    now = time.time()
    assert _p("a", created=now + 10).expired
    assert _p("b", created=now + TWO_DAYS).expired
    assert window_over(now + CLOCK_AHEAD_TOLERANCE_S + 1, 90, now)


def test_a_proposal_a_couple_of_seconds_ahead_is_still_open():
    """Ordinary skew between the engine and the dashboard host is tolerated, as for heartbeats."""
    assert not _p("a", created=time.time() + 2).expired
    assert not window_over(1_000.0 + 2, 90, 1_000.0)


@pytest.mark.parametrize("created", [float("nan"), float("inf"), float("-inf")])
def test_a_created_that_is_not_a_finite_number_is_expired(created):
    """NaN compared False with every limit and so read as open forever."""
    assert _p("a", created=created).expired
    assert window_over(created, 90, time.time())


@pytest.mark.parametrize("created", ["soon", "1760000000", True, None, float("nan")])
def test_window_over_fails_closed_on_any_created_that_is_not_a_number(created):
    assert window_over(created, 90, time.time())


@pytest.mark.parametrize("created", ["soon", float("nan"), "two_days_ahead"])
def test_a_bad_or_future_pending_file_is_never_listed_or_approvable(tmp_path, created):
    """A string, NaN or a stamp two days ahead (a backup restored from a box with a fast clock): the dashboard and
    Telegram never list it and a decision on it is refused."""
    bus = ApprovalBus(tmp_path)
    _raw_pending(bus, "x", created=time.time() + TWO_DAYS if created == "two_days_ahead" else created)
    assert bus.pending() == []
    with pytest.raises((KeyError, ValueError)):
        bus.submit("x", True, None, by="dashboard:o")
    assert not (bus.decisions_dir / "x.json").exists()


def test_a_future_stamped_proposal_held_by_the_engine_expires_and_is_never_approved(tmp_path):
    decided: list[Proposal] = []
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path), on_decision=decided.append)
    center.propose(_p("f", created=time.time() + TWO_DAYS))
    assert center.decide("f", 1, approve=True).outcome == Outcome.EXPIRED
    assert [p.outcome for p in decided] == [Outcome.EXPIRED]
    assert ApprovalBus(tmp_path).outcome("f") == "EXPIRED_UNAPPROVED"


def test_the_digest_counts_a_future_stamped_pending_proposal_as_expired(tmp_path):
    now = pd.Timestamp.now("UTC")
    bus = ApprovalBus(tmp_path)
    _raw_pending(bus, "f", created=now.timestamp() + 60)
    _raw_pending(bus, "o", created=now.timestamp() - 10)
    day0 = now.floor("D")
    line = proposals_line(tmp_path, day0, day0 + pd.Timedelta(days=1), now)[0]
    assert "Proposals 2" in line and "1 open" in line


# ------------------------------------------------------------------------------- the engine's monotonic deadline
def test_a_backward_wall_clock_step_does_not_stretch_the_window(tmp_path):
    """The wall clock stepped back by S during the window: by the wall clock almost no time has passed and the bus
    takes the click, but 91 s really went by. The engine refuses it, EXPIRED_UNAPPROVED."""
    mono, decided = _Mono(), list[Proposal]()
    engine = ApprovalCenter({1}, bus=ApprovalBus(tmp_path), on_decision=decided.append, monotonic=mono)
    engine.propose(_p("a"))
    mono.t += 91
    ApprovalBus(tmp_path).submit("a", True, None, by="dashboard:o")       # the wall clock says still open
    assert [p.outcome for p in engine.poll_bus()] == [Outcome.EXPIRED]
    assert decided[0].outcome == Outcome.EXPIRED and decided[0].decided_channel is None
    assert ApprovalBus(tmp_path).outcome("a") == "EXPIRED_UNAPPROVED"


def test_the_monotonic_deadline_also_refuses_an_in_process_click_and_the_sweep_expires_it():
    mono = _Mono()
    center = ApprovalCenter({1}, monotonic=mono)
    center.propose(_p("a"))
    center.propose(_p("b"))
    mono.t += 91
    assert center.decide("a", 1, approve=True).outcome == Outcome.EXPIRED
    assert [p.outcome for p in center.sweep_expired()] == [Outcome.EXPIRED] and center.pending == {}


def test_a_normal_approval_within_the_window_still_goes_through(tmp_path):
    mono = _Mono()
    engine = ApprovalCenter({1}, bus=ApprovalBus(tmp_path), monotonic=mono)
    engine.propose(_p("a"))
    engine.propose(_p("b"))
    mono.t += 89
    ApprovalBus(tmp_path).submit("a", True, None, by="dashboard:o")
    assert [p.outcome for p in engine.poll_bus()] == [Outcome.APPROVED]
    assert engine.decide("b", 1, approve=True).outcome == Outcome.APPROVED
    assert engine.sweep_expired() == []


def test_the_engine_never_executes_an_approval_after_its_monotonic_deadline(tmp_path):
    """End to end through the engine's decision handler: no order is sent, whatever the wall clock says."""
    mono = _Mono()
    spec = SPECIALISTS["session_open"]()
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path), monotonic=mono)
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)),
                 PaperBroker(equity=10_000), [spec], {}, center=center)
    sent: list[Any] = []
    eng._execute = lambda *a, **k: sent.append(a)          # type: ignore[method-assign]
    p = _p("icm-demo-a")
    eng.pending[p.proposal_id] = (Intent(agent_id=spec.agent_id, side=1, p=0.6, target_atr=1.5, stop_atr=1.0,
                                         atr_usd=4.0, cost_atr=0.1, multiplier=1.0, price=2400.0), p, spec)
    center.propose(p)
    mono.t += 120
    ApprovalBus(tmp_path).submit(p.proposal_id, True, None, by="telegram:o")
    center.poll_bus()
    assert sent == [] and eng.pending == {}
    assert center.bus is not None and center.bus.outcome(p.proposal_id) == "EXPIRED_UNAPPROVED"


# ------------------------------------------------------------------------------- restart
def test_after_a_restart_an_on_disk_proposal_is_never_executed_and_falls_back_to_the_wall_clock(tmp_path):
    """A monotonic deadline cannot survive a restart. The restarted engine holds no proposal, so a click on one left
    on disk is never applied; the bus and the outbox use the wall-clock rule with the future-stamp guard, and one
    stamped two days ahead is archived at the start, never listed."""
    bus = ApprovalBus(tmp_path)
    old = ApprovalCenter({1}, bus=bus)
    old.propose(_p("icm-demo-fresh"))
    old.propose(_p("icm-demo-ahead", created=time.time() + TWO_DAYS))
    spec = SPECIALISTS["session_open"]()
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))               # the restarted process: nothing in memory
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)),
                 PaperBroker(equity=10_000), [spec], {}, center=center)
    sent: list[Any] = []
    eng._execute = lambda *a, **k: sent.append(a)          # type: ignore[method-assign]
    assert not (bus.pending_dir / "icm-demo-ahead.json").exists()          # archived at the start
    assert [p.proposal_id for p in bus.pending()] == ["icm-demo-fresh"]   # still inside its wall-clock window
    bus.submit("icm-demo-fresh", True, None, by="dashboard:o")
    assert center.poll_bus() == [] and sent == []                         # dropped: fail closed
    f = bus.pending_dir / "icm-demo-fresh.json"                           # its wall-clock window runs out
    f.write_text(json.dumps({**json.loads(f.read_text()), "created": time.time() - 120}))
    assert bus.pending() == [] and center.poll_bus() == [] and sent == []
