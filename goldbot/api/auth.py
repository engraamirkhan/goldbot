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
* The owner account is pinned to `auth.owner_email`: every owner-only action (`_require("owner")`, the API's
  owner routes, recovery codes, /rearm, /mode) also needs the account's email to equal it, and fails closed when it
  is unset. An owner in a pre-database users.json whose email differs is imported as a viewer (audited, warned).
* Passwords: at least 12 characters and different from the current one. Change (logged in: current password +
  authenticator code) revokes the user's other sessions. Forgot (email + a current authenticator code) is
  enumeration-safe, counts towards the lockout and revokes every session; for the OWNER it also needs one of the
  owner's recovery codes (spent), so the phone or TOTP seed alone can never take over the admin account. Every
  successful forgot-password sends the owner a Telegram notice (a row in state/agent_runs.jsonl, which the Telegram
  service relays). Lost authenticator: the owner creates a one-time reset link (24 h) that sets a new password and
  enrols a new authenticator; the owner can instead log in with a recovery code, which also enrols a new
  authenticator. A password change, a forgot-password and disabling an account delete that account's open reset
  links; a password change, a forgot-password and a recovery-code sign-in also delete its email sign-in links.
* TOTP: each code is accepted once. The newest time step a user has spent is stored, and any code for that step or
  an earlier one is refused (RFC 6238 section 5.2), on login, password change, forgot-password, /rearm and /mode.
* Timing: login and recovery-login run scrypt against a dummy hash for an unknown email, so the response time does
  not reveal whether an account exists.
* Lockout: 5 failures per email or 20 per client IP in 15 minutes, counted in aux.db (survives a restart, pruned to
  the window). Trade-off: anyone who knows an email can keep that account locked out by failing on purpose; the
  per-IP limit slows that down but a distributed attacker can still do it. The owner can always recover from the
  server (the lockout clears 15 minutes after the last failure). Every auth flow (login, forgot-password, recovery
  login, password change, recovery-code regeneration, /rearm and /mode codes) reserves its failure row before
  checking (`_reserve`, one BEGIN IMMEDIATE transaction), so a concurrent burst gets at most 5 checks, not 5 per
  thread. Wrong passwords or codes on a signed-in password change or recovery-code regeneration count under
  `email:<e>`, like sign-in, so they also lock sign-in, re-arm and auto mode for 15 minutes.
* Authenticator codes for /rearm and /mode (dashboard and Telegram, `verify_code_guarded`) are counted under their
  own key, `code:<email>`: 5 wrong codes lock re-arm and auto mode (both channels) for 15 minutes but never sign-in,
  so a thief guessing codes from a stolen session cannot lock the owner out. Failed sign-ins lock both, so someone
  failing logins on purpose can also block re-arm and auto mode for a while (fails safe: entries stay
  propose-and-approve, /halt and /mode propose are never gated). Crossing the limit sends the owner one notice
  (at most one an hour) naming the server-side `python -m goldbot.ops.run revoke-sessions`.
* The owner account is never reset by a reset link (`create_reset_link`/`use_reset_link` refuse it): a stolen
  owner session must not set a new password and authenticator. The owner recovers with a recovery code.
* Sessions: random bearer tokens with 12 h expiry, stored server-side as SHA-256 hashes, so they can be revoked
  and survive an API restart.
* Email sign-in link (owner only): `request_email_link` reveals nothing about the address (the API always returns
  one generic 200), and issues a link only when the address is `auth.owner_email` (trimmed, case-insensitive), that
  account exists, is the owner and is enabled, and mail is configured. The token is random (32 bytes), stored as a
  SHA-256 hash, single-use and valid 15 minutes. At most 3 requests an hour per address and per client IP, counted
  whatever the address: a flood can delay the owner's link for up to an hour, but password sign-in still works.
  `redeem_email_link` STILL needs a current authenticator code: the link replaces the password, never the second
  factor, because this account approves real-money entries. Any redeem of a live link deletes every outstanding link
  of the account (a wrong code burns the link, so a stolen link gets one guess, and that guess counts towards the
  sign-in lockout); an unknown or expired token counts against the client IP only, so garbage tokens cannot lock the
  owner out. The link carries the token in the URL fragment (`#signin=`), so it is never in a log or a Referer.
* Emailed sign-in code (owner only; ADR 0028 supersedes ADR 0013's "the authenticator is still needed" for this path):
  password + a 6-digit code sent to `auth.owner_email` signs the owner in WITHOUT the authenticator, only while mail
  is ready. Accepted trade-off: password + mailbox can view, approve/reject and halt (the RiskGate still checks every
  order), but cannot keep access: the session records `method = email_code`, and invite, reset link, role change,
  disable, enable and revoke-sessions then need the authenticator or a recovery code (`verify_stepup`); re-arm, auto
  mode, password change and new recovery codes ask for the authenticator as before. The request answers the same for
  every input (scrypt always runs; a decoy challenge for anything else); the code comes from `secrets.randbelow`, is
  stored only as HMAC-SHA-256(keyring key, challenge|code), works once for 10 minutes, 3 wrong tries burn it, only
  the newest works, and it is never logged, audited or put in a URL. Mail shares the email link's 3-an-hour limit per
  address and per client IP. Three burned codes or one sign-in lockout in 24 h suspend email codes until the next
  password + authenticator sign-in (one Telegram notice). Every email-code sign-in sends the owner a Telegram notice.
  Email never resets the password.
* Audit: every login, failed login, invite, role change, password or authenticator change and decision is appended
  to the append-only `audit` table.

Storage is `state/aux.db` (SQLite WAL, docs/proposals/2026-10-state-store.md step 1). users.json and audit.jsonl
from before the database are imported once, when the database has no users, and then left untouched as a backup.
"""
from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import struct
import sys
import time
from pathlib import Path
from typing import Any, NoReturn

from pydantic import Field

from goldbot.api.schema import Role, SessionMethod
from goldbot.base import FrozenRecord, Record
from goldbot.db import Database, RecordRepo, StateDbError, db_path

log = logging.getLogger(__name__)

ROLES: tuple[Role, ...] = ("owner", "approver", "viewer")
GRANTABLE: tuple[Role, ...] = ("approver", "viewer")          # roles the owner can give; "owner" is never granted
ROLE_RANK = {r: i for i, r in enumerate(reversed(ROLES))}  # viewer 0, approver 1, owner 2
MIN_PASSWORD = 12
LOCKOUT_FAILURES, LOCKOUT_WINDOW_S = 5, 900
IP_LOCKOUT_FAILURES = 20                 # failures per client IP per window, across emails
LOCK_NOTICE_EVERY_S = 3600             # at most one code-lockout notice to the owner per hour
OWNER_RESET_REFUSED = "the owner account cannot be reset with a link; sign in with a recovery code instead"
SESSION_TTL_H, INVITE_TTL_H, RESET_TTL_H = 12, 72, 24
EMAIL_LINK_TTL_S, EMAIL_LINK_PER_HOUR, EMAIL_LINK_WINDOW_S = 900, 3, 3600
EMAIL_LINK_REFUSED = "sign-in link invalid or expired, or wrong authenticator code"
EMAIL_LINK_ABUSE_WARN = 3                # owner-address requests refused by the hourly limit in 24 h -> health warn
RECOVERY_CODES = 10
# emailed sign-in codes (BACKLOG 77, ADR 0028; docs/ux/sign-in-email-code.md security conditions 1-11)
EMAIL_CODE_TTL_S, EMAIL_CODE_TRIES, RESEND_AFTER_S = 600, 3, 60
EMAIL_CODE_KEEP_S = 3600                 # a burned or expired challenge stays findable by Resend this long
EMAIL_CODE_BURNS, EMAIL_CODE_SUSPEND_WINDOW_S = 3, 86400     # 3 burned challenges in 24 h suspend email codes
EMAIL_CODE_KEY = "email-code-hmac-key"   # the keyring entry of the codes' HMAC key (32 random bytes, hex)
EMAIL_CODE_REFUSED = "that code did not work, or it has expired or was already used"
# the sign-in lockout's words: the Login screen recognises them (web/src/lib/api.ts SERVER_SAYS, pinned by
# tests/test_web_auth_wording.py) to show State F instead of counting a wrong code
SIGN_IN_LOCKED = "too many attempts; wait 15 minutes"
SESSION_METHODS: tuple[SessionMethod, ...] = ("password_totp", "email_link", "email_code", "recovery")
_NS = 10**9


# ----------------------------------------------------------------------------- TOTP (RFC 6238)
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _totp_now() -> float:
    """The TOTP clock. A function of its own so tests can step it (each test login then gets a fresh time step)."""
    return time.time()


def totp_code(secret_b32: str, t: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    counter = int((t if t is not None else _totp_now()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return f"{code:0{digits}d}"


def totp_step(secret_b32: str, code: str, after: int = -1, window: int = 1) -> int | None:
    """The newest 30-s time step within +-window whose code matches and that is later than `after`, else None."""
    base = int(_totp_now() // 30)
    code = code.strip()
    for counter in range(base + window, base - window - 1, -1):
        if counter > after and hmac.compare_digest(totp_code(secret_b32, counter * 30.0), code):
            return counter
    return None


def totp_verify(secret_b32: str, code: str, window: int = 1) -> bool:
    """Stateless check (no replay protection); accounts use AuthStore.verify_totp, which spends the step."""
    return totp_step(secret_b32, code, window=window) is not None


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


@functools.lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A real scrypt hash of a random password: verifying against it costs the same as against a user's hash, so
    an unknown email takes as long as a wrong password (no account enumeration by timing)."""
    return hash_password(secrets.token_urlsafe(16))


def check_password_policy(new: str, current_hash: str | None = None) -> None:
    """At least 12 characters and, when the user has one, different from the current password."""
    if len(new) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
    if current_hash is not None and verify_password(new, current_hash):
        raise ValueError("the new password must differ from the current one")


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _secret_get(key: str) -> str | None:
    from goldbot.ops.accounts import get_secret
    return get_secret(key)


def _secret_set(key: str, value: str) -> None:
    from goldbot.ops.accounts import set_secret
    set_secret(key, value)


_KEY_CACHE: dict[str, bytes] = {}


def email_code_key() -> bytes | None:
    """The HMAC key of the emailed codes (security condition 4): 32 bytes in the OS keyring under `email-code-hmac-key`
    (goldbot.ops.accounts), never in aux.db or a backup. Created on first use, read back (an unwritable keyring stays
    off; the value read back, not the one written, is used, so a racing writer cannot leave two keys), then cached
    in this process. The API runs as ONE process (`uvicorn.run(create_app(...))` takes an app object, which uvicorn
    cannot fork into workers), so one cache serves every request. An unreadable keyring or a stored value that is not 32 bytes of hex is None:
    email codes are off (fail closed) and the stored value is never overwritten. Rotate with
    `python -m goldbot.ops.accounts set email-code-hmac-key` and restart the API; open codes stop working."""
    cached = _KEY_CACHE.get(EMAIL_CODE_KEY)
    if cached is not None:
        return cached
    try:
        value = _secret_get(EMAIL_CODE_KEY)
        if value is None:
            _secret_set(EMAIL_CODE_KEY, secrets.token_hex(32))
            value = _secret_get(EMAIL_CODE_KEY)
        raw = bytes.fromhex(value or "")
    except Exception as exc:                # noqa: BLE001 - the type only; an unreadable key means codes are off
        log.warning("email codes off: %s is unreadable (%s)", EMAIL_CODE_KEY, type(exc).__name__)
        return None
    if len(raw) != 32:
        log.warning("email codes off: %s is missing or not 32 bytes of hex", EMAIL_CODE_KEY)
        return None
    _KEY_CACHE[EMAIL_CODE_KEY] = raw
    return raw


def _code_mac(key: bytes, challenge: str, code: str) -> str:
    """HMAC-SHA-256 over challenge|code (condition 5): a code is worthless for any other challenge, and a copy of
    aux.db without the keyring key cannot be brute-forced over the million possible codes."""
    return hmac.new(key, f"{challenge}|{code}".encode(), hashlib.sha256).hexdigest()


def need_stepup(method: str | None) -> bool:
    """A session opened with password + emailed code needs the authenticator (or a recovery code) again for the owner's
    admin actions (security ruling 2); every other sign-in method already proved it."""
    return method == "email_code"


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


class EmailLink(Record):
    """The owner's one-time email sign-in link (token hash only)."""
    token_hash: str
    email: str
    expires: float


class EmailCodeIssue(FrozenRecord):
    """What a code request or resend answers: the challenge (a decoy with no row when nothing was sent), the code to
    mail (None when nothing is sent; never in a repr, so never in a log line) and when the screen may resend."""
    challenge: str = Field(repr=False)
    code: str | None = Field(default=None, repr=False)
    resend_after_s: int = RESEND_AFTER_S


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
_EMAIL_LINKS: RecordRepo[EmailLink] = RecordRepo("email_links", EmailLink, key="token_sha256", columns={
    "token_sha256": lambda r: r.token_hash, "email": lambda r: r.email, "expires_ns": lambda r: int(r.expires * 1e9)})


def _insert_audit(conn: sqlite3.Connection, entry: dict[str, Any]) -> None:
    actor = entry.get("by") or entry.get("email")
    conn.execute("INSERT INTO audit (ts_ns, event, actor, body) VALUES (?, ?, ?, ?)",
                 (int(float(entry.get("ts", 0)) * 1e9), str(entry["event"]), None if actor is None else str(actor),
                  json.dumps(entry)))


def _audit(conn: sqlite3.Connection, event: str, **kw: Any) -> None:
    _insert_audit(conn, {"ts": time.time(), "event": event, **kw})


def _settings_owner_email() -> str | None:
    """auth.owner_email from settings (config/settings.local.yaml on the server); unreadable settings mean unset."""
    try:
        from goldbot.config import load_settings
        return load_settings().auth.owner_email
    except Exception:                       # noqa: BLE001 - unset fails closed (no owner-only action works)
        return None


def _norm_email(email: str | None) -> str | None:
    return email.strip().lower() if email and email.strip() else None


def import_users(path: Path, conn: sqlite3.Connection, owner_email: str | None) -> int:
    """Importer for the pre-database users.json (users and open invites), validated with the same Records.

    The owner role belongs only to `auth.owner_email`: an owner whose email differs is imported as a viewer, with an
    audit row and a warning (an edited users.json cannot plant an admin). With owner_email unset the role is kept,
    since demoting it could leave no admin at all; every owner-only action still fails closed until it is set."""
    owner = _norm_email(owner_email)
    d = json.loads(path.read_text())
    users = [User(**u) for u in d.get("users", {}).values()]
    invites = [Invite(**v) for v in d.get("invites", {}).values()]
    for u in users:
        u.email = u.email.strip().lower()
        if u.role == "owner" and owner is not None and u.email != owner:
            u.role = "viewer"
            _audit(conn, "import_owner_demoted", email=u.email, reason="does not match auth.owner_email")
            log.warning("users.json import: an owner account that does not match auth.owner_email was imported as "
                        "a viewer (see the audit trail)")
        _USERS.upsert(conn, u)
    for i in invites:
        _INVITES.upsert(conn, i)
    return len(users) + len(invites)


def import_users_json(path: Path, conn: sqlite3.Connection) -> int:
    """The `import-state` loader: `import_users` with auth.owner_email read from settings."""
    return import_users(path, conn, _settings_owner_email())


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
    except sqlite3.Error as exc:                    # the type only: health reasons reach every dashboard role
        log.warning("aux.db unreadable: %s", exc)
        return f"aux.db unreadable ({type(exc).__name__})"
    return owner_conflict(owners, n, owner_email)


# ----------------------------------------------------------------------------- store
class _Refused(Exception):
    """Inside a write transaction: roll it back (nothing spent), then fail as invalid credentials."""


class _Reservation(FrozenRecord):
    """An attempt already counted as a failure (`AuthStore._reserve`): its counter key, the rows to delete on a
    success, and the counter before and after it."""
    key: str
    rows: list[int]
    prev: int
    new: int


def coarse_ip(ip: str | None) -> str:
    """A network hint for an owner notice, never the full address: IPv4 /16 ('203.0.x.x'), IPv6 /32
    ('2001:db8::/32'), and the IPv4 /16 for an IPv4-mapped IPv6 address ('::ffff:203.0.113.45' -> '203.0.x.x', a
    dual-stack listener's view of an IPv4 client); anything else (no address, a test client) is 'an unknown network'."""
    try:
        addr: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return "an unknown network"
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if addr.version == 4:
        a, b, _, _ = str(addr).split(".")
        return f"{a}.{b}.x.x"
    return str(ipaddress.ip_network(f"{addr}/32", strict=False))


def email_link_health_note(state_dir: str | Path, owner_email: str | None, now_s: float) -> str | None:
    """For the health check: a warning when email sign-in link requests for the owner's address were refused by the
    hourly limit EMAIL_LINK_ABUSE_WARN or more times in the last 24 h (someone may be requesting links, or the owner
    is locked out of that path). Never names the address. Opens aux.db read-only; missing file or table: None."""
    owner = _norm_email(owner_email)
    p = db_path(state_dir, "aux")
    if owner is None or not p.exists():
        return None
    since = int((now_s - 86400) * 1e9)
    try:
        conn = sqlite3.connect(f"{p.resolve().as_uri()}?mode=ro", uri=True)
        try:
            n = int(conn.execute("SELECT COUNT(*) FROM audit WHERE event = 'email_link_rate_limited' AND ts_ns > ? "
                                 "AND ts_ns <= ? AND json_extract(body, '$.email') = ?",
                                 (since, int(now_s * 1e9), owner)).fetchone()[0])
        finally:
            conn.close()
    except sqlite3.Error as exc:
        log.warning("aux.db unreadable: %s", exc)
        return None
    if n < EMAIL_LINK_ABUSE_WARN:
        return None
    return (f"{n} email sign-in link requests for the owner's address hit the hourly limit in the last 24 h: someone "
            f"may be requesting links (password sign-in still works; see email_link_rate_limited in the audit trail)")


def _mask(email: str) -> str:
    """'v***@x.io': enough for the owner to recognise the account, without spelling it out in a chat history."""
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


class AuthStore:
    """Users, invites, sessions, password resets, recovery codes, spent TOTP steps, failed-attempt counters and the
    audit trail in `<state_dir>/aux.db` (docs/proposals/2026-10-state-store.md, step 1). Sessions are stored as
    token hashes with their expiry, so they survive an API restart and a deploy, and any number of AuthStore
    instances (threads, processes) share them. Audit rows are append-only (SQL triggers).

    Dual-read: when the database has no users yet, users.json and audit.jsonl are imported once; the files are
    never written again and stay as a backup."""

    def __init__(self, state_dir: str | Path, owner_email: str | None = None):
        from goldbot.db.import_state import ImportSpec, import_file
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.owner_email = _norm_email(owner_email)
        self.legacy_users_path = self.dir / "users.json"
        self.legacy_audit_path = self.dir / "audit.jsonl"
        self.db = Database.open(self.dir, "aux")
        self.path = self.db.path
        self.setup_code: str | None = None
        if self.user_count() == 0:
            loader = functools.partial(import_users, owner_email=self.owner_email)
            import_file(self.db, ImportSpec(source="users.json", kind="aux", loader=loader), self.legacy_users_path)
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
            return _USERS.get(conn, email.strip().lower())

    @property
    def users(self) -> dict[str, User]:
        with self.db.connection() as conn:
            return {u.email: u for u in _USERS.select(conn)}

    def owner_conflict(self) -> str | None:
        """See `owner_conflict`; the health check can call `owner_health_note` without an AuthStore."""
        us = list(self.users.values())
        return owner_conflict([u.email for u in us if u.role == "owner"], len(us), self.owner_email)

    def is_owner(self, u: User | None) -> bool:
        """The owner role AND the configured `auth.owner_email` (fails closed when it is unset): the role column
        alone is never enough for an owner-only action."""
        return (u is not None and u.enabled and u.role == "owner" and self.owner_email is not None
                and u.email.lower() == self.owner_email)

    def audit(self, event: str, **kw: Any) -> None:
        with self.db.write() as conn:
            _audit(conn, event, **kw)

    def audit_events(self) -> list[dict[str, Any]]:
        """The audit trail, oldest first, as the entries were written ({"ts", "event", ...})."""
        with self.db.connection() as conn:
            return [json.loads(b) for (b,) in conn.execute("SELECT body FROM audit ORDER BY id")]

    # ------------------------------------------------------------------ TOTP (each step spent once)
    @staticmethod
    def _spend_totp(conn: sqlite3.Connection, u: User, code: str) -> bool:
        """Inside a write transaction: accept `code` only for a time step later than the last one this user spent,
        and record it (RFC 6238 section 5.2: the verifier must not accept the second attempt of the same OTP)."""
        row = conn.execute("SELECT last_step FROM totp_steps WHERE email = ?", (u.email,)).fetchone()
        step = totp_step(u.totp_secret, code, after=int(row[0]) if row else -1)
        if step is None:
            return False
        conn.execute("INSERT INTO totp_steps (email, last_step) VALUES (?, ?) "
                     "ON CONFLICT(email) DO UPDATE SET last_step = excluded.last_step", (u.email, step))
        return True

    def verify_totp(self, email: str, code: str) -> bool:
        """A one-time check of `email`'s authenticator code (enabled accounts only), e.g. for /rearm."""
        with self.db.write() as conn:
            u = _USERS.get(conn, email.strip().lower())
            return u is not None and u.enabled and self._spend_totp(conn, u, code)

    def verify_owner_totp(self, code: str) -> bool:
        """The owner's authenticator code, spent once; False when auth.owner_email is unset or matches no owner."""
        if self.owner_email is None:
            return False
        with self.db.write() as conn:
            u = _USERS.get(conn, self.owner_email)
            return self.is_owner(u) and u is not None and self._spend_totp(conn, u, code)

    # ------------------------------------------------------------------ lockout (aux.db, shared by every process)
    def _reserve(self, email: str, locked_event: str, ip: str | None = None, *, code: bool = False) -> _Reservation:
        """Check the lockout and count this attempt as a failure in ONE write transaction (BEGIN IMMEDIATE), before
        the password or code is checked: concurrent attempts serialise here, so a burst gets at most
        LOCKOUT_FAILURES checks. Raises PermissionError while locked. Login counts under `email:<e>`; a code
        (code=True) under `code:<e>`, and is also refused while `email:<e>` is locked (failed sign-ins lock both).
        The caller keeps the rows on a failure and deletes them on a success (`_release`)."""
        key = f"code:{email}" if code else f"email:{email}"
        now = time.time_ns()
        with self.db.write() as conn:
            conn.execute("DELETE FROM auth_failures WHERE ts_ns <= ?", (now - LOCKOUT_WINDOW_S * 10**9,))

            def n(k: str) -> int:
                return int(conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key = ?", (k,)).fetchone()[0])
            prev = n(key)
            locked = (prev >= LOCKOUT_FAILURES or (code and n(f"email:{email}") >= LOCKOUT_FAILURES)
                      or (ip is not None and n(f"ip:{ip}") >= IP_LOCKOUT_FAILURES))
            rows: list[int] = []
            suspended = False
            if locked:
                _audit(conn, locked_event, email=email, **({"ip": ip} if ip else {}))
                if not code and prev >= LOCKOUT_FAILURES:
                    suspended = self._suspend_email_codes(conn, email, "sign-in lockout")
            else:
                for k in [key] + ([f"ip:{ip}"] if ip else []):
                    cur = conn.execute("INSERT INTO auth_failures (key, ts_ns) VALUES (?, ?)", (k, now))
                    rows.append(int(cur.lastrowid or 0))
        if suspended:
            self._suspension_notice("the sign-in lockout")
        if locked:
            raise PermissionError(SIGN_IN_LOCKED)
        return _Reservation(key=key, rows=rows, prev=prev, new=prev + 1)

    @staticmethod
    def _release(conn: sqlite3.Connection, r: _Reservation) -> None:
        """Inside the success's write transaction: the attempt was not a failure; clear its counter (not the IP's)."""
        conn.executemany("DELETE FROM auth_failures WHERE rowid = ?", [(i,) for i in r.rows])
        conn.execute("DELETE FROM auth_failures WHERE key = ?", (r.key,))

    def _unreserve(self, r: _Reservation) -> None:
        """The credentials were right but the request was refused for another reason (the new password breaks the
        policy): this attempt is not a failure, so its rows go; earlier failures stay counted."""
        with self.db.write() as conn:
            conn.executemany("DELETE FROM auth_failures WHERE rowid = ?", [(i,) for i in r.rows])

    def _fail(self, email: str, event: str, ip: str | None = None) -> NoReturn:
        """A reserved attempt failed: its rows stay counted; audit it and refuse with one message for every cause."""
        self.audit(event, email=email, **({"ip": ip} if ip else {}))
        self._suspend_if_locked(email)
        raise PermissionError("invalid credentials")

    # ------------------------------------------------------------------ email-code suspension (security condition 3)
    def _suspend_email_codes(self, conn: sqlite3.Connection, email: str, reason: str) -> bool:
        """Inside a write transaction: suspend the owner's email codes until the next password + authenticator
        sign-in, deleting any open code. True only when this call suspended them (the caller sends the one notice)."""
        if email != self.owner_email:
            return False
        if conn.execute("INSERT OR IGNORE INTO auth_suspensions (email, since_ns) VALUES (?, ?)",
                        (email, time.time_ns())).rowcount != 1:
            return False
        conn.execute("DELETE FROM email_codes WHERE email = ?", (email,))
        _audit(conn, "email_code_suspended", email=email, reason=reason)
        return True

    def _suspend_if_locked(self, email: str) -> None:
        """After a failure counted under `email:<owner>`: a sign-in lockout (LOCKOUT_FAILURES in the window) suspends
        email codes."""
        if email != self.owner_email:
            return
        with self.db.write() as conn:
            n = int(conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key = ?", (f"email:{email}",)).fetchone()[0])
            new = n >= LOCKOUT_FAILURES and self._suspend_email_codes(conn, email, "sign-in lockout")
        if new:
            self._suspension_notice("the sign-in lockout")

    def _burn_suspends(self, conn: sqlite3.Connection, email: str) -> bool:
        """Inside the burn's write transaction: EMAIL_CODE_BURNS burned challenges within 24 h (counted since the last
        resume) suspend email codes. True when this call suspended them."""
        last = conn.execute("SELECT COALESCE(MAX(id), 0) FROM audit WHERE event = 'email_code_resumed' AND actor = ?",
                            (email,)).fetchone()[0]
        since = time.time_ns() - EMAIL_CODE_SUSPEND_WINDOW_S * _NS
        n = int(conn.execute("SELECT COUNT(*) FROM audit WHERE event = 'email_code_burned' AND actor = ? AND id > ? "
                             "AND ts_ns > ?", (email, last, since)).fetchone()[0])
        return n >= EMAIL_CODE_BURNS and self._suspend_email_codes(conn, email, f"{n} burned codes in 24 h")

    def _suspension_notice(self, why: str) -> None:
        self._notify_owner(f"Email codes are suspended for the owner account after {why} (at "
                           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}). Sign-in with your password and "
                           f"authenticator code still works and switches email codes back on. If this was not you, "
                           f"someone may know your password: change it, check the audit trail, and on the server run "
                           f"`python -m goldbot.ops.run revoke-sessions` to sign everyone out.")

    @staticmethod
    def _suspended(conn: sqlite3.Connection, email: str) -> bool:
        return conn.execute("SELECT 1 FROM auth_suspensions WHERE email = ?", (email,)).fetchone() is not None

    def verify_code_guarded(self, code: str, action: str, *, email: str | None = None, ip: str | None = None) -> bool:
        """An authenticator code for an authority-raising action ("rearm", "mode"), counted under `code:<email>`
        (`_reserve`, atomic): refused with PermissionError while locked (event `<action>_locked`), a wrong code
        stays counted (`<action>_failed`), a right one clears the code counter. Code failures never lock sign-in.
        Crossing the limit queues an owner notice (`_lock_notice`). email None: the owner pinned to auth.owner_email
        (unset fails closed, uncounted). Telegram /mode and the dashboard share this counter."""
        target = email.strip().lower() if email is not None else self.owner_email
        if target is None:
            return False
        r = self._reserve(target, f"{action}_locked", ip, code=True)
        ok = self.verify_totp(target, code) if email is not None else self.verify_owner_totp(code)
        if ok:
            with self.db.write() as conn:
                self._release(conn, r)
            return True
        self.audit(f"{action}_failed", email=target, **({"ip": ip} if ip else {}))
        if r.prev < LOCKOUT_FAILURES <= r.new:
            self._lock_notice(action)
        return False

    def _lock_notice(self, action: str) -> None:
        """One owner notice when the code counter crosses the limit, at most one per LOCK_NOTICE_EVERY_S (the audit
        row `code_lock_notice` is the rate limit): no code, no address; names what still works and the server-side
        revoke command, since the thief already holds a signed-in session."""
        now = time.time_ns()
        with self.db.write() as conn:
            last = conn.execute("SELECT MAX(ts_ns) FROM audit WHERE event = 'code_lock_notice'").fetchone()[0]
            if last is not None and now - int(last) < LOCK_NOTICE_EVERY_S * 10**9:
                return
            _audit(conn, "code_lock_notice", action=action)
        what = {"mode": "switching to auto mode", "rearm": "re-arming",
                "stepup": "user management after an email-code sign-in"}.get(action, action)
        mins = LOCKOUT_WINDOW_S // 60
        self._notify_owner(f"{LOCKOUT_FAILURES} wrong authenticator codes for {what} within {mins} minutes (at "
                           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}); re-arm and auto mode are locked "
                           f"for {mins} minutes. /halt and /mode propose still work, and you can still sign in. If "
                           f"this was not you, someone holds a signed-in session: sign everyone out on the server "
                           f"with `python -m goldbot.ops.run revoke-sessions`, then change your password and check "
                           f"the audit trail.")

    @staticmethod
    def _clear_failures(conn: sqlite3.Connection, email: str) -> None:
        """A success clears the account's counter; the IP counter is kept (one good account must not reset it)."""
        conn.execute("DELETE FROM auth_failures WHERE key = ?", (f"email:{email}",))

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
    def _new_session(self, conn: sqlite3.Connection, email: str, ttl_h: int, method: SessionMethod) -> str:
        """A new session that records how it signed in (`method`, always named by the caller): the step-up rule reads
        only this record. The column's default exists only so a rollback release's INSERT still works."""
        tok = secrets.token_urlsafe(32)
        now = time.time_ns()
        conn.execute("DELETE FROM sessions WHERE expires_ns < ?", (now,))
        conn.execute("INSERT INTO sessions (token_sha256, email, expires_ns, method) VALUES (?, ?, ?, ?)",
                     (_token_hash(tok), email, now + ttl_h * 3600 * 10**9, method))
        cur = _USERS.get(conn, email)
        if cur is not None:
            cur.last_login = time.time()
            _USERS.upsert(conn, cur)
        return tok

    def login(self, email: str, password: str, code: str, ttl_h: int = SESSION_TTL_H, ip: str | None = None) -> str:
        email = email.strip().lower()
        r = self._reserve(email, "login_locked", ip)          # counted before the check: a burst gets 5 checks
        u = self.get_user(email)
        # scrypt runs whether or not the account exists, so the response time does not reveal it
        pw_ok = verify_password(password, u.password_hash if u is not None else _dummy_hash())
        tok: str | None = None
        if u is not None and u.enabled and pw_ok:
            with self.db.write() as conn:
                cur = _USERS.get(conn, email)
                if cur is not None and cur.enabled and self._spend_totp(conn, cur, code):
                    tok = self._new_session(conn, email, ttl_h, "password_totp")
                    self._release(conn, r)
                    _audit(conn, "login", email=email)
                    if conn.execute("DELETE FROM auth_suspensions WHERE email = ?", (email,)).rowcount:
                        _audit(conn, "email_code_resumed", email=email)     # condition 3: the authenticator resumes
        if tok is None:
            self._fail(email, "login_failed", ip)
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
        """Ends this session and deletes the account's open email codes (condition 9: no replay after sign-out)."""
        th = _token_hash(token)
        with self.db.write() as conn:
            row = conn.execute("SELECT email FROM sessions WHERE token_sha256 = ?", (th,)).fetchone()
            conn.execute("DELETE FROM sessions WHERE token_sha256 = ?", (th,))
            if row is not None:
                conn.execute("DELETE FROM email_codes WHERE email = ?", (row[0],))

    def session_method(self, token: str | None) -> SessionMethod | None:
        """How the session signed in, from its stored record only (condition 2); None for no session. A value this
        code does not know reads as `email_code`, the method that asks for the most (fails closed)."""
        if not token:
            return None
        with self.db.connection() as conn:
            row = conn.execute("SELECT method FROM sessions WHERE token_sha256 = ?", (_token_hash(token),)).fetchone()
        if row is None:
            return None
        known: dict[str, SessionMethod] = {m: m for m in SESSION_METHODS}
        return known.get(str(row[0]), "email_code")

    # ------------------------------------------------------------------ passwords
    def change_password(self, token: str, current_password: str, code: str, new_password: str) -> None:
        """Logged in: current password + authenticator code + new password. Other sessions and any open reset link
        for the account are revoked."""
        u = self.session_user(token)
        if u is None:
            raise PermissionError("login required")
        r = self._reserve(u.email, "password_change_locked")    # counted under email: before the check
        if not verify_password(current_password, u.password_hash):
            self._fail(u.email, "password_change_failed")
        try:
            check_password_policy(new_password, u.password_hash)    # after the password: reveals nothing new
        except ValueError:
            self._unreserve(r)
            raise
        pw_hash = hash_password(new_password)
        with self.db.write() as conn:
            cur = _USERS.get(conn, u.email)
            if cur is None:
                raise PermissionError("login required")
            ok = self._spend_totp(conn, cur, code)
            if ok:
                cur.password_hash = pw_hash
                _USERS.upsert(conn, cur)
                conn.execute("DELETE FROM sessions WHERE email = ? AND token_sha256 != ?", (u.email, _token_hash(token)))
                conn.execute("DELETE FROM password_resets WHERE email = ?", (u.email,))
                conn.execute("DELETE FROM email_links WHERE email = ?", (u.email,))
                conn.execute("DELETE FROM email_codes WHERE email = ?", (u.email,))
                self._release(conn, r)
                _audit(conn, "password_change", email=u.email)
        if not ok:
            self._fail(u.email, "password_change_failed")

    def forgot_password(self, email: str, code: str, new_password: str, recovery_code: str | None = None,
                        ip: str | None = None) -> None:
        """Self-service, no email server: email + a current authenticator code + new password. The OWNER also needs
        one of the owner's recovery codes, which is spent: the authenticator alone (a stolen phone or TOTP seed)
        never resets the admin account. An unknown email, a disabled account, a wrong code and a missing or wrong
        recovery code all fail the same way and count towards the lockout. Every session and open reset link of the
        user is revoked, and the owner gets a Telegram notice (`_notify_owner`)."""
        email = email.strip().lower()
        check_password_policy(new_password)                         # length only: independent of the account
        r = self._reserve(email, "password_forgot_locked", ip)     # counted before any check: a burst gets 5
        u = self.get_user(email)
        # the same two scrypt calls on every path (unknown, disabled, owner without a recovery code, real account),
        # so the response time does not reveal which accounts exist (security review of c256c23)
        refused = u is None or not u.enabled or (u.role == "owner" and not (self.is_owner(u) and recovery_code))
        same = verify_password(new_password, _dummy_hash() if refused or u is None else u.password_hash)
        pw_hash = hash_password(new_password)
        if refused or u is None:
            self._fail(email, "password_forgot_failed", ip)
        owner = u.role == "owner"
        left: int | None = None
        try:
            with self.db.write() as conn:
                cur = _USERS.get(conn, email)
                if cur is None or not cur.enabled:
                    raise _Refused
                if owner:
                    spent = conn.execute(
                        "UPDATE recovery_codes SET used_ns = ? WHERE code_sha256 = ? AND email = ? AND used_ns IS NULL",
                        (time.time_ns(), _token_hash(_norm_code(recovery_code or "")), email)).rowcount
                    if spent != 1:
                        raise _Refused
                if not self._spend_totp(conn, cur, code):
                    raise _Refused                      # rolls back the recovery code too
                if same:
                    raise ValueError("the new password must differ from the current one")
                cur.password_hash = pw_hash
                _USERS.upsert(conn, cur)
                conn.execute("DELETE FROM sessions WHERE email = ?", (email,))
                conn.execute("DELETE FROM password_resets WHERE email = ?", (email,))
                conn.execute("DELETE FROM email_links WHERE email = ?", (email,))
                conn.execute("DELETE FROM email_codes WHERE email = ?", (email,))
                self._release(conn, r)
                if owner:
                    left = int(conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE email = ? AND used_ns IS NULL",
                                            (email,)).fetchone()[0])
                _audit(conn, "password_forgot", email=email,
                       **({"recovery_codes_left": left} if owner else {}), **({"ip": ip} if ip else {}))
        except _Refused:
            self._fail(email, "password_forgot_failed", ip)
        except ValueError:                              # right credentials, same password: not a failure
            self._unreserve(r)
            raise
        extra =f" One recovery code was spent; {left} left." if owner else ""
        self._notify_owner(f"The password of {u.role} account {_mask(email)} was reset with forgot-password at "
                           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}; every session was signed out."
                           f"{extra} If this was not expected, disable the account on the dashboard (Users) and "
                           f"check the audit trail.")

    def _notify_owner(self, text: str) -> None:
        """A Telegram notice to the owner: one row in state/agent_runs.jsonl, which the Telegram service relays to
        the owner's chat (goldbot/telegram/outbox.py new_reports) and the dashboard shows under Agents. Best effort:
        the audit row is the record; a failed write is logged, never raised."""
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        row = {"role": "account_security", "started_utc": now, "finished_utc": now, "status": "notice", "turns": 0,
               "cost_usd": 0.0, "report_path": None, "detail": text}
        try:
            with open(self.dir / "agent_runs.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")
        except OSError as exc:
            log.warning("could not queue the owner notice: %s", exc)

    def create_reset_link(self, by_email: str, email: str, ttl_h: int = RESET_TTL_H) -> str:
        """Owner only: a one-time token (24 h) that sets a new password and enrols a new authenticator. Never for an
        owner account: a stolen owner session could otherwise take the account over with no authenticator code."""
        self._require(by_email, "owner")
        email = email.strip().lower()
        token = secrets.token_urlsafe(24)
        with self.db.write() as conn:
            u = _USERS.get(conn, email)
            if u is None:
                raise KeyError(email)
            if u.role == "owner" or email == self.owner_email:
                raise PermissionError(OWNER_RESET_REFUSED)
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
        if u0.role == "owner" or u0.email == self.owner_email:      # a link stored before the refusal existed
            with self.db.write() as conn:
                conn.execute("DELETE FROM password_resets WHERE email = ?", (u0.email,))
                _audit(conn, "reset_link_refused", email=u0.email, reason="owner account")
            raise PermissionError(OWNER_RESET_REFUSED)
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
            conn.execute("DELETE FROM totp_steps WHERE email = ?", (u.email,))         # a new authenticator
            self._clear_failures(conn, u.email)
            _audit(conn, "password_reset", email=u.email, by=r.created_by)
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
        if u is None or not self.is_owner(u):
            raise PermissionError("owner role required")
        r = self._reserve(u.email, "recovery_codes_locked")       # counted under email: before the check
        if not verify_password(password, u.password_hash):
            self._fail(u.email, "recovery_codes_failed")
        codes = [_new_recovery_code() for _ in range(RECOVERY_CODES)]
        with self.db.write() as conn:
            ok = self._spend_totp(conn, u, code)
            if ok:
                self._release(conn, r)
                self._store_recovery_codes(conn, u.email, codes)
                _audit(conn, "recovery_codes", email=u.email)
        if not ok:
            self._fail(u.email, "recovery_codes_failed")
        return codes

    def recovery_login(self, email: str, password: str, recovery_code: str, ip: str | None = None) -> RecoveryLogin:
        """Owner only: password + a one-time recovery code in place of the authenticator. Spends the code, enrols a
        new authenticator, revokes every other session and returns a new one."""
        email = email.strip().lower()
        r = self._reserve(email, "recovery_login_locked", ip)      # counted before any check: a burst gets 5
        u = self.get_user(email)
        pw_ok = verify_password(password, u.password_hash if u is not None else _dummy_hash())   # same cost either way
        if u is None or not self.is_owner(u) or not pw_ok:
            self._fail(email, "recovery_login_failed", ip)
        secret = new_totp_secret()
        tok, left = "", 0
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
                conn.execute("DELETE FROM totp_steps WHERE email = ?", (email,))         # a new authenticator
                conn.execute("DELETE FROM email_links WHERE email = ?", (email,))
                conn.execute("DELETE FROM email_codes WHERE email = ?", (email,))
                tok = self._new_session(conn, email, SESSION_TTL_H, "recovery")
                self._release(conn, r)
                left = int(conn.execute("SELECT COUNT(*) FROM recovery_codes WHERE email = ? AND used_ns IS NULL",
                                        (email,)).fetchone()[0])
                _audit(conn, "recovery_login", email=email, codes_left=left)
        if spent != 1:
            self._fail(email, "recovery_login_failed", ip)
        return RecoveryLogin(token=tok, email=email, role=u.role, totp_uri=totp_uri(secret, email), recovery_codes_left=left)

    # ------------------------------------------------------------------ owner email sign-in link
    def request_email_link(self, email: str, ip: str | None = None, *, enabled: bool) -> str | None:
        """The token to mail, or None. Never raises and never says why: the caller answers every request the same.
        A link only when mail is configured (`enabled`), the address is `auth.owner_email` (trimmed, case-
        insensitive) and that account is the enabled owner (`is_owner`). Every request counts towards the hourly
        limit, per address and per client IP (EMAIL_LINK_PER_HOUR), before anything else is checked. Audited without
        the token: `email_link_sent`, `email_link_refused`, `email_link_rate_limited`, `email_link_unavailable`."""
        norm = _norm_email(email) or ""
        now = time.time_ns()
        keys = [f"email:{norm}"] + ([f"ip:{ip}"] if ip else [])
        with self.db.write() as conn:
            conn.execute("DELETE FROM email_link_requests WHERE ts_ns <= ?", (now - EMAIL_LINK_WINDOW_S * 10**9,))
            counts = [int(conn.execute("SELECT COUNT(*) FROM email_link_requests WHERE key = ?", (k,)).fetchone()[0])
                      for k in keys]
            where = {"email": norm, **({"ip": ip} if ip else {})}
            if max(counts) >= EMAIL_LINK_PER_HOUR:
                _audit(conn, "email_link_rate_limited", **where)
                return None
            conn.executemany("INSERT INTO email_link_requests (key, ts_ns) VALUES (?, ?)", [(k, now) for k in keys])
            if not enabled:
                _audit(conn, "email_link_unavailable", reason="mail not configured", **where)
                return None
            u = _USERS.get(conn, norm) if norm else None
            if self.owner_email is None or norm != self.owner_email or not self.is_owner(u):
                _audit(conn, "email_link_refused", **where)
                return None
            token = secrets.token_urlsafe(32)
            _EMAIL_LINKS.upsert(conn, EmailLink(token_hash=_token_hash(token), email=norm,
                                                expires=time.time() + EMAIL_LINK_TTL_S))
            _audit(conn, "email_link_sent", **where)
        return token

    def _count_ip_failure(self, ip: str | None, locked_event: str) -> None:
        """A failure that names no account (an unknown or expired sign-in link): counted against the client IP only,
        in one write transaction; PermissionError while that IP is locked."""
        if not ip:
            return
        now = time.time_ns()
        with self.db.write() as conn:
            conn.execute("DELETE FROM auth_failures WHERE ts_ns <= ?", (now - LOCKOUT_WINDOW_S * 10**9,))
            n = int(conn.execute("SELECT COUNT(*) FROM auth_failures WHERE key = ?", (f"ip:{ip}",)).fetchone()[0])
            if n >= IP_LOCKOUT_FAILURES:
                _audit(conn, locked_event, ip=ip)
            else:
                conn.execute("INSERT INTO auth_failures (key, ts_ns) VALUES (?, ?)", (f"ip:{ip}", now))
        if n >= IP_LOCKOUT_FAILURES:
            raise PermissionError(SIGN_IN_LOCKED)

    def redeem_email_link(self, token: str, code: str, ttl_h: int = SESSION_TTL_H, ip: str | None = None) -> str:
        """A session for the owner from a live link AND a current authenticator code (the link replaces the password,
        never the second factor). Any redeem of a live link deletes every outstanding link of the account, so the
        link is single-use and a wrong code burns it; that attempt counts under `email:<owner>` like a failed sign-in.
        An unknown or expired token counts against the IP only. One message for every cause (EMAIL_LINK_REFUSED)."""
        th = _token_hash(token)
        with self.db.connection() as conn:
            link0 = _EMAIL_LINKS.get(conn, th)
        if link0 is None or link0.expires < time.time() or link0.email != self.owner_email:
            self._count_ip_failure(ip, "email_link_locked")
            self.audit("email_link_redeem_failed", reason="unknown or expired link", **({"ip": ip} if ip else {}))
            raise PermissionError(EMAIL_LINK_REFUSED)
        email = link0.email
        r = self._reserve(email, "email_link_locked", ip)          # counted before the check: a burst gets 5
        tok: str | None = None
        with self.db.write() as conn:
            link = _EMAIL_LINKS.get(conn, th)
            if link is not None and link.expires >= time.time():
                conn.execute("DELETE FROM email_links WHERE email = ?", (email,))     # single use, and the others
                cur = _USERS.get(conn, email)
                if self.is_owner(cur) and cur is not None and self._spend_totp(conn, cur, code):
                    tok = self._new_session(conn, email, ttl_h, "email_link")
                    self._release(conn, r)
                    _audit(conn, "email_link_login", email=email, **({"ip": ip} if ip else {}))
        if tok is None:
            self.audit("email_link_redeem_failed", email=email, reason="wrong code, used link or not the owner",
                       **({"ip": ip} if ip else {}))
            raise PermissionError(EMAIL_LINK_REFUSED)
        # ADR 0013: with mail on, mailbox + authenticator is enough to sign in, so every such sign-in is announced
        self._notify_owner(f"Signed in to the dashboard as the owner with an emailed sign-in link and the "
                           f"authenticator code at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}, from "
                           f"{coarse_ip(ip)}. If this was not you, your mailbox and authenticator are both exposed: "
                           f"on the server run `python -m goldbot.ops.run revoke-sessions`, set auth.mail.enabled to "
                           f"false, then change your password and check the audit trail.")
        return tok

    # ------------------------------------------------------------------ owner sign-in with an emailed code (ADR 0028)
    def email_code_available(self) -> bool:
        """Email codes can be offered (with mail ready, the caller's half): the owner account exists and the HMAC key
        is readable from the keyring (created on first use)."""
        return (self.owner_email is not None and self.is_owner(self.get_user(self.owner_email))
                and email_code_key() is not None)

    @staticmethod
    def _send_wait(conn: sqlite3.Connection, keys: list[str], now: int, *, pace: bool) -> int:
        """Seconds before another mail may go to these keys: the shared hourly limit (EMAIL_LINK_PER_HOUR per address
        and per client IP, the same rows as the email link: ruling 5) and, with `pace`, RESEND_AFTER_S since the last
        request. 0 when a mail may go now. Prunes the hour's rows."""
        conn.execute("DELETE FROM email_link_requests WHERE ts_ns <= ?", (now - EMAIL_LINK_WINDOW_S * _NS,))
        wait = 0
        for k in keys:
            n, oldest, newest = conn.execute("SELECT COUNT(*), MIN(ts_ns), MAX(ts_ns) FROM email_link_requests "
                                             "WHERE key = ?", (k,)).fetchone()
            if n >= EMAIL_LINK_PER_HOUR:
                wait = max(wait, -(-(int(oldest) + EMAIL_LINK_WINDOW_S * _NS - now) // _NS))
            if pace and newest is not None:
                wait = max(wait, -(-(int(newest) + RESEND_AFTER_S * _NS - now) // _NS))
        return max(wait, 0)

    @staticmethod
    def _issue_code(conn: sqlite3.Connection, email: str, challenge: str, key: bytes, now: int) -> str:
        """Inside a write transaction: a new 6-digit code from the CSPRNG, zero-padded, stored only as its
        challenge-bound HMAC. Every older code of the account goes (only the newest works), as do stale rows."""
        conn.execute("DELETE FROM email_codes WHERE email = ? OR created_ns <= ?", (email, now - EMAIL_CODE_KEEP_S * _NS))
        code = f"{secrets.randbelow(10**6):06d}"
        conn.execute("INSERT INTO email_codes (challenge_sha256, email, code_hmac, expires_ns, tries, consumed, "
                     "created_ns) VALUES (?, ?, ?, ?, 0, 0, ?)",
                     (_token_hash(challenge), email, _code_mac(key, challenge, code), now + EMAIL_CODE_TTL_S * _NS, now))
        return code

    def request_email_code(self, email: str, password: str, ip: str | None = None, *, enabled: bool) -> EmailCodeIssue:
        """Email + password -> a challenge, the same shape for every input (condition 6); a code to mail only when mail
        is ready (`enabled`), the key is readable, the address is `auth.owner_email`, that account is the enabled owner,
        the password is right and email codes are not suspended. scrypt runs on every request (against a dummy hash
        for an unknown address) and a decoy challenge is as random as a real one, with no row. Raises PermissionError
        only while the sign-in lockout holds, as sign-in does. The attempt is counted under `email:<e>` before the
        check and released when the password is right (earlier failures stay). Every request counts towards the hourly
        mail limit shared with the email link. Audited without the code or challenge: `email_code_sent`,
        `email_code_refused`, `email_code_rate_limited`, `email_code_unavailable`, `email_code_suspended_refused`."""
        norm = _norm_email(email) or ""
        challenge = secrets.token_urlsafe(32)
        r = self._reserve(norm, "email_code_locked", ip)
        u = self.get_user(norm) if norm else None
        pw_ok = verify_password(password, u.password_hash if u is not None else _dummy_hash()) and u is not None
        if pw_ok:
            self._unreserve(r)
        else:
            self._suspend_if_locked(norm)
        key = email_code_key() if enabled else None
        now = time.time_ns()
        keys = [f"email:{norm}"] + ([f"ip:{ip}"] if ip else [])
        where = {"email": norm, **({"ip": ip} if ip else {})}
        code: str | None = None
        with self.db.write() as conn:
            wait = self._send_wait(conn, keys, now, pace=False)
            if wait:
                _audit(conn, "email_code_rate_limited", **where)
                return EmailCodeIssue(challenge=challenge, resend_after_s=wait)
            conn.executemany("INSERT INTO email_link_requests (key, ts_ns) VALUES (?, ?)", [(k, now) for k in keys])
            cur = _USERS.get(conn, norm) if norm else None
            if not enabled or key is None:
                _audit(conn, "email_code_unavailable", reason="mail not ready" if not enabled else "no key", **where)
            elif not pw_ok or self.owner_email is None or norm != self.owner_email or not self.is_owner(cur):
                _audit(conn, "email_code_refused", reason="wrong password" if not pw_ok else "not the owner", **where)
            elif self._suspended(conn, norm):
                _audit(conn, "email_code_suspended_refused", **where)
            else:
                code = self._issue_code(conn, norm, challenge, key, now)
                _audit(conn, "email_code_sent", **where)
        return EmailCodeIssue(challenge=challenge, code=code)

    def resend_email_code(self, challenge: str, ip: str | None = None, *, enabled: bool) -> EmailCodeIssue:
        """A new code for a challenge this browser holds (live, expired or burned within the hour): the old code stops
        working (only the newest works). The visible reply depends only on the client IP and the challenge (security
        review 1 of 68a8cfc): while this IP is paced (RESEND_AFTER_S) or over the shared hourly limit, the same
        challenge comes back with the wait. Otherwise a new challenge with the standard wait, whether a code was sent
        or not: a real challenge whose ADDRESS is paced or over its limit sends nothing, silently (audited), and
        answers exactly as an unknown challenge (a decoy) does, so a resend from another network cannot tell a right
        password from a wrong one without the mailbox."""
        ch = _token_hash(challenge)
        key = email_code_key() if enabled else None
        now = time.time_ns()
        code: str | None = None
        new = secrets.token_urlsafe(32)
        with self.db.write() as conn:
            row = conn.execute("SELECT email FROM email_codes WHERE challenge_sha256 = ? AND created_ns > ?",
                               (ch, now - EMAIL_CODE_KEEP_S * _NS)).fetchone()
            email: str | None = None if row is None else str(row[0])
            ip_keys = [f"ip:{ip}"] if ip else []
            where = {**({"email": email} if email else {}), **({"ip": ip} if ip else {})}
            wait = self._send_wait(conn, ip_keys, now, pace=True)
            if wait:
                _audit(conn, "email_code_rate_limited", resend=True, **where)
                return EmailCodeIssue(challenge=challenge, resend_after_s=wait)
            conn.executemany("INSERT INTO email_link_requests (key, ts_ns) VALUES (?, ?)", [(k, now) for k in ip_keys])
            if email is not None and self._send_wait(conn, [f"email:{email}"], now, pace=True):
                _audit(conn, "email_code_rate_limited", resend=True, silent=True, **where)
                return EmailCodeIssue(challenge=new)                  # answered as a decoy: nothing sent
            if email is not None:
                conn.execute("INSERT INTO email_link_requests (key, ts_ns) VALUES (?, ?)", (f"email:{email}", now))
            cur = _USERS.get(conn, email) if email else None
            if email is None:
                _audit(conn, "email_code_refused", reason="unknown challenge", resend=True, **where)
            elif not enabled or key is None:
                _audit(conn, "email_code_unavailable", resend=True, **where)
            elif email != self.owner_email or not self.is_owner(cur):
                _audit(conn, "email_code_refused", reason="not the owner", resend=True, **where)
            elif self._suspended(conn, email):
                _audit(conn, "email_code_suspended_refused", resend=True, **where)
            else:
                code = self._issue_code(conn, email, new, key, now)
                _audit(conn, "email_code_sent", resend=True, **where)
        return EmailCodeIssue(challenge=new, code=code)

    def verify_email_code(self, challenge: str, code: str, ttl_h: int = SESSION_TTL_H, ip: str | None = None) -> str:
        """A session for the owner (method `email_code`) from a live challenge and its code, compared as HMACs with
        `compare_digest`. The challenge is deleted on success (single use). A wrong code counts under `email:<owner>`
        like a failed sign-in; the EMAIL_CODE_TRIES-th wrong code burns the challenge, and EMAIL_CODE_BURNS burns in
        24 h suspend email codes. An unknown, expired, used, burned or decoy challenge counts against the client IP
        only. One message for every refusal (EMAIL_CODE_REFUSED), so a decoy and a wrong code look the same. Every
        success queues the owner a Telegram notice (condition 10: the attacker may hold the mailbox)."""
        ch = _token_hash(challenge)
        with self.db.connection() as conn:
            row0 = conn.execute("SELECT email FROM email_codes WHERE challenge_sha256 = ? AND consumed = 0 "
                                "AND expires_ns > ?", (ch, time.time_ns())).fetchone()
        if row0 is None or row0[0] != self.owner_email:
            try:
                self._count_ip_failure(ip, "email_code_locked")
            except PermissionError:
                raise PermissionError(EMAIL_CODE_REFUSED) from None
            self.audit("email_code_failed", reason="unknown, expired, used or burned challenge",
                       **({"ip": ip} if ip else {}))
            raise PermissionError(EMAIL_CODE_REFUSED)
        email = str(row0[0])
        try:
            r = self._reserve(email, "email_code_locked", ip)
        except PermissionError:             # locked: refused exactly as a decoy is (security review 4 of 68a8cfc)
            raise PermissionError(EMAIL_CODE_REFUSED) from None
        key = email_code_key()
        digits = "".join(code.split())
        tok: str | None = None
        burned_suspends = False
        tries_left = 0
        with self.db.write() as conn:
            row = conn.execute("SELECT code_hmac, tries FROM email_codes WHERE challenge_sha256 = ? AND consumed = 0 "
                               "AND expires_ns > ?", (ch, time.time_ns())).fetchone()
            if row is not None:
                cur = _USERS.get(conn, email)
                ok = key is not None and hmac.compare_digest(str(row[0]), _code_mac(key, challenge, digits))
                if ok and self.is_owner(cur):
                    conn.execute("DELETE FROM email_codes WHERE email = ?", (email,))
                    tok = self._new_session(conn, email, ttl_h, "email_code")
                    self._release(conn, r)
                    _audit(conn, "email_code_login", email=email, **({"ip": ip} if ip else {}))
                else:
                    tries = int(row[1]) + 1
                    tries_left = max(EMAIL_CODE_TRIES - tries, 0)
                    conn.execute("UPDATE email_codes SET tries = ?, consumed = ? WHERE challenge_sha256 = ?",
                                 (tries, int(tries_left == 0), ch))
                    if tries_left == 0:
                        _audit(conn, "email_code_burned", email=email, **({"ip": ip} if ip else {}))
                        burned_suspends = self._burn_suspends(conn, email)
        if tok is None:
            self.audit("email_code_failed", email=email, tries_left=tries_left, **({"ip": ip} if ip else {}))
            if burned_suspends:
                self._suspension_notice(f"{EMAIL_CODE_BURNS} codes were burned by wrong guesses within 24 hours")
            self._suspend_if_locked(email)
            raise PermissionError(EMAIL_CODE_REFUSED)
        self._notify_owner(f"Signed in to the dashboard as the owner with the password and an emailed code at "
                           f"{time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}, from {coarse_ip(ip)}. If this was "
                           f"not you, someone has your password and your mailbox: on the server run `python -m "
                           f"goldbot.ops.run revoke-sessions`, set auth.mail.enabled to false, then change your "
                           f"password and your email password and check the audit trail.")
        return tok

    def verify_stepup(self, email: str, code: str, ip: str | None = None) -> bool:
        """The step-up after an email-code sign-in (security ruling 2): a current authenticator code (spent once) or,
        failing that, one of the owner's recovery codes (spent), counted under `code:<email>` like re-arm and auto
        mode (`_reserve`, atomic): PermissionError while locked (`stepup_locked`), a wrong code stays counted
        (`stepup_failed`), a right one clears the code counter (`stepup`). An empty code is refused uncounted."""
        target = _norm_email(email)
        if target is None or not code.strip():
            return False
        r = self._reserve(target, "stepup_locked", ip, code=True)
        how: str | None = None
        with self.db.write() as conn:
            u = _USERS.get(conn, target)
            if self.is_owner(u) and u is not None:
                if self._spend_totp(conn, u, code):
                    how = "authenticator"
                elif conn.execute("UPDATE recovery_codes SET used_ns = ? WHERE code_sha256 = ? AND email = ? "
                                  "AND used_ns IS NULL",
                                  (time.time_ns(), _token_hash(_norm_code(code)), target)).rowcount == 1:
                    how = "recovery_code"
            if how is not None:
                self._release(conn, r)
                _audit(conn, "stepup", email=target, how=how, **({"ip": ip} if ip else {}))
        if how is None:
            self.audit("stepup_failed", email=target, **({"ip": ip} if ip else {}))
            if r.prev < LOCKOUT_FAILURES <= r.new:
                self._lock_notice("stepup")
            return False
        return True

    # ------------------------------------------------------------------ admin (owner only)
    def _require(self, email: str, role: Role) -> User:
        """The role by rank; "owner" also needs the account to be `auth.owner_email` (fails closed when unset)."""
        u = self.get_user(email)
        if u is None or not u.enabled or ROLE_RANK[u.role] < ROLE_RANK[role]:
            raise PermissionError(f"{role} role required")
        if role == "owner" and not self.is_owner(u):
            raise PermissionError("owner role required (this account is not auth.owner_email)")
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
            conn.execute("DELETE FROM password_resets WHERE email = ?", (target.email,))
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
        """Sign a user out everywhere; returns the number of sessions ended. Their email sign-in links go in the same
        transaction (a link is a session waiting to happen)."""
        self._require(by_email, "owner")
        with self.db.write() as conn:
            if _USERS.get(conn, email.lower()) is None:
                raise KeyError(email)
            n = conn.execute("DELETE FROM sessions WHERE email = ?", (email.lower(),)).rowcount
            conn.execute("DELETE FROM email_links WHERE email = ?", (email.lower(),))
            conn.execute("DELETE FROM email_codes WHERE email = ?", (email.lower(),))
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


def revoke_sessions_cli(argv: list[str]) -> int:
    """`python -m goldbot.ops.run revoke-sessions [--state DIR]`: run on the server, needs no dashboard sign-in."""
    import argparse
    ap = argparse.ArgumentParser(prog="python -m goldbot.ops.run revoke-sessions",
                                 description="Sign every dashboard account out (all sessions and email sign-in links).")
    ap.add_argument("--state", default="state", help="the state directory holding aux.db (default: state)")
    args = ap.parse_args(argv)
    path = db_path(args.state, "aux").resolve()
    if not path.exists():           # never create an empty aux.db and report a success that signed nobody out
        print(f"refused: no dashboard database at {path}; pass --state <the server's state directory>",
              file=sys.stderr)
        return 2
    try:
        db = Database(path, "aux", migrate=False)              # opens the existing file only, no migration
    except StateDbError as exc:
        print(f"refused: {path}: {exc}", file=sys.stderr)
        return 2
    with db.write() as conn:
        n = int(conn.execute("DELETE FROM sessions").rowcount)
        links = int(conn.execute("DELETE FROM email_links").rowcount)      # same transaction (security review L2)
        has_codes = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'email_codes'").fetchone()
        codes = int(conn.execute("DELETE FROM email_codes").rowcount) if has_codes else 0     # v7 file: none yet
        _audit(conn, "revoke_all_sessions", by="server:revoke-sessions", sessions=n, email_links=links,
               email_codes=codes)
    print(f"revoked {n} session{'s' if n != 1 else ''} and {links} email sign-in link{'s' if links != 1 else ''} "
          f"in {path}; every account must sign in again")
    return 0


__all__ = ["AuthStore", "User", "Invite", "PasswordReset", "EmailLink", "EmailCodeIssue", "EMAIL_CODE_KEY",
           "EMAIL_CODE_TTL_S", "EMAIL_CODE_TRIES", "EMAIL_CODE_REFUSED", "RESEND_AFTER_S", "email_code_key", "need_stepup",
           "EMAIL_LINK_PER_HOUR", "EMAIL_LINK_TTL_S",
           "EMAIL_LINK_REFUSED", "Enrolment", "RecoveryLogin", "import_users", "import_users_json",
           "import_audit_jsonl", "owner_conflict", "owner_health_note", "coarse_ip", "email_link_health_note", "check_password_policy", "has_role",
           "totp_code", "totp_step", "totp_verify", "new_totp_secret", "totp_uri", "ROLES", "GRANTABLE", "env_setup_code_hint", "os"]
