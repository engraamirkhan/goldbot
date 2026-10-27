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
  lists it. A file that cannot be read means NO arm is read (fail closed: everything stays blinded).
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import Field

from goldbot.base import FrozenRecord, UtcTimestamp

log = logging.getLogger(__name__)

__all__ = ["LABELS_PATH", "READS_PATH", "SHADOW_LABEL", "ArmInfo", "ArmRead", "Catalog", "account_label", "arm_family",
           "catalog", "load_catalog"]

CONFIG = Path(__file__).parents[2] / "config"
LABELS_PATH = CONFIG / "strategy_labels.yaml"
READS_PATH = CONFIG / "arm_reads.yaml"
SHADOW_LABEL = "Shadow (paper)"
CONTROL_RULES = frozenset({"random_bracket"})       # labs.CONTROL_RULES: the random-entry null


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


class ArmInfo(FrozenRecord):
    arm_id: str
    family: str
    timeframe: str
    tag: str | None = None              # the variant tag (config/strategy_labels.yaml `arms`)
    is_control: bool = False
    style: str | None = None            # the ADR 0043 sleeve it is registered in (scalp / intraday / swing)

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


def arm_family(rule: str, params: dict[str, object]) -> str:
    """An arm's family: its rule, or for a `specialist` arm the specialist it runs."""
    spec = params.get("specialist") if rule == "specialist" else None
    return spec if isinstance(spec, str) and spec else rule


def account_label(account: str | None) -> str:
    """The owner's word for an account (labs.ROLE); a shadow trade has none."""
    from goldbot.telegram.labs import role
    return SHADOW_LABEL if account is None else role(account)


def _registrations() -> tuple[set[str], dict[str, str]]:
    """(arms registered as controls on any account, arm -> its sleeve) from config/accounts.yaml; empty when unreadable."""
    try:
        from goldbot.ops.accounts import load_accounts
        accounts = load_accounts(logins=False)
    except Exception as exc:             # noqa: BLE001 - labels only: an unreadable file leaves the spec's controls
        log.warning("strategy labels: accounts.yaml unreadable (%s)", type(exc).__name__)
        return set(), {}
    controls: set[str] = set()
    styles: dict[str, str] = {}
    for acc in accounts.values():
        for arm_id, reg in acc.exploration.arms.items():
            if reg.control_share is not None:
                controls.add(arm_id)
            if reg.style:
                styles.setdefault(arm_id, reg.style)
    return controls, styles


def _reads(path: Path) -> dict[str, ArmRead]:
    try:
        raw = yaml.safe_load(path.read_text()) or {}
        return {str(k): ArmRead.model_validate(v) for k, v in (raw.get("reads") or {}).items()}
    except Exception as exc:             # noqa: BLE001 - fail closed: no arm counts as read
        log.error("arm reads unreadable (%s): every arm stays blinded", type(exc).__name__)
        return {}


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
    controls, styles = _registrations()
    by_id = {a.id: a for a in spec}
    arms = {}
    for a in spec:
        src = by_id.get(a.twin_of or "", a)            # a broker twin takes its source's tag
        tag = labels.arms.get(a.id) or labels.arms.get(src.id)
        control = (a.rule in CONTROL_RULES or a.rule.endswith("_control") or a.id in controls)
        arms[a.id] = ArmInfo(arm_id=a.id, family=arm_family(a.rule, a.params), timeframe=a.timeframe,
                             tag=tag.variant if tag else None, is_control=control, style=styles.get(a.id))
    return Catalog(labels=labels, arms=arms, reads=_reads(reads_path))


@lru_cache(maxsize=1)
def catalog() -> Catalog:
    """The process's catalog (config is read once; a deploy restarts the API)."""
    return load_catalog()
