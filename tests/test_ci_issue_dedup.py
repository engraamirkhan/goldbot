"""FR-5/FR-8 (ROADMAP 5a): CI failure issues are keyed by the failing test, not by the run. An open issue for the same
test gets a comment; only a test with no open issue opens one. scripts/ci_summary.py `report` is the step
`report-failure` in ci.yml runs."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_summary", ROOT / "scripts" / "ci_summary.py")
assert _spec is not None and _spec.loader is not None
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

PYTEST_OUT = """\
=========================== short test summary info ============================
FAILED tests/test_email_code.py::test_email_code_decoy_timing - assert 0.31 < 0.3
FAILED tests/test_api.py::TestThing::test_b[x-1] - boom
ERROR tests/test_foo.py::test_setup
FLAKY tests/test_email_code.py::test_email_code_decoy_timing
2 failed, 1 error, 900 passed in 80.01s
"""
E2E_OUT = """\
  1) [chromium] › e2e/dashboard.spec.ts:50:5 › first run creates the owner ───────────────
     Error: expect(received).toBe(expected)
  2) [firefox] › e2e/dashboard.spec.ts:499:3 › layout does not shift ─────────────────────
"""


def test_failing_test_ids_are_read_from_pytest_and_playwright_output_once_each():
    assert cs.failing_ids(PYTEST_OUT + E2E_OUT) == [
        "e2e/dashboard.spec.ts › first run creates the owner",
        "e2e/dashboard.spec.ts › layout does not shift",
        "tests/test_api.py::TestThing::test_b[x-1]",
        "tests/test_email_code.py::test_email_code_decoy_timing",
        "tests/test_foo.py::test_setup",
    ]


def test_the_dedup_key_ignores_line_numbers_and_the_failure_message():
    a = cs.failing_ids("FAILED tests/test_x.py::test_a - assert 1 == 2\n")
    b = cs.failing_ids("FAILED tests/test_x.py::test_a - assert 5 == 6\n")
    assert a == b and cs.issue_key(a[0]) == "<!-- ci-key: tests/test_x.py::test_a -->"
    e1 = cs.failing_ids("  1) [chromium] › e2e/a.spec.ts:50:5 › the title ──\n")
    e2 = cs.failing_ids("  1) [firefox] › e2e/a.spec.ts:61:5 › the title ──\n")
    assert e1 == e2   # a moved line, or the other browser project, is the same test


def test_a_failure_with_no_test_id_is_keyed_by_its_job_name():
    assert cs.ids_for_run("(a job failed but wrote no test lines)", ["backend-typecheck"]) == ["job:backend-typecheck"]


def test_an_open_issue_for_the_same_test_gets_a_comment_and_no_new_issue_is_opened():
    open_issues = [{"number": 164, "body": f"Run: u\n{cs.issue_key('tests/test_a.py::test_t')}\n"},
                   {"number": 99, "body": "an old issue with no key"}]
    plan = cs.plan_report(open_issues, ["tests/test_a.py::test_t"])
    assert plan.comment_on == {164: ["tests/test_a.py::test_t"]} and plan.new_ids == []


def test_a_test_with_no_open_issue_is_opened_once_and_keyed_in_the_body():
    plan = cs.plan_report([], ["tests/test_a.py::test_t", "tests/test_b.py::test_u"])
    assert plan.comment_on == {} and plan.new_ids == ["tests/test_a.py::test_t", "tests/test_b.py::test_u"]
    title = cs.new_issue_title(plan.new_ids)
    assert "tests/test_a.py::test_t" in title and "+1 more" in title
    body = cs.keys_block(plan.new_ids)
    assert cs.issue_key("tests/test_a.py::test_t") in body and cs.issue_key("tests/test_b.py::test_u") in body


def test_two_failing_tests_in_one_open_issue_get_one_comment_and_a_new_test_still_opens_an_issue():
    both = cs.keys_block(["tests/test_a.py::t1", "tests/test_a.py::t2"])
    plan = cs.plan_report([{"number": 7, "body": both}],
                          ["tests/test_a.py::t1", "tests/test_a.py::t2", "tests/test_c.py::t3"])
    assert plan.comment_on == {7: ["tests/test_a.py::t1", "tests/test_a.py::t2"]}
    assert plan.new_ids == ["tests/test_c.py::t3"]


def test_a_run_with_more_failing_tests_than_the_cap_keeps_only_the_first_sorted_ones():
    ids = [f"tests/test_x.py::t{i:03d}" for i in range(60)]
    got = cs.ids_for_run("\n".join(f"FAILED {i}" for i in ids), [])
    assert got == ids[: cs.MAX_IDS] and len(got) == cs.MAX_IDS


def test_run_report_comments_on_the_open_issue_and_opens_one_issue_for_the_new_test(tmp_path):
    (tmp_path / "failed-backend-unit-1.list").write_text("backend-unit-1\n")
    (tmp_path / "backend-unit-1.txt").write_text(
        "FAILED tests/test_a.py::test_t - x\nFAILED tests/test_b.py::test_new - y\n")
    calls = []

    def api(method, path, payload):
        calls.append((method, path, payload))
        if method == "GET":
            return [{"number": 164, "body": cs.issue_key("tests/test_a.py::test_t")},
                    {"number": 5, "body": cs.issue_key("tests/test_b.py::test_new"), "pull_request": {}}]
        return {"number": 300}

    done = cs.run_report(tmp_path, ["backend-unit-1"], api, "o/r", "abcdef123", "main", "https://run")
    assert done == ["commented on #164", "opened #300"]
    posts = [c for c in calls if c[0] == "POST"]
    assert posts[0][1] == "/repos/o/r/issues/164/comments" and "abcdef123" in posts[0][2]["body"]
    assert posts[1][2]["title"] == "CI failure: tests/test_b.py::test_new"
    assert cs.issue_key("tests/test_b.py::test_new") in posts[1][2]["body"] and posts[1][2]["labels"] == ["ci"]


def test_run_report_opens_nothing_when_every_failing_test_already_has_an_open_issue(tmp_path):
    (tmp_path / "failed-x.list").write_text("x\n")
    (tmp_path / "x.txt").write_text("FAILED tests/test_a.py::test_t - boom\n")
    calls = []

    def api(method, path, payload):
        calls.append(method)
        return [{"number": 1, "body": cs.issue_key("tests/test_a.py::test_t")}] if method == "GET" else {}

    assert cs.run_report(tmp_path, ["x"], api, "o/r", "s", "main", "u") == ["commented on #1"]
    assert calls == ["GET", "POST"]
