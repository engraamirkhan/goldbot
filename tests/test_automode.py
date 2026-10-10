"""Row A10: "/mode auto ... only offered after at least 100 proposals with no RiskGate breach and no distinguishable
difference between approved and rejected outcomes"; /mode needs a TOTP; the re-arm lock and the 12% kill switch
always force propose-and-approve; /mode propose is always allowed."""
import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.engine import Engine, EngineConfig
from goldbot.engine.shadow import ShadowTrade
from goldbot.execution.broker import OrderIntent, Tick
from goldbot.execution.paper import PaperBroker
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.automode import (
    AutoModeOffer,
    Eligibility,
    auto_mode_eligibility,
    auto_mode_eligibility_from_state,
    match_outcomes,
    mode_command,
    welch_test,
)
from goldbot.telegram.bus import ApprovalBus

OWNER = 111
T0 = 1_740_000_000                     # epoch seconds of the first signal bar's close
AGENT = "trend-g0-x"


def _evidence(n: int, approved_r: Any, rejected_r: Any, created: float | None = None) -> tuple[list[Proposal], list[ShadowTrade]]:
    """n decided proposals alternating per the R lists (approved first), each with its closed shadow trade."""
    a, r = list(approved_r), list(rejected_r)
    props, trades = [], []
    for i in range(n):
        approve = bool((i % 2 == 0 and a) or not r)
        rr = a.pop(0) if approve else r.pop(0)
        close = T0 + i * 3600
        props.append(Proposal(proposal_id=f"icm-demo-{close}-abc{i}", account_id="icm-demo", agent_id=AGENT, side=1,
                              lots=0.1, entry=2400.0, stop=2390.0, target=2420.0, p=0.6, ev_r=0.2, spread_points=20,
                              top_features=[], created=float(created if created is not None else close),
                              outcome=Outcome.APPROVED if approve else Outcome.REJECTED,
                              reason_code=None if approve else "discretion"))
        trades.append(ShadowTrade(version="v1", agent_id=AGENT, side=1, entry_ts=pd.Timestamp(close - 900, unit="s", tz="UTC"),
                                  entry=2400.0, stop=2390.0, target=2420.0, max_bars=4, p=0.6, threshold=0.55,
                                  taken=approve, exit_ts=pd.Timestamp(close + 3600, unit="s", tz="UTC"),
                                  exit=2400.0 + 10 * rr, barrier="time", ret=10 * rr / 2400.0))
    return props, trades


def _same(n: int, seed: int = 1) -> tuple[list[float], list[float]]:
    rng = np.random.default_rng(seed)
    x = list(rng.normal(0.1, 1.0, n))
    return x[::2], x[1::2]                     # one distribution on both sides


# --------------------------------------------------------------------------------------- the evidence check
def test_outcomes_are_read_in_r_from_the_matching_shadow_trade_rejected_ones_included():
    props, trades = _evidence(4, [1.0, -1.0], [2.0, 0.5])
    a, r = match_outcomes(props, trades)
    assert np.allclose(a, [1.0, -1.0]) and np.allclose(r, [2.0, 0.5])


def test_not_eligible_below_100_decided_proposals_and_eligible_at_100():
    a, r = _same(100)
    props, trades = _evidence(100, a, r)
    e99 = auto_mode_eligibility(props[:99], trades)
    assert not e99.eligible and any("99 decided proposals" in x for x in e99.reasons)
    e100 = auto_mode_eligibility(props, trades)
    assert e100.eligible and e100.reasons == [] and e100.decided == 100
    assert e100.test.indistinguishable and e100.test.ci_low is not None and e100.test.ci_low <= 0 <= (e100.test.ci_high or 0)
    n = e100.numbers()
    assert n["approved"] == 50 and n["rejected"] == 50 and n["breaches"] == 0 and n["p_value"] >= 0.10


def test_expired_and_pre_mode_change_proposals_do_not_count():
    a, r = _same(100)
    props, trades = _evidence(100, a, r)
    props[0] = props[0].model_copy(update={"outcome": Outcome.EXPIRED})
    assert not auto_mode_eligibility(props, trades).eligible
    props, trades = _evidence(100, a, r)
    assert not auto_mode_eligibility(props, trades, since=T0 + 1).eligible        # the first one predates the change


def test_not_eligible_with_a_riskgate_breach():
    a, r = _same(120)
    props, trades = _evidence(120, a, r)
    e = auto_mode_eligibility(props, trades, breaches=["icm-demo: drawdown stage size_down"])
    assert not e.eligible and any("breach" in x for x in e.reasons)


def test_not_eligible_when_approved_and_rejected_outcomes_differ():
    rng = np.random.default_rng(3)
    props, trades = _evidence(120, rng.normal(0.4, 1.0, 60), rng.normal(-0.6, 1.0, 60))   # the veto avoids losers
    e = auto_mode_eligibility(props, trades)
    assert not e.eligible and any("the veto adds value" in x for x in e.reasons)
    assert e.test.p_value is not None and e.test.p_value < 0.10 and (e.test.ci_low or 0) > 0


def test_not_eligible_without_enough_outcomes_on_each_side():
    rng = np.random.default_rng(4)
    props, trades = _evidence(110, rng.normal(0, 1, 105), rng.normal(0, 1, 5))           # 5 rejections only
    e = auto_mode_eligibility(props, trades)
    assert not e.eligible and any("too few outcomes" in x for x in e.reasons)


def test_welch_interval_and_test_agree_on_both_sides():
    same = welch_test([0.1, -0.2, 0.3, 0.0, -0.1], [0.0, 0.2, -0.3, 0.1, 0.05])
    assert same.indistinguishable and same.p_value is not None and same.p_value >= 0.10
    apart = welch_test([1.0, 1.1, 0.9, 1.2, 1.0], [-1.0, -0.9, -1.1, -1.0, -1.2])
    assert not apart.indistinguishable and (apart.ci_low or 0) > 0
    assert not welch_test([1.0], [0.0]).indistinguishable                 # no test without two outcomes a side


def test_state_reader_counts_kill_switch_and_re_arm_lock(tmp_path):
    a, r = _same(100)
    props, trades = _evidence(100, a, r)
    bus = ApprovalBus(tmp_path)
    for p in props:
        bus.archive(p)
    from goldbot.engine.shadow import ShadowBook, VersionBook
    book = ShadowBook(tmp_path)
    book.books["v1"] = VersionBook(version="v1", started_utc=pd.Timestamp(T0 - 9000, unit="s", tz="UTC"), closed=trades)
    book.save(pd.Timestamp(T0, unit="s", tz="UTC"))
    assert auto_mode_eligibility_from_state(tmp_path).eligible
    risk = tmp_path / "risk_icm-demo.json"
    risk.write_text(json.dumps({"stage": "normal", "propose_only_until": "2999-01-01T00:00:00+00:00"}))
    e = auto_mode_eligibility_from_state(tmp_path)
    assert not e.eligible and any("propose-only" in x for x in e.reasons)
    risk.write_text(json.dumps({"stage": "halted", "halted_at": "2025-03-03T09:00:00+00:00"}))
    e = auto_mode_eligibility_from_state(tmp_path)
    assert not e.eligible and len(e.breaches) == 2


# --------------------------------------------------------------------------------------- the offer
def test_offer_is_one_message_per_mode_epoch_and_never_switches(tmp_path):
    a, r = _same(100)
    e = auto_mode_eligibility(*_evidence(100, a, r))
    offer, bus = AutoModeOffer(tmp_path), ApprovalBus(tmp_path)
    text = offer.check(e, bus.control().approval_mode)
    assert text is not None and text.startswith("Auto mode can be enabled: evidence 100 decided proposals")
    assert text.endswith("reply /mode auto <TOTP>")
    assert offer.check(e, None) is not None                    # not recorded yet (send failed): offered again
    offer.record(e)
    assert offer.check(e, None) is None                         # one message
    assert bus.control().approval_mode is None                  # the offer never switches
    assert offer.check(e.model_copy(update={"since": 5.0}), None) is not None    # a new mode epoch: a new offer
    assert offer.check(e.model_copy(update={"eligible": False}), None) is None


# --------------------------------------------------------------------------------------- /mode
def _mode(bus: ApprovalBus, arg: str | None, eligible: bool, audit: list) -> str:
    a, r = _same(100)
    e = auto_mode_eligibility(*_evidence(100 if eligible else 50, a, r))
    return mode_command(bus, arg, "telegram:111", totp_ok=lambda c: c == "123456", eligibility=lambda: e,
                        audit=lambda ev, **kw: audit.append((ev, kw)))


def test_mode_auto_without_a_valid_totp_is_refused_even_when_eligible(tmp_path):
    bus, audit = ApprovalBus(tmp_path), list[tuple[str, dict]]()
    assert _mode(bus, "auto", True, audit).startswith("TOTP required")
    assert _mode(bus, "auto 000000", True, audit).startswith("TOTP required")
    assert bus.control().approval_mode is None
    assert [ev for ev, _ in audit] == ["mode_refused", "mode_refused"]


def test_mode_auto_with_totp_is_refused_without_the_evidence(tmp_path):
    bus, audit = ApprovalBus(tmp_path), list[tuple[str, dict]]()
    text = _mode(bus, "auto 123456", False, audit)
    assert text.startswith("Auto mode not available") and "50 decided proposals" in text
    assert bus.control().approval_mode is None and audit[-1][1]["reason"] == "evidence"


def test_mode_auto_with_totp_and_evidence_writes_control_and_is_audited(tmp_path):
    bus, audit = ApprovalBus(tmp_path), list[tuple[str, dict]]()
    bus.set_halt(True, by="telegram:111", reason="lunch")
    assert _mode(bus, "auto 123456", True, audit).startswith("Mode: AUTO")
    c = bus.control()
    assert c.approval_mode == "auto" and c.mode_by == "telegram:111" and c.mode_ts > 0 and c.halted   # halt kept
    assert audit[-1][0] == "mode" and audit[-1][1]["to"] == "auto" and audit[-1][1]["decided"] == 100
    bus.set_halt(False, by="x")
    assert bus.control().approval_mode == "auto"                # halts and re-arms keep the mode
    bus.owner_rearm(by="dashboard:o@x.io")
    assert bus.control().approval_mode == "auto"


def test_downgrade_to_propose_is_always_allowed_without_totp_or_evidence(tmp_path):
    bus, audit = ApprovalBus(tmp_path), list[tuple[str, dict]]()
    bus.set_mode("auto", by="telegram:111")
    assert _mode(bus, "propose", False, audit).startswith("Mode: propose-and-approve")
    assert bus.control().approval_mode == "propose"
    assert audit[-1] == ("mode", {"by": "telegram:111", "to": "propose", "previous": "auto"})
    assert _mode(bus, None, False, audit).startswith("usage")
    c = ApprovalCenter({OWNER})                                  # the in-process centre: same rule
    assert c.command(OWNER, "/mode", "propose") == "mode set to propose"
    assert c.command(OWNER, "/mode", "auto", totp="123456").startswith("TOTP required")
    ok = ApprovalCenter({OWNER}, totp_verify=lambda code: code == "123456")
    assert ok.command(OWNER, "/mode", "auto", totp="123456").startswith("auto mode not available") and ok.mode == "paper"
    yes = ApprovalCenter({OWNER}, totp_verify=lambda code: code == "123456", auto_eligible=lambda: True)
    assert yes.command(OWNER, "/mode", "auto", totp="123456") == "mode set to auto"


def test_unreadable_control_reads_as_propose(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.control_path.write_text("{not json")
    assert bus.control().approval_mode == "propose" and bus.control().halted


# --------------------------------------------------------------------------------------- the engine honours it
def _tick(t: pd.Timestamp, bid: float = 2400.0) -> Tick:
    return Tick(ts_utc=t, bid=bid, ask=bid + 0.2)


def _engine(tmp_path: Path, bus: ApprovalBus, pb: PaperBroker, mode: str = "propose") -> Engine:
    return Engine(EngineConfig(account_id="icm-demo", broker_name="icm", state_dir=str(tmp_path), owner_user_id=OWNER,
                               approval_mode=mode), pb, [], {}, ApprovalCenter({OWNER}, bus=bus))


def test_engine_reads_the_owner_mode_and_the_re_arm_lock_wins(tmp_path):
    t = pd.Timestamp("2025-03-05 10:00", tz="UTC")
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(t))
    bus = ApprovalBus(tmp_path)
    eng = _engine(tmp_path, bus, pb)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.cfg.approval_mode == "propose"                   # nothing set by the owner: as configured
    bus.set_mode("auto", by="telegram:111")
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.cfg.approval_mode == "auto"
    eng._propose_only_until = t + pd.Timedelta(days=30)         # after a re-arm: propose-only period
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.cfg.approval_mode == "propose"
    eng._propose_only_until = None
    bus.set_mode("propose", by="telegram:111")
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.cfg.approval_mode == "propose"


def test_kill_switch_returns_the_owner_mode_to_propose_and_it_stays_there(tmp_path):
    t = pd.Timestamp("2025-03-03 09:00", tz="UTC")
    pb = PaperBroker(equity=10_000)
    pb.on_tick(_tick(t))
    bus = ApprovalBus(tmp_path)
    bus.set_mode("auto", by="telegram:111")
    eng = _engine(tmp_path, bus, pb, mode="auto")
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.cfg.approval_mode == "auto"
    pb.place_order(OrderIntent(client_order_id="big", symbol="XAUUSD", side=1, lots=1.0, sl=2300.0, tp=2500.0,
                               magic=260150, comment="big"))
    before = time.time()
    pb.on_tick(_tick(t + pd.Timedelta(minutes=1), 2387.0))      # 13% drawdown
    eng._refresh_account(pb.last_tick("XAUUSD"))
    c = bus.control()
    assert eng.cfg.approval_mode == "propose" and c.approval_mode == "propose" and c.mode_by == "engine:icm-demo"
    assert c.mode_ts >= before and any(d.get("action") == "mode_propose" for d in eng.decisions)
    eng._refresh_account(pb.last_tick("XAUUSD"))
    assert eng.cfg.approval_mode == "propose"


def test_eligibility_record_round_trips():
    a, r = _same(100)
    e = auto_mode_eligibility(*_evidence(100, a, r))
    assert Eligibility.model_validate_json(e.model_dump_json()) == e
