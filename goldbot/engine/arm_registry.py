"""The hot-loaded arm registry (owner directive 2026-11-18, build 3; docs/research/arm-lifecycle-2026-11-18.md
section 3): discovered arms (`spec_rule`) the engine loads at a pass boundary from aux.db `arm_registry`, with no PR
and no restart. Rows are written only by `python -m goldbot.ops.run autodisc-register --to-db` (`append`).

The rules, as built here (trading-safety requirements 2026-11-18 and the review round of the same day, binding):

* Demo only, one predicate (`risk.gate.is_demo`: accounts.yaml mode demo, no live gate in the phase file, the
  terminal reporting trade_mode "demo", probe_only; anything missing or unreadable is not demo), checked at the load,
  on every re-read and at the signal (`exploration._send`). Every hot-loaded arm sends through purpose `exploration`,
  so the RiskGate's exploration fence refuses the same facts again: there is no lighter order path.
* A row runs only if it passes the same checks a config/spec_arms.yaml row does (`exploration.spec_arm_row`: the
  arm validates, its frozen hash is its own, the 2026-11-13 ruling, the ADR 0061 qualification stamp) and these:
  rule `spec_rule`, role strategy, `max_open` 1, params only {spec, spec_hash, prereg} (no exit, bracket or size
  parameter), no field outside the frozen row's (`FORBIDDEN`: growth sizing, a lot above the minimum, pyramid adds,
  an owner experiment, the cost-measurement or learning-probe purpose), a style with a pot, a per-trade cap in
  (0, the style's ceiling] (`ceiling`, autodisc_register's: the style's strategy arms' largest cap, SHARE_CAP x the
  pot, the exploration budget), gold's book, a live timeframe, an id and 5-character key no arm here has. A bad row
  refuses only its own arm, journalled once (`arm_registry_refused`); it never stops the engine.
* Integrity: the table is append-only and hash-chained (`digest`), and the highest tip this account's engine
  verified (seq, digest) is kept OUTSIDE aux.db, in core.db `arm_registry_seen` (`seen`). A broken chain (an altered
  or truncated row), fewer rows than the verified tip (a removed tail, an emptied table, an older aux.db copy) or
  another digest at its seq refuses new entries on every hot-loaded arm until the chain verifies, journalled and
  logged as an error (`arm_registry_chain_broken`). A read error (a lock, an I/O error) keeps the last good load for
  at most STALE_CADENCES reads (one a minute), then refuses new hot-loaded entries (`arm_registry_stale`). A load is
  applied whole or not at all. Reads go through the block's own aux.db handle (`Exploration.db`, a deferred read:
  never a migration, never the write lock), so a writer holding the lock never stalls the pass.
* Every verified load re-judges every row. A hot-loaded arm whose row is now refused (its timeframe left the live
  list, its cap or pot changed) or missing is kept configured with its entries refused (`retired`, as `config_for`
  keeps a bad row's arm): its open position keeps every exit and is never closed as an orphan. A retirement (the
  lifecycle's included) is never lifted here.
* After a restart there are zero hot-loaded entries until a full validated load (`Exploration.registry_ok`). The
  start reads the registry once; when that fails, this account's own remembered arms (`seen`, their own style) are
  kept with their entries held (`start_held`), and for at most STALE_CADENCES minutes no unknown position is closed
  as an orphan, except at its weekend backstop (`orphan_held`). `published` says how long the registry has been
  unverified; health FAILs it past HEALTH_FAIL_S (`ops.checks.engine.check_engine`).
* Caps and RiskGate inherited: a loaded arm's per-trade cap joins the gate's `exploration_arms` (`overlay`, applied
  again after every limits refresh); pots, slots, budgets, the kill switch, halts, propose-only and drift bind it as
  any arm. Loading only ever adds an arm: an open position, its stop, target and hold are never touched.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from typing import TYPE_CHECKING, Any

import pandas as pd

from goldbot.base import FrozenRecord
from goldbot.db import Database
from goldbot.instruments import GOLD

if TYPE_CHECKING:
    from goldbot.engine.exploration import Arm, Exploration
    from goldbot.engine.runner import Engine
    from goldbot.risk import RiskLimits

log = logging.getLogger(__name__)

GENESIS = "0" * 64
STALE_CADENCES = 3                  # failed reads (one a minute) the last good load is kept for; the orphan hold, minutes
HEALTH_FAIL_S = 300.0               # health FAILs an engine whose registry has been unverified this long
ROW_KEYS = frozenset({"id", "rule", "timeframe", "params", "max_open", "forward_only", "status", "config_hash",
                      "role", "qualification", "note"})
PARAM_KEYS = frozenset({"spec", "spec_hash", "prereg"})
# trading-safety 2026-11-18 item 2, named so a refusal says which: size, growth, adds and every exit or bracket knob
FORBIDDEN = frozenset({"sizing", "growth", "growth_arms", "unit_lots", "lots", "lot", "volume", "size_steps",
                       "adds", "purpose", "cost_measurement", "learning_probe", "control_share", "exit", "trail",
                       "stop_usd", "target_r", "max_stop_usd", "min_stop_usd", "hold_bars", "learning"})
REFUSED = "arm_registry_refused"
LOADED = "exploration_arm_hot_loaded"
CHAIN = "arm_registry_chain_broken"
STALE = "arm_registry_stale"
UNREAD = "arm_registry_unread"
NOT_DEMO = "arm_registry_not_demo"
UNVERIFIED = "registry_unverified"  # `_send`'s refusal of a hot-loaded arm while the registry is not verified


class RegistryRow(FrozenRecord):
    """One `arm_registry` row: the frozen arm row (JSON text, as written) and the account's registration of it."""
    seq: int
    account_id: str
    arm_id: str
    row: str
    style: str
    max_loss: float
    source: str
    recorded_ns: int
    prev_digest: str
    digest: str


def digest(prev: str, seq: int, account_id: str, arm_id: str, row: str, style: str, max_loss: float, source: str,
           recorded_ns: int) -> str:
    return hashlib.sha256(f"{prev}|{seq}|{account_id}|{arm_id}|{row}|{style}|{max_loss!r}|{source}|{recorded_ns}"
                          .encode()).hexdigest()


def _of(r: RegistryRow) -> str:
    return digest(r.prev_digest, r.seq, r.account_id, r.arm_id, r.row, r.style, r.max_loss, r.source, r.recorded_ns)


_SELECT = ("SELECT seq, account_id, arm_id, row, style, max_loss, source, recorded_ns, prev_digest, digest "
           "FROM arm_registry ORDER BY seq")


def _rows(got: list[Any]) -> list[RegistryRow]:
    return [RegistryRow.model_validate(dict(zip(RegistryRow.model_fields, r, strict=True))) for r in got]


def read_rows(db: Database) -> list[RegistryRow]:
    """Every row in order, in one deferred read of `db` (aux.db opened once by its owner: no migration, no write
    lock). Raises sqlite3.Error / OSError when the table cannot be read."""
    with db.read() as conn:
        return _rows(conn.execute(_SELECT).fetchall())


def chain_break(rows: list[RegistryRow], tip: tuple[int, str] | None = None) -> str | None:
    """Why the chain does not verify (the first bad row), or does not reach the verified `tip` (seq, digest) with the
    same digest there (a removed tail, an emptied table, an older copy), or None."""
    prev = GENESIS
    for i, r in enumerate(rows):
        if r.seq != i + 1 or r.prev_digest != prev or _of(r) != r.digest:
            return f"row {r.seq} ({r.arm_id}) does not verify"
        prev = r.digest
    if tip is not None and tip[0] > 0:
        if len(rows) < tip[0]:
            return f"{len(rows)} rows, below the verified tip at row {tip[0]} (rows removed or an older copy)"
        if rows[tip[0] - 1].digest != tip[1]:
            return f"row {tip[0]} is not the verified tip (rows replaced)"
    return None


def append(state_dir: str, *, account_id: str, row: dict[str, Any], style: str, max_loss: float, source: str,
           now_ns: int | None = None) -> RegistryRow:
    """Append one row at the chain's tip in one BEGIN IMMEDIATE transaction (two writers cannot fork it). Refused
    (ValueError) when the chain does not verify or the account already registers the arm."""
    text = json.dumps(row, sort_keys=True)
    arm_id = str(row.get("id"))
    ns = int(now_ns if now_ns is not None else time.time_ns())
    with Database.open(state_dir, "aux").write() as conn:
        rows = _rows(conn.execute(_SELECT).fetchall())
        if (why := chain_break(rows)) is not None:
            raise ValueError(f"arm_registry chain broken ({why}): nothing appended")
        if any(r.account_id == account_id and r.arm_id == arm_id for r in rows):
            raise ValueError(f"{arm_id} is registered on {account_id} already")
        seq, prev = len(rows) + 1, rows[-1].digest if rows else GENESIS
        d = digest(prev, seq, account_id, arm_id, text, style, float(max_loss), source, ns)
        conn.execute("INSERT INTO arm_registry (seq, account_id, arm_id, row, style, max_loss, source, recorded_ns, "
                     "prev_digest, digest) VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (seq, account_id, arm_id, text, style, float(max_loss), source, ns, prev, d))
    return RegistryRow(seq=seq, account_id=account_id, arm_id=arm_id, row=text, style=style, max_loss=float(max_loss),
                       source=source, recorded_ns=ns, prev_digest=prev, digest=d)


# ---------------------------------------------------------------------------------------------- the verified tip
class Seen(FrozenRecord):
    """core.db `arm_registry_seen` for one account: the verified tip and the hot-loaded arms it ran."""
    tip_seq: int = 0
    tip_digest: str = GENESIS
    arms: dict[str, dict[str, Any]] = {}       # arm id -> {style, max_loss, row}


def seen(core: Database, account_id: str) -> Seen | None:
    """This account's verified tip and arms; None before any. Raises when core.db cannot be read."""
    with core.read() as conn:
        got = conn.execute("SELECT tip_seq, tip_digest, body FROM arm_registry_seen WHERE account_id = ?",
                           (account_id,)).fetchone()
    if got is None:
        return None
    return Seen(tip_seq=int(got[0]), tip_digest=str(got[1]), arms=json.loads(got[2]).get("arms") or {})


def remember(core: Database, account_id: str, tip: tuple[int, str], arms: dict[str, dict[str, Any]]) -> None:
    """Advance this account's verified tip (never lowered) and its arms. Raises on a write error."""
    with core.write() as conn:
        got = conn.execute("SELECT tip_seq FROM arm_registry_seen WHERE account_id = ?", (account_id,)).fetchone()
        if got is not None and int(got[0]) > tip[0]:
            return
        conn.execute("INSERT OR REPLACE INTO arm_registry_seen (account_id, tip_seq, tip_digest, updated_ns, body) "
                     "VALUES (?, ?, ?, ?, ?)", (account_id, tip[0], tip[1], time.time_ns(),
                                                json.dumps({"arms": arms}, sort_keys=True)))


# ---------------------------------------------------------------------------------------------- validation
def demo_now(eng: Engine) -> bool:
    """`risk.gate.is_demo` on this engine's facts, read now (the terminal and the phase file fresh)."""
    from goldbot.engine import cost_probe
    from goldbot.risk.gate import is_demo, phase_gates
    try:
        gates = phase_gates(cost_probe._phase_file(eng))
    except Exception:
        gates = None
    return is_demo(eng.cfg.mode, gates, cost_probe._terminal_trade_mode(eng), eng.cfg.probe_only)


def ceiling(eng: Engine, ex: Exploration, style: str) -> float | None:
    """The style's per-trade cap ceiling (autodisc_register.plan's): its strategy arms' largest cap, SHARE_CAP x the
    pot's £ (when it has one) and the exploration budget; None when no strategy arm of the style sets one."""
    from goldbot.engine.exploration import SHARE_CAP
    caps = eng.gate.limits.exploration_arms
    peers = [caps[a] for a, arm in ex.arms.items() if arm.style == style and arm.control_share is None
             and arm.spec.role == "strategy" and a not in ex.hot_known and a in caps]
    pot = ex.cfg.pots.get(style)
    if not peers or pot is None:
        return None
    sleeve = [SHARE_CAP * pot.gbp] if pot.gbp is not None else []
    budget = ex.cfg.budget_share * float(eng.gate.limits.daily_loss_limit_abs or 0.0)
    return min(max(peers), *sleeve, budget)


def refusal(eng: Engine, ex: Exploration, r: RegistryRow) -> tuple[Arm | None, str | None]:
    """(the engine arm, None) for a row that may run here, else (None, why). Never raises."""
    from goldbot.engine.exploration import Arm, spec_arm_row
    from goldbot.research.arms import SPEC_RULE
    try:
        row = json.loads(r.row)
    except ValueError:
        return None, "row unreadable (not JSON)"
    if not isinstance(row, dict):
        return None, "row is not an object"
    raw = row.get("params")
    params: dict[str, Any] = raw if isinstance(raw, dict) else {}
    if bad := sorted((set(row) | set(params)) & FORBIDDEN):
        return None, f"forbidden field {', '.join(bad)} (size, growth, adds, purpose, exit and bracket fields)"
    if extra := sorted(set(row) - ROW_KEYS):
        return None, f"field {', '.join(extra)} is not a frozen arm row's"
    if extra := sorted(set(params) - PARAM_KEYS):
        return None, f"param {', '.join(extra)} is not a spec_rule param (spec, spec_hash, prereg only)"
    if row.get("rule") != SPEC_RULE:
        return None, "spec_rule arms only"
    if row.get("role", "strategy") != "strategy":
        return None, f"role {row.get('role')!r} refused (strategy only)"
    if row.get("max_open", 1) != 1:
        return None, "max_open must be 1"
    if str(row.get("id")) != r.arm_id:
        return None, f"row id {row.get('id')!r} is not the registered arm id {r.arm_id!r}"
    try:
        spec = spec_arm_row(row)
    except ValueError as exc:
        return None, f"invalid row: {str(exc).splitlines()[0][:200]}"
    arm = Arm(spec=spec, style=r.style)
    if arm.instrument != GOLD:
        return None, "hot-loaded arms run on gold's book only"
    if arm.tf not in set(eng.cfg.timeframes_live):
        return None, f"timeframe {arm.tf} is not live"
    if r.arm_id in ex.arms and r.arm_id not in ex.hot_known:
        return None, f"arm id {r.arm_id} is configured already"
    if any(a.key == arm.key and a.arm_id != r.arm_id for a in ex.arms.values()):
        return None, f"arm key {arm.key} is another arm's"
    if r.style not in ex.cfg.pots:
        return None, f"no {r.style} pot"
    top = ceiling(eng, ex, r.style)
    if top is None:
        return None, f"no {r.style} strategy arm sets the style's per-trade cap"
    if not 0 < r.max_loss <= top + 1e-9:
        return None, f"max_loss {r.max_loss:g} is outside the {r.style} caps (0, {top:g}]"
    return arm, None


# ---------------------------------------------------------------------------------------------- the engine side
def init(eng: Engine, ex: Exploration) -> None:
    """At the engine's start (the state is `Exploration.__init__`'s): one full load (`refresh`, forced), so the arms are
    known before the first reconciliation. When it fails, this account's remembered arms are kept, entries held."""
    if eng.cfg.instrument != GOLD:
        ex.registry_settled = True                 # no hot-loaded arm on another book: nothing to wait for
        _set_ok(ex, True)
        return
    try:
        ex.registry_core = Database.open(eng.cfg.state_dir, "core")
    except Exception as exc:                       # noqa: BLE001 - no verified tip: every hot entry refused
        log.error("core.db unreadable for the arm registry (%s): no hot-loaded entry", type(exc).__name__)
    refresh(eng, ex, None)
    if not ex.registry_settled:
        _keep_remembered(eng, ex)


def _keep_remembered(eng: Engine, ex: Exploration) -> None:
    """R6: this account's own remembered hot-loaded arms (`seen`), each with its own style, held (`start_held`): known
    to the exits and adoption, no entry until a verified load."""
    from goldbot.engine.exploration import Arm, spec_arm_row
    try:
        got = seen(ex.registry_core, eng.cfg.account_id) if ex.registry_core is not None else None
    except Exception as exc:                       # noqa: BLE001 - nothing remembered: the orphan hold still applies
        log.error("remembered hot-loaded arms unreadable (%s)", type(exc).__name__)
        return
    kept = []
    for arm_id, a in sorted((got.arms if got is not None else {}).items()):
        try:
            arm = Arm(spec=spec_arm_row(json.loads(a["row"])), style=str(a["style"]))
        except (ValueError, KeyError, TypeError) as exc:
            log.error("remembered hot-loaded arm %s unreadable (%s)", arm_id, exc)
            continue
        if arm_id in ex.arms or arm.instrument != GOLD or arm.style not in ex.cfg.pots \
                or any(x.key == arm.key for x in ex.arms.values()):
            continue
        _install(ex, arm)
        ex.start_held = ex.start_held | {arm_id}
        kept.append(arm_id)
    if kept:
        log.error("hot-loaded arms %s kept from this account's last verified load, entries held (registry not "
                  "verified)", kept)


def _set_ok(ex: Exploration, ok: bool) -> None:
    ex.registry_ok = ok
    if ok:
        ex.registry_bad_since = None
    elif ex.registry_bad_since is None:
        ex.registry_bad_since = time.time()


def _note(eng: Engine, ex: Exploration, action: str, **detail: Any) -> None:
    """Journal a registry state once until it changes."""
    if ex.registry_note == action:
        return
    ex.registry_note = action
    eng.decisions.append({"ts": time.time(), "agent": None, "action": action, **detail})


def refresh(eng: Engine, ex: Exploration, now: Any) -> None:
    """Once a minute (a pass boundary, `exploration._fast`), and at the start (`now` None): read, verify and load.
    Never raises."""
    if isinstance(now, pd.Timestamp):
        minute = now.floor("min")
        if ex.registry_read_at == minute:
            return
        ex.registry_read_at = minute
    try:
        _refresh(eng, ex)
    except Exception as exc:                    # a load part way: nothing of it applied, no hot entry
        _set_ok(ex, False)
        log.exception("hot-loaded arm registry load failed; no hot-loaded entry until a full load")
        _note(eng, ex, UNREAD, error=type(exc).__name__)


def _hold(eng: Engine, ex: Exploration, arm_id: str, why: str) -> None:
    """R1/R4: a hot-loaded arm whose row is refused now or gone: kept configured (every exit, never an orphan), its
    entries refused for good in this process, journalled once."""
    if arm_id in ex.retired and ex.unqualified.get(arm_id) == why:
        return
    ex.unqualified[arm_id] = why
    ex.retired = ex.retired | {arm_id}
    log.error("hot-loaded arm %s: %s; kept configured, entries refused", arm_id, why)
    eng.decisions.append({"ts": time.time(), "agent": "explore:" + arm_id, "action": REFUSED, "arm": arm_id,
                          "why": why, "kept": True})


def _refresh(eng: Engine, ex: Exploration) -> None:
    if eng.cfg.instrument != GOLD:
        return
    try:
        if ex.registry_core is None:
            ex.registry_core = Database.open(eng.cfg.state_dir, "core")
        rows = read_rows(ex.db)
        got = seen(ex.registry_core, eng.cfg.account_id)
    except (sqlite3.Error, OSError) as exc:
        ex.registry_fails += 1
        log.warning("hot-loaded arm registry unreadable (%s), %d of %d reads", type(exc).__name__,
                    ex.registry_fails, STALE_CADENCES)
        if ex.registry_fails > STALE_CADENCES or not ex.registry_settled:
            _set_ok(ex, False)
            _note(eng, ex, STALE, error=type(exc).__name__, reads=ex.registry_fails)
        return
    tip = (got.tip_seq, got.tip_digest) if got is not None else None
    if (why := chain_break(rows, tip)) is not None:
        _set_ok(ex, False)
        log.error("hot-loaded arm registry chain broken (%s): every hot-loaded entry refused", why)
        _note(eng, ex, CHAIN, why=why)
        return
    mine = {r.arm_id: r for r in rows if r.account_id == eng.cfg.account_id}
    if (mine or ex.hot_known) and not demo_now(eng):
        _set_ok(ex, False)
        log.error("hot-loaded arm registry: account %s is not demo; every hot-loaded entry refused", eng.cfg.account_id)
        _note(eng, ex, NOT_DEMO, account=eng.cfg.account_id)
        return
    accepted: list[tuple[Arm, float]] = []
    refused: dict[str, str] = {}
    for r in mine.values():
        arm, why = refusal(eng, ex, r)
        if arm is None:
            refused[r.arm_id] = why or "refused"
        elif r.arm_id not in ex.hot_caps:
            accepted.append((arm, r.max_loss))
    # the load is whole: applied only now that every row was read and judged
    for arm, cap in accepted:
        ex.hot_caps[arm.arm_id] = cap
        _add(eng, ex, arm)
    for a, why in sorted(refused.items()):
        if a in ex.hot_known:                   # R4: loaded before (or held from the start): kept, entries refused
            _hold(eng, ex, a, f"hot-loaded row now refused: {why}")
        elif ex.hot_journalled.get(a) != why:
            ex.hot_journalled[a] = why
            log.error("hot-loaded arm %s refused: %s", a, why)
            eng.decisions.append({"ts": time.time(), "agent": "explore:" + a, "action": REFUSED, "arm": a,
                                  "why": why})
    for a in sorted(ex.hot_known - set(mine)):  # R1: an arm whose row no longer verifies here
        _hold(eng, ex, a, "hot-loaded row missing from the verified registry")
    ex.start_held = frozenset()                 # R3: a held arm is now loaded or held for good, never by subtraction
    if accepted:
        eng.gate.limits = overlay(eng, eng.gate.limits, ex)
    if rows:
        loaded = {a: {"style": mine[a].style, "max_loss": mine[a].max_loss, "row": mine[a].row}
                  for a in ex.hot_caps if a in mine}
        tip_now = (rows[-1].seq, rows[-1].digest)
        if got is None or (got.tip_seq, got.tip_digest) != tip_now or got.arms != loaded:
            # safety S2: core.db's write lock only when the tip or the arm set changed, not every minute
            remember(ex.registry_core, eng.cfg.account_id, tip_now, loaded)
    ex.registry_fails = 0
    ex.registry_settled = True
    _set_ok(ex, True)
    ex.registry_note = None


def _install(ex: Exploration, arm: Arm) -> None:
    """`arm` in the running block (never removing or changing another arm)."""
    from goldbot.engine.exploration_context import needs_context
    ex.arms[arm.arm_id] = arm
    ex.by_key[arm.key] = arm
    ex.hot_known = ex.hot_known | {arm.arm_id}
    ex.cfg = ex.cfg.model_copy(update={"arms": tuple(a for a in ex.cfg.arms if a.arm_id != arm.arm_id) + (arm,)})
    if needs_context(arm.spec):
        ex.needs_context = ex.needs_context | {arm.arm_id}


def _add(eng: Engine, ex: Exploration, arm: Arm) -> None:
    """A loaded arm: installed, held as the start holds any arm (entries refused when its row is: the ADR 0061
    stamp), journalled. Never lifts a retirement (R3)."""
    from goldbot.engine.exploration import PREFIX
    _install(ex, arm)
    if arm.spec.entry_refused:
        ex.unqualified[arm.arm_id] = arm.spec.entry_refused
        ex.retired = ex.retired | {arm.arm_id}
    log.warning("exploration arm %s hot-loaded on %s (%s)", arm.arm_id, eng.cfg.account_id,
                arm.spec.entry_refused or "may enter")
    eng.decisions.append({"ts": time.time(), "agent": PREFIX + arm.arm_id, "action": LOADED, "arm": arm.arm_id,
                          "style": arm.style, "max_loss": ex.hot_caps.get(arm.arm_id),
                          "entry_refused": arm.spec.entry_refused or None})


def entry_refusal(eng: Engine, ex: Exploration, arm_id: str) -> str | None:
    """`exploration._send`'s check for a hot-loaded arm at its signal: not held from the start, the registry verified
    and the account demo now (`risk.gate.is_demo`). None for every other arm."""
    if arm_id not in getattr(ex, "hot_known", frozenset()):
        return None
    if arm_id in ex.start_held or not ex.registry_ok:
        return UNVERIFIED
    if not demo_now(eng):
        return "not_demo"
    return None


def orphan_held(ex: Exploration, opened: pd.Timestamp | None, at: pd.Timestamp) -> bool:
    """R5: whether a position of no known arm is held back from the orphan close: only while no registry load has
    succeeded, for at most STALE_CADENCES minutes from the first such look, and never at or past its weekend
    backstop (`exploration.weekend_backstop`)."""
    from goldbot.engine.exploration import weekend_backstop
    if ex.registry_settled:
        return False
    if ex.orphan_hold_from is None:
        ex.orphan_hold_from = at
    if opened is not None and at >= weekend_backstop(pd.Timestamp(opened)):
        return False
    return bool(at - ex.orphan_hold_from < pd.Timedelta(minutes=STALE_CADENCES))


def published(eng: Engine) -> dict[str, Any]:
    """R7, for the engine state file: since when the primary book's registry has been unverified, and why (health
    FAILs past HEALTH_FAIL_S). Nothing while it verifies."""
    ex = getattr(getattr(eng, "primary", None), "exploration", None)
    since = getattr(ex, "registry_bad_since", None)
    if ex is None or since is None:
        return {}
    return {"arm_registry_unverified_since": float(since), "arm_registry_reason": str(ex.registry_note or "unverified")}


def overlay(eng: Engine, limits: RiskLimits, loading: Exploration | None = None) -> RiskLimits:
    """`limits` with every book's hot-loaded per-trade caps added to `exploration_arms` (an arm configured there
    keeps its own); the limits unchanged when there are none. Applied at a load (`loading`: the block loading, not
    on its book yet at the engine's start) and after every limits refresh."""
    caps: dict[str, float] = {}
    for ex in [*(getattr(b, "exploration", None) for b in getattr(eng, "books", {}).values()), loading]:
        caps.update(getattr(ex, "hot_caps", None) or {})
    add = {a: c for a, c in caps.items() if a not in limits.exploration_arms}
    if not add:
        return limits
    return limits.model_copy(update={"exploration_arms": {**limits.exploration_arms, **add}})


__all__ = ["CHAIN", "FORBIDDEN", "GENESIS", "HEALTH_FAIL_S", "LOADED", "REFUSED", "STALE", "STALE_CADENCES",
           "UNVERIFIED", "RegistryRow", "Seen", "append", "chain_break", "ceiling", "demo_now", "digest",
           "entry_refusal", "init", "orphan_held", "overlay", "published", "read_rows", "refresh", "refusal",
           "remember", "seen"]
