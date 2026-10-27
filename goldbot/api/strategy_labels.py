"""The owner's names for strategies, arms and accounts, and which arms are read (docs/design/trades-ux-2026-10-27.md
§2.1-2.2, ADR 0050). Read-only; nothing here trades.

Design rules kept:
* "No raw ids on screen" (design-system rule 3): a family is named from config/strategy_labels.yaml; a family the
  file does not name falls back to its key in words ("box_retest" -> "Box retest"), and tests/test_strategy_labels.py
  fails CI for any running arm or specialist family without a label.
* An arm's family is its frozen rule (config/exploration_arms.yaml); a `specialist` arm's is its `params.specialist`.
  Its variant is the timeframe, plus the arm's tag when two running arms share a family and a timeframe.
* Controls are the null (quant 2026-10-26): an arm with a `random_bracket` rule, a `*_control` rule, or registered
  with a `control_share` on any account (config/accounts.yaml).
* Read sizes and units are the evaluator's, in config/arm_reads.yaml: `read_at` (the pre-registered n of every arm
  that has a read; "each arm is read once, at its 300th admissible trade", H-61, unless its prereg says otherwise),
  `read_units` (families read POOLED, e.g. W1, W2, H-80, H-81: "the n is the family's pooled count"; quant
  2026-10-27; `counted` names the arms whose fills make the n when others are virtual exits on them, H-80) and
  `counts` (an arm whose n is not its trades, S0R: stop exits). A unit is read only once `reads` lists it (the arm
  id, or the unit id). An arm not in `read_at`: a strategy arm reads at READ_AT; a control has no read. A file that
  cannot be read, or whose units disagree on n, means NO unit is read and every arm is its own unit at READ_AT (fail
  closed: no read verdict is shown and no arm is promoted on a guess).
* Each strategy arm is measured against one control (the strategy board, item 132), in this order: a control whose
  frozen `params.control` names the arm; else a control of the arm's own family (`<family>_control`, or the same rule)
  on its timeframe and a shared account; else a generic random control (one of no strategy's family) on a shared
  account and sleeve, preferring the arm's pre-registration, then its timeframe, then the spec's order.
"""
from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp

if TYPE_CHECKING:
    from goldbot.research.arms import Arm

log = logging.getLogger(__name__)

__all__ = ["LABELS_PATH", "READS_PATH", "READ_AT", "SHADOW_LABEL", "ArmInfo", "ArmRead", "Catalog", "account_label",
           "arm_family", "catalog", "load_catalog"]

CONFIG = Path(__file__).parents[2] / "config"
LABELS_PATH = CONFIG / "strategy_labels.yaml"
READS_PATH = CONFIG / "arm_reads.yaml"
SHADOW_LABEL = "Shadow (paper)"
CONTROL_RULES = frozenset({"random_bracket"})       # labs.CONTROL_RULES: the random-entry null
READ_AT = 300                                       # prereg H-61 (labs.READ_AT): "read once, at its 300th admissible trade"


class FamilyLabel(FrozenRecord):
    label: str = Field(min_length=1, max_length=40)
    why: str = Field(min_length=1, max_length=70)


class ArmVariant(FrozenRecord):
    variant: str | None = Field(default=None, min_length=1, max_length=30)
    # a rule edit is a new arm id with its own config_hash (config/exploration_arms.yaml); its version starts a new
    # ranking row ("v2", n from 0; ADR 0053: one more registry trial)
    version: int = Field(default=1, ge=1)


class Labels(FrozenRecord):
    families: dict[str, FamilyLabel] = Field(default_factory=dict)
    arms: dict[str, ArmVariant] = Field(default_factory=dict)


class ArmRead(FrozenRecord):
    read_utc: UtcTimestamp
    verdict: str = Field(min_length=1)
    # the annealing state the read put the arm in (volume-500 plan 4.1): C1 candidate, P proven, R retired. H-61: "not
    # graduated at 300 -> retired", so a row that does not say is R
    state: Literal["C1", "P", "R"] = "R"
    # the evaluator's RECORDED comparison with the control, as its prereg defines it (e.g. H-81 section 4: the day-paired
    # family minus control; the controls are never read themselves). Never computed by a view (quant 2026-10-27)
    vs_control_r: float | None = None
    vs_control_ci90: tuple[float, float] | None = None


class ReadUnit(FrozenRecord):
    """A family read pooled (config/arm_reads.yaml `read_units`)."""
    arms: list[str] = Field(min_length=1)
    counted: list[str] | None = None    # the arms whose fills make the pooled n (default: all of `arms`)


class ReadsFile(FrozenRecord):
    reads: dict[str, ArmRead] = Field(default_factory=dict)
    read_at: dict[str, int] = Field(default_factory=dict)
    read_units: dict[str, ReadUnit] = Field(default_factory=dict)
    counts: dict[str, Literal["trades", "stop_exits"]] = Field(default_factory=dict)


class ArmInfo(FrozenRecord):
    arm_id: str
    family: str
    timeframe: str
    tag: str | None = None              # the variant tag (config/strategy_labels.yaml `arms`)
    is_control: bool = False
    style: str | None = None            # the ADR 0043 sleeve it is registered in (scalp / intraday / swing)
    accounts: tuple[str, ...] = ()      # the accounts that register it (config/accounts.yaml)
    running: bool = True                # spec status `run` (a blocked arm has no rule to trade)
    control_arm: str | None = None      # the control it is measured against (module docstring); None for a control
    unit: str = ""                      # its read unit: the arm id, or the pooled family's unit id
    read_n: int | None = READ_AT        # the unit's read size; None: no read registered (a control)
    counted: bool = True                # its fills count toward its unit's n (False: virtual exits on another's)
    count: Literal["trades", "stop_exits"] = "trades"
    version: int = 1                    # its rule version (strategy_labels `arms: <id>: {version: 2}`): a ranking row

    @property
    def pooled(self) -> bool:
        return bool(self.unit) and self.unit != self.arm_id

    @property
    def variant(self) -> str:
        return f"{self.timeframe} · {self.tag}" if self.tag else self.timeframe


def _words(key: str) -> str:
    text = key.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else "Unnamed strategy"


class Catalog(FrozenRecord):
    labels: Labels
    arms: dict[str, ArmInfo]
    reads: dict[str, ArmRead]           # by read unit

    def family_label(self, family: str) -> str:
        f = self.labels.families.get(family)
        return f.label if f is not None else _words(family)

    def why(self, family: str) -> str:
        f = self.labels.families.get(family)
        return f.why if f is not None else f"{_words(family)} signal"

    def arm(self, arm_id: str) -> ArmInfo | None:
        return self.arms.get(arm_id)

    def unit_of(self, arm_id: str) -> str:
        """The arm's read unit (an arm the spec does not know is its own unit)."""
        info = self.arms.get(arm_id)
        return info.unit if info is not None and info.unit else arm_id

    def read_of(self, arm_id: str) -> ArmRead | None:
        return self.reads.get(self.unit_of(arm_id))

    def is_read(self, arm_id: str) -> bool:
        """Whether the arm's read unit is read (a pooled family's arms are read together)."""
        return self.unit_of(arm_id) in self.reads

    def read_size(self, arm_id: str) -> int | None:
        """The n its unit's single read is taken at (the evaluator's `read_at`); None when no read is registered."""
        info = self.arms.get(arm_id)
        return info.read_n if info is not None else READ_AT


def arm_family(rule: str, params: dict[str, object]) -> str:
    """An arm's family: its rule, or for a `specialist` arm the specialist it runs."""
    spec = params.get("specialist") if rule == "specialist" else None
    return spec if isinstance(spec, str) and spec else rule


def account_label(account: str | None) -> str:
    """The owner's word for an account (labs.ROLE); a shadow trade has none."""
    from goldbot.telegram.labs import role
    return SHADOW_LABEL if account is None else role(account)


def _registrations() -> tuple[set[str], dict[str, str], dict[str, tuple[str, ...]]]:
    """(arms registered as controls on any account, arm -> its sleeve, arm -> the accounts registering it) from
    config/accounts.yaml; empty when unreadable."""
    try:
        from goldbot.ops.accounts import load_accounts
        accounts = load_accounts(logins=False)
    except Exception as exc:             # noqa: BLE001 - labels only: an unreadable file leaves the spec's controls
        log.warning("strategy labels: accounts.yaml unreadable (%s)", type(exc).__name__)
        return set(), {}, {}
    controls: set[str] = set()
    styles: dict[str, str] = {}
    where: dict[str, list[str]] = {}
    for name, acc in accounts.items():
        for arm_id, reg in acc.exploration.arms.items():
            if reg.control_share is not None:
                controls.add(arm_id)
            if reg.style:
                styles.setdefault(arm_id, reg.style)
            where.setdefault(arm_id, []).append(name)
    return controls, styles, {a: tuple(sorted(v)) for a, v in where.items()}


def _reads_file(path: Path) -> ReadsFile:
    """config/arm_reads.yaml. Fail closed: a broken file, a size below 1, an arm in two units, a unit's arms of
    different sizes or `counted` outside its arms reads NO unit and pools nothing."""
    try:
        f = ReadsFile.model_validate(yaml.safe_load(path.read_text()) or {})
        seen: set[str] = set()
        for uid, u in f.read_units.items():
            sizes = {f.read_at.get(a) for a in u.arms}
            if len(sizes) != 1 or None in sizes or seen & set(u.arms) or not set(u.counted or u.arms) <= set(u.arms):
                raise ValueError(f"read unit {uid}: arms without one shared read size, listed twice or not counted")
            seen |= set(u.arms)
        if any(v < 1 for v in f.read_at.values()):
            raise ValueError("a read size below 1")
        return f
    except Exception as exc:             # noqa: BLE001 - fail closed: no arm counts as read
        log.error("arm reads unreadable (%s): no arm counts as read", type(exc).__name__)
        return ReadsFile()


def _prereg(arm: Arm) -> str | None:
    """The pre-registration file an arm was frozen under (`params.prereg` or `params.spec`, without the anchor)."""
    p = arm.params.get("prereg") or arm.params.get("spec")
    return str(p).split("#", 1)[0] if p else None


def _owns(control_family: str, family: str) -> bool:
    """A control is of `family` when it runs the same rule (G1C) or `<base>_control` with `family` == base or
    `<base>_...` (F1C hurst_band_break_control -> hurst_band_break, C30C low30_control -> low30_touch)."""
    base = control_family.removesuffix("_control")
    return family == base or family.startswith(base + "_")


def _pairs(spec: list[Arm], arms: dict[str, ArmInfo]) -> dict[str, str]:
    """Strategy arm -> the control it is measured against (module docstring, in that order of preference)."""
    by_id = {a.id: a for a in spec}
    strategy_families = {i.family for i in arms.values() if not i.is_control}
    controls = [i for i in arms.values() if i.is_control and i.running]
    out: dict[str, str] = {}
    for info in arms.values():
        if info.is_control:
            continue
        shared = [c for c in controls if not info.accounts or set(c.accounts) & set(info.accounts)]
        token = re.compile(rf"(?<![A-Z0-9]){re.escape(info.arm_id)}(?![A-Z0-9])")
        named = [c for c in shared if token.search(str(by_id[c.arm_id].params.get("control", "")))]
        own = [c for c in shared if _owns(c.family, info.family) and c.timeframe == info.timeframe]
        generic = [c for c in shared if not any(_owns(c.family, f) for f in strategy_families)
                   and (c.style is None or info.style is None or c.style == info.style)]
        prereg = _prereg(by_id[info.arm_id])
        generic.sort(key=lambda c: (prereg is None or _prereg(by_id[c.arm_id]) != prereg, c.timeframe != info.timeframe))
        pick = (named or own or generic)[:1]
        if pick:
            out[info.arm_id] = pick[0].arm_id
    return out


def load_catalog(labels_path: Path = LABELS_PATH, spec_path: Path | None = None,
                 reads_path: Path = READS_PATH) -> Catalog:
    from goldbot.engine.exploration import SPEC_PATH
    from goldbot.research.arms import load_arms
    try:
        labels = Labels.model_validate(yaml.safe_load(labels_path.read_text()) or {})
    except Exception as exc:             # noqa: BLE001 - names fall back to the keys in words
        log.warning("strategy labels unreadable (%s): keys shown in words", type(exc).__name__)
        labels = Labels()
    try:
        spec = load_arms(spec_path or SPEC_PATH, check_hash=False)
    except (OSError, ValueError) as exc:
        log.warning("arm spec unreadable (%s): arms named by their ledger rows", type(exc).__name__)
        spec = []
    controls, styles, where = _registrations()
    rf = _reads_file(reads_path)
    unit_of = {a: (uid, u) for uid, u in rf.read_units.items() for a in u.arms}
    by_id = {a.id: a for a in spec}
    arms = {}
    for a in spec:
        src = by_id.get(a.twin_of or "", a)            # a broker twin takes its source's tag
        tag = labels.arms.get(a.id) or labels.arms.get(src.id)
        own = labels.arms.get(a.id)
        control = (a.rule in CONTROL_RULES or a.rule.endswith("_control") or a.id in controls)
        family = arm_family(a.rule, a.params)
        uid, u = unit_of.get(a.id, (a.id, None))
        n = rf.read_at[a.id] if a.id in rf.read_at else None if control else READ_AT
        arms[a.id] = ArmInfo(arm_id=a.id, family=family, timeframe=a.timeframe,
                             tag=tag.variant if tag else None, version=own.version if own else 1,
                             is_control=control, style=styles.get(a.id),
                             accounts=where.get(a.id, ()), running=a.status == "run", unit=uid, read_n=n,
                             counted=u is None or a.id in (u.counted or u.arms), count=rf.counts.get(a.id, "trades"))
    pairs = _pairs(spec, arms)
    arms = {k: v.model_copy(update={"control_arm": pairs.get(k)}) for k, v in arms.items()}
    return Catalog(labels=labels, arms=arms, reads=rf.reads)


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    """The process's catalog (config is read once; a deploy restarts the API)."""
    return load_catalog()
