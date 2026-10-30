"""`python -m goldbot.ops.run autodisc-register`: a discovery survivor becomes a demo exploration arm (ADR 0058
phase 4; design autonomous-discovery-2026-10-28.md section 4.1: "A holdout passer gets an arm row ... The arm runs only
on demo accounts ... Caps, pots, `max_stop_usd`, `min_stop_usd` and RiskGate are inherited unchanged").

What it does, in this order, and nothing else:
1. Reads the survivor from the search ledger (`state/discovery.db`, a "real" ledger only, chain verified) by its spec
   hash, with its honesty-set results: the explore floors, fitness > 0, excess over the drift baseline > 0, net R > 0
   at 1.5x cost, and fitness above the 95th percentile of the ledger's best-of-search null (every null result in the
   ledger, replication by replication: the widest search, so the highest bar). A spec that fails any is refused.
2. Builds the frozen arm row `{id: D<yymm>-<n>, rule: spec_rule, timeframe, params: {spec, spec_hash, prereg},
   max_open: 1, forward_only: true}` (validated by `arm_rules_spec.check`) and the account's registration line, and
   refuses unless: the account is demo, probe_only and exploring; the style has a pot; the per-trade cap is inside
   the account's caps for that style (at most its strategy arms' largest, the exploration budget, and the ADR 0057
   room floor's ceiling SHARE_CAP x the pot, so one loss fits the arm's capped day); the engine can warm the spec's
   leaves up (`arm_rules_spec.required_bars` within `exploration.LOOKBACK_MAX_1M`); the prereg file exists; at most
   WEEKLY_MAX registrations this ISO week (ADR 0045 weekly tier); the spec is not registered already. The edited
   files are loaded through `load_accounts` and `exploration.config_for` before anything is written.
3. Journals the registration (`arm_registrations` in the ledger's database: spec hash, ledger K_search and K_null, the
   ledger head, the honesty results; append-only, hash-chained) BEFORE any file changes, then writes
   config/spec_arms.yaml and config/accounts.yaml. Nothing runs until those files are merged and the engine restarts;
   it never touches the engine, a broker, RiskGate or an order. `register` documents the crash replay, the file lock
   and the feature-branch rule.

Phase 3 (ADR 0058, the phase-3 amendment): registration follows the stage reads. By default only a confirm passer
(design 3.3) or a holdout passer (3.5) registers, read from the stage journal (`autodisc.stages`, in the same database)
and journalled as `confirm_passer` / `holdout_passer`. An explore survivor that never had a confirm read registers only
behind the explicit `--stage explore` flag (a demo-learning arm), journalled as `explore_survivor` with
`requested_stage: explore`; its forward demo read (design 4.4) is then its first out-of-sample evidence and the prereg
must say so. A spec whose confirm or holdout read failed, or was voided, never registers ("the failed hash can never be
re-confirmed"; the holdout is a one-way stop), whatever the flag.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, NamedTuple

import pandas as pd
from pydantic import Field

from goldbot.base import FrozenRecord
from goldbot.engine import exploration as X
from goldbot.ops import accounts as A
from goldbot.research.arms import SPEC_RULE, Arm, arm_hash, load_arms
from goldbot.research.autodisc.ledger import GENESIS, SearchLedger
from goldbot.research.autodisc.nightly import LEDGER_FILE
from goldbot.research.autodisc.nulls import NEG_INF, NullDistribution
from goldbot.research.autodisc.spec import StrategySpec, canonical_json
from goldbot.research.autodisc.stages import StageJournal

ROOT = Path(__file__).resolve().parents[2]
LEDGER_PATH = ROOT / "state" / LEDGER_FILE["real"]      # the nightly search's real ledger (and its stage journal)
WEEKLY_MAX = 4                     # ADR 0045 weekly tier: at most 4 reviewed variants a week across all sources
STYLE_BY_TF = {"15m": "intraday", "1h": "intraday", "4h": "swing", "1d": "swing"}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS arm_registrations (
    seq INTEGER PRIMARY KEY,
    arm_id TEXT NOT NULL UNIQUE,
    spec_hash TEXT NOT NULL UNIQUE,
    written_utc TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_digest TEXT NOT NULL,
    digest TEXT NOT NULL
);
"""


class RegistrationRefused(ValueError):
    """The survivor cannot be registered; the message says why. Nothing was journalled or written."""


class Honesty(FrozenRecord):
    """The survivor's honesty-set results from the ledger (its result row and the ledger's search null)."""
    n_events: int
    mean_r: float | None
    fitness: float | None
    excess_r: float | None
    stress_mean_r: float | None
    floors: bool
    null_threshold: float
    null_reps: int

    @property
    def failures(self) -> list[str]:
        out = [] if self.floors else ["floors"]
        for name, v in (("fitness", self.fitness), ("excess_r", self.excess_r), ("stress_mean_r", self.stress_mean_r)):
            if v is None or not v > 0:
                out.append(name)
        if self.fitness is None or not self.fitness > max(self.null_threshold, 0.0):
            out.append("search_null")
        return out


Reached = Literal["explore_survivor", "confirm_passer", "holdout_passer"]


class Registration(FrozenRecord):
    """One journalled registration (`arm_registrations.payload`)."""
    arm_id: str
    account_id: str
    spec_hash: str
    timeframe: str
    style: str
    max_loss: float = Field(gt=0)
    prereg: str
    stage: Reached = "explore_survivor"
    requested_stage: Literal["explore", "confirm"] | None = None     # the --stage given; None: before phase 3
    k_search: int
    k_null: int
    ledger_seq: int
    ledger_digest: str
    honesty: Honesty
    config_hash: str
    written_utc: str


# ---------------------------------------------------------------- the journal
def _connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=60, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=FULL")
    con.executescript(_SCHEMA)
    return con


class Head(NamedTuple):
    """The journal's tip: how many rows and the last row's digest (the anchor `--head` checks against)."""
    rows: int
    digest: str


def _rows(path: Path) -> list[tuple[Any, ...]]:
    con = _connect(path)
    try:
        return con.execute("SELECT seq, arm_id, spec_hash, written_utc, payload, prev_digest, digest "
                           "FROM arm_registrations ORDER BY seq").fetchall()
    finally:
        con.close()


def verify(path: Path, head: str | None = None) -> Head:
    """Verify the journal's chain (every column but the digest is hashed in) and return its tip. With `head` (a digest
    kept outside the database) a journal whose tip differs, as when the tail rows were deleted, is refused naming the
    expected head: a hash chain alone cannot see a truncated tail."""
    rows = _rows(path) if path.exists() else []
    prev = GENESIS
    for seq, arm_id, spec_hash, written, payload, p, d in rows:
        if p != prev or _digest(prev, seq, arm_id, spec_hash, written, payload) != d:
            raise RegistrationRefused(f"arm_registrations row {seq} does not verify: the journal was altered")
        prev = d
    if head is not None and prev != head:
        raise RegistrationRefused(f"the journal does not end at the anchored head: expected head {head}, found "
                                  f"{prev} ({len(rows)} rows): rows were removed or replaced")
    return Head(len(rows), prev)


def journal(path: Path) -> list[Registration]:
    """Every registration journalled in `path`, in order (the chain verified)."""
    if not path.exists():
        return []
    verify(path)
    return [Registration.model_validate_json(r[4]) for r in _rows(path)]


def _digest(prev: str, seq: int, arm_id: str, spec_hash: str, written_utc: str, payload: str) -> str:
    return hashlib.sha256(f"{prev}|{seq}|{arm_id}|{spec_hash}|{written_utc}|{payload}".encode()).hexdigest()


def _week(utc: str) -> tuple[int, int]:
    return pd.Timestamp(utc).isocalendar()[:2]


def _append(path: Path, reg: Registration) -> None:
    """Journal `reg`. The weekly cap, the duplicate spec and the taken arm id are checked again here, inside
    `BEGIN IMMEDIATE` (the write lock), so two registrations racing past `plan` cannot both pass."""
    payload = reg.model_dump_json()
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            have = con.execute("SELECT arm_id, spec_hash, written_utc FROM arm_registrations").fetchall()
            if reg.spec_hash in {r[1] for r in have}:
                raise RegistrationRefused(f"spec {reg.spec_hash[:12]} is registered already")
            if reg.arm_id in {r[0] for r in have}:
                raise RegistrationRefused(f"arm id {reg.arm_id} is taken by another registration: run it again")
            if sum(_week(r[2]) == _week(reg.written_utc) for r in have) >= WEEKLY_MAX:
                raise RegistrationRefused(f"{WEEKLY_MAX} registrations this week already (ADR 0045 weekly tier)")
            tip = con.execute("SELECT seq, digest FROM arm_registrations ORDER BY seq DESC LIMIT 1").fetchone()
            seq, prev = (tip[0] + 1, tip[1]) if tip else (1, GENESIS)
            con.execute("INSERT INTO arm_registrations (seq, arm_id, spec_hash, written_utc, payload, prev_digest, "
                        "digest) VALUES (?,?,?,?,?,?,?)",
                        (seq, reg.arm_id, reg.spec_hash, reg.written_utc, payload, prev,
                         _digest(prev, seq, reg.arm_id, reg.spec_hash, reg.written_utc, payload)))
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


@contextmanager
def _locked(path: Path) -> Iterator[None]:
    """An exclusive advisory lock (fcntl) beside the ledger: one registration at a time reads, journals and writes."""
    with open(path.with_name(path.name + ".register.lock"), "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _require_branch(root: Path) -> None:
    """The rows must go through a PR and CI: refuse on main, master, a detached HEAD or outside a git checkout."""
    try:
        out = subprocess.run(["git", "-C", str(root), "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True,
                             text=True, check=False, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RegistrationRefused(f"cannot read the git branch of {root} ({exc}): register on a feature branch") from exc
    branch = out.stdout.strip()
    if out.returncode != 0 or not branch or branch in ("main", "master"):
        raise RegistrationRefused(f"the checkout is on {branch or 'no git branch'!r}: register on a feature branch "
                                  "(the rows go through a PR and CI, never straight to main)")


# ---------------------------------------------------------------- the survivor
def honesty_of(ledger: SearchLedger, spec_hash: str) -> Honesty:
    """The spec's recorded explore result and the ledger's search null threshold (refused when either is missing)."""
    res = ledger.result(spec_hash)
    if res is None:
        raise RegistrationRefused(f"spec {spec_hash[:12]} has no explore result in the ledger")
    nulls = [json.loads(e.payload).get("null_fitness") or [] for e in ledger.entries()
             if e.kind == "result" and e.is_null]
    reps = max((len(n) for n in nulls), default=0)
    if reps == 0:
        raise RegistrationRefused("the ledger holds no search null: a survivor must beat its search's null")
    best = tuple(max((NEG_INF if i >= len(n) or n[i] is None else float(n[i]) for n in nulls), default=NEG_INF)
                 for i in range(reps))
    thr = NullDistribution(best, ledger.k_null()).threshold()
    return Honesty(n_events=int(res.get("n_events") or 0), mean_r=res.get("mean_r"), fitness=res.get("fitness"),
                   excess_r=res.get("excess_r"), stress_mean_r=res.get("stress_mean_r"),
                   floors=bool(res.get("floors")), null_threshold=float(thr), null_reps=reps)


def stage_of(ledger_path: Path, spec_hash: str, requested: str) -> Reached:
    """The stage a registration rests on (module docstring): holdout_passer, confirm_passer, or explore_survivor when
    `requested` is "explore" and the spec never had a confirm read; RegistrationRefused otherwise."""
    st = StageJournal(ledger_path).state(spec_hash)
    for name, got in (("confirm", st.confirm), ("holdout", st.holdout)):
        if got is not None and got != "passed":
            raise RegistrationRefused(f"spec {spec_hash[:12]}: its {name} read is {got if got != 'open' else 'void'}: "
                                      "that result is final, it never registers (ADR 0058 design 3.3/3.5)")
    if st.holdout == "passed":
        return "holdout_passer"
    if st.confirm == "passed":
        return "confirm_passer"
    if requested == "explore":
        return "explore_survivor"
    raise RegistrationRefused(f"spec {spec_hash[:12]} is not a confirm passer: registration follows the confirm read "
                              "(ADR 0058 phase 3); a demo-learning arm of an explore survivor needs --stage explore")


# ---------------------------------------------------------------- the files
def _next_id(now: pd.Timestamp, taken: set[str]) -> str:
    prefix = f"D{now:%y%m}-"
    used = [int(i[len(prefix):]) for i in taken if i.startswith(prefix) and i[len(prefix):].isdigit()]
    return f"{prefix}{max(used, default=0) + 1}"


def arm_line(arm: Arm) -> str:
    """The frozen row as config/spec_arms.yaml carries it (flow YAML; params as JSON, which YAML reads as is)."""
    return (f"  - {{ id: {arm.id}, rule: {arm.rule}, timeframe: {arm.timeframe}, params: "
            f"{json.dumps(arm.params, sort_keys=True)}, max_open: {arm.max_open}, forward_only: true, status: run, "
            f"config_hash: {arm.config_hash} }}")


def with_arm(spec_text: str, arm: Arm) -> str:
    line = arm_line(arm)
    if re.search(r"^arms:\s*\[\]\s*$", spec_text, flags=re.M):
        return re.sub(r"^arms:\s*\[\]\s*$", lambda _: "arms:\n" + line, spec_text, count=1, flags=re.M)
    if not re.search(r"^arms:\s*$", spec_text, flags=re.M):
        raise RegistrationRefused("config/spec_arms.yaml has no top-level arms list")
    return spec_text.rstrip("\n") + "\n" + line + "\n"


def with_registration(accounts_text: str, account_id: str, arm_id: str, style: str, max_loss: float,
                      spec_hash: str) -> str:
    """`accounts_text` with `<arm_id>: { style, max_loss }` appended to the end of the account's `exploration.arms`
    block (block form only), comments and layout kept."""
    lines = accounts_text.splitlines()
    start = next((i for i, x in enumerate(lines) if re.match(rf"^  {re.escape(account_id)}:\s*(#.*)?$", x)), None)
    if start is None:
        raise RegistrationRefused(f"account {account_id!r} is not in config/accounts.yaml")
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^ {0,2}\S", lines[i])), len(lines))
    arms = next((i for i in range(start, end) if re.match(r"^      arms:\s*(#.*)?$", lines[i])), None)
    if arms is None:
        raise RegistrationRefused(f"{account_id}: no block-form exploration.arms to register into")
    last = arms
    for i in range(arms + 1, end):
        if not lines[i].strip():
            continue
        if len(lines[i]) - len(lines[i].lstrip()) < 8:
            break
        last = i
    new = f"        {arm_id}: {{ style: {style}, max_loss: {max_loss:g} }}  # ADR 0058 spec_rule, spec {spec_hash[:12]}"
    return "\n".join([*lines[:last + 1], new, *lines[last + 1:]]) + "\n"


# ---------------------------------------------------------------- the plan
def plan(spec_hash: str, account_id: str, prereg: str, *, ledger_path: Path = LEDGER_PATH,
         accounts_path: Path = A.ACCOUNTS_FILE, spec_path: Path = X.SPEC_PATH,
         spec_arms_path: Path = X.SPEC_ARMS_PATH, style: str | None = None, max_loss: float | None = None,
         now: pd.Timestamp | None = None, root: Path = ROOT, stage: Literal["explore", "confirm"] = "confirm"
         ) -> tuple[Registration, str, str]:
    """Every check, nothing written: the registration and the two files' new text (spec arms, accounts). `stage`:
    "confirm" (default) needs a confirm or holdout passer; "explore" also takes an explore survivor (`stage_of`)."""
    now = now if now is not None else pd.Timestamp.now("UTC")
    if not ledger_path.exists():
        raise RegistrationRefused(f"no search ledger at {ledger_path}")
    ledger = SearchLedger(ledger_path, mode="real")          # a synthetic ledger is refused (LedgerError)
    ledger.verify()
    spec: StrategySpec | None = ledger.get_spec(spec_hash)
    if spec is None:
        raise RegistrationRefused(f"spec {spec_hash[:12]} is not in the ledger")
    honesty = honesty_of(ledger, spec_hash)
    if bad := honesty.failures:
        raise RegistrationRefused(f"spec {spec_hash[:12]} is not an explore survivor: fails {bad}")
    reached = stage_of(ledger_path, spec_hash, stage)
    path, _, section = prereg.partition("#")
    inside = not Path(path).is_absolute() and ".." not in Path(path).parts
    if not section or not inside or not (root / path).is_file():
        raise RegistrationRefused(f"prereg {prereg!r} must be <existing file under the repo>#<section> (the forward "
                                  "reading rule, written before the arm runs)")
    done = journal(ledger_path)
    spec_text = spec_arms_path.read_text()
    shipped = load_arms(spec_arms_path)
    if spec_hash in {r.spec_hash for r in done} | {str(a.params.get("spec_hash")) for a in shipped}:
        raise RegistrationRefused(f"spec {spec_hash[:12]} is registered already")
    week = now.isocalendar()[:2]
    if sum(pd.Timestamp(r.written_utc).isocalendar()[:2] == week for r in done) >= WEEKLY_MAX:
        raise RegistrationRefused(f"{WEEKLY_MAX} registrations this week already (ADR 0045 weekly tier)")
    arm_id = _next_id(now, {r.arm_id for r in done} | {a.id for a in shipped}
                      | {a.id for a in load_arms(spec_path)})
    try:
        arm = Arm(id=arm_id, rule=SPEC_RULE, timeframe=spec.timeframe,
                  params={"spec": canonical_json(spec), "spec_hash": spec_hash, "prereg": prereg},
                  max_open=1, forward_only=True)
    except ValueError as exc:
        raise RegistrationRefused(f"spec {spec_hash[:12]} cannot run live: {exc}") from exc
    arm = arm.model_copy(update={"config_hash": arm_hash(arm)})

    acc = A.load_accounts(accounts_path, logins=False).get(account_id)
    if acc is None:
        raise RegistrationRefused(f"account {account_id!r} is not in config/accounts.yaml")
    ex = acc.exploration
    if acc.mode != "demo" or not acc.probe_only or not ex.enabled:
        raise RegistrationRefused(f"{account_id}: spec_rule arms register on demo probe_only exploring accounts only "
                                  "(ADR 0058)")
    style = style or STYLE_BY_TF[spec.timeframe]
    pot = ex.pots.get(style)
    if pot is None:
        raise RegistrationRefused(f"{account_id}: no {style} pot")
    peers = [a.max_loss for a in ex.arms.values() if a.style == style and a.control_share is None]
    if not peers:
        raise RegistrationRefused(f"{account_id}: no {style} strategy arm sets the style's per-trade cap")
    cap = round(float(max_loss if max_loss is not None else min(peers)), 2)     # pence: journal and config agree
    ceiling = min(max(peers), X.SHARE_CAP * pot.gbp, ex.budget_share * float(acc.daily_loss_limit_abs or 0.0))
    if not 0 < cap <= ceiling + 1e-9:
        raise RegistrationRefused(f"max_loss {cap:g} is outside the {account_id} {style} caps (0, {ceiling:g}]: its "
                                  "strategy arms' largest, SHARE_CAP x the pot (ADR 0057) and the exploration budget")

    new_spec = with_arm(spec_text, arm)
    new_accounts = with_registration(accounts_path.read_text(), account_id, arm_id, style, cap, spec_hash)
    _validate(new_spec, new_accounts, account_id, arm_id, spec_path)
    k_seq, k_digest = ledger.head()
    reg = Registration(arm_id=arm_id, account_id=account_id, spec_hash=spec_hash, timeframe=spec.timeframe,
                       style=style, max_loss=cap, prereg=prereg, k_search=ledger.k_search(), k_null=ledger.k_null(),
                       ledger_seq=k_seq, ledger_digest=k_digest, honesty=honesty, config_hash=str(arm.config_hash),
                       written_utc=now.isoformat(), stage=reached, requested_stage=stage)
    return reg, new_spec, new_accounts


def _validate(spec_text: str, accounts_text: str, account_id: str, arm_id: str, spec_path: Path) -> None:
    """The edited files as the engine would read them: the account loads, and its exploration block runs the arm."""
    with tempfile.TemporaryDirectory() as d:
        sp, ap = Path(d) / "spec_arms.yaml", Path(d) / "accounts.yaml"
        sp.write_text(spec_text)
        ap.write_text(accounts_text)
        try:
            acc = A.load_accounts(ap, logins=False)[account_id]
            cfg = X.config_for(acc, spec_path, sp)
        except ValueError as exc:
            raise RegistrationRefused(f"the registered config does not load: {exc}") from exc
    if cfg is None or arm_id not in {a.arm_id for a in cfg.arms} or arm_id not in A.exploration_arm_caps(acc):
        raise RegistrationRefused(f"{arm_id} would not run on {account_id} (dropped by the engine's config)")


def _write_atomic(path: Path, text: str) -> None:
    tmp = Path(path).with_suffix(".yaml.tmp")
    tmp.write_text(text)
    tmp.replace(path)


def _replay(reg: Registration, *, ledger_path: Path, accounts_path: Path, spec_arms_path: Path, spec_path: Path) -> None:
    """Finish a registration whose journal row exists but whose file edits did not all land (a crash between the row and
    the two writes): rebuild the arm from the journalled spec and write whichever file lacks it. No new journal row, no
    new weekly slot. Both files having it already is `registered already`."""
    in_spec = reg.arm_id in {a.id for a in load_arms(spec_arms_path)}
    acc = A.load_accounts(accounts_path, logins=False).get(reg.account_id)
    in_acc = acc is not None and reg.arm_id in acc.exploration.arms
    if in_spec and in_acc:
        raise RegistrationRefused(f"spec {reg.spec_hash[:12]} is registered already ({reg.arm_id})")
    spec = SearchLedger(ledger_path, mode="real").get_spec(reg.spec_hash)
    if spec is None:
        raise RegistrationRefused(f"journalled spec {reg.spec_hash[:12]} is not in the ledger: cannot replay")
    arm = Arm(id=reg.arm_id, rule=SPEC_RULE, timeframe=spec.timeframe,
              params={"spec": canonical_json(spec), "spec_hash": reg.spec_hash, "prereg": reg.prereg},
              max_open=1, forward_only=True)
    arm = arm.model_copy(update={"config_hash": arm_hash(arm)})
    if arm.config_hash != reg.config_hash:
        raise RegistrationRefused(f"{reg.arm_id}: the rebuilt arm's hash differs from the journalled one: refusing to replay")
    spec_text = spec_arms_path.read_text()
    accounts_text = accounts_path.read_text()
    new_spec = spec_text if in_spec else with_arm(spec_text, arm)
    new_accounts = accounts_text if in_acc else with_registration(accounts_text, reg.account_id, reg.arm_id, reg.style,
                                                                  reg.max_loss, reg.spec_hash)
    _validate(new_spec, new_accounts, reg.account_id, reg.arm_id, spec_path)
    for path, old, text in ((spec_arms_path, spec_text, new_spec), (accounts_path, accounts_text, new_accounts)):
        if text != old:
            _write_atomic(path, text)


def register(spec_hash: str, account_id: str, prereg: str, **kw: Any) -> Registration:
    """`plan`, then the journal row, then the two files (each replaced whole). The journal comes first: a file
    written without its journal row cannot happen. A journal row whose files did not all land (a crash) is completed by
    running the same command again (`_replay`: no new row, no new weekly slot). Refused unless the checkout is on a git
    branch other than main; one registration at a time (file lock)."""
    ledger_path: Path = kw.get("ledger_path", LEDGER_PATH)
    spec_arms_path: Path = kw.get("spec_arms_path", X.SPEC_ARMS_PATH)
    accounts_path: Path = kw.get("accounts_path", A.ACCOUNTS_FILE)
    _require_branch(kw.get("root", ROOT))
    with _locked(ledger_path):
        done = {r.spec_hash: r for r in journal(ledger_path)}
        if spec_hash in done:
            reg = done[spec_hash]
            if reg.account_id != account_id:
                raise RegistrationRefused(f"spec {spec_hash[:12]} is journalled for {reg.account_id}, not {account_id}")
            _replay(reg, ledger_path=ledger_path, accounts_path=accounts_path, spec_arms_path=spec_arms_path,
                    spec_path=kw.get("spec_path", X.SPEC_PATH))
            return reg
        reg, spec_text, accounts_text = plan(spec_hash, account_id, prereg, **kw)
        _append(ledger_path, reg)
        _write_atomic(spec_arms_path, spec_text)
        _write_atomic(accounts_path, accounts_text)
        return reg


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run autodisc-register",
                                 description="Register a discovery survivor as a demo exploration arm (ADR 0058). "
                                             "Journals first, then writes config/spec_arms.yaml and "
                                             "config/accounts.yaml; it places no order and starts nothing.")
    ap.add_argument("spec_hash", nargs="?", help="the survivor's spec hash in the search ledger")
    ap.add_argument("--account", help="a demo probe_only account with exploration enabled")
    ap.add_argument("--prereg", help="the forward reading rule: <path>#<section>")
    ap.add_argument("--verify", action="store_true", help="verify the registration journal's chain and print its head")
    ap.add_argument("--head", help="with --verify: the head digest kept outside the database (detects a deleted tail)")
    ap.add_argument("--ledger", type=Path, default=LEDGER_PATH)
    ap.add_argument("--style", choices=sorted(set(STYLE_BY_TF.values())), default=None)
    ap.add_argument("--max-loss", type=float, default=None,
                    help="per-trade cap, account currency (default: the style's smallest strategy arm cap)")
    ap.add_argument("--dry-run", action="store_true", help="every check, nothing journalled or written")
    ap.add_argument("--stage", choices=["confirm", "explore"], default="confirm",
                    help="confirm (default): only a confirm or holdout passer registers; explore: an explore survivor "
                         "may register as a demo-learning arm (journalled as such)")
    a = ap.parse_args(argv)
    if a.verify:
        try:
            tip = verify(a.ledger, a.head)
        except (RegistrationRefused, sqlite3.Error, OSError) as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 2
        print(json.dumps({"rows": tip.rows, "head": tip.digest}))
        return 0
    if not (a.spec_hash and a.account and a.prereg):
        ap.error("spec_hash, --account and --prereg are required (or --verify)")
    try:
        kw: dict[str, Any] = {"ledger_path": a.ledger, "style": a.style, "max_loss": a.max_loss, "stage": a.stage}
        reg = plan(a.spec_hash, a.account, a.prereg, **kw)[0] if a.dry_run else \
            register(a.spec_hash, a.account, a.prereg, **kw)
    except (RegistrationRefused, ValueError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except (sqlite3.IntegrityError, OSError) as exc:
        print(f"failed: {type(exc).__name__}: {exc}".splitlines()[0], file=sys.stderr)
        return 2
    print(json.dumps({"dry_run": a.dry_run, **json.loads(reg.model_dump_json())}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
