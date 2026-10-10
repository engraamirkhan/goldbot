"""Multi-user authentication for the dashboard and API. Standard library only.

* Users: email, scrypt password hash, TOTP secret (RFC 6238), role in {owner, approver, viewer}, enabled flag.
* Roles: viewer reads everything; approver can also approve/reject proposals; owner can also invite, remove,
  change roles, /rearm and /mode. Trade entries are still confirmed by a human (approver or owner); exits never need anyone.
* The owner is the sole admin: the owner role belongs only to `auth.owner_email` (set in the server's git-ignored
  config/settings.local.yaml; the repo is public). It is never granted through an invite or a role change, and the
  owner can never be demoted or disabled.
* New users only through the owner: owner creates an invite (approver or viewer, 72 h expiry) -> link with a
  one-time token -> invitee sets a password and enrols an authenticator -> account active. No email server needed;
  the owner sends the link however they like. The only other path that creates a user is the owner bootstrap.
* Bootstrap: when no users exist, the server prints a one-time setup code to its log (and Telegram when running),
  and the owner account is created with that code and the configured owner email. It returns 10 one-time recovery
  codes, shown once; only their hashes are stored.
* Passwords: at least 12 characters and different from the current one. Change (logged in: current password +
  authenticator code) revokes the user's other sessions. Forgot (email + a current authenticator code) is
  enumeration-safe, counts towards the lockout and revokes every session. Lost authenticator: the owner creates a
  one-time reset link (24 h) that sets a new password and enrols a new authenticator; the owner can instead log in
  with a recovery code, which also enrols a new authenticator.
* Sessions: random bearer tokens with 12 h expiry, stored server-side as SHA-256 hashes, so they can be revoked
  and survive an API restart.
* Audit: every login, failed login, invite, role change, password or authenticator change and decision is appended
  to the append-only `audit` table.

Storage is `state/aux.db` (SQLite WAL, docs/proposals/2026-10-state-store.md step 1). users.json and audit.jsonl
from before the database are imported once, when the database has no users, and then left untouched as a backup.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
import struct
import time
from pathlib import Path
from typing import Any, NoReturn

from pydantic import Field

from goldbot.api.schema import Role
from goldbot.base import Record
from goldbot.db import Database, RecordRepo, db_path

log = logging.getLogger(__name__)

ROLES: tuple[Role, ...] = ("owner", "approver", "viewer")
GRANTABLE: tuple[Role, ...] = ("approver", "viewer")          # roles the owner can give; "owner" is never granted
ROLE_RANK = {r: i for i, r in enumerate(reversed(ROLES))}  # viewer 0, approver 1, owner 2
MIN_PASSWORD = 12
LOCKOUT_FAILURES, LOCKOUT_WINDOW_S = 5, 900
SESSION_TTL_H, INVITE_TTL_H, RESET_TTL_H = 12, 72, 24
RECOVERY_CODES = 10


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


def check_password_policy(new: str, current_hash: str | None = None) -> None:
    """At least 12 characters and, when the user has one, different from the current password."""
    if len(new) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if current_hash is not None and verify_password(new, current_hash):
        raise ValueError("the new password must differ from the current one")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_recovery_code() -> str:
    raw = base64.b32encode(secrets.token_bytes(10)).decode()          # 80 bits, 16 characters
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4)).lower()


def _norm_code(code: str) -> str:
    return code.strip().lower().replace(" ", "")


# ----------------------------------------------------------------------------- records
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


class PasswordReset(Record):
    token_hash: str
    email: str
    expires: float
    created_by: str


class Enrolment(Record):
    """A new authenticator; recovery_codes only when the owner account is created (shown once, never stored)."""
    email: str
    totp_uri: str
    recovery_codes: list[str] = []


class RecoveryLogin(Record):
    token: str
    email: str
    role: Role
    totp_uri: str
    recovery_codes_left: int


_USERS: RecordRepo[User] = RecordRepo("users", User, key="email", columns={
    "email": lambda u: u.email, "role": lambda u: u.role, "enabled": lambda u: int(u.enabled)})
_INVITES: RecordRepo[Invite] = RecordRepo("invites", Invite, key="token_sha256", columns={
    "token_sha256": lambda i: i.token_hash, "email": lambda i: i.email, "expires_ns": lambda i: int(i.expires * 1e9)})
_RESETS: RecordRepo[PasswordReset] = RecordRepo("password_resets", PasswordReset, key="token_sha256", columns={
    "token_sha256": lambda r: r.token_hash, "email": lambda r: r.email, "expires_ns": lambda r: int(r.expires * 1e9)})


def _insert_audit(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    actor = entry.get("by") or entry.get("email")
    conn.execute("INSERT INTO audit (ts_ns, event, actor, body) VALUES (?, ?, ?, ?)",
                 (int(float(entry.get("ts", 0)) * 1e9), str(entry["event"]), None if actor is None else str(actor),
                  json.dumps(entry)))


def _audit(conn: sqlite3.Connection, event: str, **kw: Any) -> None:
    _insert_audit(conn, {"ts": time.time(), "event": event, **kw})


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


def owner_conflict(owner_emails: list[str], n_users: int, configured: str | None) -> str | None:
    """Why the stored owner accounts do not match `auth.owner_email`, or None. Never echoes an email."""
    if n_users == 0:
        return None
    if not configured:
        return ("auth.owner_email is not set: put it in config/settings.local.yaml on the server so the dashboard "
                "owner can be verified")
    stray = [e for e in owner_emails if e.lower() != configured.lower()]
    if stray:
        return (f"{len(stray)} dashboard owner account(s) do not match auth.owner_email; nothing was changed. "
                "Correct config/settings.local.yaml, or move the owner role deliberately")
    if not owner_emails:
        return "no dashboard account holds the owner role"
    return None


def owner_health_note(state_dir: str | Path, owner_email: str | None) -> str | None:
    """For the health check: a warning when aux.db's owner accounts differ from `auth.owner_email`. Opens aux.db
    read-only (never creates or migrates it); a missing file or users table means no users yet."""
    p = db_path(state_dir, "aux")
    if not p.exists():
        return None
    try:
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
        try:
            owners = [e for (e,) in conn.execute("SELECT email FROM users WHERE role = 'owner'")]
            n = int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"aux.db unreadable: {exc}"
    return owner_conflict(owners, n, owner_email)


# ----------------------------------------------------------------------------- store
class AuthStore:
    """Users, invites, sessions, password resets, recovery codes and the audit trail in `<state_dir>/aux.db`
    (docs/proposals/2026-10-state-store.md, step 1). Sessions are stored as token hashes with their expiry, so they
    survive an API restart and a deploy, and any number of AuthStore instances (threads, processes) share them.
    Audit rows are append-only (SQL triggers).

    Dual-read: when the database has no users yet, users.json and audit.jsonl are imported once; the files are
    never written again and stay as a backup. Failed-login counters stay in memory (a restart clears a lockout)."""

    def __init__(self, state_dir: str | Path, owner_email: str | None = None):
        from goldbot.db.import_state import ImportSpec, import_file
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.owner_email = owner_email.strip().lower() if owner_email and owner_email.strip() else None
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
        note = self.owner_conflict()
        if note:
            log.warning("dashboard auth: %s", note)

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

    def owner_conflict(self) -> str | None:
        """See `owner_conflict`; the health check can call `owner_health_note` without an AuthStore."""
        us = list(self.users.values())
        return owner_conflict([u.email for u in us if u.role == "owner"], len(us), self.owner_email)

    def audit(self, event: str, **kw: Any) -> None:
        with self.db.write() as conn:
            _audit(conn, event, **kw)

    def audit_events(self) -> list[dict[str, Any]]:
        """The audit trail, oldest first, as the entries were written ({"ts", "event", ...})."""
        with self.db.connection() as conn:
            return [json.loads(b) for (b,) in conn.execute("SELECT body FROM audit ORDER BY id")]

    # ------------------------------------------------------------------ lockout (in memory, per process)
    def _check_lockout(self, email: str, event: str) -> list[float]:
        recent = [t for t in self.failed.get(email, []) if t > time.time() - LOCKOUT_WINDOW_S]
        if len(recent) >= LOCKOUT_FAILURES:
            self.audit(event, email=email)
            raise PermissionError("too many attempts; wait 15 minutes")
        return recent

    def _fail(self, email: str, recent: list[float], event: str) -> NoReturn:
        self.failed[email] = recent + [time.time()]
        self.audit(event, email=email)
        raise PermissionError("invalid credentials")

    # ------------------------------------------------------------------ bootstrap and invites
    def bootstrap_owner(self, setup_code: str, email: str, password: str) -> Enrolment:
        """Create the owner: needs the setup code, `auth.owner_email` set and this email matching it."""
        if not self.setup_code or not hmac.compare_digest(setup_code, self.setup_code):
            raise PermissionError("setup not available or bad code")
        if not self.owner_email:
            raise PermissionError("auth.owner_email is not set: add `auth: {owner_email: ...}` to "
                                  "config/settings.local.yaml on the server and restart the API")
        if email.strip().lower() != self.owner_email:
            self.audit("bootstrap_refused", reason="email does not match auth.owner_email")
            raise PermissionError("this email is not the configured owner (auth.owner_email)")
        check_password_policy(password)
        email = self.owner_email
        secret = new_totp_secret()
        codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
        pw_hash = hash_password(password)
        with self.db.write() as conn:
            if _USERS.count(conn):                       # another process created the owner first
                self.setup_code = None
                raise PermissionError("setup not available or bad code")
            _USERS.upsert(conn, User(email=email, role="owner", password_hash=pw_hash, totp_secret=secret))
            self._store_recovery_codes(conn, email, codes)
            _audit(conn, "bootstrap_owner", email=email)
        self.setup_code = None
        return Enrolment(email=email, totp_uri=totp_uri(secret, email), recovery_codes=codes)

    def create_invite(self, by_email: str, email: str, role: Role, ttl_h: int = INVITE_TTL_H) -> str:
        self._require(by_email, "owner")
        if role not in GRANTABLE:
            raise ValueError("an invite can only grant approver or viewer; the owner role is never granted")
        email = email.strip().lower()
        if self.get_user(email) is not None:
            raise ValueError("already a user; use a reset link for a lost password or authenticator")
        token = secrets.token_urlsafe(24)
        th = _token_hash(token)
        with self.db.write() as conn:
            _INVITES.upsert(conn, Invite(token_hash=th, email=email, role=role, expires=time.time() + ttl_h * 3600, invited_by=by_email))
            _audit(conn, "invite", by=by_email, email=email, role=role)
        return token

    def accept_invite(self, token: str, password: str) -> tuple[str, str]:
        th = _token_hash(token)
        if len(password) < MIN_PASSWORD:
            with self.db.connection() as conn:
                inv0 = _INVITES.get(conn, th)
            if inv0 is None or inv0.expires < time.time():
                raise PermissionError("invite invalid or expired")
            check_password_policy(password)
        secret = new_totp_secret()
        pw_hash = hash_password(password)                # scrypt outside the write lock
        with self.db.write() as conn:
            inv = _INVITES.get(conn, th)
            if inv is None or inv.expires < time.time():
                raise PermissionError("invite invalid or expired")
            if inv.role not in GRANTABLE or _USERS.get(conn, inv.email) is not None:
                raise PermissionError("invite invalid or expired")     # never overwrites an existing account
            _USERS.upsert(conn, User(email=inv.email, role=inv.role, password_hash=pw_hash, totp_secret=secret))
            _INVITES.delete(conn, th)                    # one-time: deleted in the same transaction
            _audit(conn, "accept_invite", email=inv.email, role=inv.role)
        return inv.email, totp_uri(secret, inv.email)

    # ------------------------------------------------------------------ login and sessions
    def _new_session(self, conn: sqlite3.Connection, email: str, ttl_h: int = SESSION_TTL_H) -> str:
        tok = secrets.token_urlsafe(32)
        now = time.time_ns()
        conn.execute("DELETE FROM sessions WHERE expires_ns < ?", (now,))
        conn.execute("INSERT INTO sessions (token_sha256, email, expires_ns) VALUES (?, ?, ?)",
                     (_token_hash(tok), email, now + ttl_h * 3600 * 10**9))
        cur = _USERS.get(conn, email)
        if cur is not None:
            cur.last_login = time.time()
            _USERS.upsert(conn, cur)
        return tok

    def login(self, email: str, password: str, code: str, ttl_h: int = SESSION_TTL_H) -> str:
        email = email.lower()
        recent = self._check_lockout(email, "login_locked")
        u = self.get_user(email)
        ok = u is not None and u.enabled and verify_password(password, u.password_hash) and totp_verify(u.totp_secret, code)
        if u is None or not ok:
            self._fail(email, recent, "login_failed")
        self.failed.pop(email, None)
        with self.db.write() as conn:
            tok = self._new_session(conn, email, ttl_h)
            _audit(conn, "login", email=email)
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

    # ------------------------------------------------------------------ passwords
    def change_password(self, token: str, current_password: str, code: str, new_password: str) -> None:
        """Logged in: current password + authenticator code + new password. Other sessions are revoked."""
        u = self.session_user(token)
        if u is None:
            raise PermissionError("login required")
        recent = self._check_lockout(u.email, "password_change_locked")
        if not (verify_password(current_password, u.password_hash) and totp_verify(u.totp_secret, code)):
            self._fail(u.email, recent, "password_change_failed")
        check_password_policy(new_password, u.password_hash)
        pw_hash = hash_password(new_password)
        with self.db.write() as conn:
            cur = _USERS.get(conn, u.email)
            if cur is None:
                raise PermissionError("login required")
            cur.password_hash = pw_hash
            _USERS.upsert(conn, cur)
            conn.execute("DELETE FROM sessions WHERE email = ? AND token_sha256 != ?", (u.email, _token_hash(token)))
            _audit(conn, "password_change", email=u.email)
        self.failed.pop(u.email, None)

    def forgot_password(self, email: str, code: str, new_password: str) -> None:
        """Self-service, no email server: email + a current authenticator code + new password. An unknown email,
        a disabled account and a wrong code all fail the same way and count towards the lockout. Every session of
        the user is revoked."""
        email = email.strip().lower()
        check_password_policy(new_password)                         # length only: independent of the account
        recent = self._check_lockout(email, "password_forgot_locked")
        u = self.get_user(email)
        if u is None or not u.enabled or not totp_verify(u.totp_secret, code):
            self._fail(email, recent, "password_forgot_failed")
        check_password_policy(new_password, u.password_hash)
        pw_hash = hash_password(new_password)
        with self.db.write() as conn:
            cur = _USERS.get(conn, email)
            if cur is None:
                raise PermissionError("invalid credentials")
            cur.password_hash = pw_hash
            _USERS.upsert(conn, cur)
            conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
            _audit(conn, "password_forgot", email=email)
        self.failed.pop(email, None)

    def create_reset_link(self, by_email: str, email: str, ttl_h: int = RESET_TTL_H) -> str:
        """Owner only: a one-time token (24 h) that sets a new password and enrols a new authenticator."""
        self._require(by_email, "owner")
        email = email.strip().lower()
        token = secrets.token_urlsafe(24)
        with self.db.write() as conn:
            if _USERS.get(conn, email) is None:
                raise KeyError(email)
            _RESETS.upsert(conn, PasswordReset(token_hash=_token_hash(token), email=email,
                                               expires=time.time() + ttl_h * 3600, created_by=by_email))
            _audit(conn, "reset_link", by=by_email, email=email)
        return token

    def use_reset_link(self, token: str, new_password: str) -> Enrolment:
        th = _token_hash(token)
        with self.db.connection() as conn:
            r0 = _RESETS.get(conn, th)
            u0 = _USERS.get(conn, r0.email) if r0 is not None else None
        if r0 is None or r0.expires < time.time() or u0 is None:
            raise PermissionError("reset link invalid or expired")
        check_password_policy(new_password, u0.password_hash)
        secret = new_totp_secret()
        pw_hash = hash_password(new_password)
        with self.db.write() as conn:
            r = _RESETS.get(conn, th)
            u = _USERS.get(conn, r.email) if r is not None else None
            if r is None or r.expires < time.time() or u is None:
                raise PermissionError("reset link invalid or expired")
            u.password_hash, u.totp_secret = pw_hash, secret
            _USERS.upsert(conn, u)
            conn.execute("DELETE FROM password_resets WHERE email = ?", (u.email,))     # single use, older links too
            conn.execute("DELETE FROM sessions WHERE email = ?", (u.email,))
            _audit(conn, "password_reset", email=u.email, by=r.created_by)
        self.failed.pop(u.email, None)
        return Enrolment(email=u.email, totp_uri=totp_uri(secret, u.email))

    # ------------------------------------------------------------------ owner recovery codes
    @staticmethod
    def _store_recovery_codes(conn: sqlite3.Connection, email: str, codes: list[str]) -> None:
        conn.execute("DELETE FROM recovery_codes WHERE email = ?", (email,))
        now = time.time_ns()
        conn.executemany("INSERT INTO recovery_codes (code_sha256, email, created_ns, used_ns) VALUES (?, ?, ?, NULL)",
                         [(_token_hash(_norm_code(c)), email, now) for c in codes])

    def recovery_codes_left(self, email: str) -> int:
        with self.db.connection() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE email = ? AND used_ns IS NULL",
                                    (email.lower(),)).fetchone()[0])

    def regenerate_recovery_codes(self, token: str, password: str, code: str) -> list[str]:
        """Owner, logged in, with password + authenticator code: 10 new codes (shown once); the old ones stop working."""
        u = self.session_user(token)
        if u is None or u.role != "owner":
            raise PermissionError("owner role required")
        recent = self._check_lockout(u.email, "recovery_codes_locked")
        if not (verify_password(password, u.password_hash) and totp_verify(u.totp_secret, code)):
            self._fail(u.email, recent, "recovery_codes_failed")
        codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
        with self.db.write() as conn:
            self._store_recovery_codes(conn, u.email, codes)
            _audit(conn, "recovery_codes", email=u.email)
        return codes

    def recovery_login(self, email: str, password: str, recovery_code: str) -> RecoveryLogin:
        """Owner only: password + a one-time recovery code in place of the authenticator. Spends the code, enrols a
        new authenticator, revokes every other session and returns a new one."""
        email = email.strip().lower()
        recent = self._check_lockout(email, "recovery_login_locked")
        u = self.get_user(email)
        if u is None or not u.enabled or u.role != "owner" or not verify_password(password, u.password_hash):
            self._fail(email, recent, "recovery_login_failed")
        secret = new_totp_secret()
        with self.db.write() as conn:
            spent = conn.execute("UPDATE recovery_codes SET used_ns = ? WHERE code_sha256 = ? AND email = ? "
                                 "AND used_ns IS NULL", (time.time_ns(), _token_hash(_norm_code(recovery_code)), email)).rowcount
            if spent == 1:
                cur = _USERS.get(conn, email)
                if cur is None:
                    raise PermissionError("invalid credentials")
                cur.totp_secret = secret
                _USERS.upsert(conn, cur)
                conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
                tok = self._new_session(conn, email)
                left = int(conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE email = ? AND used_ns IS NULL",
                                        (email,)).fetchone()[0])
                _audit(conn, "recovery_login", email=email, codes_left=left)
        if spent != 1:
            self._fail(email, recent, "recovery_login_failed")
        self.failed.pop(email, None)
        return RecoveryLogin(token=tok, email=email, role=u.role, totp_uri=totp_uri(secret, email), recovery_codes_left=left)

    # ------------------------------------------------------------------ admin (owner only)
    def _require(self, email: str, role: Role) -> User:
        u = self.get_user(email)
        if u is None or ROLE_RANK[u.role] < ROLE_RANK[role]:
            raise PermissionError(f"{role} role required")
        return u

    def set_role(self, by_email: str, email: str, role: Role) -> None:
        """Only approver or viewer can be assigned; the owner is never demoted."""
        self._require(by_email, "owner")
        if role not in ROLES:
            raise ValueError("bad role")
        if role not in GRANTABLE:
            raise ValueError("the owner role is never granted; it belongs to auth.owner_email")
        with self.db.write() as conn:
            target = _USERS.get(conn, email.lower())
            if target is None:
                raise KeyError(email)
            if target.role == "owner":
                raise ValueError("cannot demote the owner (the last owner is never demoted)")
            target.role = role
            _USERS.upsert(conn, target)
            _audit(conn, "set_role", by=by_email, email=email, role=role)

    def disable(self, by_email: str, email: str) -> None:
        self._require(by_email, "owner")
        if email.lower() == by_email.lower():
            raise ValueError("cannot disable yourself")
        with self.db.write() as conn:
            target = _USERS.get(conn, email.lower())
            if target is None:
                raise KeyError(email)
            if target.role == "owner":
                raise ValueError("cannot disable the owner")
            target.enabled = False
            _USERS.upsert(conn, target)
            conn.execute("DELETE FROM sessions WHERE email = ?", (target.email,))   # revoked in the same commit
            _audit(conn, "disable", by=by_email, email=email)

    def enable(self, by_email: str, email: str) -> None:
        self._require(by_email, "owner")
        with self.db.write() as conn:
            target = _USERS.get(conn, email.lower())
            if target is None:
                raise KeyError(email)
            target.enabled = True
            _USERS.upsert(conn, target)
            _audit(conn, "enable", by=by_email, email=email)

    def revoke_sessions(self, by_email: str, email: str) -> int:
        """Sign a user out everywhere; returns the number of sessions ended."""
        self._require(by_email, "owner")
        with self.db.write() as conn:
            if _USERS.get(conn, email.lower()) is None:
                raise KeyError(email)
            n = conn.execute("DELETE FROM sessions WHERE email = ?", (email.lower(),)).rowcount
            _audit(conn, "revoke_sessions", by=by_email, email=email, sessions=n)
        return int(n)

    def list_users(self) -> list[dict]:
        return [{"email": u.email, "role": u.role, "enabled": u.enabled, "last_login": u.last_login} for u in self.users.values()]


def has_role(u: User | None, role: Role) -> bool:
    return u is not None and ROLE_RANK[u.role] >= ROLE_RANK[role]


def env_setup_code_hint(store: AuthStore) -> str | None:
    """Printed once at startup when no owner exists."""
    if store.setup_code:
        missing = "" if store.owner_email else " (first set auth.owner_email in config/settings.local.yaml and restart)"
        return f"goldbot first-run: open the dashboard and create the owner account with setup code {store.setup_code}{missing}"
    return None


__all__ = ["AuthStore", "User", "Invite", "PasswordReset", "Enrolment", "RecoveryLogin", "import_users_json",
           "import_audit_jsonl", "owner_conflict", "owner_health_note", "check_password_policy", "has_role",
           "totp_code", "totp_verify", "new_totp_secret", "totp_uri", "ROLES", "GRANTABLE", "env_setup_code_hint", "os"]
