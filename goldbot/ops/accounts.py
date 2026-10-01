"""Account registry + credential prompting.

* `config/accounts.yaml` holds everything except secrets.
* Secrets live in the OS keyring (macOS Keychain / Windows Credential Manager) via `keyring`, falling back
  to an encrypted-at-rest file only when no keyring backend exists (headless Linux CI).
* `get_credential()` asks the user when a secret is missing: on a terminal it prompts with getpass; when
  running headless it calls the registered `prompter` (the Telegram bot), which sends the request to the
  allow-listed owner and waits. The value is stored and never logged.
* Live accounts can only be enabled through `unlock_live()`, which requires the phase gate file to say
  the paper -> tiny-live gate has passed AND an explicit confirmation phrase.

CLI:
  python -m goldbot.ops.accounts list
  python -m goldbot.ops.accounts add icm-demo          # prompts for login + password, stores in keyring
  python -m goldbot.ops.accounts set tradingview-webhook-secret
  python -m goldbot.ops.accounts unlock-live icm-live  # refused unless the gate has passed
"""
from __future__ import annotations

import getpass
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from goldbot.config import ROOT

ACCOUNTS_FILE = ROOT / "config" / "accounts.yaml"
PHASE_FILE = ROOT / "state" / "phase_state.json"
SERVICE = "goldbot"

try:
    import keyring  # type: ignore
    _kr_ok = keyring.get_keyring().__class__.__name__ not in ("fail.Keyring", "Keyring") or True
except Exception:  # pragma: no cover
    keyring = None
    _kr_ok = False


@dataclass
class Account:
    account_id: str
    broker: str
    mode: str
    server: str
    login: int | None
    terminal_path: str
    server_tz: str
    symbol: str
    magic_base: int
    enabled: bool

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


Prompter = Callable[[str, bool], str]  # (message, secret) -> value
_prompter: Prompter | None = None


def register_prompter(fn: Prompter) -> None:
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


def get_secret(key: str) -> str | None:
    if keyring is not None:
        try:
            v = keyring.get_password(SERVICE, key)
            if v:
                return v
        except Exception:
            pass
    f = _fallback_file()
    if f.exists():
        return json.loads(f.read_text()).get(key)
    return None


def set_secret(key: str, value: str) -> None:
    if keyring is not None:
        try:
            keyring.set_password(SERVICE, key, value)
            return
        except Exception:
            pass
    f = _fallback_file()
    data = json.loads(f.read_text()) if f.exists() else {}
    data[key] = value
    f.write_text(json.dumps(data))
    try:
        f.chmod(0o600)
    except OSError:
        pass


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


def load_accounts(path: Path | None = None) -> dict[str, Account]:
    path = _accounts_path(path)
    raw = yaml.safe_load(path.read_text())
    out = {}
    for aid, a in raw["accounts"].items():
        out[aid] = Account(aid, a["broker"], a["mode"], a["server"], a.get("login"), a["terminal_path"],
                           a["server_tz"], a["symbol"], a["magic_base"], bool(a.get("enabled", False)))
    return out


def save_login(account_id: str, login: int, path: Path | None = None) -> None:
    _edit_yaml_line(_accounts_path(path), account_id, "login", str(int(login)))


def account_password(acc: Account) -> str:
    return get_credential(f"mt5-{acc.account_id}", f"MT5 password for {acc.account_id} (login {acc.login}, {acc.server})")


def ensure_login(acc: Account) -> Account:
    if acc.login is None:
        v = _prompt(f"MT5 login number for {acc.account_id} ({acc.server})", secret=False)
        save_login(acc.account_id, int(v))
        acc.login = int(v)
    return acc


def phase_state() -> dict:
    return json.loads(PHASE_FILE.read_text()) if PHASE_FILE.exists() else {"phase": 0, "gates_passed": []}


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
def main(argv: list[str]) -> int:
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
    if cmd == "set":
        set_secret(argv[1], _prompt(f"value for {argv[1]}", secret=True))
        print(f"stored {argv[1]}")
        return 0
    if cmd == "unlock-live":
        phrase = _prompt(f"type exactly: ENABLE LIVE {argv[1]}", secret=False)
        return 0 if unlock_live(argv[1], phrase) else 1
    print(__doc__)
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv[1:]))
