"""Auto-mode evidence, offer and `/mode` command (design: Operating mode; row A10).

Design: "A fully autonomous entry mode exists behind `/mode auto` plus TOTP and is only offered, never taken: after at
least 100 proposals with no RiskGate breach and no statistically distinguishable difference between approved and
rejected outcomes, the engine reports that the veto is no longer adding value and Aamir decides. A 12% kill-switch
event always returns the system to propose-and-approve."

* Evidence (`auto_mode_eligibility`, pure): proposals decided (approved or rejected; expired ones are not decisions)
  since the last mode change, RiskGate breaches over the same span, and the outcome of each decided proposal from
  the shadow book, which records every candidate's counterfactual barrier outcome whether or not the owner took it.
  Outcomes are compared in R (return over the initial stop distance) with Welch's two-sample t-test. "No
  distinguishable difference" = the test fails to reject at 10% AND the difference's 90% confidence interval spans 0.
  A side with fewer than `auto_min_outcomes_per_side` matched outcomes gives no evidence (a test with no power proves
  nothing), so the check fails closed.
* Offer (`AutoModeOffer`): when eligible, the Telegram service sends one message per mode epoch. It never switches.
* `/mode auto <TOTP>` (`mode_command`): refused unless the code verifies and the evidence holds; writes the mode to
  control.json through the bus, as /halt writes the halt. `/mode propose` lowers authority: no code needed. Both
  directions, and refusals, are audited. Inside the engine the 30-day re-arm lock and the 12% kill switch still force
  propose (goldbot/engine/runner.py `_refresh_account`).
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from scipy import stats

from goldbot.base import Record, write_atomic
from goldbot.config import TelegramSettings, tf_seconds
from goldbot.engine.shadow import ShadowBook, ShadowTrade
from goldbot.telegram.approvals import Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus


class VetoTest(Record):
    """Welch's two-sample t-test of mean R, approved minus rejected."""
    n_approved: int
    n_rejected: int
    mean_r_approved: float | None = None
    mean_r_rejected: float | None = None
    diff: float | None = None
    ci_low: float | None = None
    ci_high: float | None = None
    p_value: float | None = None
    alpha: float = 0.10

    @property
    def indistinguishable(self) -> bool:
        """The test fails to reject at alpha AND the (1 - alpha) interval of the difference spans 0."""
        if self.p_value is None or self.ci_low is None or self.ci_high is None:
            return False
        return self.p_value >= self.alpha and self.ci_low <= 0.0 <= self.ci_high


class Eligibility(Record):
    eligible: bool
    reasons: list[str]
    since: float                    # epoch seconds: the last mode change (0: the owner never set a mode)
    decided: int
    approved: int
    rejected: int
    breaches: list[str]
    test: VetoTest

    def numbers(self) -> dict[str, Any]:
        return {"decided": self.decided, "approved": self.approved, "rejected": self.rejected,
                "breaches": len(self.breaches), **self.test.model_dump()}

    def evidence_text(self) -> str:
        t = self.test

        def fmt(v: float | None) -> str:
            return "n/a" if v is None else f"{v:+.2f}"
        return (f"{self.decided} decided proposals ({self.approved} approved, {self.rejected} rejected), "
                f"{len(self.breaches)} RiskGate breaches; mean R approved {fmt(t.mean_r_approved)} (n={t.n_approved}) "
                f"vs rejected {fmt(t.mean_r_rejected)} (n={t.n_rejected}), difference {fmt(t.diff)} "
                f"[{fmt(t.ci_low)}, {fmt(t.ci_high)}] at {1 - t.alpha:.0%}, Welch p="
                + ("n/a" if t.p_value is None else f"{t.p_value:.2f}"))


def welch_test(approved: Sequence[float], rejected: Sequence[float], alpha: float = 0.10) -> VetoTest:
    """Welch's unequal-variance t-test on mean R and the matching (1 - alpha) interval of approved - rejected."""
    a, r = [float(x) for x in approved], [float(x) for x in rejected]
    out = VetoTest(n_approved=len(a), n_rejected=len(r), alpha=alpha,
                   mean_r_approved=sum(a) / len(a) if a else None, mean_r_rejected=sum(r) / len(r) if r else None)
    if len(a) < 2 or len(r) < 2:
        return out
    ma, mr = out.mean_r_approved or 0.0, out.mean_r_rejected or 0.0
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vr = sum((x - mr) ** 2 for x in r) / (len(r) - 1)
    diff = ma - mr
    se2 = va / len(a) + vr / len(r)
    if se2 <= 0:                                  # both samples constant: the difference is exact
        return out.model_copy(update={"diff": diff, "ci_low": diff, "ci_high": diff, "p_value": 1.0 if diff == 0 else 0.0})
    df = se2 ** 2 / ((va / len(a)) ** 2 / (len(a) - 1) + (vr / len(r)) ** 2 / (len(r) - 1))
    half = float(stats.t.ppf(1 - alpha / 2, df)) * math.sqrt(se2)
    p = float(stats.ttest_ind(a, r, equal_var=False).pvalue)
    return out.model_copy(update={"diff": diff, "ci_low": diff - half, "ci_high": diff + half, "p_value": p})


def r_multiple(t: ShadowTrade) -> float | None:
    """A closed shadow trade's outcome in R: return over the initial stop distance (the trade's risk)."""
    risk = abs(t.entry - t.stop)
    if t.ret is None or risk <= 0:
        return None
    return float(t.ret) * t.entry / risk


def _bar_close(p: Proposal) -> float:
    """The signal bar's close (epoch s), which the engine writes into the id: `<account>-<close ts>-<hash>`."""
    try:
        return float(p.proposal_id.rsplit("-", 2)[1])
    except (IndexError, ValueError):
        return p.created


def match_outcomes(proposals: Iterable[Proposal], trades: Iterable[ShadowTrade]) -> tuple[list[float], list[float]]:
    """R of each approved and rejected proposal, from the closed shadow trade of the same agent, side and signal bar
    (entry_ts is the bar's open, the proposal's bar close is entry_ts + timeframe). The barrier outcome depends on the
    signal and the agent's label spec, not on which version scored it, so the first match serves. Proposals with no
    closed shadow trade (still open, or the shadow agent already held a position) are left out."""
    by_key: dict[tuple[str, int], list[ShadowTrade]] = {}
    for t in trades:
        if t.exit_ts is not None and t.ret is not None:
            by_key.setdefault((t.agent_id, t.side), []).append(t)
    approved: list[float] = []
    rejected: list[float] = []
    for p in proposals:
        if p.outcome not in (Outcome.APPROVED, Outcome.REJECTED):
            continue
        close = _bar_close(p)
        for t in by_key.get((p.agent_id, p.side), []):
            start = pd.Timestamp(t.entry_ts).timestamp()
            if start < close <= start + tf_seconds(t.timeframe) + 60:
                r = r_multiple(t)
                if r is not None:
                    (approved if p.outcome == Outcome.APPROVED else rejected).append(r)
                break
    return approved, rejected


def auto_mode_eligibility(proposals: Iterable[Proposal], trades: Iterable[ShadowTrade], *, since: float = 0.0,
                          breaches: Sequence[str] = (), blockers: Sequence[str] = (),
                          settings: TelegramSettings | None = None) -> Eligibility:
    """Whether `/mode auto` may be offered. proposals: finished proposals (any outcome); only those created at or after
    `since` (the last mode change) count. trades: shadow trades, counterfactual ones included. breaches: RiskGate
    breaches since `since`. blockers: other conditions that keep the system in propose (re-arm lock, halts)."""
    s = settings or TelegramSettings()
    decided = [p for p in proposals if p.created >= since and p.outcome in (Outcome.APPROVED, Outcome.REJECTED)]
    n_app = sum(p.outcome == Outcome.APPROVED for p in decided)
    approved_r, rejected_r = match_outcomes(decided, trades)
    test = welch_test(approved_r, rejected_r, s.auto_alpha)
    reasons: list[str] = []
    if len(decided) < s.auto_min_proposals:
        reasons.append(f"{len(decided)} decided proposals since the last mode change; {s.auto_min_proposals} needed")
    if breaches:
        reasons.append(f"{len(breaches)} RiskGate breach(es): " + "; ".join(breaches))
    reasons.extend(blockers)
    if min(test.n_approved, test.n_rejected) < s.auto_min_outcomes_per_side:
        reasons.append(f"too few outcomes to compare: {test.n_approved} approved, {test.n_rejected} rejected; "
                       f"{s.auto_min_outcomes_per_side} needed on each side")
    elif not test.indistinguishable:
        reasons.append(f"approved and rejected outcomes differ (Welch p={test.p_value:.3f}, difference "
                       f"{test.diff:+.2f}R, interval [{test.ci_low:+.2f}, {test.ci_high:+.2f}]): the veto adds value")
    return Eligibility(eligible=not reasons, reasons=reasons, since=since, decided=len(decided), approved=n_app,
                       rejected=len(decided) - n_app, breaches=list(breaches), test=test)


# ----------------------------------------------------------------------------- reading the state directory
def _finished_proposals(bus: ApprovalBus) -> list[Proposal]:
    out = []
    for f in bus.done_dir.glob("*.json"):
        try:
            out.append(Proposal.model_validate_json(f.read_text()))
        except (ValueError, OSError):
            continue
    return out


def _risk_conditions(state_dir: Path, since: float, now: float) -> tuple[list[str], list[str]]:
    """(breaches, blockers) from each engine's risk_<account>.json: a drawdown stage reached (8% size-down or 12%
    halt), a kill switch since `since`, and a running re-arm lock (which forces propose regardless)."""
    breaches, blockers = [], []
    for f in sorted(state_dir.glob("risk_*.json")):
        acct = f.stem.removeprefix("risk_")
        try:
            d = json.loads(f.read_text())
        except (ValueError, OSError):
            blockers.append(f"{acct}: risk state unreadable")        # fail closed
            continue
        if d.get("stage", "normal") != "normal":
            breaches.append(f"{acct}: drawdown stage {d.get('stage')}")
        if d.get("halted_at") and pd.Timestamp(d["halted_at"]).timestamp() >= since:
            breaches.append(f"{acct}: 12% kill switch at {d['halted_at']}")
        until = d.get("propose_only_until")
        if until and pd.Timestamp(until).timestamp() > now:
            blockers.append(f"{acct}: propose-only after a re-arm until {until}")
    return breaches, blockers


def auto_mode_eligibility_from_state(state_dir: str | Path, settings: TelegramSettings | None = None,
                                     now: float | None = None) -> Eligibility:
    """The evidence check over the live state directory: archived proposals, the shadow book, engine risk files and
    control.json (the last mode change and the owner halt)."""
    root = Path(state_dir)
    bus = ApprovalBus(root)
    c = bus.control()
    since = c.mode_ts
    breaches, blockers = _risk_conditions(root, since, time.time() if now is None else now)
    if c.halted:
        blockers.append("entries halted" + (f" ({c.reason})" if c.reason else ""))
    trades = [t for b in ShadowBook(root).books.values() for t in b.closed]
    return auto_mode_eligibility(_finished_proposals(bus), trades, since=since, breaches=breaches, blockers=blockers,
                                 settings=settings)


# ----------------------------------------------------------------------------- the offer
class AutoModeOffer:
    """One offer per mode epoch (control.mode_ts): sent when the evidence first holds, never repeated until the mode
    changes again. The record is written only after delivery, so a failed send is retried."""

    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / "automode_offer.json"

    def _offered_epoch(self) -> float | None:
        try:
            return float(json.loads(self.path.read_text())["epoch"]) if self.path.exists() else None
        except (ValueError, OSError, KeyError, TypeError):
            return None

    def check(self, e: Eligibility, current_mode: str | None) -> str | None:
        if not e.eligible or current_mode == "auto" or self._offered_epoch() == e.since:
            return None
        return f"Auto mode can be enabled: evidence {e.evidence_text()}; reply /mode auto <TOTP>"

    def record(self, e: Eligibility) -> None:
        write_atomic(self.path, json.dumps({"epoch": e.since, "ts": time.time(), **e.numbers()}))


# ----------------------------------------------------------------------------- /mode
def owner_totp_ok(state_dir: str | Path, code: str) -> bool:
    """The dashboard's TOTP check (goldbot/api/auth.py, RFC 6238, +-1 step) against the enabled owner accounts: the
    same authenticator that re-arms on the dashboard. Read fresh each time, so a disabled owner stops working at once."""
    from goldbot.api.auth import AuthStore, totp_verify
    store = AuthStore(state_dir)
    return any(u.enabled and u.role == "owner" and totp_verify(u.totp_secret, code) for u in store.users.values())


def audit_to(state_dir: str | Path) -> Callable[..., None]:
    """Append to state/audit.jsonl, the dashboard's audit log (goldbot/api/auth.py AuthStore.audit)."""
    from goldbot.api.auth import AuthStore
    return AuthStore(state_dir).audit


MODE_USAGE = "usage: /mode propose  |  /mode auto <6-digit code>"


def mode_command(bus: ApprovalBus, arg: str | None, by: str, *, totp_ok: Callable[[str], bool],
                 eligibility: Callable[[], Eligibility], audit: Callable[..., None]) -> str:
    """`/mode propose` (always allowed: it lowers authority) or `/mode auto <code>` (TOTP, then the evidence check).
    The caller has already checked the owner allow-list. Every outcome is audited."""
    parts = (arg or "").split()
    mode = parts[0].lower() if parts else ""
    if mode == "propose":
        prev = bus.control().approval_mode
        bus.set_mode("propose", by=by, reason="owner")
        audit("mode", by=by, to="propose", previous=prev)
        return "Mode: propose-and-approve. Every entry waits for your click again; open trades keep their exits."
    if mode != "auto":
        return MODE_USAGE
    code = parts[1] if len(parts) > 1 else ""
    if not code or not totp_ok(code):
        audit("mode_refused", by=by, to="auto", reason="totp")
        return "TOTP required: /mode auto <6-digit code>"
    e = eligibility()
    if not e.eligible:
        audit("mode_refused", by=by, to="auto", reason="evidence", reasons=e.reasons, **e.numbers())
        return "Auto mode not available:\n• " + "\n• ".join(e.reasons)
    prev = bus.control().approval_mode
    bus.set_mode("auto", by=by, reason="owner, evidence held")
    audit("mode", by=by, to="auto", previous=prev, **e.numbers())
    return ("Mode: AUTO. Entries that pass the RiskGate are placed without asking you; exits stay automatic. "
            "A 12% drawdown or a re-arm returns to propose. Turn it off any time with /mode propose (or /halt).")
