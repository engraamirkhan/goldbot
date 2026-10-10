"""The owner's daily Telegram digest: one short message every morning (telegram.digest_at, default 06:45 UTC, before
London) with signal, not noise. Read on a phone, so it stays under about 15 lines:

* status   all good / attention needed, from the health report, failing and warning checks named;
* yesterday (the previous UTC day)  proposals made, approved, rejected, expired; trades closed with net P&L ($) and R
           (state/closed_trades_*.jsonl via goldbot.ops.gates_phase.load_closed_trades); open positions now;
* risk     worst drawdown stage and drawdown from peak, daily and weekly loss-cap use, any halt (owner, drift,
           supervisor, drawdown);
* research the next pre-registered trial or the quarter's trial budget, and the attribution headline (best and worst
           non-noise cell of state/attribution.json) when it exists;
* decisions waiting for the owner, with counts: pending approvals, the owner decision queue in docs/ROADMAP.md, a
           deploy on offer.

Every number carries its unit and every time its zone (UTC). No identifiers: check names lose their account suffix,
halts do not say who set them, and no account, login, email or Telegram id is printed. Money is in account currency
($, the accounts are USD, as `Proposal.risk_usd`).

Robust by construction: each section is built on its own, a missing file reads as "none yet", and an unreadable one
gives that section a "could not read" line; one broken input never stops the digest. Reporting only: nothing here
changes an order, a halt or a setting.

`DigestSchedule` decides when to send (no network, so it is unit-tested): once per scheduled slot, recorded in
state/telegram_digest.json only after delivery (a failed send is retried on the next pass), and a slot missed while
the service was down is still sent when it starts, if it is under LATE_LIMIT (6 h) late; later than that it is
skipped (a stale morning digest at night is noise).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from goldbot.base import Record, write_atomic
from goldbot.config import ROOT, Settings

STATE_FILE = "telegram_digest.json"
LATE_LIMIT = pd.Timedelta(hours=6)
MAX_NAMES = 5                       # check names listed per status before "+N more"
ATTRIBUTION_SKIP = ("agent", "decision")   # agent ids are per-version detail; "not taken" is not a trading cell
DEFAULT_ROADMAP = ROOT / "docs" / "ROADMAP.md"
_DECISION_ROW = re.compile(r"^\|\s*D\d+\s*\|")
_STAGE_RANK = {"normal": 0, "size_down": 1, "halted": 2}


# ---------------------------------------------------------------------------------------------- schedule and dedupe
class DigestState(Record):
    last_slot: str | None = None     # ISO date (UTC) of the last scheduled slot whose digest was delivered
    sent_utc: str | None = None


class DigestSchedule:
    """When the digest is due: the most recent slot (today's digest_at, or yesterday's if today's is still ahead) not
    yet delivered, and less than LATE_LIMIT ago. One rule covers the running loop (due within a pass of the slot) and a
    start after downtime (due if under 6 h late)."""

    def __init__(self, state_dir: str | Path, digest_at: str = "06:45"):
        self.path = Path(state_dir) / STATE_FILE
        hh, mm = digest_at.split(":")
        self.at = pd.Timedelta(hours=int(hh), minutes=int(mm))
        try:
            self.state = DigestState.model_validate_json(self.path.read_text(encoding="utf-8")) \
                if self.path.exists() else DigestState()
        except (ValueError, OSError):
            self.state = DigestState()      # unreadable: at worst one digest is sent twice, never one missed

    def slot(self, now: pd.Timestamp) -> pd.Timestamp:
        now = _utc(now)
        today = now.normalize() + self.at
        return today if now >= today else today - pd.Timedelta(days=1)

    def due(self, now: pd.Timestamp) -> bool:
        s = self.slot(now)
        return self.state.last_slot != s.date().isoformat() and _utc(now) - s < LATE_LIMIT

    def record(self, now: pd.Timestamp) -> None:
        """Mark the current slot delivered; call only after the send succeeded."""
        self.state = DigestState(last_slot=self.slot(now).date().isoformat(), sent_utc=_utc(now).isoformat())
        write_atomic(self.path, self.state.model_dump_json())


# ---------------------------------------------------------------------------------------------- helpers
def _utc(ts: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(ts)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _money(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):,.2f}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _engines(state_dir: Path) -> list[dict[str, Any]]:
    out = []
    for f in sorted(state_dir.glob("engine_*.json")):
        try:
            e = _json(f)
        except (ValueError, OSError):
            raise ValueError(f"{f.name} unreadable") from None
        if isinstance(e, dict):
            out.append(e)
    return out


def _section(build: Callable[[], list[str]], what: str) -> list[str]:
    """One section; any error becomes a single line so the rest of the digest still goes out."""
    try:
        return build()
    except Exception as exc:                # noqa: BLE001 - one broken input must never stop the digest
        return [f"• {what}: could not read ({type(exc).__name__})"]


# ---------------------------------------------------------------------------------------------- sections
def status_line(report: Any) -> list[str]:
    """✅ all good, or ⚠️ attention with failing then warning checks named (account suffixes removed)."""
    if report is None:
        return ["❔ Health: unknown (the health check did not run)"]

    def names(status: str) -> list[str]:
        seen: list[str] = []
        for c in report.checks:
            n = c.name.split(":", 1)[0]
            if c.status == status and n not in seen:
                seen.append(n)
        return seen

    def listed(ns: list[str]) -> str:
        more = f" +{len(ns) - MAX_NAMES} more" if len(ns) > MAX_NAMES else ""
        return ", ".join(ns[:MAX_NAMES]) + more

    fails, warns = names("fail"), names("warn")
    if not fails and not warns:
        return [f"✅ All good: {_plural(len(report.checks), 'health check')} ok"]
    parts = ([f"fail {listed(fails)}"] if fails else []) + ([f"warn {listed(warns)}"] if warns else [])
    return ["⚠️ Attention: " + " · ".join(parts)]


def proposals_line(state_dir: Path, day0: pd.Timestamp, day1: pd.Timestamp, now: pd.Timestamp) -> list[str]:
    """Proposals created in [day0, day1): outcome from the archive; a pending one past its window counts as expired."""
    root = state_dir / "approvals"
    lo, hi = day0.timestamp(), day1.timestamp()
    counts = {"APPROVED": 0, "REJECTED": 0, "EXPIRED_UNAPPROVED": 0, "open": 0}
    refused = bad = 0
    for sub in ("done", "pending"):
        d = root / sub
        for f in sorted(d.glob("*.json")) if d.exists() else []:
            try:
                if f.stat().st_mtime < lo:          # written before the day began: cannot hold a proposal from it
                    continue
                p = _json(f)
                created = float(p["created"])
            except (ValueError, OSError, KeyError, TypeError):
                bad += 1
                continue
            if not lo <= created < hi:
                continue
            outcome = p.get("outcome")
            if outcome in counts:
                counts[outcome] += 1
                refused += bool(outcome == "APPROVED" and p.get("gate_refusal"))
            elif sub == "pending" and now.timestamp() > created + float(p.get("window_s", 90)):
                counts["EXPIRED_UNAPPROVED"] += 1
            else:
                counts["open"] += 1
    n = sum(counts.values())
    note = f" ({refused} refused by the risk check)" if refused else ""
    tail = f" · {counts['open']} open" if counts["open"] else ""
    tail += f" · {bad} unreadable" if bad else ""
    if n == 0:
        return [f"• Proposals: none{tail}"]
    return [f"• Proposals {n}: ✅ {counts['APPROVED']} approved{note} · ❌ {counts['REJECTED']} rejected · "
            f"⌛ {counts['EXPIRED_UNAPPROVED']} expired{tail}"]


def trades_line(state_dir: Path, day0: pd.Timestamp, day1: pd.Timestamp) -> list[str]:
    from goldbot.ops.gates_phase import load_closed_trades
    trades, err = load_closed_trades(state_dir)
    day = [t for t in trades if day0 <= t.exit_utc < day1]
    warn = " (⚠️ unreadable record lines skipped)" if err else ""
    if not day:
        return [f"• Trades closed: none{warn}"]
    pnl = sum(t.pnl for t in day)
    rs = [t.r for t in day if t.r is not None]
    r = f" · {sum(rs):+.2f}R" + (f" ({len(rs)} of {len(day)} with R)" if len(rs) < len(day) else "") if rs else " · R n/a"
    wins = sum(t.pnl > 0 for t in day)
    return [f"• Closed {_plural(len(day), 'trade')}: net {_money(pnl)}{r} ({wins} won, {len(day) - wins} lost){warn}"]


def open_line(state_dir: Path) -> list[str]:
    engines = _engines(state_dir)
    if not engines:
        return ["• Open now: no engine state yet"]
    n = sum(int(e.get("open_positions") or 0) for e in engines)
    lots = sum(float(e.get("open_lots") or 0.0) for e in engines)
    return [f"• Open now: {_plural(n, 'position')}" + (f" ({lots:.2f} lots)" if n else "")]


def risk_lines(state_dir: Path, settings: Settings | None) -> list[str]:
    """Worst engine's drawdown stage and drawdown from the closed-balance peak (the RiskGate's arithmetic), loss-cap use
    against the settings' caps, and every halt in force (owner, drift, supervisor, drawdown)."""
    out: list[str] = []
    halts: list[str] = []
    try:
        engines = _engines(state_dir)
        if engines:
            stage = max((str(e.get("stage") or "normal") for e in engines), key=lambda s: _STAGE_RANK.get(s, 1))
            dd = max((1 - float(e.get("equity") or 0) / float(e["balance_closed_hwm"])
                      for e in engines if float(e.get("balance_closed_hwm") or 0) > 0), default=0.0)
            day = max((1 - float(e.get("equity") or 0) / float(e["day_start_equity"])
                       for e in engines if float(e.get("day_start_equity") or 0) > 0), default=0.0)
            week = max((1 - float(e.get("equity") or 0) / float(e["week_start_equity"])
                        for e in engines if float(e.get("week_start_equity") or 0) > 0), default=0.0)
            r = settings.risk if settings is not None else None
            halt_at = f" (halt at {100 * r.drawdown_stage2:.0f}%)" if r is not None else ""
            dcap = f" of {100 * r.daily_cap:.1f}% cap" if r is not None else ""
            wcap = f" of {100 * r.weekly_cap:.1f}% cap" if r is not None else ""
            out.append(f"Risk: stage {stage} · drawdown {100 * max(dd, 0):.1f}% from peak{halt_at} · "
                       f"day loss {100 * max(day, 0):.1f}%{dcap} · week {100 * max(week, 0):.1f}%{wcap}")
            if stage == "halted":
                halts.append("drawdown halt (re-arm on the dashboard)")
        else:
            out.append("Risk: no engine state yet")
    except Exception as exc:                # noqa: BLE001
        out.append(f"Risk: could not read engine state ({type(exc).__name__})")
    for name, read in (("owner", _owner_halt), ("drift", _drift_halt), ("supervisor", _supervisor_halt)):
        try:
            h = read(state_dir)
        except Exception:                   # noqa: BLE001 - unreadable halt files fail closed in the services
            h = f"{name} state unreadable (entries blocked)"
        if h:
            halts.append(h)
    out.append("🛑 Halts: " + " · ".join(halts) if halts else "Halts: none")
    return out


def _owner_halt(state_dir: Path) -> str | None:
    f = state_dir / "control.json"
    if not f.exists():
        return None
    c = _json(f)
    if not c.get("halted"):
        return None
    since = pd.Timestamp(float(c.get("ts") or 0), unit="s", tz="UTC")
    return f"owner halt since {since:%a %d %b %H:%M} UTC"


def _drift_halt(state_dir: Path) -> str | None:
    f = state_dir / "drift.json"
    if not f.exists():
        return None
    d = _json(f)
    if d.get("system_halt"):
        return "drift system halt (review pending)"
    if d.get("halted"):
        n = len(d["halted"])
        return f"drift: {n} strateg{'y' if n == 1 else 'ies'} halted"
    return None


def _supervisor_halt(state_dir: Path) -> str | None:
    f = state_dir / "supervisor.json"
    if not f.exists():
        return None
    return "supervisor combined-cap halt" if _json(f).get("halt") else None


def research_lines(state_dir: Path, settings: Settings | None, registry_path: Path | None, now: pd.Timestamp) -> list[str]:
    return _section(lambda: [_trial_line(settings, registry_path, now)], "Research") + \
        _section(lambda: _attribution_line(state_dir), "Attribution")


def _trial_line(settings: Settings | None, registry_path: Path | None, now: pd.Timestamp) -> str:
    """The quarter's budget and the next pre-registered trial, by the research director's own matching
    (director._prereg_matches / reserved_trials): a trial uses up its pre-registration by link or by family and config
    hash, so a run recorded without the link (research_pass) is not shown as still waiting."""
    from goldbot.research.director import _prereg_matches, reserved_trials
    from goldbot.research.registry import quarter_of, quarter_trials
    budget = settings.research.trial_budget_quarter if settings is not None else 20
    setting = settings.research.reserved_trials_quarter if settings is not None else 0
    rows: list[dict[str, Any]] = []
    if registry_path is not None and registry_path.exists():
        rows = [json.loads(x) for x in registry_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    q = quarter_of(_utc(now).to_pydatetime())
    used = quarter_trials(rows, q)
    reserved = reserved_trials(rows, q, setting).reserved
    budget_txt = (f"budget {q}: {used} of {budget} trials used, {reserved} reserved for pre-registered, "
                  f"{max(budget - used - reserved, 0)} open")
    prereg, matched = _prereg_matches(rows, q)
    waiting = [p for i, p in enumerate(prereg) if i not in matched]
    if waiting:
        nxt = min(waiting, key=lambda r: int(r.get("trial", 0)))
        return f"Research: next pre-registered trial #{nxt.get('trial')} ({nxt.get('family', '?')}) · {budget_txt}"
    return f"Research: no pre-registered trial waiting · {budget_txt}"


def _attribution_line(state_dir: Path) -> list[str]:
    f = state_dir / "attribution.json"
    if not f.exists():
        return []
    rep = _json(f)
    cells = [(k, name, c) for k, group in (rep.get("breakdowns") or {}).items() if k not in ATTRIBUTION_SKIP
             for name, c in group.items() if c.get("verdict") != "noise" and c.get("mean_r_net") is not None]
    if not cells:
        return [f"Attribution: every cell is still noise (< {rep.get('min_trades', '?')} trades)"]
    cells.sort(key=lambda x: float(x[2]["mean_r_net"]))

    def cell(x: tuple[str, str, dict[str, Any]]) -> str:
        return f"{x[0]} {x[1]} {float(x[2]['mean_r_net']):+.2f}R/trade net (n {x[2].get('n')})"

    best = cell(cells[-1])
    worst = f" · worst {cell(cells[0])}" if len(cells) > 1 else ""
    return [f"Attribution: best {best}{worst}"]


def decisions_line(state_dir: Path, roadmap: Path) -> list[str]:
    parts: list[str] = []
    try:
        from goldbot.telegram.bus import ApprovalBus
        parts.append(f"{_plural(len(ApprovalBus(state_dir).pending()), 'approval')} pending")
    except Exception:                       # noqa: BLE001
        parts.append("approvals unreadable")
    try:
        parts.append(f"{_plural(owner_decisions(roadmap), 'owner decision')} open (see OWNER_GUIDE)")
    except OSError:
        parts.append("owner decisions: see OWNER_GUIDE")
    try:
        pend = state_dir / "deploy" / "pending.json"
        if pend.exists() and _json(pend).get("sha"):
            parts.append("1 deploy on offer")
    except (ValueError, OSError):
        pass
    return ["👉 For you: " + " · ".join(parts)]


def owner_decisions(roadmap: Path) -> int:
    """Rows of docs/ROADMAP.md's '## 5. Owner decision queue' table (lines starting '| D<n> |')."""
    lines = roadmap.read_text(encoding="utf-8").splitlines()
    n, inside = 0, False
    for line in lines:
        if line.startswith("## "):
            inside = "owner decision queue" in line.lower()
            continue
        n += bool(inside and _DECISION_ROW.match(line))
    return n


# ---------------------------------------------------------------------------------------------- the message
def build_digest(state_dir: str | Path, now: pd.Timestamp, *, report: Any = None, settings: Settings | None = None,
                 roadmap: Path = DEFAULT_ROADMAP, registry_path: Path | None = None) -> str:
    """The digest text for `now` (UTC). `report` is a goldbot.ops.health.HealthReport (None: unknown); yesterday is the
    previous UTC calendar day."""
    sd = Path(state_dir)
    now = _utc(now)
    day1 = now.normalize()
    day0 = day1 - pd.Timedelta(days=1)
    lines = [f"☀️ goldbot daily · {now:%a %d %b %Y} · {now:%H:%M} UTC"]
    lines += _section(lambda: status_line(report), "Health")
    lines.append(f"Yesterday ({day0:%a %d %b}, 00:00-24:00 UTC)")
    lines += _section(lambda: proposals_line(sd, day0, day1, now), "Proposals")
    lines += _section(lambda: trades_line(sd, day0, day1), "Trades closed")
    lines += _section(lambda: open_line(sd), "Open now")
    lines += _section(lambda: risk_lines(sd, settings), "Risk")
    lines += research_lines(sd, settings, registry_path, now)
    lines += _section(lambda: decisions_line(sd, roadmap), "For you")
    return "\n".join(lines)


def digest_from_runtime(state_dir: str | Path, now: pd.Timestamp) -> str:  # pragma: no cover - VPS wiring
    """The digest on the VPS: health checks as the alert loop runs them, settings re-read from disk."""
    report, settings = None, None
    try:
        from goldbot.ops.health import HealthContext, run_checks
        ctx = HealthContext.from_runtime(state_dir, now=_utc(now))
        settings = ctx.settings
        report = run_checks(ctx)
    except Exception:                       # noqa: BLE001 - the digest still goes out, health shown as unknown
        pass
    registry = None
    if settings is not None:
        registry = Path(settings.research.registry)
        registry = registry if registry.is_absolute() else ROOT / registry
    return build_digest(state_dir, now, report=report, settings=settings, registry_path=registry)
