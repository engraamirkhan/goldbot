"""Entry status wording for the account type: self-clearing while the classifier lacks ticks, an info note while a
declared class has no measurement yet, "Needs you" for every other unknown reason."""
from __future__ import annotations

import json

from goldbot.api import entry_status as es

from .test_api_entry_status import WED, codes, engine, status

FEW = {"class": "unknown", "runs": [{"reason": "only 238 London/NY ticks (< 1000)", "n_ticks": 238}]}


def test_too_few_ticks_for_the_account_type_is_a_self_clearing_wait_with_the_count():
    e = engine(account_class="unknown", entry_blocks=["account_class_unknown"])
    s = status(e, class_ticks=238)
    (b,) = s.blocks
    assert b.kind == "wait" and s.state == "waiting"
    assert b.text == "Account type not identified yet: 238 of 1,000 market ticks recorded"
    assert "every night" in b.fix and "by itself" in b.fix


def test_an_unknown_account_type_for_any_other_reason_still_needs_the_owner():
    e = engine(account_class="unknown", entry_blocks=["account_class_unknown"])
    for s in (status(e), status(e, class_ticks=None)):
        (b,) = s.blocks
        assert b.kind == "act" and s.state == "blocked" and b.text == "Account type (raw or standard) not identified yet"


def test_class_ticks_are_read_only_from_the_classifiers_too_few_ticks_reason(tmp_path):
    f = tmp_path / "classifier_icm-demo.json"
    assert es.class_ticks_pending(tmp_path, "icm-demo") is None                      # no file
    f.write_text(json.dumps(FEW))
    assert es.class_ticks_pending(tmp_path, "icm-demo") == 238
    f.write_text(json.dumps({"class": "unknown", "runs": [{"reason": "spread in dead band", "n_ticks": 5000}]}))
    assert es.class_ticks_pending(tmp_path, "icm-demo") is None
    f.write_text(json.dumps({**FEW, "class": "raw"}))
    assert es.class_ticks_pending(tmp_path, "icm-demo") is None                      # a known class is not pending
    f.write_text("{torn")
    assert es.class_ticks_pending(tmp_path, "icm-demo") is None
    assert es.class_ticks_pending(tmp_path, "a/b") is None


def test_statuses_from_state_words_the_pending_ticks(tmp_path):
    (tmp_path / "engine_icm-demo.json").write_text(json.dumps(
        engine(account_class="unknown", entry_blocks=["account_class_unknown"])))
    (tmp_path / "classifier_icm-demo.json").write_text(json.dumps(FEW))
    ((_, _, s),) = es.statuses_from_state(tmp_path, WED, None)
    b = next(b for b in s.blocks if b.code == "account_class_unknown")
    assert b.kind == "wait" and "238 of 1,000" in b.text


def test_a_declared_class_with_no_measurement_shows_an_info_note_and_no_account_type_block():
    e = engine(account_class="raw", account_class_declared="raw", account_class_measured="unknown")
    s = status(e)
    assert s.state == "open" and codes(s) == []
    assert any("Account type: Raw (from the account settings" in n and "confirms" not in n for n in s.notes)


def test_a_measured_class_that_confirms_the_declared_one_shows_no_note():
    e = engine(account_class="raw", account_class_declared="raw", account_class_measured="raw")
    assert status(e).notes == []


def test_a_mismatch_or_unreadable_file_never_claims_the_declared_class_is_confirmed():
    for measured in ("standard", "unreadable"):
        e = engine(account_class="standard", account_class_declared="raw", account_class_measured=measured)
        assert status(e).notes == []
