"""`goldbot run setup`: a guided, re-runnable walk through every secret and per-server setting on the brain.

Each step says in plain words what the value is, where to get it, and what happens without it. Rules:

* Secrets are typed with hidden input and go only to the existing secret store (`goldbot.ops.accounts.set_secret`:
  the OS keyring, or the owner-only `state/.secrets.json` on a server). Nothing typed is ever printed back, logged or
  put in an error message.
* Per-server settings (the Telegram allow-list and the dashboard owner email) are merged into the git-ignored
  `config/settings.local.yaml`, written atomically with mode 0600; every other key in that file is kept. The merged
  result must still load as Settings, or nothing is written.
* Anything already set is skipped unless the owner answers "y" to "change it?"; pressing Enter keeps it.

Order (RUNBOOK 0.4): MT5 login, bridge, Telegram bot token, Telegram user id, dashboard owner email, the optional
anthropic-api-key and github-token, then the backup (restic) keys and bucket.

  goldbot run setup [--account icm-demo]
"""
from __future__ import annotations

import argparse
import getpass
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from goldbot.base import write_private
from goldbot.config import DEFAULT_SETTINGS, Settings, _deep_merge, load_yaml
from goldbot.ops import accounts

Ask = Callable[[str], str]

TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
OCI_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")
BUCKET_RE = re.compile(r"^[A-Za-z0-9._-]{1,256}$")
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765"
DEFAULT_BUCKET = "goldbot-backup"
RESTIC_KEYS = ("restic-repository", "restic-password", "restic-s3-access-key", "restic-s3-secret-key")
MAX_TRIES = 3


def local_settings_path(settings_path: Path = DEFAULT_SETTINGS) -> Path:
    return Path(settings_path).with_name("settings.local.yaml")


def merge_local_settings(update: dict[str, Any], settings_path: Path = DEFAULT_SETTINGS) -> None:
    """Deep-merge `update` into config/settings.local.yaml (0600, atomic), keeping every other key. Raises ValueError
    (with no value in the message) when the existing file does not parse or the merged settings would not load."""
    local = local_settings_path(settings_path)
    current: dict[str, Any] = {}
    if local.exists():
        try:
            current = load_yaml(local) or {}
        except Exception as exc:
            raise ValueError(f"{local.name} does not parse ({type(exc).__name__}); fix or move it, then run setup "
                             "again. Nothing was changed") from None
        if not isinstance(current, dict):
            raise ValueError(f"{local.name} is not a mapping; nothing was changed")
    merged = _deep_merge(current, update)
    try:
        Settings.model_validate(_deep_merge(load_yaml(settings_path), merged))
    except Exception as exc:
        raise ValueError(f"the new value would make the settings invalid ({type(exc).__name__}); "
                         "nothing was changed") from None
    write_private(local, yaml.safe_dump(merged, sort_keys=False, default_flow_style=False))


def local_settings(settings_path: Path = DEFAULT_SETTINGS) -> dict[str, Any]:
    local = local_settings_path(settings_path)
    try:
        data = load_yaml(local) if local.exists() else {}
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


@dataclass
class Wizard:
    ask: Ask
    ask_secret: Ask
    out: Callable[[str], None]
    account_id: str = "icm-demo"
    settings_path: Path = DEFAULT_SETTINGS

    # ------------------------------------------------------------------ small helpers
    def say(self, text: str = "") -> None:
        self.out(text)

    def yes(self, question: str) -> bool:
        return self.ask(f"{question} [y/N]").strip().lower() in ("y", "yes")

    def keep_existing(self, label: str, is_set: bool) -> bool:
        """True when the value is already set and the owner keeps it (the default)."""
        if not is_set:
            return False
        self.say(f"  {label} is already set.")
        return not self.yes("  Change it?")

    def read(self, prompt: str, *, hidden: bool, check: Callable[[str], str | None], default: str = "") -> str | None:
        """Ask up to MAX_TRIES times; None when the owner leaves it blank (skip) or keeps typing an invalid value.
        `check` returns why a value is wrong, never quoting it."""
        for _ in range(MAX_TRIES):
            raw = (self.ask_secret if hidden else self.ask)(prompt).strip()
            if not raw and default:
                raw = default
            if not raw:
                return None
            why = check(raw)
            if why is None:
                return raw
            self.say(f"  That does not look right: {why}. Try again (or press Enter to skip).")
        self.say("  Skipped after three tries; run `goldbot run setup` again when you have it.")
        return None

    def secret(self, key: str) -> bool:
        try:
            return bool(accounts.get_secret(key))
        except Exception:
            return False

    def store(self, key: str, value: str) -> None:
        accounts.set_secret(key, value)
        self.say("  Saved.")

    def step(self, n: int, total: int, title: str, body: str) -> None:
        self.say("")
        self.say(f"Step {n} of {total}: {title}")
        for line in body.strip().splitlines():
            self.say(f"  {line.strip()}")

    # ------------------------------------------------------------------ steps
    def mt5_login(self) -> None:
        acc = accounts.load_accounts().get(self.account_id)
        if acc is None:
            self.say(f"  {self.account_id} is not in config/accounts.yaml; skipped.")
            return
        if not self.keep_existing("The MT5 login number", self.secret(accounts.login_key(self.account_id))):
            v = self.read("  MT5 login number (digits only)", hidden=False,
                          check=lambda s: None if s.isdigit() and 4 <= len(s) <= 12 else "use the digits of the login only")
            if v is not None:
                accounts.save_login(self.account_id, int(v))
                self.say("  Saved.")
        if not self.keep_existing("The MT5 password", self.secret(f"mt5-{self.account_id}")):
            v = self.read("  MT5 password (hidden as you type)", hidden=True, check=lambda s: None)
            if v is not None:
                self.store(f"mt5-{self.account_id}", v)

    def bridge(self) -> None:
        url_key, tok_key = f"mt5-bridge-url-{self.account_id}", accounts.bridge_token_key(self.account_id)
        if not self.keep_existing("The bridge address", self.secret(url_key)):
            v = self.read(f"  Bridge address (press Enter for {DEFAULT_BRIDGE_URL})", hidden=False, default=DEFAULT_BRIDGE_URL,
                          check=lambda s: None if re.match(r"^https?://[^\s/]+(:\d+)?/?$", s) else "it starts with http:// and has no spaces")
            if v is not None:
                self.store(url_key, v)
        if not self.keep_existing("The bridge token", self.secret(tok_key)):
            v = self.read("  Bridge token (hidden as you type)", hidden=True,
                          check=lambda s: None if len(s) >= 32 and " " not in s else "the token is one long word of 32+ characters")
            if v is not None:
                self.store(tok_key, v)

    def telegram_token(self) -> None:
        if not self.keep_existing("The Telegram bot token", self.secret("telegram-bot-token")):
            v = self.read("  Telegram bot token (hidden as you type)", hidden=True,
                          check=lambda s: None if TELEGRAM_TOKEN_RE.match(s) else "it looks like 123456789:ABC... (digits, a colon, then letters)")
            if v is not None:
                self.store("telegram-bot-token", v)

    def telegram_user(self) -> None:
        ids = (local_settings(self.settings_path).get("telegram") or {}).get("allowed_user_ids")
        if self.keep_existing("Your Telegram user id", bool(ids)):
            return
        v = self.read("  Your Telegram user id (digits only)", hidden=False,
                      check=lambda s: None if s.isdigit() and 5 <= len(s) <= 15 else "it is a number of 5 to 15 digits")
        if v is not None:
            self.merge({"telegram": {"allowed_user_ids": [int(v)]}})

    def owner_email(self) -> None:
        cur = (local_settings(self.settings_path).get("auth") or {}).get("owner_email")
        if self.keep_existing("The dashboard owner email", bool(cur)):
            return
        v = self.read("  Your email for the dashboard owner account", hidden=False,
                      check=lambda s: None if EMAIL_RE.match(s) else "it needs a name, an @ and a domain")
        if v is not None:
            self.merge({"auth": {"owner_email": v}})

    def optional_secret(self, key: str, label: str) -> None:
        if self.keep_existing(label, self.secret(key)):
            return
        v = self.read(f"  {label} (hidden as you type; press Enter to skip)", hidden=True,
                      check=lambda s: None if len(s) >= 20 and " " not in s else "a key is one long word without spaces")
        if v is not None:
            self.store(key, v)

    def restic(self) -> None:
        if all(self.secret(k) for k in RESTIC_KEYS):
            if not self.yes("  The backup keys are already set. Change them?"):
                return
        elif not self.yes("  Set up backups now? (you can skip this and come back later)"):
            self.say("  Skipped. Preflight will show backups as a warning until this is done.")
            return
        if not self.keep_existing("The backup bucket address", self.secret("restic-repository")):
            ns = self.read("  Object Storage namespace (on the bucket page)", hidden=False,
                           check=lambda s: None if OCI_NAME_RE.match(s) else "lower-case letters, digits and dashes")
            region = self.read("  Region identifier (e.g. eu-frankfurt-1)", hidden=False,
                               check=lambda s: None if OCI_NAME_RE.match(s) and "-" in s else "it looks like eu-frankfurt-1")
            bucket = self.read(f"  Bucket name (press Enter for {DEFAULT_BUCKET})", hidden=False, default=DEFAULT_BUCKET,
                               check=lambda s: None if BUCKET_RE.match(s) else "letters, digits, dots, dashes, underscores")
            if ns and region and bucket:
                self.store("restic-repository", f"s3:https://{ns}.compat.objectstorage.{region}.oraclecloud.com/{bucket}/goldbot")
        for key, label, explain in (
            ("restic-password", "The backup password", "the long random password saved in your password manager first"),
            ("restic-s3-access-key", "The BRAIN WRITER access key", "user goldbot-backup-writer -> Customer secret keys"),
            ("restic-s3-secret-key", "The BRAIN WRITER secret", "shown only once when the writer key was generated"),
        ):
            if self.keep_existing(label, self.secret(key)):
                continue
            v = self.read(f"  {label}: {explain} (hidden as you type)", hidden=True, check=lambda s: None)
            if v is not None:
                self.store(key, v)
        if all(self.secret(k) for k in RESTIC_KEYS):
            self.say("  Next, once: `goldbot run backup --init`, then `goldbot run backup` (RUNBOOK 0.6).")

    def merge(self, update: dict[str, Any]) -> None:
        try:
            merge_local_settings(update, self.settings_path)
        except ValueError as exc:
            self.say(f"  Not saved: {exc}.")
            return
        self.say("  Saved to config/settings.local.yaml (only on this server, never in the repository).")

    # ------------------------------------------------------------------ the walk
    def run(self) -> int:
        a = self.account_id
        steps: list[tuple[str, str, Callable[[], None]]] = [
            ("MT5 demo login", f"""
                The login number and password of your IC Markets demo account ({a}). goldbot uses them to check that
                the terminal on the MT5 box is logged in to this exact demo account before it trades.
                Where: your IC Markets welcome email, or MT5 -> File -> Login to Trade Account.
                Stored only in this server's secret store; never in the repository.""", self.mt5_login),
            ("MT5 bridge", f"""
                How this server talks to the MT5 box. The address is almost always {DEFAULT_BRIDGE_URL} (the tunnel's
                end on this server): just press Enter. The token was printed ONCE on the MT5 box by
                `goldbot-mt5 accounts bridge-serve {a}` (RUNBOOK 0.3). If you no longer have it, run that again there.""",
             self.bridge),
            ("Telegram bot token", """
                Lets goldbot send you entry proposals and alerts. Where: in Telegram, open @BotFather, send /newbot
                (or /token for an existing bot) and copy the token it gives you (RUNBOOK 2.8).
                Without it: no Approve buttons and no alerts.""", self.telegram_token),
            ("Your Telegram user id", """
                Only this Telegram user may approve entries or send commands. Where: in Telegram, message
                @userinfobot; it replies with your numeric id. This replaces the allow-list with your id.""",
             self.telegram_user),
            ("Dashboard owner email", """
                The email you will sign in to the dashboard with; only this address can be the owner (admin).
                Kept in config/settings.local.yaml on this server only.""", self.owner_email),
            ("Anthropic API key (optional)", """
                Turns on the staff agents and news-headline scoring. Where: console.anthropic.com -> API keys.
                Without it: everything trades as normal; headlines are kept but not scored.""",
             lambda: self.optional_secret("anthropic-api-key", "Anthropic API key")),
            ("GitHub token (optional)", """
                Lets the server refresh bar history and share the trial registry. Where: github.com -> Settings ->
                Developer settings -> Fine-grained tokens, repository engraamirkhan/goldbot, Contents: read and write.
                Without it: the trial registry stays on this server.""",
             lambda: self.optional_secret("github-token", "GitHub token")),
            ("Backups (restic keys and bucket)", """
                Encrypted daily backups to Oracle Object Storage. First do the one-time console steps in RUNBOOK 0.6
                (bucket, versioning, the two users and keys, a restic password in your password manager).""",
             self.restic),
        ]
        self.say("goldbot setup: each step explains one value and where to get it.")
        self.say("Secrets are hidden as you type and never shown again. Press Enter to keep a value or skip a step.")
        for n, (title, body, fn) in enumerate(steps, 1):
            self.step(n, len(steps), title, body)
            fn()
        self.say("")
        self.say("Setup finished. Next: `goldbot run preflight` checks everything before you start the services.")
        return 0


def main(argv: list[str], *, ask: Ask | None = None, ask_secret: Ask | None = None,
         out: Callable[[str], None] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="goldbot run setup", description="Guided setup of secrets and server settings.")
    ap.add_argument("--account", default="icm-demo", help="the MT5 account this server trades (default icm-demo)")
    args = ap.parse_args(argv)
    if ask is None and not (sys.stdin is not None and sys.stdin.isatty()):
        print("goldbot run setup needs a terminal to type into (it hides secrets as you type). "
              "Run it in an SSH session on the server.", file=sys.stderr)
        return 2
    wiz = Wizard(ask=ask or (lambda p: input(p + ": ")), ask_secret=ask_secret or (lambda p: getpass.getpass(p + ": ")),
                 out=out or print, account_id=args.account)
    try:
        return wiz.run()
    except (KeyboardInterrupt, EOFError):
        print("\nStopped. Everything saved so far is kept; run `goldbot run setup` again to continue.")
        return 130
