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
* "Each arm is read once, at its 300th admissible trade" (prereg H-61): an arm is read only once config/arm_reads.yaml
  lists it. A file that cannot be read means NO arm is read (fail closed: everything stays blinded). An arm whose
  pre-registration reads it at another n is listed under `read_at` in the same file (default READ_AT).
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
    variant: str = Field(min_length=1, max_length=30)


class Labels(FrozenRecord):
    families: dict[str, FamilyLabel] = Field(default_factory=dict)
    arms: dict[str, ArmVariant] = Field(default_factory=dict)


class ArmRead(FrozenRecord):
    read_utc: UtcTimestamp
    verdict: str = Field(min_length=1)
    # the annealing state the read put the arm in (volume-500 plan 4.1): C1 candidate, P proven, R retired. H-61: "not
    # graduated at 300 -> retired", so a row that does not say is R
    state: Literal["C1", "P", "R"] = "R"


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

    @property
    def variant(self) -> str:
        return f"{self.timeframe} · {self.tag}" if self.tag else self.timeframe


def _words(key: str) -> str:
    text = key.replace("_", " ").replace("-", " ").strip()
    return text[:1].upper() + text[1:] if text else "Unnamed strategy"


class Catalog(FrozenRecord):
    labels: Labels
    arms: dict[str, ArmInfo]
    reads: dict[str, ArmRead]
    read_at: dict[str, int] = Field(default_factory=dict)

    def family_label(self, family: str) -> str:
        f = self.labels.families.get(family)
        return f.label if f is not None else _words(family)

    def why(self, family: str) -> str:
        f = self.labels.families.get(family)
        return f.why if f is not None else f"{_words(family)} signal"

    def arm(self, arm_id: str) -> ArmInfo | None:
        return self.arms.get(arm_id)

    def is_read(self, arm_id: str) -> bool:
        return arm_id in self.reads

    def read_size(self, arm_id: str) -> int:
        """The n its single read is taken at: its pre-registration's (`read_at`), else H-61's READ_AT."""
        return self.read_at.get(arm_id, READ_AT)


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


def _reads(path: Path) -> tuple[dict[str, ArmRead], dict[str, int]]:
    """(the recorded reads, the arms read at another n than READ_AT). Fail closed: no arm read, every size READ_AT."""
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        reads = {str(k): ArmRead.model_validate(v) for k, v in (raw.get("reads") or {}).items()}
        sizes = {str(k): int(v) for k, v in (raw.get("read_at") or {}).items()}
        if any(v < 1 for v in sizes.values()):
            raise ValueError("a read size below 1")
        return reads, sizes
    except Exception as exc:             # noqa: BLE001 - fail closed: no arm counts as read
        log.error("arm reads unreadable (%s): every arm stays blinded", type(exc).__name__)
        return {}, {}


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
    by_id = {a.id: a for a in spec}
    arms = {}
    for a in spec:
        src = by_id.get(a.twin_of or "", a)            # a broker twin takes its source's tag
        tag = labels.arms.get(a.id) or labels.arms.get(src.id)
        control = (a.rule in CONTROL_RULES or a.rule.endswith("_control") or a.id in controls)
        arms[a.id] = ArmInfo(arm_id=a.id, family=arm_family(a.rule, a.params), timeframe=a.timeframe,
                             tag=tag.variant if tag else None, is_control=control, style=styles.get(a.id),
                             accounts=where.get(a.id, ()), running=a.status == "run")
    pairs = _pairs(spec, arms)
    arms = {k: v.model_copy(update={"control_arm": pairs.get(k)}) for k, v in arms.items()}
    reads, sizes = _reads(reads_path)
    return Catalog(labels=labels, arms=arms, reads=reads, read_at=sizes)


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    """The process's catalog (config is read once; a deploy restarts the API)."""
    return load_catalog()
