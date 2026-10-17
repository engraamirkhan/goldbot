"""Every API request body rejects unknown fields (422) instead of silently dropping them: a misspelt or
attacker-supplied field on a security-relevant request (sign-in, invite, reset, password change, recovery, TOTP,
mode / re-arm / halt, approvals) must never be ignored. Response models are unaffected."""
from typing import Any

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel

from goldbot.api.app import create_app
from tests.test_api_owner_controls import _owner_secret

# One body per request route that passes validation as it stands; the test adds one unknown field to it.
VALID_BODIES: dict[str, dict[str, Any]] = {
    "/api/auth/setup": {"setup_code": "x", "email": "o@x.io", "password": "owner password 123"},
    "/api/auth/login": {"email": "o@x.io", "password": "owner password 123", "totp": "000000"},
    "/api/auth/invite": {"email": "v@x.io", "role": "viewer"},
    "/api/auth/accept": {"token": "t", "password": "viewer password 123"},
    "/api/auth/password/change": {"current_password": "a", "totp": "000000", "new_password": "b"},
    "/api/auth/password/forgot": {"email": "o@x.io", "totp": "000000", "new_password": "b", "recovery_code": ""},
    "/api/auth/reset-link": {"email": "v@x.io"},
    "/api/auth/reset": {"token": "t", "new_password": "b"},
    "/api/auth/recovery/login": {"email": "o@x.io", "password": "a", "recovery_code": "c"},
    "/api/auth/recovery/codes": {"password": "a", "totp": "000000"},
    "/api/auth/email-link": {"email": "o@x.io"},
    "/api/auth/email-link/redeem": {"token": "t", "totp": "000000"},
    "/api/auth/email-code": {"email": "o@x.io", "password": "a"},
    "/api/auth/email-code/verify": {"challenge": "c", "code": "000000"},
    "/api/auth/email-code/resend": {"challenge": "c"},
    "/api/users/role": {"email": "v@x.io", "role": "approver"},
    "/api/users/disable": {"email": "v@x.io"},
    "/api/users/enable": {"email": "v@x.io"},
    "/api/users/revoke-sessions": {"email": "v@x.io"},
    "/api/decisions": {"proposal_id": "p1", "action": "reject", "reason_code": "other", "created": 0.0},
    "/api/halt": {"reason": "test"},
    "/api/rearm": {"totp": "000000"},
    "/api/automode": {"mode": "propose", "totp": ""},
}


@pytest.fixture
def app_client(tmp_path):
    state, docs = tmp_path / "state", tmp_path / "docs"
    state.mkdir()
    docs.mkdir()
    app = create_app(state, web_dist=tmp_path / "nodist", data_root=tmp_path / "data", docs_dir=docs, owner_email="o@x.io")
    return app, TestClient(app)


def _body_routes(app) -> dict[str, type[BaseModel]]:
    """Path -> request body model for every route that takes a JSON body."""
    out: dict[str, type[BaseModel]] = {}
    for r in app.routes:
        if isinstance(r, APIRoute) and r.dependant.body_params:
            (param,) = r.dependant.body_params
            model = param.field_info.annotation
            assert isinstance(model, type) and issubclass(model, BaseModel), (r.path, model)
            out[r.path] = model
    return out


def test_every_request_body_model_forbids_extra_fields(app_client):
    app, _ = app_client
    routes = _body_routes(app)
    assert routes, "no request body routes found"
    loose = {path: m.__name__ for path, m in routes.items() if m.model_config.get("extra") != "forbid"}
    assert not loose, f"request bodies that would silently drop unknown fields: {loose}"


def test_the_unknown_field_table_covers_every_request_route(app_client):
    app, _ = app_client
    assert set(_body_routes(app)) == set(VALID_BODIES)


@pytest.mark.parametrize("path", sorted(VALID_BODIES))
def test_each_request_endpoint_rejects_an_unknown_field_with_422(app_client, path):
    app, c = app_client
    owner, _ = _owner_secret(c, app)          # auth runs before body validation: send it as the owner
    r = c.post(path, json={**VALID_BODIES[path], "is_owner": True}, headers=owner)
    assert r.status_code == 422, (r.status_code, r.text)
    errors = r.json()["detail"]
    assert [e["type"] for e in errors] == ["extra_forbidden"]
    assert errors[0]["loc"] == ["body", "is_owner"]


# The 422 body names the field and the rule, never the value: a password sent under a misspelt key, or the whole body
# on a `missing` error, must not be echoed back (security review LOW on 1a14ed8).
def test_a_misspelt_password_key_is_rejected_without_echoing_the_password(app_client):
    _, c = app_client
    r = c.post("/api/auth/login", json={"email": "o@x.io", "pasword": "HUNTER2-SECRET", "totp": "000000"})
    assert r.status_code == 422
    assert "HUNTER2-SECRET" not in r.text
    assert {"type": "extra_forbidden", "loc": ["body", "pasword"]}.items() <= r.json()["detail"][0].items()


def test_a_missing_field_error_does_not_echo_the_request_body(app_client):
    _, c = app_client
    r = c.post("/api/auth/setup", json={"setup_code": "x", "password": "HUNTER2-SECRET"})
    assert r.status_code == 422
    assert "HUNTER2-SECRET" not in r.text
    errors = r.json()["detail"]
    assert [(e["type"], e["loc"]) for e in errors] == [("missing", ["body", "email"])]
    assert all(set(e) == {"type", "loc", "msg"} for e in errors)
