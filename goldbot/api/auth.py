"""Multi-user authentication for the dashboard and API. Standard library only.

* Users: email, scrypt password hash, TOTP secret (RFC 6238), role in {owner, approver, viewer}, enabled flag.
* Roles: viewer reads everything; approver can also approve/reject proposals; owner can also invite, remove,
  change roles, /rearm and /mode. Trade entries are still confirmed by a human (approver or owner); exits never need anyone.
* Invites: owner creates an invite (role, 72 h expiry) -> link with a one-time token -> invitee sets a password
  and enrols an authenticator -> account active. No email server needed; the owner sends the link however they like.
* Bootstrap: when no users exist, the server prints a one-time setup code to its log (and Telegram when running),
  and the first account created with that code becomes the owner.
* Sessions: random bearer tokens with 12 h expiry, stored server-side as SHA-256 hashes, so they can be revoked
  and survive an API restart.
* Audit: every login, failed login, invite, role change and decision is appended to the append-only `audit` table.

Storage is `state/aux.db` (SQLite WAL, docs/proposals/2026-10-state-store.md step 1). users.json and audit.jsonl
from before the database are imported once, when the database has no users, and then left untouched as a backup.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any

from pydantic import Field

from goldbot.api.schema import Role
from goldbot.base import Record
from goldbot.db import Database, RecordRepo

ROLES: tuple[Role, ...] = ("owner", "approver", "viewer")
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
class User(Record):
    email: str
    role: Role
    password_hash: str
    totp_secret: str
    enabled: bool = True
    created: float = Field(default_factory=time.time)
    last_login: float | None = None


class Invite(Record):
    token_hash: str
    email: str
    role: Role
    expires: float
    invited_by: str


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


_USERS: RecordRepo[User] = RecordRepo("users", User, key="email", columns={
    "email": lambda u: u.email, "role": lambda u: u.role, "enabled": lambda u: int(u.enabled)})
_INVITES: RecordRepo[Invite] = RecordRepo("invites", Invite, key="token_sha256", columns={
    "token_sha256": lambda i: i.token_hash, "email": lambda i: i.email, "expires_ns": lambda i: int(i.expires * 1e9)})


def _insert_audit(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    actor = entry.get("by") or entry.get("email")
    conn.execute("INSERT INTO audit (ts_ns, event, actor, body) VALUES (?, ?, ?, ?)",
                 (int(float(entry.get("ts", 0)) * 1e9), str(entry["event"]), None if actor is None else str(actor),
                  json.dumps(entry)))


def import_users_json(path: Path, conn: sqlite3.Connection) -> int:
    """Importer for the pre-database users.json (users and open invites), validated with the same Records."""
    d = json.loads(path.read_text())
    users = [User(**u) for u in d.get("users", {}).values()]
    invites = [Invite(**v) for v in d.get("invites", {}).values()]
    for u in users:
        _USERS.upsert(conn, u)
    for i in invites:
        _INVITES.upsert(conn, i)
    return len(users) + len(invites)


def import_audit_jsonl(path: Path, conn: sqlite3.Connection) -> int:
    """Importer for the pre-database audit.jsonl; unreadable lines are skipped (they were never valid entries)."""
    n = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and "event" in entry:
            _insert_audit(conn, entry)
            n += 1
    return n


class AuthStore:
    """Users, invites, sessions and the audit trail in `<state_dir>/aux.db` (docs/proposals/2026-10-state-store.md,
    step 1). Sessions are stored as token hashes with their expiry, so they survive an API restart and a deploy, and
    any number of AuthStore instances (threads, processes) share them. Audit rows are append-only (SQL triggers).

    Dual-read: when the database has no users yet, users.json and audit.jsonl are imported once; the files are
    never written again and stay as a backup. Failed-login counters stay in memory (a restart clears a lockout)."""

    def __init__(self, state_dir: str | Path):
        from goldbot.db.import_state import ImportSpec, import_file
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.legacy_users_path = self.dir / "users.json"
        self.legacy_audit_path = self.dir / "audit.jsonl"
        self.db = Database.open(self.dir, "aux")
        self.path = self.db.path
        self.setup_code: str | None = None
        self.failed: dict[str, list[float]] = {}
        if self.user_count() == 0:
            import_file(self.db, ImportSpec(source="users.json", kind="aux", loader=import_users_json), self.legacy_users_path)
        import_file(self.db, ImportSpec(source="audit.jsonl", kind="aux", loader=import_audit_jsonl, once=True),
                    self.legacy_audit_path)
        if self.user_count() == 0:
            self.setup_code = secrets.token_urlsafe(12)

    # ------------------------------------------------------------------ reads
    def user_count(self) -> int:
        with self.db.connection() as conn:
            return _USERS.count(conn)

    def get_user(self, email: str) -> User | None:
        with self.db.connection() as conn:
            return _USERS.get(conn, email.lower())

    @property
    def users(self) -> dict[str, User]:
        with self.db.connection() as conn:
            return {u.email: u for u in _USERS.select(conn)}

    def audit(self, event: str, **kw: Any) -> None:
        with self.db.write() as conn:
            _insert_audit(conn, {"ts": time.time(), "event": event, **kw})

    def audit_events(self) -> list[dict[str, Any]]:
        """The audit trail, oldest first, as the entries were written ({"ts", "event", ...})."""
        with self.db.connection() as conn:
            return [json.loads(b) for (b,) in conn.execute("SELECT body FROM audit ORDER BY id")]

    # ------------------------------------------------------------------ bootstrap and invites
    def bootstrap_owner(self, setup_code: str, email: str, password: str) -> str:
        if not self.setup_code or not hmac.compare_digest(setup_code, self.setup_code):
            raise PermissionError("setup not available or bad code")
        secret = new_totp_secret()
        with self.db.write() as conn:
            if _USERS.count(conn):                       # another process created the owner first
                self.setup_code = None
                raise PermissionError("setup not available or bad code")
            _USERS.upsert(conn, User(email=email.lower(), role="owner", password_hash=hash_password(password), totp_secret=secret))
            _insert_audit(conn, {"ts": time.time(), "event": "bootstrap_owner", "email": email})
        self.setup_code = None
        return totp_uri(secret, email)

    def create_invite(self, by_email: str, email: str, role: Role, ttl_h: int = 72) -> str:
        self._require(by_email, "owner")
        if role not in ROLES:
            raise ValueError("bad role")
        token = secrets.token_urlsafe(24)
        th = _token_hash(token)
        with self.db.write() as conn:
            _INVITES.upsert(conn, Invite(token_hash=th, email=email.lower(), role=role, expires=time.time() + ttl_h * 3600, invited_by=by_email))
            _insert_audit(conn, {"ts": time.time(), "event": "invite", "by": by_email, "email": email, "role": role})
        return token

    def accept_invite(self, token: str, password: str) -> tuple[str, str]:
        th = _token_hash(token)
        if len(password) < 12:
            with self.db.connection() as conn:
                inv0 = _INVITES.get(conn, th)
            if inv0 is None or inv0.expires < time.time():
                raise PermissionError("invite invalid or expired")
            raise ValueError("password must be at least 12 characters")
        secret = new_totp_secret()
        pw_hash = hash_password(password)                # scrypt outside the write lock
        with self.db.write() as conn:
            inv = _INVITES.get(conn, th)
            if inv is None or inv.expires < time.time():
                raise PermissionError("invite invalid or expired")
            _USERS.upsert(conn, User(email=inv.email, role=inv.role, password_hash=pw_hash, totp_secret=secret))
            _INVITES.delete(conn, th)                    # one-time: deleted in the same transaction
            _insert_audit(conn, {"ts": time.time(), "event": "accept_invite", "email": inv.email, "role": inv.role})
        return inv.email, totp_uri(secret, inv.email)

    # ------------------------------------------------------------------ login and sessions
    def login(self, email: str, password: str, code: str, ttl_h: int = 12) -> str:
        email = email.lower()
        recent = [t for t in self.failed.get(email, []) if t > time.time() - 900]
        if len(recent) >= 5:
            self.audit("login_locked", email=email)
            raise PermissionError("too many attempts; wait 15 minutes")
        u = self.get_user(email)
        ok = u is not None and u.enabled and verify_password(password, u.password_hash) and totp_verify(u.totp_secret, code)
        if u is None or not ok:
            self.failed[email] = recent + [time.time()]
            self.audit("login_failed", email=email)
            raise PermissionError("invalid credentials")
        self.failed.pop(email, None)
        tok = secrets.token_urlsafe(32)
        now = time.time_ns()
        with self.db.write() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_ns < ?", (now,))
            conn.execute("INSERT INTO sessions (token_sha256, email, expires_ns) VALUES (?, ?, ?)",
                         (_token_hash(tok), email, now + ttl_h * 3600 * 10**9))
            cur = _USERS.get(conn, email)
            if cur is not None:
                cur.last_login = time.time()
                _USERS.upsert(conn, cur)
            _insert_audit(conn, {"ts": time.time(), "event": "login", "email": email})
        return tok

    def session_user(self, token: str | None) -> User | None:
        if not token:
            return None
        th = _token_hash(token)
        with self.db.connection() as conn:
            r = conn.execute("SELECT s.expires_ns, u.body FROM sessions s JOIN users u ON u.email = s.email "
                             "WHERE s.token_sha256 = ?", (th,)).fetchone()
        if r is None:
            return None
        if r[0] < time.time_ns():
            with self.db.write() as conn:
                conn.execute("DELETE FROM sessions WHERE token_sha256 = ?", (th,))
            return None
        u = User.model_validate_json(r[1])
        return u if u.enabled else None

    def logout(self, token: str) -> None:
        with self.db.write() as conn:
            conn.execute("DELETE FROM sessions WHERE token_sha256 = ?", (_token_hash(token),))

    # ------------------------------------------------------------------ admin
    def _require(self, email: str, role: Role) -> User:
        u = self.get_user(email)
        if u is None or ROLE_RANK[u.role] < ROLE_RANK[role]:
            raise PermissionError(f"{role} role required")
        return u

    def set_role(self, by_email: str, email: str, role: Role) -> None:
        self._require(by_email, "owner")
        if role not in ROLES:
            raise ValueError("bad role")
        with self.db.write() as conn:
            target = _USERS.get(conn, email.lower())
            if target is None:
                raise KeyError(email)
            owners = conn.execute("SELECT COUNT(*) FROM users WHERE role = 'owner'").fetchone()[0]
            if target.role == "owner" and role != "owner" and owners == 1:
                raise ValueError("cannot demote the last owner")
            target.role = role
            _USERS.upsert(conn, target)
            _insert_audit(conn, {"ts": time.time(), "event": "set_role", "by": by_email, "email": email, "role": role})

    def disable(self, by_email: str, email: str) -> None:
        self._require(by_email, "owner")
        if email.lower() == by_email.lower():
            raise ValueError("cannot disable yourself")
        with self.db.write() as conn:
            target = _USERS.get(conn, email.lower())
            if target is None:
                raise KeyError(email)
            target.enabled = False
            _USERS.upsert(conn, target)
            conn.execute("DELETE FROM sessions WHERE email = ?", (target.email,))   # revoked in the same commit
            _insert_audit(conn, {"ts": time.time(), "event": "disable", "by": by_email, "email": email})

    def list_users(self) -> list[dict]:
        return [{"email": u.email, "role": u.role, "enabled": u.enabled, "last_login": u.last_login} for u in self.users.values()]


def has_role(u: User | None, role: Role) -> bool:
    return u is not None and ROLE_RANK[u.role] >= ROLE_RANK[role]


def env_setup_code_hint(store: AuthStore) -> str | None:
    """Printed once at startup when no owner exists."""
    if store.setup_code:
        return f"goldbot first-run: open the dashboard and create the owner account with setup code {store.setup_code}"
    return None


__all__ = ["AuthStore", "User", "Invite", "import_users_json", "import_audit_jsonl", "has_role", "totp_code", "totp_verify", "new_totp_secret", "totp_uri", "ROLES", "env_setup_code_hint", "os"]
