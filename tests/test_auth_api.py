"""The account-recovery and owner-admin endpoints under /api/auth and /api/users."""
import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import totp_code

pytestmark = pytest.mark.integration

PW = "a long password here"


def _secret(uri: str) -> str:
    return uri.split("secret=")[1].split("&")[0]


def _login(c: TestClient, email: str, pw: str, secret: str) -> dict[str, str]:
    r = c.post("/api/auth/login", json={"email": email, "password": pw, "totp": totp_code(secret)})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    st = app.state.st
    r = c.post("/api/auth/setup", json={"setup_code": st.auth.setup_code, "email": "o@x.io", "password": PW}).json()
    owner = _login(c, "o@x.io", PW, _secret(r["totp_uri"]))
    users = {}
    for email, role in (("a@x.io", "approver"), ("v@x.io", "viewer")):
        tok = c.post("/api/auth/invite", json={"email": email, "role": role}, headers=owner).json()["invite_token"]
        uri = c.post("/api/auth/accept", json={"token": tok, "password": f"{role} password 123"}).json()["totp_uri"]
        users[role] = (_login(c, email, f"{role} password 123", _secret(uri)), _secret(uri))
    return c, owner, users, r


def test_setup_returns_ten_recovery_codes_once(tmp_path):
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    code = app.state.st.auth.setup_code
    assert c.post("/api/auth/setup", json={"setup_code": code, "email": "x@x.io", "password": PW}).status_code == 403
    r = c.post("/api/auth/setup", json={"setup_code": code, "email": "o@x.io", "password": PW})
    assert r.status_code == 200 and len(r.json()["recovery_codes"]) == 10
    assert c.post("/api/auth/setup", json={"setup_code": code, "email": "o@x.io", "password": PW}).status_code == 403


def test_approver_and_viewer_get_403_on_every_admin_action(api):
    c, _, users, _ = api
    calls = [("/api/auth/invite", {"email": "n@x.io", "role": "viewer"}), ("/api/users/role", {"email": "v@x.io", "role": "approver"}),
             ("/api/users/disable", {"email": "v@x.io"}), ("/api/users/enable", {"email": "v@x.io"}),
             ("/api/auth/reset-link", {"email": "v@x.io"}), ("/api/users/revoke-sessions", {"email": "v@x.io"}),
             ("/api/auth/recovery/codes", {"password": "x", "totp": "000000"})]
    for role in ("approver", "viewer"):
        headers = users[role][0]
        for path, body in calls:
            assert c.post(path, json=body, headers=headers).status_code == 403, (role, path)
        assert c.get("/api/users", headers=headers).status_code == 403


def test_owner_role_is_refused_by_the_contract(api):
    c, owner, _, _ = api
    assert c.post("/api/auth/invite", json={"email": "n@x.io", "role": "owner"}, headers=owner).status_code == 422
    assert c.post("/api/users/role", json={"email": "a@x.io", "role": "owner"}, headers=owner).status_code == 422
    assert c.post("/api/users/role", json={"email": "o@x.io", "role": "viewer"}, headers=owner).status_code == 400
    assert c.post("/api/users/role", json={"email": "v@x.io", "role": "approver"}, headers=owner).json()["ok"]


def test_change_and_forgot_password_endpoints(api):
    c, _, users, _ = api
    headers, secret = users["viewer"]
    assert c.post("/api/auth/password/change", json={"current_password": "viewer password 123", "totp": totp_code(secret),
                                                       "new_password": "x"}).status_code == 401
    assert c.post("/api/auth/password/change", json={"current_password": "viewer password 123", "totp": "000000",
                                                       "new_password": "a new viewer password"}, headers=headers).status_code == 403
    assert c.post("/api/auth/password/change", json={"current_password": "viewer password 123", "totp": totp_code(secret),
                                                       "new_password": "viewer password 123"}, headers=headers).status_code == 400
    assert c.post("/api/auth/password/change", json={"current_password": "viewer password 123", "totp": totp_code(secret),
                                                       "new_password": "a new viewer password"}, headers=headers).json()["ok"]
    assert c.get("/api/me", headers=headers).status_code == 200                # the session that changed it stays
    bad = [c.post("/api/auth/password/forgot", json={"email": e, "totp": "000000", "new_password": "forgot password 123"})
           for e in ("v@x.io", "nobody@x.io")]
    assert [r.status_code for r in bad] == [403, 403] and bad[0].json() == bad[1].json()
    r = c.post("/api/auth/password/forgot", json={"email": "v@x.io", "totp": totp_code(secret), "new_password": "forgot password 123"})
    assert r.json()["ok"] and c.get("/api/me", headers=headers).status_code == 401   # every session revoked


def test_reset_link_endpoints(api):
    c, owner, users, _ = api
    headers, _ = users["approver"]
    assert c.post("/api/auth/reset-link", json={"email": "nobody@x.io"}, headers=owner).status_code == 400
    r = c.post("/api/auth/reset-link", json={"email": "a@x.io"}, headers=owner).json()
    assert r["expires_h"] == 24
    e = c.post("/api/auth/reset", json={"token": r["reset_token"], "new_password": "reset approver pw"})
    assert e.status_code == 200 and e.json()["email"] == "a@x.io"
    assert c.get("/api/me", headers=headers).status_code == 401
    assert c.post("/api/auth/reset", json={"token": r["reset_token"], "new_password": "reset approver pw2"}).status_code == 400
    _login(c, "a@x.io", "reset approver pw", _secret(e.json()["totp_uri"]))


def test_recovery_endpoints(api):
    c, owner, _, setup = api
    r = c.post("/api/auth/recovery/login", json={"email": "o@x.io", "password": PW, "recovery_code": setup["recovery_codes"][0]})
    assert r.status_code == 200 and r.json()["recovery_codes_left"] == 9 and r.json()["role"] == "owner"
    assert c.get("/api/me", headers=owner).status_code == 401                  # old sessions revoked
    assert c.post("/api/auth/recovery/login", json={"email": "o@x.io", "password": PW,
                                                     "recovery_code": setup["recovery_codes"][0]}).status_code == 403
    new_owner = {"Authorization": f"Bearer {r.json()['token']}"}
    codes = c.post("/api/auth/recovery/codes", json={"password": PW, "totp": totp_code(_secret(r.json()["totp_uri"]))},
                   headers=new_owner)
    assert codes.status_code == 200 and len(codes.json()["recovery_codes"]) == 10


def test_enable_and_revoke_endpoints(api):
    c, owner, users, _ = api
    headers, secret = users["approver"]
    assert c.post("/api/users/revoke-sessions", json={"email": "a@x.io"}, headers=owner).json()["sessions"] == 1
    assert c.get("/api/me", headers=headers).status_code == 401
    assert c.post("/api/users/disable", json={"email": "a@x.io"}, headers=owner).json()["ok"]
    assert c.post("/api/users/disable", json={"email": "o@x.io"}, headers=owner).status_code == 400
    assert c.post("/api/users/enable", json={"email": "a@x.io"}, headers=owner).json()["ok"]
    _login(c, "a@x.io", "approver password 123", secret)
