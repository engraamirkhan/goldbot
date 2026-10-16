"""BACKLOG 52: approvals keep only the decision channel on disk and in the API. An archived proposal in
`approvals/done/` and the dashboard's decided cards carry `decided_channel` ("telegram" | "dashboard") and
`decided_ts`, never the owner's Telegram id or email; identity, when needed, lives in the auth audit log. Older done
files that still carry `decided_by` / `decided_via` load (ADR 0010) and are reported by channel only."""
import json

import pytest
from fastapi.testclient import TestClient

from goldbot.api.app import create_app
from goldbot.api.auth import totp_code
from goldbot.base import schema_skew
from goldbot.telegram.approvals import ApprovalCenter, Outcome, Proposal
from goldbot.telegram.bus import ApprovalBus
from tests.cards import submit_shown

TELEGRAM_ID = 987654321
EMAIL = "owner-secret@example.org"


def _prop(pid: str, **kw) -> Proposal:
    return Proposal(proposal_id=pid, account_id="icm-demo", agent_id="a", side=1, lots=0.1, entry=2000.0, stop=1998.0,
                    target=2004.0, p=0.6, ev_r=0.2, spread_points=20.0, top_features=[], **kw)


def _assert_no_identity(text: str) -> None:
    assert str(TELEGRAM_ID) not in text and EMAIL not in text
    assert "decided_by" not in text and "decided_via" not in text


def _old_done_file(bus: ApprovalBus, pid: str, **legacy) -> None:
    """A done file written by a release before BACKLOG 52 (and before decided_channel existed)."""
    row = json.loads(_prop(pid, outcome=Outcome.APPROVED).model_dump_json())
    row.pop("decided_channel")
    row.pop("decided_ts")
    (bus.done_dir / f"{pid}.json").write_text(json.dumps({**row, **legacy}))


def test_a_dashboard_decision_archived_through_the_bus_has_no_email_in_its_done_file(tmp_path):
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({TELEGRAM_ID}, bus=bus)
    center.propose(_prop("p1"))
    submit_shown(bus, "p1", approve=True, reason_code=None, by=f"dashboard:{EMAIL}")
    (p,) = center.poll_bus()
    text = (bus.done_dir / "p1.json").read_text()
    _assert_no_identity(text)
    assert json.loads(text)["decided_channel"] == "dashboard" and p.decided_channel == "dashboard"


def test_a_telegram_decision_made_in_process_has_no_telegram_id_in_its_done_file(tmp_path):
    bus = ApprovalBus(tmp_path)
    center = ApprovalCenter({TELEGRAM_ID}, bus=bus)
    center.propose(_prop("p1"))
    center.decide("p1", TELEGRAM_ID, approve=False, reason_code="news")
    text = (bus.done_dir / "p1.json").read_text()
    _assert_no_identity(text)
    assert json.loads(text)["decided_channel"] == "telegram"


def test_a_proposal_archived_by_the_engine_from_a_telegram_click_has_no_telegram_id_in_its_done_file(tmp_path):
    bus = ApprovalBus(tmp_path)
    p = _prop("p1")
    bus.publish(p)
    submit_shown(bus, "p1", approve=True, reason_code=None, by=f"telegram:{TELEGRAM_ID}")
    bus.archive(p.model_copy(update={"outcome": Outcome.APPROVED, "gate_refusal": ["owner_halt"]}))
    text = (bus.done_dir / "p1.json").read_text()
    _assert_no_identity(text)
    assert json.loads(text)["decided_channel"] == "telegram"


@pytest.mark.parametrize(("legacy", "channel"), [
    ({"decided_via": f"dashboard:{EMAIL}"}, "dashboard"),
    ({"decided_via": f"telegram:{TELEGRAM_ID}"}, "telegram"),
    ({"decided_by": TELEGRAM_ID}, "telegram"),                 # the in-process Telegram path before decided_via
    ({"decided_by": None, "decided_via": None}, None),         # expired: nobody decided
    ({"decided_via": EMAIL}, None),                            # malformed, no channel prefix: never kept as one
])
def test_an_older_done_file_with_the_identity_fields_loads_as_its_channel_without_schema_skew(tmp_path, legacy,
                                                                                               channel):
    bus = ApprovalBus(tmp_path)
    _old_done_file(bus, "old", **legacy)
    before = {k: v for k, v in schema_skew().items() if k[1] == "Proposal"}
    p = bus.get("old")
    assert p is not None and p.outcome == Outcome.APPROVED and p.decided_channel == channel
    assert {k: v for k, v in schema_skew().items() if k[1] == "Proposal"} == before   # known history, not a newer file
    _assert_no_identity(p.model_dump_json())                     # and it never travels further


def test_an_older_done_file_keeps_its_recorded_channel_over_the_legacy_identity(tmp_path):
    bus = ApprovalBus(tmp_path)
    _old_done_file(bus, "old", decided_via=f"telegram:{TELEGRAM_ID}", decided_channel="telegram", decided_ts=5.0)
    p = bus.get("old")
    assert p is not None and (p.decided_channel, p.decided_ts) == ("telegram", 5.0)


def test_a_proposal_given_the_legacy_identity_fields_in_code_does_not_keep_them():
    p = _prop("p1", decided_via=f"dashboard:{EMAIL}")
    _assert_no_identity(p.model_dump_json())
    assert p.decided_channel == "dashboard"


@pytest.mark.integration
def test_the_decided_cards_show_the_channel_and_never_an_id_or_email(tmp_path):
    bus = ApprovalBus(tmp_path)
    bus.publish(_prop("sub"))
    submit_shown(bus, "sub", True, None, by=f"dashboard:{EMAIL}")                 # decided, not yet applied by the engine
    bus.archive(_prop("new", outcome=Outcome.REJECTED, reason_code="cost", decided_channel="telegram", decided_ts=7.0))
    _old_done_file(bus, "old-via", decided_via=f"dashboard:{EMAIL}")
    _old_done_file(bus, "old-by", decided_by=TELEGRAM_ID)
    app = create_app(tmp_path, web_dist=tmp_path / "nodist", owner_email="o@x.io")
    c = TestClient(app)
    uri = c.post("/api/auth/setup", json={"setup_code": app.state.st.auth.setup_code, "email": "o@x.io",
                                          "password": "a long password here"}).json()["totp_uri"]
    tok = c.post("/api/auth/login", json={"email": "o@x.io", "password": "a long password here",
                                          "totp": totp_code(uri.split("secret=")[1].split("&")[0])}).json()["token"]
    r = c.get("/api/proposals/recent", headers={"Authorization": f"Bearer {tok}"})
    _assert_no_identity(r.text)
    rows = {row["proposal_id"]: row for row in r.json()}
    assert {k: v["decided_channel"] for k, v in rows.items()} == {"sub": "dashboard", "new": "telegram",
                                                                   "old-via": "dashboard", "old-by": "telegram"}
    assert rows["new"]["decided_ts"] == 7.0 and rows["sub"]["decided_ts"] is not None
    assert rows["old-via"]["decided_ts"] is None
