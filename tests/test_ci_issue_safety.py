"""scripts/ci_summary.py `report`: precise test-id parsing, ids and issue bodies that cannot break out of the key comment
or the code fence, and one failing GitHub call never skipping the others (review of the platform-friction PR)."""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_summary", ROOT / "scripts" / "ci_summary.py")
assert _spec is not None and _spec.loader is not None
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def test_ids_with_spaces_or_dashes_in_their_params_keep_the_whole_test_id():
    out = ("FAILED tests/test_a.py::test_p[a b] - boom\n"
           "FAILED tests/test_a.py::test_q[x - y] - boom\n"
           "FAILED tests/test_a.py::test_q[x - z] - boom\n")
    assert cs.failing_ids(out) == ["tests/test_a.py::test_p[a b]", "tests/test_a.py::test_q[x - y]",
                                   "tests/test_a.py::test_q[x - z]"]


def test_a_collection_error_is_keyed_on_the_path():
    out = "ERROR tests/test_broken.py - ImportError: no module named x\nERROR tests/test_b.py - SyntaxError\n"
    assert cs.failing_ids(out) == ["tests/test_b.py", "tests/test_broken.py"]
    assert cs.failing_ids("ERROR tests/test_broken.py - other message\n") == ["tests/test_broken.py"]


def test_ids_are_restricted_to_a_safe_charset_so_nothing_can_close_the_marker_comment():
    ids = cs.failing_ids("FAILED tests/test_a.py::test_x[--> <script>`id`] - boom\n")
    assert len(ids) == 1 and "-->" not in ids[0] and "`" not in ids[0] and "<" not in ids[0]
    assert ids[0].startswith("tests/test_a.py::test_x[")


def test_nothing_that_can_break_out_of_an_issue_body_is_posted(tmp_path):
    (tmp_path / "failed-x.list").write_text("x\n")
    (tmp_path / "x.txt").write_text("FAILED tests/test_a.py::test_n[-->] - boom\nlog --> and ``` fence\n")
    posted = []

    def api(method, path, payload):
        posted.append(payload)
        return [] if method == "GET" else {"number": 1}

    cs.run_report(tmp_path, ["x"], api, "o/r", "s", "br-->`x`", "u")
    body = posted[1]["body"]
    assert "-->" not in body.replace("<!-- ci-key: ", "").replace(" -->", "")
    assert "`x`" not in body and body.count("```") == 2   # only the log fence the reporter itself writes


def test_one_failing_api_call_does_not_skip_the_rest_and_is_reported(tmp_path):
    (tmp_path / "failed-x.list").write_text("x\n")
    (tmp_path / "x.txt").write_text("FAILED tests/test_a.py::t1 - x\nFAILED tests/test_b.py::t2 - y\n")
    calls = []

    def api(method, path, payload):
        calls.append((method, path))
        if method == "GET":
            return [{"number": 7, "body": cs.issue_key("tests/test_a.py::t1")}]
        if path.endswith("/comments"):
            raise OSError("503")
        return {"number": 9}

    done = cs.run_report(tmp_path, ["x"], api, "o/r", "s", "main", "u")
    assert [c[0] for c in calls] == ["GET", "POST", "POST"] and "opened #9" in done
    assert any(d.startswith("FAILED") and "#7" in d for d in done)
    assert cs.report_failed(done) is True and cs.report_failed(["opened #1"]) is False
