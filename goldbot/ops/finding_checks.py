"""The registered checks of the findings register (config/findings.yaml, goldbot/ops/findings.py; owner 2026-11-20:
"feedback must close itself").

Design rule, as built here: a finding is closed only by a measurement on the brain's own data, never by a status
someone types. Each row of the register names one check below and its parameters; the check returns a verdict:

  pass         the measurement says the finding is fixed (the row reads `closed`)
  fail         it says it is not (the value names the offending accounts, days or arms)
  cant_check   the data is missing or unreadable: never a pass, and past its due date it counts as overdue

A check reads only: aux.db opened read-only, the trade ledger (`goldbot.ops.learning_ledger`), the last health report
(state/health_last.json), files in this checkout, and the GitHub API through a read-only `RepoReader` (the keyring's
github-token; none -> cant_check). Nothing here touches an order, a limit, an arm or a setting. A check that raises is
reported cant_check by the caller (`findings.evaluate`), never a pass.

  controls_to_signal_ratio  A4 / P8: broker-filled random controls per signal (exploration) trade, per account, over
                            the last `days` (cost probes are neither)
  trades_per_day            owner decision 2026-10-30: demo trades a day, every account, each completed weekday of the
                            last `days` inside [min, max]
  bleeding_arms             no arm with mean net R below `max_mean_r` over n >= `min_n` in `days` still trading
  mean_net_r                A1: the book's mean net R over `days` at least `min_mean_r` (too thin: cant_check)
  arms_not_trading          a decided retirement or replay-only switch took effect: no fill of the named arms
  failed_but_filled         A6: exploration sends reported failed that the look-up found filled, at most `max_count`
  learner_outputs           the learner trains: rows in each aux.db output table (models, weights)
  file_exists               a document exists in this checkout (a postmortem, a published ledger)
  file_text                 a regex present in / absent from files of this checkout (a hook, an ADR status, a pin)
  state_field_set           a field of an existing state file is set (the backup's last prune)
  health_ok                 named health checks ok in the last report (fnmatch patterns)
  github_issue_closed       a numbered issue is closed
  github_security           repository security features enabled (secret scanning, push protection, Dependabot)
  manual                    a step with no data trail: closed only by a dated entry with evidence in the register
                            (reviewed in its PR); use it only where nothing can be measured
"""
from __future__ import annotations

import fnmatch
import re
import sqlite3
from collections import Counter, defaultdict
from collections.abc import Callable
from datetime import date
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, Protocol

import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from goldbot.base import FrozenRecord, Record, UtcTimestamp
from goldbot.config import Settings

if TYPE_CHECKING:
    from goldbot.engine.exploration import ExplorationRecord
    from goldbot.ops.learning_ledger import LearningCloses

Verdict = Literal["pass", "fail", "cant_check"]
VALUE_CHARS = 400
_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class CheckResult(FrozenRecord):
    verdict: Verdict
    reason: str
    value: str | None = None


class RepoReader(Protocol):
    """Read-only GitHub reads (`GitHubIssues` on the brain, a stub in tests)."""

    def issue(self, number: int) -> dict[str, Any]: ...

    def repository(self) -> dict[str, Any]: ...


class FindingsContext(Record):
    """What the checks read. The trade sources are loaders, called at most once per run (`memo`)."""
    state_dir: Path
    repo_root: Path
    now: UtcTimestamp
    settings: Settings | None = None
    github: Any = None                                 # a RepoReader (GitHubIssues); None: GitHub not read
    closes_loader: Callable[[], Any] | None = None    # -> LearningCloses; default: learning_ledger.read_learning_closes
    records_loader: Callable[[], Any] | None = None   # -> {trade id: ExplorationRecord}; default: trades_ledger.exploration_records
    memo: dict[str, Any] = Field(default_factory=dict)

    def closes(self) -> LearningCloses:
        if "closes" not in self.memo:
            if self.closes_loader is not None:
                self.memo["closes"] = self.closes_loader()
            else:
                from goldbot.ops.learning_ledger import read_learning_closes
                self.memo["closes"] = read_learning_closes(self.state_dir, self.now)
        out: LearningCloses = self.memo["closes"]
        return out

    def records(self) -> dict[str, ExplorationRecord]:
        if "records" not in self.memo:
            if self.records_loader is not None:
                self.memo["records"] = self.records_loader()
            else:
                from goldbot.api.trades_ledger import exploration_records
                self.memo["records"] = exploration_records(self.state_dir)
        out: dict[str, ExplorationRecord] = self.memo["records"]
        return out


CheckFn = Callable[[FindingsContext, Any], CheckResult]


class CheckDef(FrozenRecord):
    name: str
    params: type[BaseModel]
    fn: CheckFn


CHECKS: dict[str, CheckDef] = {}


def register(name: str, params: type[BaseModel]) -> Callable[[CheckFn], CheckFn]:
    """Decorator: `fn(ctx, params)` answers rows whose check is `name`, its params validated by `params` at load."""
    def add(fn: CheckFn) -> CheckFn:
        if name in CHECKS:
            raise ValueError(f"finding check {name} is registered twice")
        CHECKS[name] = CheckDef(name=name, params=params, fn=fn)
        return fn
    return add


def parse_params(name: str, params: dict[str, Any]) -> BaseModel:
    """`params` validated for check `name`; ValueError on an unknown check or a bad parameter."""
    spec = CHECKS.get(name)
    if spec is None:
        raise ValueError(f"unknown finding check {name!r} (registered: {', '.join(sorted(CHECKS))})")
    return spec.params.model_validate(params)


def _clip(text: str) -> str:
    return text if len(text) <= VALUE_CHARS else text[: VALUE_CHARS - 3] + "..."


# --------------------------------------------------------------------------------------------- trade-based checks
Kind = Literal["exploration", "control", "cost_probe"]
KINDS: tuple[Kind, ...] = ("exploration", "control", "cost_probe")


def _every_kind() -> list[Kind]:
    return list(KINDS)


def _arm_kinds() -> list[Kind]:
    return ["exploration", "control"]                 # arm trades; a cost probe has no arm


def _window(ctx: FindingsContext, days: int) -> tuple[pd.Timestamp, pd.Timestamp]:
    return ctx.now - pd.Timedelta(days=days), ctx.now


def _unreadable(ctx: FindingsContext) -> CheckResult | None:
    closes = ctx.closes()
    if closes.unreadable:
        return CheckResult(verdict="cant_check", reason=f"trade ledger source unreadable: {', '.join(closes.unreadable)}")
    return None


def _traded(ctx: FindingsContext, days: int) -> list[Any] | CheckResult:
    """The closed trades opened in the last `days`, or cant_check when the ledger is unreadable or holds none: an
    empty window (a weekend, a stopped book, a state dir without trades) is no evidence, never a pass."""
    if (bad := _unreadable(ctx)) is not None:
        return bad
    since, end = _window(ctx, days)
    rows = [c for c in ctx.closes().rows if since <= c.opened_utc < end]
    if not rows:
        return CheckResult(verdict="cant_check", reason=f"no trade opened in the last {days} d")
    return rows


class RatioParams(FrozenRecord):
    max_ratio: float = Field(1.0, gt=0)
    days: int = Field(7, ge=1, le=60)


@register("controls_to_signal_ratio", RatioParams)
def controls_to_signal_ratio(ctx: FindingsContext, p: RatioParams) -> CheckResult:
    """A4 (owner volume decision 2026-10-30, "controls <= 1:1"): per account, broker-filled control trades over signal
    (exploration) trades opened in the window. An account with controls and no signal trade fails; one with neither is
    not judged. Replay-only controls never reach the broker, so they never count."""
    rows = _traded(ctx, p.days)
    if isinstance(rows, CheckResult):
        return rows
    ctrl: Counter[str] = Counter(c.account for c in rows if c.kind == "control")
    sig: Counter[str] = Counter(c.account for c in rows if c.kind == "exploration")
    parts, over = [], []
    for acc in sorted(set(ctrl) | set(sig)):
        ratio = ctrl[acc] / sig[acc] if sig[acc] else float("inf")
        txt = f"{acc} {ctrl[acc]}:{sig[acc]}" + (f" ({ratio:.1f}:1)" if sig[acc] else "")
        parts.append(txt)
        if ratio > p.max_ratio:
            over.append(txt)
    value = "; ".join(parts) or "no trades"
    if over:
        return CheckResult(verdict="fail", value=_clip(value),
                           reason=f"controls:signal over {p.max_ratio:g}:1 in {p.days} d on " + "; ".join(over))
    return CheckResult(verdict="pass", value=_clip(value), reason=f"controls:signal at most {p.max_ratio:g}:1 in {p.days} d")


class PerDayParams(FrozenRecord):
    min: int = Field(..., ge=0)
    max: int = Field(..., ge=1)
    days: int = Field(5, ge=1, le=30)
    kinds: list[Kind] = Field(default_factory=_every_kind)

    @model_validator(mode="after")
    def _band(self) -> PerDayParams:
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


def _weekdays_before(now: pd.Timestamp, n: int) -> list[date]:
    """The `n` completed UTC weekdays before `now`'s day, oldest first."""
    out: list[date] = []
    day = now.normalize() - pd.Timedelta(days=1)
    while len(out) < n:
        if day.dayofweek < 5:
            out.append(day.date())
        day -= pd.Timedelta(days=1)
    return out[::-1]


@register("trades_per_day", PerDayParams)
def trades_per_day(ctx: FindingsContext, p: PerDayParams) -> CheckResult:
    """Owner decision 2026-10-30: 150-250 demo trades a day over every account. Counted by entry (UTC day) over the
    closed trades of the ledger, each of the last `days` completed weekdays; a trade still open is not counted yet (a
    1h/4h hold crossing midnight, a handful at most). A market holiday counts as a day like any other."""
    if (bad := _unreadable(ctx)) is not None:
        return bad
    days = _weekdays_before(ctx.now, p.days)
    first = pd.Timestamp(days[0], tz="UTC")
    per: Counter[date] = Counter(c.opened_utc.date() for c in ctx.closes().rows
                                 if c.kind in p.kinds and c.opened_utc >= first)
    value = ", ".join(f"{d:%m-%d} {per[d]}" for d in days)
    out = [f"{d:%m-%d} {per[d]}" for d in days if not p.min <= per[d] <= p.max]
    if out:
        return CheckResult(verdict="fail", value=value,
                           reason=f"{len(out)} of {len(days)} weekdays outside {p.min}-{p.max} a day: {', '.join(out)}")
    return CheckResult(verdict="pass", value=value, reason=f"every weekday of {len(days)} inside {p.min}-{p.max} a day")


class BleedParams(FrozenRecord):
    max_mean_r: float = Field(..., le=0)
    min_n: int = Field(..., ge=5)
    days: int = Field(14, ge=1, le=90)
    active_days: int = Field(2, ge=1, le=14)
    kinds: list[Kind] = Field(default_factory=_arm_kinds)


@register("bleeding_arms", BleedParams)
def bleeding_arms(ctx: FindingsContext, p: BleedParams) -> CheckResult:
    """Performance audit 2026-11-20 (leaks 1, 5): no (account, arm) whose mean net R over its trades opened in the
    last `days` is below `max_mean_r` on at least `min_n` trades still trading (an entry in the last `active_days`).
    Censored trades (closed by anything but the arm's own stop, target or hold) and trades without R are left out."""
    rows = _traded(ctx, p.days)
    if isinstance(rows, CheckResult):
        return rows
    active_from = ctx.now - pd.Timedelta(days=p.active_days)
    rs: dict[tuple[str, str], list[float]] = defaultdict(list)
    last: dict[tuple[str, str], pd.Timestamp] = {}
    for c in rows:
        if c.kind not in p.kinds or c.arm_id is None:
            continue
        key = (c.account, c.arm_id)
        last[key] = max(last.get(key, c.opened_utc), c.opened_utc)
        if c.r is not None and not c.censored:
            rs[key].append(c.r)
    bleeding = []
    for key, vals in sorted(rs.items()):
        mean = sum(vals) / len(vals)
        if len(vals) >= p.min_n and mean < p.max_mean_r and last[key] >= active_from:
            bleeding.append(f"{key[1]}@{key[0]} {mean:+.2f} R n {len(vals)}")
    if bleeding:
        return CheckResult(verdict="fail", value=_clip("; ".join(bleeding)),
                           reason=f"{len(bleeding)} arm(s) below {p.max_mean_r:+.2f} R on n >= {p.min_n} still trading")
    return CheckResult(verdict="pass", value=f"{len(rs)} arm(s) read",
                       reason=f"no arm below {p.max_mean_r:+.2f} R on n >= {p.min_n} still trading")


class FilledParams(FrozenRecord):
    days: int = Field(14, ge=1, le=90)
    max_count: int = Field(0, ge=0)


@register("failed_but_filled", FilledParams)
def failed_but_filled(ctx: FindingsContext, p: FilledParams) -> CheckResult:
    """A6 / P4 (finance audit 2026-11-20 N7): exploration sends the broker answered without a fill or a definite
    reject that the look-up then found filled (`resolution` adopted or opened_closed), sent in the window. Cost probes
    are not in this source (4 of the audit's 13)."""
    since, end = _window(ctx, p.days)
    sent = [r for r in ctx.records().values() if since <= r.sent_utc < end]
    if not sent:
        return CheckResult(verdict="cant_check", reason=f"no exploration send in the last {p.days} d")
    hits: Counter[str] = Counter(r.account_id for r in sent if r.resolution in ("adopted", "opened_closed"))
    n = sum(hits.values())
    value = "; ".join(f"{a} {k}" for a, k in sorted(hits.items())) or "none"
    if n > p.max_count:
        return CheckResult(verdict="fail", value=value, reason=f"{n} send(s) reported failed but filled in {p.days} d")
    return CheckResult(verdict="pass", value=value, reason=f"{n} send(s) reported failed but filled in {p.days} d")


class MeanRParams(FrozenRecord):
    min_mean_r: float = 0.0
    min_n: int = Field(100, ge=10)
    days: int = Field(20, ge=1, le=90)
    kinds: list[Kind] = Field(default_factory=_arm_kinds)


@register("mean_net_r", MeanRParams)
def mean_net_r(ctx: FindingsContext, p: MeanRParams) -> CheckResult:
    """Finance audit A1/N1-N3 ("losing, significantly"): the mean net R of the trades of `kinds` opened in the last
    `days`, censored ones and ones without R left out, at least `min_mean_r` on at least `min_n` trades. Fewer than
    `min_n`: cant_check (too thin to call it fixed). A point estimate, not a test: it closes the finding when the book
    stops losing, it does not certify an edge (that stays with the gates)."""
    if (bad := _unreadable(ctx)) is not None:
        return bad
    since, end = _window(ctx, p.days)
    rs = [c.r for c in ctx.closes().rows
          if c.kind in p.kinds and c.r is not None and not c.censored and since <= c.opened_utc < end]
    if len(rs) < p.min_n:
        return CheckResult(verdict="cant_check", value=f"n {len(rs)}", reason=f"{len(rs)} trades in {p.days} d, need {p.min_n}")
    mean = sum(rs) / len(rs)
    value = f"{mean:+.3f} R n {len(rs)}"
    if mean < p.min_mean_r:
        return CheckResult(verdict="fail", value=value, reason=f"mean net R {value} in {p.days} d, under {p.min_mean_r:+.2f}")
    return CheckResult(verdict="pass", value=value, reason=f"mean net R {value} in {p.days} d")


class StoppedParams(FrozenRecord):
    arms: list[str] = Field(..., min_length=1)
    days: int = Field(2, ge=1, le=30)


@register("arms_not_trading", StoppedParams)
def arms_not_trading(ctx: FindingsContext, p: StoppedParams) -> CheckResult:
    """A decided retirement or replay-only switch took effect: no broker fill of the named arms (exploration or
    control) opened in the last `days` while the book traded (no trade at all in the window, a weekend: cant_check)."""
    rows = _traded(ctx, p.days)
    if isinstance(rows, CheckResult):
        return rows
    want = set(p.arms)
    hits: Counter[str] = Counter(c.arm_id for c in rows if c.arm_id is not None and c.arm_id in want)
    if hits:
        value = "; ".join(f"{a} {n}" for a, n in sorted(hits.items()))
        return CheckResult(verdict="fail", value=value, reason=f"still filling in {p.days} d: {value}")
    return CheckResult(verdict="pass", value="none", reason=f"no fill of {', '.join(p.arms)} in {p.days} d")


# --------------------------------------------------------------------------------------------- state and files
class StateFieldParams(FrozenRecord):
    file: str = Field(..., pattern=r"^[\w.-]+\.json$")
    field: str = Field(..., pattern=r"^[\w]+(\.[\w]+)*$")


@register("state_field_set", StateFieldParams)
def state_field_set(ctx: FindingsContext, p: StateFieldParams) -> CheckResult:
    """A field of an existing state file (a file name in state/, dotted path) is present and not null: the backup's
    `last_prune_ts` once retention has run (SRE audit 2026-11-20 #7). No such file: cant_check."""
    import json
    path = ctx.state_dir / p.file
    try:
        doc: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return CheckResult(verdict="cant_check", reason=f"{p.file} unreadable ({type(exc).__name__})")
    for part in p.field.split("."):
        doc = doc.get(part) if isinstance(doc, dict) else None
    if doc is None:
        return CheckResult(verdict="fail", reason=f"{p.file} {p.field} not set")
    return CheckResult(verdict="pass", value=_clip(str(doc)), reason=f"{p.file} {p.field} = {_clip(str(doc))}")


class TextParams(FrozenRecord):
    patterns: list[str] = Field(..., min_length=1)
    regex: str = Field(..., min_length=1)
    expect: Literal["present", "absent"]

    @field_validator("patterns")
    @classmethod
    def _inside(cls, v: list[str]) -> list[str]:
        for pat in v:
            pp = PurePosixPath(pat)
            if pp.is_absolute() or ".." in pp.parts:
                raise ValueError("patterns must be relative to the checkout, without '..'")
        return v

    @field_validator("regex")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v, re.MULTILINE)
        return v


@register("file_text", TextParams)
def file_text(ctx: FindingsContext, p: TextParams) -> CheckResult:
    """Text in files of this checkout (globs relative to the repository root, regex in MULTILINE mode). `present`:
    some matching file contains it (a gitleaks hook in .pre-commit-config.yaml); `absent`: no matching file does (an
    ADR on an order path still `Status: proposed`, a vulnerable pin in uv.lock). No file matches the globs: cant_check."""
    rx = re.compile(p.regex, re.MULTILINE)
    files = sorted({f for pat in p.patterns for f in ctx.repo_root.glob(pat) if f.is_file()})
    if not files:
        return CheckResult(verdict="cant_check", reason=f"no file matches {', '.join(p.patterns)}")
    hits = [str(f.relative_to(ctx.repo_root)) for f in files if rx.search(f.read_text(encoding="utf-8", errors="replace"))]
    value = ", ".join(hits[:6]) + (f" (+{len(hits) - 6})" if len(hits) > 6 else "") if hits else "none"
    ok = bool(hits) if p.expect == "present" else not hits
    what = f"{len(hits)} of {len(files)} file(s) match"
    return CheckResult(verdict="pass" if ok else "fail", value=_clip(value), reason=f"{what} (expected {p.expect})")
class LearnerParams(FrozenRecord):
    tables: list[str] = Field(default_factory=lambda: ["exploration_models", "exploration_weights"], min_length=1)
    min_rows: int = Field(1, ge=1)

    @field_validator("tables")
    @classmethod
    def _idents(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _IDENT.match(t):
                raise ValueError(f"bad table name {t!r}")
        return v


@register("learner_outputs", LearnerParams)
def learner_outputs(ctx: FindingsContext, p: LearnerParams) -> CheckResult:
    """Research audit 2026-11-20 section 3 ("Does the learner train? No."): each named aux.db table holds at least
    `min_rows` rows. aux.db is opened read-only; a missing file is cant_check, a missing table a fail."""
    from goldbot.db.connection import db_path
    path = db_path(ctx.state_dir, "aux")
    if not path.exists():
        return CheckResult(verdict="cant_check", reason="no aux.db on this host")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            counts = {t: (con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] if t in have else None)
                      for t in p.tables}
        finally:
            con.close()
    except sqlite3.Error as exc:
        return CheckResult(verdict="cant_check", reason=f"aux.db unreadable ({type(exc).__name__})")
    value = ", ".join(f"{t} {'no table' if n is None else n}" for t, n in counts.items())
    short = [t for t, n in counts.items() if n is None or n < p.min_rows]
    if short:
        return CheckResult(verdict="fail", value=value, reason=f"under {p.min_rows} row(s): {', '.join(short)}")
    return CheckResult(verdict="pass", value=value, reason="every learner output table has rows")


class FileParams(FrozenRecord):
    pattern: str = Field(..., min_length=1)
    min_count: int = Field(1, ge=1)

    @field_validator("pattern")
    @classmethod
    def _inside(cls, v: str) -> str:
        pp = PurePosixPath(v)
        if pp.is_absolute() or ".." in pp.parts:
            raise ValueError("pattern must be relative to the checkout, without '..'")
        return v


@register("file_exists", FileParams)
def file_exists(ctx: FindingsContext, p: FileParams) -> CheckResult:
    """A document exists in this checkout (glob relative to the repository root): a postmortem in docs/incidents/, a
    committed ledger in docs/finance/. The brain deploys main, so a file counts once it is merged."""
    found = sorted(str(f.relative_to(ctx.repo_root)) for f in ctx.repo_root.glob(p.pattern) if f.is_file())
    value = ", ".join(found[:5]) + (f" (+{len(found) - 5})" if len(found) > 5 else "") if found else "none"
    if len(found) < p.min_count:
        return CheckResult(verdict="fail", value=value, reason=f"{len(found)} file(s) match {p.pattern}, need {p.min_count}")
    return CheckResult(verdict="pass", value=value, reason=f"{len(found)} file(s) match {p.pattern}")


class HealthParams(FrozenRecord):
    names: list[str] = Field(..., min_length=1)
    allow_warn: bool = False


@register("health_ok", HealthParams)
def health_ok(ctx: FindingsContext, p: HealthParams) -> CheckResult:
    """Every health check matching `names` (fnmatch: `reconcile:*`) is ok in the last report the Telegram service
    stored (state/health_last.json `statuses`); warn passes only with `allow_warn`. No match: cant_check."""
    from goldbot.base import load_record_json
    from goldbot.ops.checks._base import WATCH_FILE, WatchState
    path = ctx.state_dir / WATCH_FILE
    try:
        watch = load_record_json(WatchState, path.read_text(encoding="utf-8"), source=WATCH_FILE)
    except (OSError, ValueError) as exc:
        return CheckResult(verdict="cant_check", reason=f"no readable health report ({type(exc).__name__})")
    hit = {n: s for n, s in watch.statuses.items() if any(fnmatch.fnmatchcase(n, pat) for pat in p.names)}
    if not hit:
        return CheckResult(verdict="cant_check", reason=f"no health check matches {', '.join(p.names)}")
    ok = ("ok", "warn") if p.allow_warn else ("ok",)
    bad = sorted(f"{n} {s}" for n, s in hit.items() if s not in ok)
    value = ", ".join(f"{n} {s}" for n, s in sorted(hit.items()))
    if bad:
        return CheckResult(verdict="fail", value=_clip(value), reason=f"not ok: {', '.join(bad)}")
    return CheckResult(verdict="pass", value=_clip(value), reason=f"{len(hit)} check(s) ok")


# --------------------------------------------------------------------------------------------- GitHub
class IssueParams(FrozenRecord):
    number: int = Field(..., ge=1)


@register("github_issue_closed", IssueParams)
def github_issue_closed(ctx: FindingsContext, p: IssueParams) -> CheckResult:
    """A numbered GitHub issue is closed (read with the keyring's github-token; none: cant_check)."""
    if ctx.github is None:
        return CheckResult(verdict="cant_check", reason="no github-token: GitHub not read")
    state = str(ctx.github.issue(p.number).get("state", "?"))
    return CheckResult(verdict="pass" if state == "closed" else "fail", value=f"#{p.number} {state}",
                       reason=f"issue #{p.number} is {state}")


SecurityFeature = Literal["secret_scanning", "secret_scanning_push_protection", "dependabot_security_updates",
                          "secret_scanning_non_provider_patterns", "secret_scanning_validity_checks"]


class SecurityParams(FrozenRecord):
    features: list[SecurityFeature] = Field(..., min_length=1)


@register("github_security", SecurityParams)
def github_security(ctx: FindingsContext, p: SecurityParams) -> CheckResult:
    """Security audit 2026-11-20 F1: the repository's `security_and_analysis` features are enabled. GitHub returns
    that block only to an admin token; without it the answer is cant_check (never a pass)."""
    if ctx.github is None:
        return CheckResult(verdict="cant_check", reason="no github-token: GitHub not read")
    block = ctx.github.repository().get("security_and_analysis")
    if not isinstance(block, dict):
        return CheckResult(verdict="cant_check", reason="the token cannot read the repository's security settings")
    states = {f: str((block.get(f) or {}).get("status", "absent")) for f in p.features}
    value = ", ".join(f"{f} {s}" for f, s in states.items())
    off = [f for f, s in states.items() if s != "enabled"]
    if off:
        return CheckResult(verdict="fail", value=value, reason=f"not enabled: {', '.join(off)}")
    return CheckResult(verdict="pass", value=value, reason="every listed security feature is enabled")


# --------------------------------------------------------------------------------------------- manual
class ManualParams(FrozenRecord):
    done_on: date | None = None
    evidence: str | None = None

    @model_validator(mode="after")
    def _both(self) -> ManualParams:
        if (self.done_on is None) != (not self.evidence):
            raise ValueError("a manual close needs both done_on and evidence")
        return self


@register("manual", ManualParams)
def manual(ctx: FindingsContext, p: ManualParams) -> CheckResult:
    """A step with no data trail: closed only by a dated entry with its evidence (a commit, a PR, a document),
    written into the register and reviewed in its PR. Open until then, and overdue past its due date."""
    if p.done_on is not None and p.evidence:
        return CheckResult(verdict="pass", value=f"{p.done_on}", reason=f"done {p.done_on}: {p.evidence}")
    return CheckResult(verdict="fail", reason="not done (no dated evidence in the register)")
