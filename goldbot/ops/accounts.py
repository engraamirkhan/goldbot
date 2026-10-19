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
import os
import re
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

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
SERVICE = "goldbot"

try:
    import keyring
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]

_PLATFORM = sys.platform
# Opt-in for the plaintext fallback (state/.secrets.json): CI / headless Linux only, and only when no keyring backend
# exists. macOS and Windows never fall back (a missing keyring there is a broken install: fix it, do not write plaintext).
ALLOW_PLAINTEXT_ENV = "GOLDBOT_ALLOW_PLAINTEXT"


class CostProbe(FrozenRecord):
    """ADR 0037 (the research director's ruling 2026-10-19): minimum-lot demo orders mirroring challengers' shadow
    entries, for measured costs (goldbot/engine/cost_probe.py). Off unless set; demo accounts only."""
    enabled: bool = False
    # trading-safety review: no new probe once today's realised probe loss reaches this share of the day's loss
    # allowance, nor while less than this share of the allowance remains (real trades keep the rest)
    budget_share: float = Field(0.2, gt=0, le=1)


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

    # backstop for records built outside load_accounts: validation errors never carry the input (names, logins)
    model_config = ConfigDict(hide_input_in_errors=True)      # merged with Record's config by pydantic

    @field_validator("account_id")
    @classmethod
    def _no_login_in_name(cls, v: str) -> str:
        """Backstop of load_accounts' own check (goldbot/telegram/entry_text.account_name is the display backstop)."""
        _check_account_name(v)
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


def _plaintext_allowed() -> bool:
    """Linux only. The opt-in is GOLDBOT_ALLOW_PLAINTEXT=1, or the deliberate PYTHON_KEYRING_BACKEND=...fail.Keyring a
    server unit sets (so units installed before the opt-in existed keep working)."""
    if _PLATFORM in ("darwin", "win32"):
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
    GOLDBOT_ALLOW_PLAINTEXT=1 on a non-macOS/Windows host; otherwise RuntimeError (nothing written)."""
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


def load_accounts(path: Path | None = None) -> dict[str, Account]:
    path = _accounts_path(path)
    raw = yaml.safe_load(path.read_text())
    out = {}
    for aid, a in raw["accounts"].items():
        _check_account_name(str(aid))      # before the keyring lookup and the record: the error never echoes it
        login = a.get("login")
        if login is None:
            stored = get_secret(login_key(aid))
            login = int(stored) if stored and stored.strip().isdigit() else None
        out[aid] = Account(account_id=aid, broker=a["broker"], mode=a["mode"], server=a["server"], login=login, terminal_path=a["terminal_path"],
                           server_tz=a["server_tz"], symbol=a["symbol"], magic_base=a["magic_base"], enabled=bool(a.get("enabled", False)),
                           daily_loss_limit_abs=a.get("daily_loss_limit_abs"), account_class=a.get("account_class"),
                           risk_overrides=a.get("risk_overrides"), approval_mode=a.get("approval_mode", "propose"),
                           cost_probe=CostProbe(**(a.get("cost_probe") or {})))
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


def enabled_accounts(mode: str | None = None) -> list[Account]:
    accs = [a for a in load_accounts().values() if a.enabled]
    if mode:
        accs = [a for a in accs if a.mode == mode]
    # safety: live accounts are never returned unless the gate has passed, whatever the yaml says
    if "paper_to_tiny_live" not in phase_state().get("gates_passed", []):
        accs = [a for a in accs if not a.is_live]
    return accs


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
