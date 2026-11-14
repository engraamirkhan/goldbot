"""`scrub` (goldbot/research/daily_review.py): "a line carrying a login or a host address is dropped" before any text
is written or sent; the repo and its issues are public (backlog 139). One test per identifier shape the security
review of 2026-11-14 found leaking, and the ordinary text the review, alert reasons and issue bodies carry survives.
Every value here is fake."""
from __future__ import annotations

import pytest

from goldbot.research.daily_review import scrub


@pytest.mark.parametrize("line", [
    "logged in to 51234567, not icm",
    "login 51234567.",
    "login 51234567; retrying",
])
def test_a_login_followed_by_punctuation_is_dropped(line):
    assert scrub(line) == ""


@pytest.mark.parametrize("line", ["account 51,234,567 halted", "account 512,345 halted"])
def test_a_login_written_with_digit_grouping_is_dropped(line):
    assert scrub(line) == ""


@pytest.mark.parametrize("line", [
    "bridge brain-1.subnet1.vcn1.oraclevcn.com unreachable",
    "connect to mt5-host.example.net:443 refused",
    "resolved vps.example.org",
])
def test_a_fully_qualified_host_name_is_dropped(line):
    assert scrub(line) == ""


@pytest.mark.parametrize("line", [
    "bridge fd00:abcd::12 down",
    "peer [2001:db8:85a3::8a2e:370:7334]:443 reset",
    "link fe80::1%eth0 lost",
])
def test_an_ipv6_address_is_dropped(line):
    assert scrub(line) == ""


def test_an_email_address_is_dropped():
    assert scrub("sent to someone.fake+ops@example.com") == ""


@pytest.mark.parametrize("line", [
    "GET https://user:hunter2@example.invalid/x failed",
    "GET https://fake-token@example.invalid/x failed",
    "GET https://example.invalid/hook?token=abc123 failed",
    "GET /status?id=1&api_key=xyz failed",
    "GET /status?ACCESS_TOKEN=xyz failed",
])
def test_a_url_carrying_credentials_or_a_query_token_is_dropped(line):
    assert scrub(line) == ""


@pytest.mark.parametrize("line", [
    "close 2,650.25 (spread 0.18)",
    "balance £10,000.00 · equity $1,234,567.89",
    "volume 12,345 of 20,000 planned (61.7%)",
    "fill rate 97.5% · -0.3R · +12%",
    "day 2026-11-14, week 2026-W46, 14/11/2026",
    "at 13:45:00 UTC, 09:30 London, 2026-11-14T13:45:00Z",
    "price 2650.25 · ratio 0.1234567",
    "Detection: goldbot/ops/checks/loop.py; recovery steps: docs/RUNBOOK.md \"Feedback loop\".",
    "settings in config/settings.yaml, e.g. pd.Timestamp",
    "HTTP 502 from api.github.com and api.telegram.org",
    "late 30 of 1,440 minutes",
])
def test_ordinary_text_survives(line):
    assert scrub(line) == line


def test_only_the_offending_line_is_dropped_and_the_rest_is_kept_in_order():
    text = "costs ICM $0.37/oz\nlogin 51234567.\nhost a.b.oraclevcn.com\n+0.3R ok"
    assert scrub(text).splitlines() == ["costs ICM $0.37/oz", "+0.3R ok"]
