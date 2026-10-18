"""scripts/ci_summary.py: the CI job summary (timings, slowest tests) and the flaky-test quarantine report. A test that
failed and then passed when re-run alone is FLAKY, one that failed again is FAIL; both still fail the build."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ci_summary", ROOT / "scripts" / "ci_summary.py")
assert _spec is not None and _spec.loader is not None
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)


def _junit(path: Path, cases: list[tuple[str, str, float, str]], suite_time: float = 12.5) -> Path:
    body = []
    for classname, name, t, outcome in cases:
        inner = {"passed": "", "failed": '<failure message="boom">trace</failure>',
                 "error": '<error message="setup">trace</error>', "skipped": '<skipped message="no"/>'}[outcome]
        body.append(f'<testcase classname="{classname}" name="{name}" time="{t}">{inner}</testcase>')
    path.write_text('<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest" errors="0" '
                    f'failures="0" skipped="0" tests="{len(cases)}" time="{suite_time}">{"".join(body)}'
                    '</testsuite></testsuites>')
    return path


def test_parse_maps_classnames_to_node_ids(tmp_path):
    p = _junit(tmp_path / "j.xml", [("tests.test_ci_summary", "test_a[x-1]", 1.5, "passed"),
                                    ("tests.test_ci_summary.TestThing", "test_b", 0.2, "failed"),
                                    ("tests.test_ci_summary", "test_c", 0.0, "skipped")])
    cases = cs.parse_junit(p, root=ROOT)
    assert [(c.nodeid, c.outcome) for c in cases] == [
        ("tests/test_ci_summary.py::test_a[x-1]", "passed"),
        ("tests/test_ci_summary.py::TestThing::test_b", "failed"),
        ("tests/test_ci_summary.py::test_c", "skipped")]
    assert cases[0].seconds == 1.5


def test_a_failure_that_passes_alone_is_flaky_and_one_that_fails_again_is_fail(tmp_path):
    first = cs.parse_junit(_junit(tmp_path / "a.xml", [("tests.test_m", "test_flaky", 1, "failed"),
                                                      ("tests.test_m", "test_broken", 1, "error"),
                                                      ("tests.test_m", "test_ok", 1, "passed")]), root=ROOT)
    rerun = cs.parse_junit(_junit(tmp_path / "b.xml", [("tests.test_m", "test_flaky", 1, "passed"),
                                                      ("tests.test_m", "test_broken", 1, "failed")]), root=ROOT)
    assert cs.classify(first, rerun) == {"tests/test_m.py::test_flaky": "FLAKY", "tests/test_m.py::test_broken": "FAIL"}
    # no re-run report at all (the re-run itself crashed): every failure stays FAIL, never silently FLAKY
    assert set(cs.classify(first, None).values()) == {"FAIL"}


def test_summary_shows_counts_wall_time_slowest_tests_and_verdicts(tmp_path):
    a = _junit(tmp_path / "a.xml", [("tests.test_m", f"test_{i}", float(i), "passed") for i in range(15)]
               + [("tests.test_m", "test_flaky", 0.5, "failed")], suite_time=61.0)
    b = _junit(tmp_path / "b.xml", [("tests.test_m", "test_flaky", 0.5, "passed")])
    md = cs.render("backend-unit 1/4", a, b, top=3, root=ROOT)
    assert "backend-unit 1/4" in md and "15 passed" in md and "1 failed" in md and "61 s" in md
    assert "tests/test_m.py::test_14" in md and "tests/test_m.py::test_11" not in md      # top 3 slowest only
    assert "FLAKY" in md and "tests/test_m.py::test_flaky" in md and "still fails the build" in md


def test_summary_without_a_report_says_so(tmp_path):
    md = cs.render("backend-integration 2/4", tmp_path / "missing.xml", None, root=ROOT)
    assert "no test report" in md


def test_main_appends_verdicts_to_the_failure_log_for_the_ci_issue(tmp_path):
    a = _junit(tmp_path / "a.xml", [("tests.test_m", "test_flaky", 1, "failed")])
    b = _junit(tmp_path / "b.xml", [("tests.test_m", "test_flaky", 1, "passed")])
    log = tmp_path / "backend-unit-1.txt"
    log.write_text("pytest output\n")
    summary = tmp_path / "summary.md"
    assert cs.main(["summary", "--title", "t", str(a), "--rerun", str(b), "--append", str(log),
                    "--step-summary", str(summary)]) == 0
    assert "FLAKY tests/test_m.py::test_flaky" in log.read_text() and "FLAKY" in summary.read_text()


def test_weights_keep_slow_tests_only_and_the_slowest_time_seen(tmp_path):
    a = _junit(tmp_path / "a.xml", [("tests.test_m", "test_slow", 30.0, "passed"), ("tests.test_m", "test_fast", 0.1, "passed")])
    b = _junit(tmp_path / "b.xml", [("tests.test_m", "test_slow", 42.0, "passed")])
    out = tmp_path / "w.json"
    assert cs.main(["weights", "--min-seconds", "5", "--out", str(out), str(a), str(b)]) == 0
    assert json.loads(out.read_text())["seconds"] == {"tests/test_m.py::test_slow": 42.0}
