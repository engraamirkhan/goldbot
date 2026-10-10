"""Trial registry: every evaluated variant is recorded with its config hash, fold results and a global
trial counter. The counter feeds the deflated-Sharpe correction, so the more we search the higher
the bar. Stored as JSON lines so it is diffable and survives without a database server.

Research discipline (docs/proposals/2026-10-design-improvements.md, P2):
* a quarterly budget of pre-registered trials (research.trial_budget_quarter): `check_budget` refuses a run that would
  exceed it. The count is every registry row stamped in the quarter, whatever its status (each one was a look at the
  data); the research director plans from the same count (`quarter_trials`), so there is one cap and one count.
  Check, run and record happen under `locked()` so two writers cannot both pass the last free slot;
* a held-out window is scored at most once per configuration (`holdout_scored`, rows with status "holdout");
* the population's shadow -> live promotion needs a research trial of the configuration that passed the design's
  gates (`passed_gates`);
* pre-registration (deferred item L4): `preregister` writes a row with status "preregistered" BEFORE a run, holding its
  config and the rule its result will be read by. A preregistered row is a promise, not a look at the data: it is not
  a trial (`is_trial`), takes no budget slot and does not raise the deflated-Sharpe count; the result row that follows
  carries the same trial number and config hash and a `preregistration` reference;
* the pre-registered queue's reservation (research.reserved_trials_quarter, director.reserved_trials): the manual paths
  (research_pass.py, discovery) check through `check_budget_reserved`, so only a run matching a pending queued
  `preregistered` row (family and config hash) may spend the reserved trials, and it is linked to that row; any other
  run gets budget - used - reserved. A queued row counts in its `target_quarter` (default `planned_quarter`: the
  next quarter when written within PLAN_AHEAD_DAYS of its start), and a trial uses it up whichever quarter it runs in.
  A queued row must be written before its target quarter starts (`director.is_queued`);
  a pre-registration a run writes for itself just before it runs (`preregister`'s default `queue=False`, an ad-hoc
  discovery) is not part of the queue: it neither holds nor uses the reservation;
* a feature-discovery trial (status "discovery", goldbot/research/discovery.py) is ONE trial, but choosing survivors
  from its selection frequencies looks at many features, so `n_trials_effective` (registry trials plus every
  discovery's K_eff: features screened when survivors go forward as features, groups screened only when whole groups
  do) is the count the deflated Sharpe of any later trial uses;
* evidence (row M16): a re-evaluation of an already-registered configuration that chooses nothing, such as
  combinatorial purged CV (research/cpcv.py), is attached to that trial in a sidecar file
  (`<registry>.evidence.jsonl`, `attach_evidence` / `evidence`), never written as a trial row: it takes no budget
  slot and does not raise the deflated-Sharpe count. The sidecar is local to the host that wrote it: registry_sync
  and research.yml do not carry it (evidence is advisory and can only veto; docs/decisions/0002)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

LOCK_STALE_S = 4 * 3600          # a lock older than this was left by a crashed writer (a trial runs well under it)
LOCK_WAIT_S = 6 * 3600


PREREGISTERED = "preregistered"
DISCOVERY = "discovery"
FROM_DISCOVERY_KEY = "from_discovery"   # set on a config whose features came from a discovery trial


class TrialBudgetExceeded(ValueError):
    """The quarter's pre-registered trial budget would be exceeded."""


class PreregisteredForLaterQuarter(TrialBudgetExceeded):
    """The configuration is pre-registered (queued) for a quarter that has not started: running it now would spend this
    quarter's budget on a trial the plan put in a later one, and read it by a rule meant for later data."""


def config_hash(config: dict) -> str:
    return hashlib.sha1(json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12]


def quarter_of(ts: datetime | None = None) -> str:
    ts = ts or datetime.now(timezone.utc)
    if ts.tzinfo is not None:
        ts = ts.astimezone(timezone.utc)
    return f"{ts.year}Q{(ts.month - 1) // 3 + 1}"


PLAN_AHEAD_DAYS = 14             # a queued pre-registration written this close to a quarter's start targets that quarter
_QUARTER_RE = re.compile(r"^\d{4}Q[1-4]$")


def quarter_start(quarter: str) -> datetime:
    """00:00 UTC on the first day of `quarter` ("2027Q1" -> 2027-01-01); ValueError for anything else."""
    if not _QUARTER_RE.match(quarter):
        raise ValueError(f"a quarter looks like 2027Q1, got {quarter!r}")
    return datetime(int(quarter[:4]), 3 * (int(quarter[5]) - 1) + 1, 1, tzinfo=timezone.utc)


def _utcnow() -> datetime:
    """The registry's clock for pre-registrations (a function so tests can set it; callers cannot pass a time)."""
    return datetime.now(timezone.utc)


def planned_quarter(ts: datetime | None = None) -> str:
    """The quarter a queued pre-registration written at `ts` is planned for (its default `target_quarter`): the quarter
    of `ts`, or the next one when `ts` falls within PLAN_AHEAD_DAYS of the next quarter's start (a Q1 plan written in
    the last two weeks of December counts in Q1)."""
    ts = ts or datetime.now(timezone.utc)
    ts = ts.astimezone(timezone.utc) if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)
    return quarter_of(ts + timedelta(days=PLAN_AHEAD_DAYS))


def is_trial(row: dict[str, Any]) -> bool:
    """Every row is a trial (a look at the data) except a pre-registration, which is written before the look."""
    return row.get("status") != PREREGISTERED


def discovery_k_eff(disc: dict[str, Any]) -> int:
    """K_eff of one discovery's results: the recorded `k_eff`, else by its survivor unit: `n_groups_screened` only when
    whole groups go forward, otherwise `n_features_screened` (a feature-level survivor was picked from every screened
    column), falling back to the groups when a row recorded no feature count."""
    if disc.get("k_eff") is not None:
        return int(disc["k_eff"])
    groups = int(disc.get("n_groups_screened") or 0)
    if disc.get("survivor_unit") == "group":
        return groups
    return int(disc.get("n_features_screened") or groups)


def n_trials_effective(rows: list[dict[str, Any]]) -> int:
    """The deflated-Sharpe trial count for a later trial: registry trials plus every discovery trial's K_eff (indicator
    survey 4b: N = registry count + K_eff). A survivor of a discovery was chosen by looking at K_eff units' selection
    frequencies over the whole research window (features, or groups when only whole groups go forward), so its later
    trial pays for them."""
    trials = [r for r in rows if is_trial(r)]
    k_eff = sum(discovery_k_eff((r.get("results") or {}).get("discovery") or {})
                for r in trials if r.get("status") == DISCOVERY)
    return len(trials) + k_eff


def quarter_trials(rows: list[dict[str, Any]], quarter: str) -> int:
    """Trial rows (`is_trial`) stamped (`ts`, UTC) in `quarter`, whatever their status: the one count the budget uses."""
    n = 0
    for r in rows:
        if not is_trial(r):
            continue
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if quarter_of(ts) == quarter:
            n += 1
    return n


class TrialRegistry:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def _rows(self) -> list[dict]:
        return [json.loads(line) for line in self.path.read_text().splitlines() if line.strip()]

    @property
    def n_trials(self) -> int:
        return sum(1 for r in self._rows() if is_trial(r))

    @property
    def n_trials_effective(self) -> int:
        return n_trials_effective(self._rows())

    def record(self, *, agent_id: str, family: str, config: dict, feature_version: str, rationale: str,
               results: dict, status: str = "evaluated", budget_quarter: str | None = None,
               preregistration: dict[str, Any] | None = None) -> dict:
        row: dict[str, Any] = {
            "trial": self.n_trials + 1,
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent_id": agent_id, "family": family, "config_hash": config_hash(config), "config": config,
            "feature_version": feature_version, "rationale": rationale, "results": results, "status": status,
        }
        if budget_quarter is not None:
            row["budget_quarter"] = budget_quarter
        if preregistration is not None:
            row["preregistration"] = {"ts": preregistration["ts"], "trial": preregistration["trial"],
                                      "config_hash": preregistration["config_hash"]}
        self._append(row)
        return row

    def _append(self, row: dict[str, Any]) -> None:
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, default=str) + "\n")

    def preregister(self, *, agent_id: str, family: str, config: dict, feature_version: str, rationale: str,
                    reading_rule: str, plan: dict[str, Any] | None = None, queue: bool = False,
                    target_quarter: str | None = None) -> dict:
        """Write the pre-registration of the next trial BEFORE it runs: its config (hashed as the result row will be),
        the rule its result will be read by, and the plan (e.g. the number of features to be screened). Not a trial:
        it carries the number the result row will take. By default (`queue=False`) it is the pre-registration a run
        writes for itself just before it runs: not part of the pre-registered queue, it neither holds nor uses the
        quarter's reservation (director.reserved_trials). `queue=True` writes a row of the queue, stamped with the
        `target_quarter` it is planned for (default `planned_quarter(now)`), which must not have started yet (ValueError
        otherwise: a row written once its quarter is under way would only be an ad-hoc run spending the reservation);
        it holds and is counted in that quarter's reservation, and only a trial run in that quarter or later uses it
        up. The row is stamped with the registry's clock (`_utcnow`), never a time passed in: a queued row cannot be
        backdated to before its quarter. `target_quarter` without `queue=True` is a ValueError (it would be ignored)."""
        if target_quarter is not None and not queue:
            raise ValueError("target_quarter is only for a queued pre-registration: pass queue=True as well")
        ts = _utcnow()
        row: dict[str, Any] = {
            "trial": self.n_trials + 1,
            "ts": ts.isoformat(),
            "agent_id": agent_id, "family": family, "config_hash": config_hash(config), "config": config,
            "feature_version": feature_version, "rationale": rationale, "results": {}, "status": PREREGISTERED,
            "reading_rule": reading_rule, "plan": plan or {},
        }
        if not queue:
            row["queue"] = False
        else:
            target = target_quarter or planned_quarter(ts)
            if ts >= quarter_start(target):
                raise ValueError(
                    f"a queued pre-registration must be written before its target quarter starts ({target} is under "
                    f"way): pass target_quarter= a later quarter, or queue=False for an ad-hoc pre-registration")
            row["target_quarter"] = target
        self._append(row)
        return row

    def preregistration(self, family: str, config: dict) -> dict | None:
        """The latest pre-registration of exactly this configuration, or None."""
        h = config_hash(config)
        rows = [r for r in self._rows() if r.get("status") == PREREGISTERED and r.get("family") == family
                and r.get("config_hash") == h]
        return rows[-1] if rows else None

    def row(self, trial: int) -> dict | None:
        """The trial row (not a pre-registration) numbered `trial`, or None."""
        return next((r for r in self._rows() if is_trial(r) and int(r.get("trial", -1)) == int(trial)), None)

    # ------------------------------------------------------------------ evidence on existing trials (not trials)
    @property
    def evidence_path(self) -> Path:
        return self.path.with_name(self.path.stem + ".evidence.jsonl")

    def attach_evidence(self, trial: int, kind: str, payload: dict[str, Any],
                        now: datetime | None = None) -> dict[str, Any]:
        """Attach a re-evaluation (e.g. kind "cpcv") to an existing trial row; raises KeyError for an unknown trial.
        Written to the evidence sidecar, so the trial count, the budget and the deflated Sharpe are unchanged.
        `now`: the time it is stamped with (the scheduler's slot), default the wall clock."""
        row = self.row(trial)
        if row is None:
            raise KeyError(f"no trial #{trial} in {self.path}")
        ts = now or datetime.now(timezone.utc)
        ev = {"trial": int(trial), "kind": kind, "ts": ts.isoformat(), "family": row.get("family"),
              "config_hash": row.get("config_hash"), "quarter": quarter_of(ts), "payload": payload}
        with open(self.evidence_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, default=str) + "\n")
        return ev

    def evidence(self, trial: int | None = None, kind: str | None = None) -> list[dict[str, Any]]:
        """Evidence rows, oldest first, optionally for one trial and/or kind."""
        if not self.evidence_path.exists():
            return []
        rows = [json.loads(line) for line in self.evidence_path.read_text().splitlines() if line.strip()]
        return [e for e in rows
                if (trial is None or e.get("trial") == int(trial)) and (kind is None or e.get("kind") == kind)]

    def by_family(self, family: str) -> list[dict]:
        return [r for r in self._rows() if r["family"] == family]

    def best(self, family: str, key: str = "sharpe_ann") -> dict | None:
        rows = [r for r in self.by_family(family) if key in r.get("results", {})]
        return max(rows, key=lambda r: r["results"][key]) if rows else None

    # ------------------------------------------------------------------ research discipline
    def budget_used(self, quarter: str) -> int:
        return quarter_trials(self._rows(), quarter)

    def check_budget(self, n_new: int, cap: int, now: datetime | None = None) -> str:
        """Raise TrialBudgetExceeded if `n_new` more trials would exceed the cap of the quarter they will be recorded in
        (the wall clock's: rows are stamped when written); returns the quarter."""
        q = quarter_of(now)
        used = self.budget_used(q)
        if used + n_new > cap:
            raise TrialBudgetExceeded(
                f"trial budget exceeded: {q} allows {cap} pre-registered trials, {used} already run, {n_new} requested. "
                f"Every trial raises the deflated-Sharpe bar for all later ones; pre-register fewer variants, wait for "
                f"next quarter, or raise research.trial_budget_quarter in config/settings.yaml (an owner decision).")
        return q

    def check_budget_reserved(self, jobs: Sequence[tuple[str, dict[str, Any]]], cap: int, reserved_setting: int,
                              now: datetime | None = None) -> tuple[str, list[dict[str, Any] | None]]:
        """`check_budget` for `jobs` ((family, config) each) that honours the pre-registered queue's reservation, as
        the analyst, the director and the label grid do (a job whose config is queued for a later quarter is refused,
        PreregisteredForLaterQuarter): a job matching a pending queued `preregistered` row of the
        quarter (`director.pending_preregistration`: same family and config hash) may use the full `cap` and must be
        recorded linked to that row (so the reservation goes down by one); every other job must fit in
        cap - used - reserved (`director.reserved_trials`, `reserved_setting` = research.reserved_trials_quarter).
        Raises TrialBudgetExceeded; returns (the quarter, the matching pre-registration or None, per job)."""
        # director imports this module, so the import is local
        from goldbot.research.director import (
            later_preregistration,
            later_refusal,
            pending_preregistration,
            reserved_trials,
        )
        q = quarter_of(now)
        rows = self._rows()
        preregs: list[dict[str, Any] | None] = []
        for family, config in jobs:
            p = pending_preregistration(rows, q, family, config_hash(config))
            later = None if p is not None else later_preregistration(rows, q, family, config_hash(config))
            if later is not None:
                raise PreregisteredForLaterQuarter(later_refusal(family, later))
            preregs.append(None if p is not None and any(p is c for c in preregs) else p)   # one row covers one run
        n_open = sum(p is None for p in preregs)
        reserved = reserved_trials(rows, q, reserved_setting).reserved
        if n_open:
            try:
                self.check_budget(n_open, max(cap - reserved, 0), now)
            except TrialBudgetExceeded as exc:
                raise TrialBudgetExceeded(
                    f"{exc} ({reserved} of the quarter's {cap} are held for the pre-registered queue, "
                    f"research.reserved_trials_quarter; a run may use them only when it matches a pending "
                    f"preregistered row, same family and config hash)") from None
        self.check_budget(len(preregs), cap, now)
        return q, preregs

    def holdout_scored(self, family: str, config: dict) -> bool:
        h = config_hash(config)
        return any(r.get("status") == "holdout" and r.get("family") == family and r.get("config_hash") == h
                   for r in self._rows())

    def passed_gates(self, family: str, config: dict) -> bool:
        """A walk-forward trial (not a holdout scoring) of exactly this configuration passed the design's gates. A
        configuration chosen from a feature-discovery trial (config key FROM_DISCOVERY_KEY) must ALSO have passed its
        holdout scoring: its features were picked on the same window the walk-forward scored, which the deflated
        Sharpe corrects only partly (quant review; preregistration-2027Q1.md)."""
        h = config_hash(config)
        rows = self._rows()
        gates_ok = any(r.get("status") == "evaluated" and r.get("family") == family and r.get("config_hash") == h
                       and ((r.get("results") or {}).get("gates") or {}).get("passed") is True for r in rows)
        if not gates_ok or not config.get(FROM_DISCOVERY_KEY):
            return gates_ok
        return any(r.get("status") == "holdout" and r.get("family") == family and r.get("config_hash") == h
                   and ((r.get("results") or {}).get("holdout_verdict") or {}).get("passed") is True for r in rows)

    @contextmanager
    def locked(self, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S) -> Iterator[None]:
        """Exclusive lock beside the registry file, portable (Windows VPS and Linux runners): created with
        O_CREAT | O_EXCL, removed on exit; a lock older than `stale_s` is taken over (its writer crashed)."""
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        deadline = time.monotonic() + wait_s
        while True:
            try:
                fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > stale_s:
                        lock.unlink(missing_ok=True)
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() > deadline:
                    raise TimeoutError(f"trial registry lock {lock} held for more than {wait_s:.0f}s") from None
                time.sleep(0.5)
        try:
            os.write(fd, f"{os.getpid()} {datetime.now(timezone.utc).isoformat()}".encode())
            os.close(fd)
            yield
        finally:
            lock.unlink(missing_ok=True)
