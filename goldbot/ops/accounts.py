"""Account registry + credential prompting.

* `config/accounts.yaml` holds everything except secrets and MT5 login numbers (the repo is public): a login lives in
  the keyring under `mt5-login-<account_id>`, next to the password `mt5-<account_id>`; a login left in the yaml is
  still read, but nothing writes one there.
* Secrets live in the OS keyring (macOS Keychain / Windows Credential Manager) via `keyring`, falling back
  to state/.secrets.json only when no keyring backend exists AND GOLDBOT_ALLOW_PLAINTEXT=1 (headless Linux CI; never
  on macOS/Windows). That file is plaintext, owner-only (0600, replaced atomically), not encrypted; an existing one is
  moved into the keyring on first read once a keyring works (`migrate` does it on demand).
* `get_credential()` asks the user when a secret is missing: on a terminal it prompts with getpass; when
  running headless it calls the registered `prompter` (the Telegram bot), which sends the request to the
  allow-listed owner and waits. The value is stored and never logged.
* Live accounts can only be enabled through `unlock_live()`, which requires the phase gate file to say
  the paper -> tiny-live gate has passed AND an explicit confirmation phrase.

CLI:
  python -m goldbot.ops.accounts list
  python -m goldbot.ops.accounts add icm-demo          # prompts for login + password, stores in keyring
  python -m goldbot.ops.accounts set tradingview-webhook-secret
  python -m goldbot.ops.accounts set smtp-password      # the SMTP (Gmail App) password for email sign-in links
  ... set <key> --stdin                                  # the value from a pipe (no prompt; never echoed)
  ... export <key> | ssh <host> goldbot accounts set <key> --stdin   # copy a secret; refuses a terminal stdout
  python -m goldbot.ops.accounts bridge-serve icm-demo # MT5 box: listen address + a new token (printed once)
  python -m goldbot.ops.accounts bridge-use icm-demo   # engine box: bridge URL + that token
  python -m goldbot.ops.accounts unlock-live icm-live  # refused unless the gate has passed
"""
from __future__ import annotations

import getpass
import hashlib
import json
import logging
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Collection, Iterator, Literal

import yaml
from pydantic import ConfigDict, Field, field_validator, model_validator

from goldbot.base import FrozenRecord, Record, write_private
from goldbot.config import ROOT
from goldbot.data.timeutil import server_zone
from goldbot.risk.limits_profile import RiskOverrides

ACCOUNTS_FILE = ROOT / "config" / "accounts.yaml"
ACCOUNT_ID_LOGIN = re.compile(r"\d{5,}")      # an account name with this could carry an MT5 login: refused at load
# BACKLOG 63: the account name starts every proposal id, which a Telegram Approve/Reject button carries with the card
# stamp in at most 64 bytes (goldbot.telegram.approvals.decision_callbacks); a longer name is refused at load
ACCOUNT_ID_MAX_BYTES = 24
PHASE_FILE = ROOT / "state" / "phase_state.json"
# an engine file of an account accounts.yaml does not list is shown while written this recently (engines write at
# least every 15m bar close, and at every tick while trading): a newer engine than the brain's config is never hidden
UNLISTED_FRESH_S = 10 * 60
ENGINE_FILE_PREFIX = "engine_"                # state/engine_<account>.json (goldbot/engine/store.py writes it)
SERVICE = "goldbot"
log = logging.getLogger(__name__)

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]

_PLATFORM = sys.platform
# Opt-in for the plaintext fallback (state/.secrets.json): CI / headless Linux only, and only when no keyring backend
# exists. macOS and Windows never fall back (a missing keyring there is a broken install: fix it, do not write plaintext).
ALLOW_PLAINTEXT_ENV = "GOLDBOT_ALLOW_PLAINTEXT"


_HHMM = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


class ScheduledProbe(FrozenRecord):
    """The measured-cost protocol's M1 arm (docs/research/prereg-cost-measurement-2026-10.md, section 1): "0.01 lot at
    a fixed slot time; side = +1 on even slot index, -1 on odd ... market close after 30 s; server SL at $3 from fill
    (safety only)", one slot per 15m close (goldbot/engine/cost_probe.py `scheduled_step`). Off unless set; it needs the
    cost probe on (so a demo account), and it trades through the probes' RiskGate check like every probe."""
    enabled: bool = False
    # section 3: "a hold <= 60 s, closed by the engine": closed this many seconds after the fill on the host clock,
    # whether entries are halted or not (exits are never gated)
    hold_s: int = Field(30, ge=1, le=60)
    # section 3: "a stop of at most $3 from the fill", USD per oz; its loss at 0.01 lot stays within max_loss too
    stop_usd: float = Field(3.0, gt=0, le=3.0)
    # no slot in this window (HH:MM wall time in `blackout_tz`, start inclusive, end exclusive): the rollover, the
    # daily break (17:00-18:00 New York) and the reopen minute; nor after the Friday weekend cut (server time) or on
    # a UTC weekend day. In New York time so it moves with US DST (code review 2026-10-20: a fixed UTC window left
    # winter slots in the break and on the reopen)
    blackout: tuple[str, str] = ("16:45", "18:15")
    blackout_tz: str = "America/New_York"
    # prereg K10: scheduled probes' realised loss + open risk take at most this share of the day's probe budget
    budget_share: float = Field(0.5, gt=0, le=0.5)

    @field_validator("blackout")
    @classmethod
    def _hhmm(cls, v: tuple[str, str]) -> tuple[str, str]:
        if not all(_HHMM.fullmatch(x) for x in v):
            raise ValueError("cost_probe.scheduled.blackout is two HH:MM times (in blackout_tz)")
        return v

    @field_validator("blackout_tz")
    @classmethod
    def _tz(cls, v: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"cost_probe.scheduled.blackout_tz is not an IANA time zone: {v!r}") from exc
        return v


class CostProbe(FrozenRecord):
    """ADR 0037 (the research director's ruling 2026-10-19): minimum-lot demo orders mirroring challengers' shadow
    entries, for measured costs (goldbot/engine/cost_probe.py). Off unless set; demo accounts only."""
    enabled: bool = False
    # trading-safety review: no new probe once today's realised probe loss reaches this share of the day's loss
    # allowance, nor while less than this share of the allowance remains (real trades keep the rest)
    budget_share: float = Field(0.2, gt=0, le=1)
    # ADR 0037 amendments (rulings 2026-10-19): at most this many probe positions open at once (amendment 3 allows 4)
    max_open: int = Field(2, ge=1, le=4)
    # the most one probe may lose at its strategy's own stop (the broker's loss_per_lot at 0.01 lot), in the account
    # currency (icm-demo: £4); a wider own stop skips the probe (never clamped, architect trace 2026-10-25), and a
    # probe whose loss cannot be computed is refused
    max_loss: float = Field(4.0, gt=0)
    # the measured-cost protocol's scheduled market probes (M1); off by default
    scheduled: ScheduledProbe = Field(default_factory=ScheduledProbe)

    @model_validator(mode="after")
    def _scheduled_needs_probes(self) -> CostProbe:
        if self.scheduled.enabled and not self.enabled:
            raise ValueError("cost_probe.scheduled needs cost_probe.enabled (demo accounts only, ADR 0037)")
        return self


class ExplorationArm(FrozenRecord):
    """One registered exploration arm (plan 2026-10-25): the RiskGate refuses an arm id that is not registered here."""
    # the most one entry of this arm may lose at the stop the RiskGate sets, at the minimum lot, account currency;
    # equal passes, above is refused (`exploration_arm_cap`). Never clamped: a wider stop is a refused entry
    max_loss: float = Field(gt=0)
    # ADR 0043 section 1: the sleeve (a key of `Exploration.pots`) the arm draws on, and a random control's fixed
    # share of it (controls are the null: never allocated by results); None = a strategy arm (research weights)
    style: str | None = None
    control_share: float | None = Field(None, gt=0, le=1)


class ExplorationPot(FrozenRecord):
    """ADR 0043 section 1: a style's sleeve, enforced by the engine (goldbot/engine/exploration.py): today's realised
    loss + open risk at the stops + the new trade's loss at its stop <= `gbp` (account currency), and at most
    `max_open` of its arms' positions open."""
    gbp: float = Field(gt=0)
    max_open: int = Field(ge=1)


class Exploration(FrozenRecord):
    """Exploration arms (plan 2026-10-25, lane L1): minimum-lot demo entries that trade to MEASURE an edge, so the
    RiskGate's purpose `exploration` skips only its expected-value rules, inside its fence (goldbot/risk/gate.py
    `_exploration_reasons`). Off unless set; refused at load unless the account is demo and probe_only."""
    enabled: bool = False
    # share of the day's loss allowance (daily_loss_limit_abs) the arms together may lose; with cost_probe.budget_share
    # at most 1, so exploration and the cost probes never claim more than the day
    budget_share: float = Field(0.1, gt=0, le=1)
    # at most this many exploration positions open at once, all arms together (the engine's count)
    max_open_total: int = Field(1, ge=1)
    # arm id -> its config; an arm not listed is refused by the gate (`exploration_arm_unregistered`)
    arms: dict[str, ExplorationArm] = Field(default_factory=dict)
    # ADR 0043: style -> its sleeve (scalp, intraday, swing); every enabled arm names one, and the sleeves together fit
    # inside budget_share x daily_loss_limit_abs
    pots: dict[str, ExplorationPot] = Field(default_factory=dict)


def exploration_arm_caps(acc: Account) -> dict[str, float]:
    """`RiskLimits.exploration_arms` for this account: each registered arm's per-trade loss cap; empty (every
    exploration entry refused) unless exploration is enabled on a demo probe_only account."""
    ex = acc.exploration
    if not (ex.enabled and acc.mode == "demo" and acc.probe_only):
        return {}
    return {arm: a.max_loss for arm, a in ex.arms.items()}


class Account(Record):
    account_id: str
    broker: str
    mode: Literal["demo", "live"]
    server: str
    login: int | None
    terminal_path: str
    server_tz: str
    symbol: str
    magic_base: int
    enabled: bool
    # BACKLOG 29 / ADR 0004: the owner's absolute daily loss limit for THIS account, in its own deposit currency
    # (icm-demo: 50 = £50). None = off. Per account because one number read in different currencies is not one limit
    daily_loss_limit_abs: float | None = Field(None, gt=0)
    # the owner's declared account type: the baseline until the classifier stores a measured class (it keeps running;
    # a measured class that disagrees makes the stricter one win and health warns: execution/classifier.effective_class)
    account_class: Literal["raw", "standard"] | None = None
    # ADR 0032: the owner's demo ceilings ("approve demo risk limits: daily cap £50, min-lot cap 1%, weekly 10%, demo
    # only", 2026-10-18). Accepted on a demo account only, and only next to daily_loss_limit_abs (which still binds)
    risk_overrides: RiskOverrides | None = None
    # ADR 0036, the owner 2026-10-18: "for the demo account, the trades will be opened and closed by the system, no
    # confirmation is needed, for real account with real money, stick to original plan of confirming from me".
    # auto on a demo account only (refused at load otherwise); the engine re-checks the terminal's own trade mode
    approval_mode: Literal["propose", "auto"] = "propose"
    # ADR 0037: the demo cost probe (min-lot mirror of challengers' shadow entries); refused at load unless mode: demo
    cost_probe: CostProbe = Field(default_factory=CostProbe)
    # ADR 0037 amendment 4: this account's engine runs the shadow book (state/shadow_book.json, shared by the state
    # dir, so a new host continues the books). At most one ENABLED account is the host (refused at load); the engine
    # hosts only on the canonical-cost broker (goldbot/ops/services/engine.is_shadow_host)
    shadow_host: bool = False
    # ADR 0037 amendment 4 ("probes must never consume the real trades' allowance"): a dedicated probe account. Its
    # engine has no live agents and refuses any entry that is not a cost probe or an exploration arm (fail closed,
    # Engine._execute). Amendment 6: a probe host runs cost probes with the shadow book, or exploration, or both
    probe_only: bool = False
    # PD decision 2026-10-26 (ADR 0043 host cap): the RiskGate's open-position cap for THIS account, replacing
    # risk.max_positions_per_account (2) here only. Accepted on a demo probe_only account only (refused at load
    # otherwise); None = the global value
    max_positions: int | None = Field(None, ge=1, le=18)
    # owner 2026-10-19 (docs/design/dynamic-limits-2026-10-19.md): "loss per trade, daily loss ... dynamic according to
    # strategy". On: per-strategy risk per trade from the nightly controller, tighten-only intraday factors and the
    # probes' loss and count from their own stops and the budget (cost_probe.probe_limits); the configured values stay
    # the ceilings and the fallback. Off (the default) is today's behaviour; on a live account it is owner-only
    dynamic_limits: bool = False
    # plan 2026-10-25 (exploration arms): minimum-lot demo entries that measure an edge; refused at load unless the
    # account is demo and probe_only, with its budget share and the cost probe's together at most the day
    exploration: Exploration = Field(default_factory=Exploration)

    # backstop for records built outside load_accounts: validation errors never carry the input (names, logins)
    model_config = ConfigDict(hide_input_in_errors=True)      # merged with Record's config by pydantic

    @field_validator("account_id")
    @classmethod
    def _no_login_in_name(cls, v: str) -> str:
        """Backstop of load_accounts' own check (goldbot/telegram/entry_text.account_name is the display backstop)."""
        _check_account_name(v)
        return v

    @field_validator("broker")
    @classmethod
    def _broker_slug(cls, v: str) -> str:
        """The broker key names evidence, cost tables and model paths (state/models/h62/<broker>/): a lowercase slug."""
        if not re.fullmatch(r"[a-z0-9_-]+", v):
            raise ValueError("broker must be a lowercase slug (a-z, 0-9, _ and -)")
        return v

    @field_validator("server_tz")
    @classmethod
    def _known_server_tz(cls, v: str) -> str:
        """`NY+7` (US-switching UTC+2/+3) or an IANA name; a typo fails at load, never shifts timestamps silently."""
        try:
            server_zone(v)
        except Exception as exc:
            raise ValueError(f"server_tz {v!r} is neither NY+7 nor an IANA zone ({type(exc).__name__})") from exc
        return v

    @model_validator(mode="after")
    def _overrides_demo_only(self) -> Account:
        """ADR 0032: "demo only". risk_overrides on any other mode is refused at load, never ignored silently; and the
        5% ceiling is safe only under the account's absolute daily limit (ADR 0004: min of the two applies)."""
        if self.risk_overrides is not None:
            if self.mode != "demo":
                raise ValueError("risk_overrides are allowed on demo accounts only (ADR 0032)")
            if self.daily_loss_limit_abs is None:
                raise ValueError("risk_overrides need daily_loss_limit_abs on the same account (ADR 0004, ADR 0032)")
        return self

    @model_validator(mode="after")
    def _auto_demo_only(self) -> Account:
        """ADR 0036: "for real account with real money, stick to original plan of confirming from me". A live account
        configured auto fails at load, loudly; it is never run as propose quietly."""
        if self.approval_mode == "auto" and self.mode != "demo":
            raise ValueError("approval_mode auto is allowed on demo accounts only (ADR 0036): real money asks the owner")
        return self

    @model_validator(mode="after")
    def _probe_demo_only(self) -> Account:
        """ADR 0037: probes are demo orders. A live account with cost_probe enabled fails at load, loudly; it is never
        ignored quietly (the engine's start and every probe order check again)."""
        if self.cost_probe.enabled and self.mode != "demo":
            raise ValueError("cost_probe is allowed on demo accounts only (ADR 0037): a live account never probes")
        cap = self.daily_loss_limit_abs
        if self.cost_probe.enabled and cap is not None and self.cost_probe.max_loss > self.cost_probe.budget_share * cap:
            raise ValueError("cost_probe.max_loss must fit inside budget_share x daily_loss_limit_abs (ADR 0037)")
        return self

    @model_validator(mode="after")
    def _max_positions_on_the_probe_host_only(self) -> Account:
        """PD 2026-10-26: a per-account max_positions only on a demo probe_only account (where nothing real is at
        stake); every other account keeps the global risk.max_positions_per_account."""
        if self.max_positions is not None and (self.mode != "demo" or not self.probe_only):
            raise ValueError("max_positions is allowed on demo probe_only accounts only (PD 2026-10-26, ADR 0043)")
        return self

    @model_validator(mode="after")
    def _probe_only_is_a_probe_host(self) -> Account:
        """ADR 0037 amendment 4: a probe_only account exists to mirror the shadow book's challengers at the minimum
        lot. Amendment 6 (PD 2026-10-26, ADR 0043 per-style hosts): "or to run exploration arms", so a second demo
        account can host other arms without the shadow book. With neither it would trade nothing, so the config is
        refused at load; demo only either way."""
        if not self.probe_only:
            return self
        if self.mode != "demo":
            raise ValueError("probe_only is allowed on demo accounts only (ADR 0037): a live account never probes")
        if not ((self.cost_probe.enabled and self.shadow_host) or self.exploration.enabled):
            raise ValueError("probe_only needs cost_probe.enabled and shadow_host, or exploration.enabled, on the same "
                             "account (ADR 0037 amendments 4 and 6)")
        return self

    @model_validator(mode="after")
    def _exploration_fenced(self) -> Account:
        """Exploration arms trade without an expected-value judgement, so they live only where nothing real is at
        stake: a demo, probe_only account (refused at load otherwise, as cost_probe). Their budget and the cost
        probe's are shares of one day's allowance (sum <= 1), and no arm's per-trade cap exceeds the exploration
        budget. A disabled block is inert and not judged."""
        ex = self.exploration
        if not ex.enabled:
            return self
        if self.mode != "demo" or not self.probe_only:
            raise ValueError("exploration is allowed on demo probe_only accounts only (plan 2026-10-25)")
        if ex.budget_share + (self.cost_probe.budget_share if self.cost_probe.enabled else 0.0) > 1 + 1e-9:
            raise ValueError("exploration.budget_share + cost_probe.budget_share must be at most 1 (one day's allowance)")
        cap = self.daily_loss_limit_abs
        if cap is None:
            raise ValueError("exploration needs daily_loss_limit_abs on the same account (its budget is a share of it)")
        if any(a.max_loss > ex.budget_share * cap + 1e-9 for a in ex.arms.values()):
            raise ValueError("exploration arm max_loss must fit inside exploration.budget_share x daily_loss_limit_abs")
        if any(a.style is None or a.style not in ex.pots for a in ex.arms.values()):
            raise ValueError("every exploration arm names a style with a pot (exploration.pots, ADR 0043)")
        if sum(pot.gbp for pot in ex.pots.values()) > ex.budget_share * cap + 1e-9:
            raise ValueError("exploration pots must fit inside exploration.budget_share x daily_loss_limit_abs")
        return self

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


Prompter = Callable[[str, bool], str]  # (message, secret) -> value
_prompter: Prompter | None = None


def register_prompter(fn: Prompter | None) -> None:
    """The Telegram bot registers itself here so headless prompts reach the owner."""
    global _prompter
    _prompter = fn


def _prompt(message: str, secret: bool) -> str:
    if _prompter is not None:
        return _prompter(message, secret)
    if sys.stdin is not None and sys.stdin.isatty():
        return getpass.getpass(message + ": ") if secret else input(message + ": ")
    raise RuntimeError(f"credential needed ({message}) but no interactive prompter is available; "
                       "start the Telegram bot or run `python -m goldbot.ops.accounts add <account>`")


# ---------------------------------------------------------------------------- secret storage
def _fallback_file() -> Path:
    p = ROOT / "state" / ".secrets.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


@contextmanager
def _fallback_lock() -> Iterator[None]:
    """flock on state/.secrets.lock so a migration and a service's `set` never interleave their read-modify-write of the
    plaintext file. No-op where fcntl is missing (Windows never uses the file)."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover
        yield
        return
    with open(_fallback_file().with_name(".secrets.lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)


def _under_wine() -> bool:
    """True for Windows Python running under Wine on a Linux host (the MT5 boxes): ntdll exports `wine_get_version`,
    which a real Windows never has. Wine Python has no keyring backend, so it uses the Linux rule below."""
    if _PLATFORM != "win32":
        return False
    try:
        import ctypes
        return hasattr(ctypes.WinDLL("ntdll"), "wine_get_version")  # type: ignore[attr-defined]  # Windows-only API
    except Exception:                       # noqa: BLE001 - not provable: treat as real Windows (refused)
        return False


def _plaintext_allowed() -> bool:
    """Linux only (Wine on a Linux host counts as Linux: the MT5 boxes, `_under_wine`; found bringing up the icm-probe
    box 2026-10-19). The opt-in is GOLDBOT_ALLOW_PLAINTEXT=1, or the deliberate PYTHON_KEYRING_BACKEND=...fail.Keyring
    a server unit sets (so units installed before the opt-in existed keep working). macOS and real Windows: never."""
    if _PLATFORM == "darwin" or (_PLATFORM == "win32" and not _under_wine()):
        return False
    return (os.environ.get(ALLOW_PLAINTEXT_ENV) == "1"
            or os.environ.get("PYTHON_KEYRING_BACKEND") == "keyring.backends.fail.Keyring")


def _read_fallback() -> dict[str, str]:
    f = _fallback_file()
    return json.loads(f.read_text()) if f.exists() else {}


def migrate_fallback() -> int:
    """Move every key of state/.secrets.json into the OS keyring and delete the moved keys (the file when empty).
    A key the keyring refuses stays in the file. Values are never printed. Returns how many keys moved."""
    if secret_store() != "keyring":
        return 0
    with _fallback_lock():
        data = _read_fallback()
        moved = 0
        for k, v in list(data.items()):
            try:
                keyring.set_password(SERVICE, k, v)
            except Exception:
                continue
            del data[k]
            moved += 1
        f = _fallback_file()
        if moved and data:
            write_private(f, json.dumps(data))
        elif moved:
            f.unlink()
    return moved


def get_secret(key: str) -> str | None:
    if keyring is not None:
        try:
            v = keyring.get_password(SERVICE, key)
            if v:
                return v
        except Exception:
            pass
    if key not in _read_fallback():
        return None
    # automatic migration only for a person at a terminal: a service (fail backend, no TTY) just reads the file
    if sys.stdin is not None and sys.stdin.isatty() and migrate_fallback():
        return get_secret(key)
    return _read_fallback().get(key)


def fallback_note() -> None:
    """CLI only, once per process: say that secrets sit in the plaintext file and how to move them. Never called from
    get_secret (a service reads secrets all day; the journal must not fill with it)."""
    if _read_fallback() and secret_store() == "keyring":
        print(f"note: secrets are in the plaintext {_fallback_file()}; run `python -m goldbot.ops.accounts migrate` "
              "to move them into the OS keyring", file=sys.stderr)


def set_secret(key: str, value: str) -> str:
    """Store a secret; returns "keyring" or "file". The file is used only when no keyring backend works AND
    GOLDBOT_ALLOW_PLAINTEXT=1 on a non-macOS/Windows host; otherwise RuntimeError (nothing written). Storing an MT5
    password or login (`mt5-<account>`, `mt5-login-<account>`) clears that account's refused-password mark and login
    attempts (goldbot/ops/login_ledger.py): the new credentials may be tried."""
    where = _store_secret(key, value)
    from goldbot.ops.login_ledger import clear_for_secret
    clear_for_secret(key)
    return where


def _store_secret(key: str, value: str) -> str:
    if secret_store() == "keyring":
        try:
            keyring.set_password(SERVICE, key, value)
            return "keyring"
        except Exception:
            pass
    if not _plaintext_allowed():
        raise RuntimeError(f"no working OS keyring to store {key}: install/unlock it (`uv sync`; macOS Keychain, Windows "
                           f"Credential Manager). Plaintext fallback is refused on macOS/Windows and elsewhere needs "
                           f"{ALLOW_PLAINTEXT_ENV}=1 (CI only)")
    with _fallback_lock():
        data = _read_fallback()
        data[key] = value
        write_private(_fallback_file(), json.dumps(data))
    return "file"


def get_credential(key: str, message: str, *, secret: bool = True) -> str:
    """Return the stored secret or ask the owner for it now and store it."""
    v = get_secret(key)
    if v:
        return v
    v = _prompt(message, secret)
    if not v:
        raise RuntimeError(f"no value given for {key}")
    set_secret(key, v)
    return v


# ---------------------------------------------------------------------------- registry
def _accounts_path(path: Path | None) -> Path:
    return path or ACCOUNTS_FILE


def _edit_yaml_line(path: Path, account_id: str, key: str, value: str) -> None:
    """Edit one `key:` line inside one account block, preserving comments and layout."""
    lines = path.read_text().splitlines()
    in_block = False
    for i, line in enumerate(lines):
        if re.match(rf"^  {re.escape(account_id)}:\s*$", line):
            in_block = True
            continue
        if in_block and re.match(r"^  \S", line):
            break
        if in_block and re.match(rf"^    {key}:", line):
            comment = line.split("#", 1)[1] if "#" in line else None
            lines[i] = f"    {key}: {value}" + (f"  #{comment}" if comment else "")
            path.write_text("\n".join(lines) + "\n")
            return
    raise KeyError(f"{account_id}.{key} not found in {path}")


def _check_account_name(name: str) -> None:
    """A name with 5+ digits in a row could carry an MT5 login (the repo is public; names reach the dashboard,
    Telegram /status and the digest), and one over ACCOUNT_ID_MAX_BYTES would not fit the approval buttons (BACKLOG
    63). Plain ValueError giving only the length, never the name."""
    if ACCOUNT_ID_LOGIN.search(name):
        raise ValueError(f"an account name in config/accounts.yaml has 5 or more digits in a row (could be a login): "
                         f"rename it ({len(name)} characters)")
    if len(name.encode("utf-8")) > ACCOUNT_ID_MAX_BYTES:
        raise ValueError(f"an account name in config/accounts.yaml is too long for the Telegram approval buttons: "
                         f"{ACCOUNT_ID_MAX_BYTES} bytes at most, rename it ({len(name.encode('utf-8'))} bytes)")


def load_accounts(path: Path | None = None, *, logins: bool = True) -> dict[str, Account]:
    """config/accounts.yaml as Account records. `logins` False: a `login: null` stays None (no keyring read), for the
    readers that need only which accounts exist and are enabled (`enabled_account_ids`, polled by the dashboard)."""
    path = _accounts_path(path)
    raw = yaml.safe_load(path.read_text())
    out = {}
    for aid, a in raw["accounts"].items():
        _check_account_name(str(aid))      # before the keyring lookup and the record: the error never echoes it
        login = a.get("login")
        if login is None and logins:
            stored = get_secret(login_key(aid))
            login = int(stored) if stored and stored.strip().isdigit() else None
        out[aid] = Account(account_id=aid, broker=a["broker"], mode=a["mode"], server=a["server"], login=login, terminal_path=a["terminal_path"],
                           server_tz=a["server_tz"], symbol=a["symbol"], magic_base=a["magic_base"], enabled=bool(a.get("enabled", False)),
                           daily_loss_limit_abs=a.get("daily_loss_limit_abs"), account_class=a.get("account_class"),
                           risk_overrides=a.get("risk_overrides"), approval_mode=a.get("approval_mode", "propose"),
                           cost_probe=CostProbe(**(a.get("cost_probe") or {})),
                           shadow_host=bool(a.get("shadow_host", False)), probe_only=bool(a.get("probe_only", False)),
                           dynamic_limits=a.get("dynamic_limits", False),
                           exploration=Exploration(**(a.get("exploration") or {})),
                           max_positions=a.get("max_positions"))
    hosts = [a for a in out.values() if a.enabled and a.shadow_host]
    if len(hosts) > 1:      # ADR 0037 amendment 4: one shadow book, one host; two would trade it twice
        raise ValueError(f"{len(hosts)} enabled accounts are the shadow host in config/accounts.yaml: exactly one may "
                         f"be (move shadow_host and cost_probe off the other, ADR 0037 amendment 4)")
    return out


def login_key(account_id: str) -> str:
    return f"mt5-login-{account_id}"


def save_login(account_id: str, login: int) -> None:
    """The login number goes to the keyring, never to config/accounts.yaml (tracked in a public repo)."""
    set_secret(login_key(account_id), str(int(login)))


def bridge_token_key(account_id: str) -> str:
    return f"mt5-bridge-token-{account_id}"


def bridge_endpoint(account_id: str) -> tuple[str, str] | None:
    """(url, token) of the MT5 bridge for this account (goldbot/execution/bridge.py), from the keyring; None when the
    terminal runs on this machine. Neither value is ever in the repository."""
    url = get_secret(f"mt5-bridge-url-{account_id}")
    token = get_secret(bridge_token_key(account_id))
    return (url, token) if url and token else None


def verify_terminal_account(acc: Account, info: Any) -> str | None:
    """Why the terminal's logged-in account must not be traded as `acc`, or None when it matches: the login (when
    known), the server, demo vs real, and a hedging margin mode. A terminal on its own saved login (the Wine bridge)
    could otherwise trade a live account under a demo account's limits, past the phase gate. The engine keys one
    position per order ticket (review MEDIUM-1 follow-up): on a netting or exchange account fills join one position per
    symbol, so anything but hedging is refused, and so is a mode the terminal does not report (an older bridge)."""
    if acc.login is None:
        return f"no login stored for {acc.account_id} (python -m goldbot.ops.accounts add {acc.account_id})"
    if int(info.login) != int(acc.login):
        return f"terminal is logged in to another account than {acc.account_id}"
    if str(info.server) != acc.server:
        return f"terminal server {info.server!r} is not {acc.server!r}"
    want = "demo" if acc.mode == "demo" else "real"
    if info.trade_mode != want:
        return f"terminal account is {info.trade_mode or 'unknown'}, {acc.account_id} is {acc.mode}"
    margin = getattr(info, "margin_mode", None)
    if margin is None:          # a v3 bridge does not report it (review LOW-A: say what to do)
        return ("terminal margin mode unknown (not reported; the engine needs a hedging account): bridge older than "
                "protocol v4: deploy the MT5 box first")
    if margin != "hedging":
        return f"terminal account margin mode is {margin}; the engine needs a hedging account"
    return None


def secret_store() -> str:
    """"keyring" when the OS keyring takes writes, "file" when secrets fall back to state/.secrets.json."""
    if keyring is None:
        return "file"
    try:
        keyring.get_password(SERVICE, "__probe__")
        return "file" if "fail" in type(keyring.get_keyring()).__module__ else "keyring"
    except Exception:
        return "file"


def account_password(acc: Account) -> str:
    return get_credential(f"mt5-{acc.account_id}", f"MT5 password for {acc.account_id} (login {acc.login}, {acc.server})")


def stored_password(acc: Account) -> str | None:
    """The account's MT5 password from the keyring (`mt5-<account_id>`, as `add` stores it), or None; never prompts
    (the MT5 box login in goldbot/ops/login.py runs in services with no one to ask)."""
    return get_secret(f"mt5-{acc.account_id}")


def ensure_login(acc: Account) -> Account:
    if acc.login is None:
        v = _prompt(f"MT5 login number for {acc.account_id} ({acc.server})", secret=False)
        save_login(acc.account_id, int(v))
        acc.login = int(v)
    return acc


# The four roadmap gates in order; passing gate i puts the system in phase i + 1 (0 foundation, 1 backtest,
# 2 paper on demo accounts, 3 tiny live, 4 full size).
GATES = ("foundation_to_backtest", "backtest_to_paper", "paper_to_tiny_live", "tiny_live_to_full_size")


def phase_state() -> dict:
    return json.loads(PHASE_FILE.read_text()) if PHASE_FILE.exists() else {"phase": 0, "gates_passed": []}


def record_gate(gate: str, evidence: str, path: Path | None = None) -> dict:
    """Record a passed roadmap gate in phase_state.json (atomically): appended to `gates_passed`, with a timestamp and
    the evidence (a file path is recorded with its sha256) in `gate_log`. Refuses unknown names, a gate recorded twice
    and a gate whose predecessor has not passed. Raises ValueError on refusal.

    Recording only: it does not check the thresholds (`run.py gates`, goldbot/ops/gates_phase.py, shows whether the
    evidence meets them) and it never enables a live account; that still needs `unlock_live` and the typed phrase."""
    path = path or PHASE_FILE
    if gate not in GATES:
        raise ValueError(f"unknown gate {gate!r}; known: {', '.join(GATES)}")
    if not evidence.strip():
        raise ValueError("evidence is required")
    st: dict[str, Any] = json.loads(path.read_text()) if path.exists() else {"phase": 0, "gates_passed": []}
    passed = list(st.get("gates_passed", []))
    if gate in passed:
        raise ValueError(f"gate {gate} is already recorded")
    i = GATES.index(gate)
    if i > 0 and GATES[i - 1] not in passed:
        raise ValueError(f"gate {GATES[i - 1]} must be recorded before {gate}")
    ev: dict[str, str] = {"text": evidence}
    f = Path(evidence)
    if f.is_file():
        ev = {"path": str(f.resolve()), "sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    st["gates_passed"] = passed + [gate]
    st["phase"] = max(int(st.get("phase", 0)), i + 1)
    st.setdefault("gate_log", []).append({"gate": gate, "ts_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                          "evidence": ev})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(st, indent=2))
    os.replace(tmp, path)
    return st


def tiny_live_risk(acc: Account) -> bool:
    """True when the account trades at the tiny-live risk per trade: a live account before the full-size gate, or
    whenever the phase file cannot be read (the smaller risk is the safe default). Demo accounts use the normal risk."""
    if not acc.is_live:
        return False
    try:
        return "tiny_live_to_full_size" not in phase_state().get("gates_passed", [])
    except (ValueError, OSError, AttributeError):
        return True


def unlock_live(account_id: str, confirmation: str, path: Path | None = None) -> bool:
    """Enable a live account only after the paper->tiny-live gate has passed and with a typed phrase."""
    st = phase_state()
    if "paper_to_tiny_live" not in st.get("gates_passed", []):
        print("refused: the paper -> tiny-live gate has not passed (state/phase_state.json)")
        return False
    if confirmation != f"ENABLE LIVE {account_id}":
        print("refused: confirmation phrase mismatch")
        return False
    _edit_yaml_line(_accounts_path(path), account_id, "enabled", "true")
    return True


def enabled_accounts(mode: str | None = None, *, logins: bool = True,
                     loaded: dict[str, Account] | None = None) -> list[Account]:
    """`loaded`: accounts already read by `load_accounts` (not read again)."""
    accs = [a for a in (load_accounts(logins=logins) if loaded is None else loaded).values() if a.enabled]
    if mode:
        accs = [a for a in accs if a.mode == mode]
    # safety: live accounts are never returned unless the gate has passed, whatever the yaml says
    if "paper_to_tiny_live" not in phase_state().get("gates_passed", []):
        accs = [a for a in accs if not a.is_live]
    return accs


class AccountIds(FrozenRecord):
    """Which accounts config/accounts.yaml lists, and which of them `enabled_accounts` returns."""
    listed: frozenset[str]
    enabled: frozenset[str]


def account_ids() -> AccountIds | None:
    """The listed and the enabled ids (as `enabled_accounts`: live ones only after the phase gate), without the
    keyring; None when config/accounts.yaml cannot be read (health's `accounts` check FAILs on that and names it)."""
    try:
        accs = load_accounts(logins=False)
        return AccountIds(listed=frozenset(accs), enabled=frozenset(a.account_id for a in enabled_accounts(loaded=accs)))
    except Exception as exc:               # noqa: BLE001 - any config fault: the callers fall back to every file
        log.warning("accounts.yaml unreadable (%s): engine state files are not filtered", type(exc).__name__)
        return None


def enabled_account_ids() -> frozenset[str] | None:
    """The ids `enabled_accounts` returns, without the keyring; None when config/accounts.yaml cannot be read."""
    ids = account_ids()
    return None if ids is None else ids.enabled


def engine_state_files(state_dir: str | Path, enabled: Collection[str] | None = None) -> list[Path]:
    """state/engine_<account>.json of the ENABLED accounts only, sorted: what the dashboard, Telegram (status,
    proposal budget, digest), deploy notes and health show. A disabled account's leftover file is ignored entirely,
    never shown, never blocking, never in a loss budget or tile (owner bug 2026-10-20: a stale engine_icm-probe.json
    from a brief run showed "Entries blocked: Engine not reporting (icm-probe)"). An enabled account whose file is
    missing or stale is not affected: it still shows "Engine not reporting".

    An account accounts.yaml does not list at all is shown while its file was written within UNLISTED_FRESH_S (an
    engine running ahead of the brain's config is never hidden), hidden once older (a retired account's leftover).
    `enabled` None: accounts.yaml's enabled accounts; health passes its own. When accounts.yaml cannot be read every
    file is returned (fail open: a config error must not hide a running engine from the risk view). Reporting only:
    the supervisor keeps reading every file (goldbot/risk/supervisor.py), where a leftover only tightens."""
    files = sorted(Path(state_dir).glob(f"{ENGINE_FILE_PREFIX}*.json"))
    ids = account_ids()
    if ids is None:
        return files
    on = ids.enabled if enabled is None else frozenset(enabled)
    now = time.time()

    def shown(f: Path) -> bool:
        acct = f.stem.removeprefix(ENGINE_FILE_PREFIX)
        if acct in on:
            return True
        if acct in ids.listed:
            return False
        try:
            return now - f.stat().st_mtime <= UNLISTED_FRESH_S
        except OSError:                    # removed meanwhile
            return False
    return [f for f in files if shown(f)]


# ---------------------------------------------------------------------------- CLI
MAX_PIPED_SECRET = 8192          # far beyond any cookie or token; a larger input is not one secret


def read_piped_secret(stream: Any) -> str:
    """One secret from a pipe (`set <key> --stdin`): the whole input with surrounding whitespace removed. Refused
    (ValueError, the value never in the message) when stdin is a terminal (use the hidden prompt), empty (the
    sending side failed: nothing is stored), more than one line, or oversized."""
    if stream is None or stream.isatty():
        raise ValueError("--stdin reads a pipe, not a terminal: drop --stdin to get the hidden prompt")
    raw = stream.read(MAX_PIPED_SECRET + 1)
    if len(raw) > MAX_PIPED_SECRET:
        raise ValueError(f"piped value longer than {MAX_PIPED_SECRET} characters: not stored")
    value = raw.strip()
    if not value:
        raise ValueError("nothing arrived on stdin (the sending side failed?): not stored")
    if "\n" in value or "\r" in value:
        raise ValueError("piped value has more than one line: not stored")
    return value


def _is_pipe(out: Any) -> bool:
    """True only when `out` is a pipe or a socket (os.fstat): a terminal, a regular file (`> file`) or an object
    without a file descriptor is not."""
    import stat
    try:
        mode = os.fstat(out.fileno()).st_mode
    except (AttributeError, OSError, ValueError):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode)


def export_secret(key: str, out: Any) -> None:
    """Write one stored secret to `out` for a pipe (`export <key> | ssh ... set <key> --stdin`) and nowhere else: a
    terminal or a regular file is refused, so the value cannot land on screen, in scrollback or on disk. A pipe into a
    printing command (`| cat`) cannot be told apart: pipe it only into ssh (RUNBOOK 2.7d). Missing key: KeyError."""
    if not _is_pipe(out):
        raise ValueError("export writes to a pipe only (e.g. `| ssh <host> goldbot accounts set <key> --stdin`)")
    value = get_secret(key)
    if not value:
        raise KeyError(key)
    out.write(value)
    out.flush()


def main(argv: list[str]) -> int:
    fallback_note()
    if not argv or argv[0] == "list":
        for a in load_accounts().values():
            has_pw = "yes" if get_secret(f"mt5-{a.account_id}") else "no"
            print(f"{a.account_id:14} {a.broker:8} {a.mode:5} enabled={a.enabled!s:5} login={a.login} password_stored={has_pw}")
        return 0
    cmd = argv[0]
    if cmd == "add":
        acc = ensure_login(load_accounts()[argv[1]])
        pw = _prompt(f"MT5 password for {acc.account_id} (login {acc.login}, {acc.server})", secret=True)
        set_secret(f"mt5-{acc.account_id}", pw)
        print(f"stored credentials for {acc.account_id} in the OS keyring")
        return 0
    if cmd == "bridge-serve":
        import secrets
        aid = argv[1]
        ensure_login(load_accounts()[aid])          # the bridge serves only the account with this login
        set_secret(f"mt5-bridge-listen-{aid}", _prompt("bridge listen address host:port (127.0.0.1:8765; reached through the SSH tunnel)", secret=False))
        token = secrets.token_urlsafe(32)
        set_secret(bridge_token_key(aid), token)
        if secret_store() == "file":
            print(f"note: no OS keyring here; secrets are in {_fallback_file()} (owner-only file, not encrypted)")
        print(f"bridge token for {aid} (enter it on the engine machine with bridge-use; shown only now):\n{token}")
        return 0
    if cmd == "bridge-use":
        aid = argv[1]
        set_secret(f"mt5-bridge-url-{aid}", _prompt("bridge URL (http://127.0.0.1:8765, the tunnel's local end)", secret=False))
        set_secret(bridge_token_key(aid), _prompt("bridge token (from bridge-serve)", secret=True))
        ensure_login(load_accounts()[aid])          # the engine checks the terminal is logged in to this login
        if secret_store() == "file":
            print(f"note: no OS keyring here; secrets are in {_fallback_file()} (owner-only file, not encrypted)")
        print(f"engine for {aid} will use the bridge")
        return 0
    if cmd == "set":
        if "--stdin" in argv[2:]:
            try:
                value = read_piped_secret(sys.stdin)
            except ValueError as exc:
                print(f"refused: {exc}", file=sys.stderr)
                return 1
        else:
            value = _prompt(f"value for {argv[1]}", secret=True)
        where = set_secret(argv[1], value)
        print(f"stored {argv[1]} in the OS keyring" if where == "keyring"
              else f"stored {argv[1]} in {_fallback_file()} (plaintext, owner-only file)")
        return 0
    if cmd == "export":
        try:
            export_secret(argv[1], sys.stdout)
        except KeyError:
            print(f"refused: {argv[1]} is not stored here", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        return 0
    if cmd == "migrate":
        print(f"moved {migrate_fallback()} secret(s) from state/.secrets.json into the OS keyring")
        return 0
    if cmd == "unlock-live":
        phrase = _prompt(f"type exactly: ENABLE LIVE {argv[1]}", secret=False)
        return 0 if unlock_live(argv[1], phrase) else 1
    print(__doc__)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
