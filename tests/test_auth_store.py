"""Dashboard users, sessions and audit in aux.db (docs/proposals/2026-10-state-store.md, step 1)."""
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from goldbot.api.auth import AuthStore, Invite, User, hash_password, new_totp_secret, totp_code, totp_uri

PW = "a long password here"
OWNER = "o@x.io"


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


def _owner(tmp_path) -> tuple[AuthStore, str]:
    store = AuthStore(tmp_path, owner_email=OWNER)
    secret = _secret(store.bootstrap_owner(store.setup_code or "", "o@x.io", PW).totp_uri)
    return store, secret


def test_sessions_survive_a_new_auth_store_instance(tmp_path):
    store, secret = _owner(tmp_path)
    tok = store.login("o@x.io", PW, totp_code(secret))
    restarted = AuthStore(tmp_path, owner_email=OWNER)                                        # an API restart or deploy
    u = restarted.session_user(tok)
    assert u is not None and u.email == "o@x.io" and u.role == "owner"
    assert restarted.setup_code is None                                    # an owner exists: no new setup code
    assert (ru := restarted.get_user("o@x.io")) is not None and ru.last_login is not None


def test_logout_and_disable_revoke_sessions_in_every_instance(tmp_path):
    store, secret = _owner(tmp_path)
    inv = store.create_invite("o@x.io", "v@x.io", "viewer")
    _, uri = store.accept_invite(inv, "viewer password 123")
    other = AuthStore(tmp_path, owner_email=OWNER)                                            # e.g. a second API worker
    tok_o = store.login("o@x.io", PW, totp_code(secret))
    tok_v = store.login("v@x.io", "viewer password 123", totp_code(_secret(uri)))
    other.logout(tok_o)
    assert store.session_user(tok_o) is None
    store.disable("o@x.io", "v@x.io")
    assert other.session_user(tok_v) is None


def test_expired_sessions_are_refused_and_removed(tmp_path):
    store, secret = _owner(tmp_path)
    tok = store.login("o@x.io", PW, totp_code(secret), ttl_h=0)
    time.sleep(0.01)
    assert store.session_user(tok) is None
    with store.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_only_token_hashes_are_stored(tmp_path):
    store, secret = _owner(tmp_path)
    tok = store.login("o@x.io", PW, totp_code(secret))
    inv = store.create_invite("o@x.io", "v@x.io", "viewer")
    with store.db.connection() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dump = "\n".join(conn.iterdump())
    raw = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.name.startswith("aux.db"))
    for secret_token in (tok, inv):
        assert secret_token not in dump and secret_token.encode() not in raw


def test_imports_an_existing_users_json_once_and_leaves_it_untouched(tmp_path):
    secret = new_totp_secret()
    owner = User(email="o@x.io", role="owner", password_hash=hash_password(PW), totp_secret=secret, created=1.0)
    viewer = User(email="v@x.io", role="viewer", password_hash=hash_password("viewer password 123"),
                  totp_secret=new_totp_secret(), enabled=False, created=2.0)
    inv_tok = "legacy-invite-token"
    invite = Invite(token_hash=hashlib.sha256(inv_tok.encode()).hexdigest(), email="a@x.io", role="approver",
                    expires=time.time() + 3600, invited_by="o@x.io")
    users_json = json.dumps({"users": {u.email: u.model_dump() for u in (owner, viewer)},
                             "invites": {invite.token_hash: invite.model_dump()}}, indent=1)
    (tmp_path / "users.json").write_text(users_json)
    old_audit = [{"ts": 1.0, "event": "bootstrap_owner", "email": "o@x.io"}, {"ts": 2.0, "event": "login", "email": "o@x.io"}]
    (tmp_path / "audit.jsonl").write_text("\n".join(json.dumps(e) for e in old_audit) + "\nnot json\n")
    before = {p: p.read_bytes() for p in (tmp_path / "users.json", tmp_path / "audit.jsonl")}

    store = AuthStore(tmp_path, owner_email=OWNER)
    assert store.setup_code is None                                        # imported owner: no bootstrap
    assert {u["email"]: u["enabled"] for u in store.list_users()} == {"o@x.io": True, "v@x.io": False}
    tok = store.login("o@x.io", PW, totp_code(secret))                     # same password and authenticator
    assert (su := store.session_user(tok)) is not None and su.email == "o@x.io"
    assert store.accept_invite(inv_tok, "approver password 1")[0] == "a@x.io"   # open invites carried over
    assert [e["event"] for e in store.audit_events()][:2] == ["bootstrap_owner", "login"]

    store.set_role("o@x.io", "v@x.io", "approver")
    n_audit = len(store.audit_events())
    again = AuthStore(tmp_path, owner_email=OWNER)                                            # restart: no second import
    assert (av := again.get_user("v@x.io")) is not None and av.role == "approver"
    assert len(again.audit_events()) == n_audit
    assert {p: p.read_bytes() for p in before} == before                   # backup files never written


def test_bootstrap_race_creates_one_owner(tmp_path):
    a, b = AuthStore(tmp_path, owner_email=OWNER), AuthStore(tmp_path, owner_email=OWNER)                        # both started with no users
    a.bootstrap_owner(a.setup_code or "", "o@x.io", PW)
    with pytest.raises(PermissionError):
        b.bootstrap_owner(b.setup_code or "", "o@x.io", "another long password")
    assert [u["email"] for u in a.list_users()] == ["o@x.io"]


def test_concurrent_threads_do_not_corrupt_the_store(tmp_path):
    store, secret = _owner(tmp_path)
    errors: list[BaseException] = []

    def worker(i: int) -> None:
        try:
            s = AuthStore(tmp_path, owner_email=OWNER)
            for j in range(20):
                s.audit("probe", by=f"t{i}", n=j)
                s.create_invite("o@x.io", f"u{i}-{j}@x.io", "viewer")
        except BaseException as exc:      # pragma: no cover - reported below
            errors.append(exc)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    with store.db.connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 120
    assert sum(e["event"] == "probe" for e in store.audit_events()) == 120


_CHILD = textwrap.dedent("""
    import sys
    from goldbot.api.auth import AuthStore
    s = AuthStore(sys.argv[1], owner_email="o@x.io")
    for j in range(25):
        s.audit("probe", by=sys.argv[2], n=j)
        s.create_invite("o@x.io", f"{sys.argv[2]}-{j}@x.io", "viewer")
""")


def test_concurrent_processes_do_not_corrupt_the_store(tmp_path):
    store, _ = _owner(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    procs = [subprocess.Popen([sys.executable, "-c", _CHILD, str(tmp_path), f"p{i}"], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE) for i in range(4)]
    for p in procs:
        _, err = p.communicate(timeout=120)
        assert p.returncode == 0, err.decode()
    with store.db.connection() as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 100
    events = store.audit_events()
    assert sum(e["event"] == "probe" for e in events) == 100 and sum(e["event"] == "invite" for e in events) == 100


def test_existing_behaviour_lockout_roles_and_one_time_invites(tmp_path):
    store, secret = _owner(tmp_path)
    for _ in range(5):
        with pytest.raises(PermissionError):
            store.login("o@x.io", "wrong wrong wrong", "000000")
    with pytest.raises(PermissionError, match="too many"):
        store.login("o@x.io", PW, totp_code(secret))
    inv = store.create_invite("o@x.io", "v@x.io", "viewer")
    with pytest.raises(ValueError):
        store.accept_invite(inv, "short")
    store.accept_invite(inv, "viewer password 123")
    with pytest.raises(PermissionError):
        store.accept_invite(inv, "viewer password 123")
    with pytest.raises(PermissionError):
        store.create_invite("v@x.io", "x@x.io", "viewer")                  # only the owner invites
    with pytest.raises(ValueError, match="demote the owner"):
        store.set_role("o@x.io", "o@x.io", "viewer")
    with pytest.raises(KeyError):
        store.set_role("o@x.io", "nobody@x.io", "viewer")
    assert totp_uri(secret, "o@x.io").startswith("otpauth://")
    assert {"login_locked", "login_failed", "invite", "accept_invite"} <= {e["event"] for e in store.audit_events()}
