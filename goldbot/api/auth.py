"""Multi-user authentication for the dashboard and API. Standard library only.

* Users: email, scrypt password hash, TOTP secret (RFC 6238), role in {owner, approver, viewer}, enabled flag.
* Roles: viewer reads everything; approver can also approve/reject proposals; owner can also invite, remove,
  change roles, /rearm and /mode. Trade entries are still confirmed by a human (approver or owner); exits never need anyone.
* Invites: owner creates an invite (role, 72 h expiry) -> link with a one-time token -> invitee sets a password
  and enrols an authenticator -> account active. No email server needed; the owner sends the link however they like.
* Bootstrap: when no users exist, the server prints a one-time setup code to its log (and Telegram when running),
  and the first account created with that code becomes the owner.
* Sessions: random bearer tokens with 12 h expiry, stored server-side so they can be revoked.
* Audit: every login, failed login, invite, role change and decision is appended to state/audit.jsonl.

Storage is a JSON file under state/; it is small (tens of users) and swapped for SQLite if that ever changes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROLES = ("owner", "approver", "viewer")
ROLE_RANK = {r: i for i, r in enumerate(reversed(ROLES))}  # viewer 0, approver 1, owner 2


# ----------------------------------------------------------------------------- TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret_b32: str, t: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter = int((t if t is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret_b32, now + k * 30), code.strip()) for k in range(-window, window + 1))


def totp_uri(secret_b32: str, email: str, issuer: str = "goldbot") -> str:
    return f"otpauth://totp/{issuer}:{email}?secret={secret_b32}&issuer={issuer}&digits=6&period=30"


# ----------------------------------------------------------------------------- passwords
def hash_password(pw: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return base64.b64encode(salt).decode() + "$" + base64.b64encode(h).decode()


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt_b64, h_b64 = stored.split("$")
        h = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt_b64), n=2 ** 14, r=8, p=1, dklen=32)
        return hmac.compare_digest(h, base64.b64decode(h_b64))
    except Exception:
        return False


# ----------------------------------------------------------------------------- store
@dataclass
class User:
    email: str
    role: str
    password_hash: str
    totp_secret: str
    enabled: bool = True
    created: float = field(default_factory=time.time)
    last_login: float | None = None


@dataclass
class Invite:
    token_hash: str
    email: str
    role: str
    expires: float
    invited_by: str


class AuthStore:
    def __init__(self, state_dir: str | Path):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "users.json"
        self.audit_path = self.dir / "audit.jsonl"
        self.users: dict[str, User] = {}
        self.invites: dict[str, Invite] = {}
        self.sessions: dict[str, tuple[str, float]] = {}   # token -> (email, expires)
        self.setup_code: str | None = None
        self.failed: dict[str, list[float]] = {}
        self._load()
        if not self.users:
            self.setup_code = secrets.token_urlsafe(12)

    def _load(self) -> None:
        if self.path.exists():
            d = json.loads(self.path.read_text())
            self.users = {e: User(**u) for e, u in d.get("users", {}).items()}
            self.invites = {k: Invite(**v) for k, v in d.get("invites", {}).items()}

    def _save(self) -> None:
        self.path.write_text(json.dumps({"users": {e: asdict(u) for e, u in self.users.items()},
                                         "invites": {k: asdict(v) for k, v in self.invites.items()}}, indent=1))
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def audit(self, event: str, **kw) -> None:
        with open(self.audit_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": time.time(), "event": event, **kw}) + "\n")

    # ------------------------------------------------------------------ bootstrap and invites
    def bootstrap_owner(self, setup_code: str, email: str, password: str) -> str:
        if self.users or not self.setup_code or not hmac.compare_digest(setup_code, self.setup_code):
            raise PermissionError("setup not available or bad code")
        secret = new_totp_secret()
        self.users[email.lower()] = User(email.lower(), "owner", hash_password(password), secret)
        self.setup_code = None
        self._save()
        self.audit("bootstrap_owner", email=email)
        return totp_uri(secret, email)

    def create_invite(self, by_email: str, email: str, role: str, ttl_h: int = 72) -> str:
        self._require(by_email, "owner")
        if role not in ROLES:
            raise ValueError("bad role")
        token = secrets.token_urlsafe(24)
        th = hashlib.sha256(token.encode()).hexdigest()
        self.invites[th] = Invite(th, email.lower(), role, time.time() + ttl_h * 3600, by_email)
        self._save()
        self.audit("invite", by=by_email, email=email, role=role)
        return token

    def accept_invite(self, token: str, password: str) -> tuple[str, str]:
        th = hashlib.sha256(token.encode()).hexdigest()
        inv = self.invites.get(th)
        if inv is None or inv.expires < time.time():
            raise PermissionError("invite invalid or expired")
        if len(password) < 12:
            raise ValueError("password must be at least 12 characters")
        secret = new_totp_secret()
        self.users[inv.email] = User(inv.email, inv.role, hash_password(password), secret)
        del self.invites[th]
        self._save()
        self.audit("accept_invite", email=inv.email, role=inv.role)
        return inv.email, totp_uri(secret, inv.email)

    # ------------------------------------------------------------------ login and sessions
    def login(self, email: str, password: str, code: str, ttl_h: int = 12) -> str:
        email = email.lower()
        recent = [t for t in self.failed.get(email, []) if t > time.time() - 900]
        if len(recent) >= 5:
            self.audit("login_locked", email=email)
            raise PermissionError("too many attempts; wait 15 minutes")
        u = self.users.get(email)
        ok = u is not None and u.enabled and verify_password(password, u.password_hash) and totp_verify(u.totp_secret, code)
        if not ok:
            self.failed[email] = recent + [time.time()]
            self.audit("login_failed", email=email)
            raise PermissionError("invalid credentials")
        self.failed.pop(email, None)
        tok = secrets.token_urlsafe(32)
        self.sessions[tok] = (email, time.time() + ttl_h * 3600)
        u.last_login = time.time()
        self._save()
        self.audit("login", email=email)
        return tok

    def session_user(self, token: str | None) -> User | None:
        if not token or token not in self.sessions:
            return None
        email, exp = self.sessions[token]
        if exp < time.time():
            del self.sessions[token]
            return None
        u = self.users.get(email)
        return u if u and u.enabled else None

    def logout(self, token: str) -> None:
        self.sessions.pop(token, None)

    # ------------------------------------------------------------------ admin
    def _require(self, email: str, role: str) -> User:
        u = self.users.get(email.lower())
        if u is None or ROLE_RANK[u.role] < ROLE_RANK[role]:
            raise PermissionError(f"{role} role required")
        return u

    def set_role(self, by_email: str, email: str, role: str) -> None:
        self._require(by_email, "owner")
        if role not in ROLES:
            raise ValueError("bad role")
        target = self.users[email.lower()]
        if target.role == "owner" and role != "owner" and sum(u.role == "owner" for u in self.users.values()) == 1:
            raise ValueError("cannot demote the last owner")
        target.role = role
        self._save()
        self.audit("set_role", by=by_email, email=email, role=role)

    def disable(self, by_email: str, email: str) -> None:
        self._require(by_email, "owner")
        if email.lower() == by_email.lower():
            raise ValueError("cannot disable yourself")
        self.users[email.lower()].enabled = False
        self.sessions = {t: s for t, s in self.sessions.items() if s[0] != email.lower()}
        self._save()
        self.audit("disable", by=by_email, email=email)

    def list_users(self) -> list[dict]:
        return [{"email": u.email, "role": u.role, "enabled": u.enabled, "last_login": u.last_login} for u in self.users.values()]


def has_role(u: User | None, role: str) -> bool:
    return u is not None and ROLE_RANK[u.role] >= ROLE_RANK[role]


def env_setup_code_hint(store: AuthStore) -> str | None:
    """Printed once at startup when no owner exists."""
    if store.setup_code:
        return f"goldbot first-run: open the dashboard and create the owner account with setup code {store.setup_code}"
    return None


__all__ = ["AuthStore", "User", "has_role", "totp_code", "totp_verify", "new_totp_secret", "totp_uri", "ROLES", "env_setup_code_hint", "os"]
