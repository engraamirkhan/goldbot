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
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from goldbot.data.synthetic import synthetic_ticks
from goldbot.data.timeutil import CLOCK_AHEAD_TOLERANCE_S
from goldbot.engine import ConstantModel, Engine, EngineConfig
from goldbot.execution.broker import Tick
from goldbot.execution.paper import PaperBroker
from goldbot.risk import Intent
from goldbot.specialists import SPECIALISTS
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal, window_over
from goldbot.telegram.bus import ApprovalBus
from goldbot.telegram.digest import proposals_line
from goldbot.telegram.outbox import Outbox
from tests.cards import submit_shown
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
        submit_shown(bus, "x", True, None, by="dashboard:o")
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
    submit_shown(ApprovalBus(tmp_path), "a", True, None, by="dashboard:o")       # the wall clock says still open
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
    submit_shown(ApprovalBus(tmp_path), "a", True, None, by="dashboard:o")
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
    submit_shown(ApprovalBus(tmp_path), p.proposal_id, True, None, by="telegram:o")
    center.poll_bus()
    assert sent == [] and eng.pending == {}
    assert center.bus is not None and center.bus.outcome(p.proposal_id) == "EXPIRED_UNAPPROVED"


# ------------------------------------------------------------------------------- restart
def test_after_a_restart_an_on_disk_proposal_is_never_executed_and_falls_back_to_the_wall_clock(tmp_path):
    """A monotonic deadline cannot survive a restart. The restarted engine holds no proposal, so it archives every
    pending file of its account at the start, inside its window or not (trading-safety F60-1): none is listed, a click
    on one is refused, and one stamped two days ahead goes the same way. Another account's proposal is left alone, even one whose id starts with this account's."""
    bus = ApprovalBus(tmp_path)
    old = ApprovalCenter({1}, bus=bus)
    old.propose(_p("icm-demo-fresh"))
    old.propose(_p("icm-demo-ahead", created=time.time() + TWO_DAYS))
    old.propose(_p("other-acct-x"))
    old.propose(_p("icm-demo-2-x").model_copy(update={"account_id": "icm-demo-2"}))    # matches the glob, not ours
    spec = SPECIALISTS["session_open"]()
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))               # the restarted process: nothing in memory
    eng = Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)),
                 PaperBroker(equity=10_000), [spec], {}, center=center)
    sent: list[Any] = []
    eng._execute = lambda *a, **k: sent.append(a)          # type: ignore[method-assign]
    for pid in ("icm-demo-fresh", "icm-demo-ahead"):
        assert not (bus.pending_dir / f"{pid}.json").exists()             # archived at the start, window open or not
        assert (bus.done_dir / f"{pid}.json").exists() and bus.outcome(pid) is None   # decided by nobody
    assert [p.proposal_id for p in bus.pending()] == ["icm-demo-2-x", "other-acct-x"]
    with pytest.raises(KeyError):
        submit_shown(bus, "icm-demo-fresh", True, None, by="dashboard:o")
    assert center.poll_bus() == [] and sent == []


def test_a_click_made_before_a_restart_is_never_applied_to_the_same_proposal_raised_again(tmp_path):
    """F60-1: the proposal id depends only on account, agent and bar close, and the restarted engine can re-decide the
    last close. The owner clicked X, the engine died before polling and restarted inside the window: the start
    archives X and deletes its decision, and the owner is told the tap was not applied (F60 re-verify finding 3).
    Even raised again directly on the center (the engine itself never re-raises it, see the integration test
    below), the old click is gone."""
    bus = ApprovalBus(tmp_path)
    ApprovalCenter({1}, bus=bus).propose(_p("icm-demo-X"))
    submit_shown(bus, "icm-demo-X", True, None, by="dashboard:o")                # clicked; the engine dies before polling
    spec = SPECIALISTS["session_open"]()
    center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
    Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path)),
           PaperBroker(equity=10_000), [spec], {}, center=center)
    assert not (bus.decisions_dir / "icm-demo-X.json").exists()
    assert _notices(tmp_path) and "icm-demo-X" in _notices(tmp_path)[-1] and "not applied" in _notices(tmp_path)[-1]
    center.propose(_p("icm-demo-X"))
    assert center.poll_bus() == [] and "icm-demo-X" in center.pending


def _notices(state_dir: Path) -> list[str]:
    """The owner notices queued for Telegram (state/agent_runs.jsonl rows with status "notice")."""
    f = state_dir / "agent_runs.jsonl"
    rows = [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    return [r["detail"] for r in rows if r.get("status") == "notice"]


def test_a_decision_bound_to_an_earlier_card_with_the_same_id_is_discarded_and_the_owner_is_told(tmp_path):
    """F60 re-verify finding 2: `submit` binds the decision to the card it read (`card_created`), and `poll_bus`
    applies it only to that card. A click on an earlier card with the same id is discarded, never applied, whatever
    either clock says, and the owner is told; the card now shown stays decidable."""
    bus = ApprovalBus(tmp_path)
    bus.publish(_p("icm-demo-X", created=time.time() - 30))               # the earlier card
    submit_shown(bus, "icm-demo-X", True, None, by="telegram")
    center = ApprovalCenter({1}, bus=bus)
    center.propose(_p("icm-demo-X", created=time.time() - 60))           # raised again, even stamped earlier
    assert center.poll_bus() == [] and "icm-demo-X" in center.pending
    assert not (bus.decisions_dir / "icm-demo-X.json").exists()
    assert "icm-demo-X" in _notices(tmp_path)[-1] and "not applied" in _notices(tmp_path)[-1]
    submit_shown(bus, "icm-demo-X", False, "news", by="telegram")                # the owner's click on the card shown
    assert [p.outcome for p in center.poll_bus()] == [Outcome.REJECTED]


def test_submit_binds_the_decision_to_the_card_it_read(tmp_path):
    bus = ApprovalBus(tmp_path)
    p = _p("icm-demo-X", created=time.time() - 3)
    bus.publish(p)
    assert submit_shown(bus, "icm-demo-X", True, None, by="dashboard").card_created == p.created


@pytest.mark.parametrize("offset", [-1.0, 0.0, 1.0])
def test_an_unbound_decision_from_an_older_release_is_never_applied_whatever_its_stamp(tmp_path, offset):
    """A decision file with no `card_created` (written by a release before the binding) is refused, fail closed
    (BACKLOG 63 replaced BACKLOG 60's fallback to the decision's own time stamp)."""
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({1}, bus=bus)
    p = center.propose(_p("icm-demo-X"))
    (bus.decisions_dir / "icm-demo-X.json").write_text(json.dumps(
        {"proposal_id": "icm-demo-X", "approve": True, "by": "dashboard", "ts": p.created + offset}))
    assert center.poll_bus() == [] and "icm-demo-X" in center.pending


@pytest.mark.integration
def test_a_restart_inside_the_window_never_raises_the_same_proposal_again_and_the_old_card_expires(tmp_path):
    """F60 re-verify finding 1: the engine proposes at a bar close and the Telegram card goes out; the engine restarts
    inside the 90 s window and re-decides the same close (possibly at another size). The id was archived at the start,
    so it is journalled `restart_archived` and never proposed again; the old card is edited to expired at once and a
    tap on it is refused, so it can never approve a re-sized proposal."""
    ticks = synthetic_ticks("2025-03-03", "2025-03-05", ticks_per_minute=1, seed=5)
    rows = list(zip(ticks["ts_utc"], ticks["bid"].to_numpy(float), ticks["ask"].to_numpy(float)))

    def engine() -> tuple[Engine, ApprovalCenter]:
        center = ApprovalCenter({1}, bus=ApprovalBus(tmp_path))
        spec = SPECIALISTS["session_open"](min_body_pct=0.0, asia_range_max_atr_d=99.0)
        return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", approval_mode="propose",
                                   state_dir=str(tmp_path)),
                      PaperBroker(equity=10_000), [spec], {"session_open": ConstantModel(p=0.65)}, center), center

    def run_until_proposed(eng: Engine, upto: int | None = None) -> tuple[int, list[dict]]:
        out: list[dict] = []
        for i, (ts, bid, ask) in enumerate(rows[:upto]):
            out += eng.on_tick(Tick(ts_utc=pd.Timestamp(ts), bid=float(bid), ask=float(ask)))
            if upto is None and any(d["action"] == "proposed" for d in out):
                return i, out
        return len(rows), out

    eng1, _ = engine()
    i, first = run_until_proposed(eng1)
    (pid,) = [d["proposal"] for d in first if d["action"] == "proposed"]
    bus = ApprovalBus(tmp_path)
    ob = Outbox(tmp_path, bus)
    assert [p.proposal_id for p in ob.unsent_proposals()] == [pid]
    ob.mark_sent(pid, [(1, 100)])                                          # the old card, buttons live

    eng2, center2 = engine()                                               # the restart, inside the window
    assert ob.finished() == [(pid, "EXPIRED_UNAPPROVED", [(1, 100)])]    # the old card loses its buttons at once
    _, again = run_until_proposed(eng2, upto=i + 1)                        # the same closes re-decided
    assert not [d for d in again if d["action"] == "proposed"] and pid not in center2.pending
    assert [d["action"] for d in again if d.get("proposal") == pid] == ["restart_archived"]
    assert not (bus.pending_dir / f"{pid}.json").exists() and ob.unsent_proposals() == []
    with pytest.raises(KeyError):
        submit_shown(bus, pid, True, None, by="telegram")                         # a tap on the old card
