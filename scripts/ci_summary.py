#!/usr/bin/env python3
"""CI job summaries and the flaky-test quarantine report, from pytest's JUnit XML (`--junitxml`).

  ci_summary.py summary --title T JUNIT [--rerun RERUN_JUNIT] [--append LOG] [--step-summary FILE] [--top N]
      Markdown for the job summary: counts, wall time, the N slowest tests and, when the failed tests were re-run
      alone (`pytest --last-failed -n 0`), a verdict per failure: FLAKY (passed when re-run) or FAIL (failed again,
      or was never re-run). The verdicts never change the build result: the test step has already failed the job.
      --append adds the verdict lines to LOG (ci-out/<check>.txt, which report-failure posts in the CI issue).
      Always exits 0: a summary must never turn a red job green or a green one red.
  ci_summary.py weights --min-seconds S --out tests/shard_weights.json JUNIT...
      The known-heavy tests (slowest time seen >= S seconds) that tests/conftest.py spreads across shards first.

Standard library only: it runs on the CI runner before or without the project's dependencies.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FAILED = ("failed", "error")


class Case(NamedTuple):
    nodeid: str
    seconds: float
    outcome: str   # passed | failed | error | skipped


def nodeid_of(classname: str, name: str, root: Path = ROOT) -> str:
    """pytest's node id from a JUnit classname: the longest dotted prefix that is a file under root is the module,
    the rest are classes (`tests.test_x.TestA` -> `tests/test_x.py::TestA`)."""
    parts = classname.split(".") if classname else []
    for cut in range(len(parts), 0, -1):
        path = "/".join(parts[:cut]) + ".py"
        if (root / path).is_file():
            return "::".join([path, *parts[cut:], name])
    return "::".join([("/".join(parts) + ".py") if parts else "<collection>", name])


def parse_junit(path: Path, root: Path = ROOT) -> list[Case]:
    cases = []
    for tc in ET.parse(path).getroot().iter("testcase"):
        outcome = "passed"
        for tag in ("failure", "error", "skipped"):
            if tc.find(tag) is not None:
                outcome = {"failure": "failed"}.get(tag, tag)
                break
        cases.append(Case(nodeid_of(tc.get("classname", ""), tc.get("name", ""), root),
                          float(tc.get("time") or 0.0), outcome))
    return cases


def suite_seconds(path: Path) -> float:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    return max((float(s.get("time") or 0.0) for s in suites), default=0.0)


def classify(first: list[Case], rerun: list[Case] | None) -> dict[str, str]:
    """FLAKY when a failed test passed in the re-run; FAIL otherwise (failed again, or no re-run report)."""
    again = {c.nodeid: c.outcome for c in rerun or []}
    return {c.nodeid: "FLAKY" if again.get(c.nodeid) == "passed" else "FAIL" for c in first if c.outcome in FAILED}


def _load(path: Path | None, root: Path) -> list[Case] | None:
    if path is None or not path.is_file():
        return None
    try:
        return parse_junit(path, root)
    except ET.ParseError:
        return None


def render(title: str, junit: Path, rerun: Path | None, top: int = 10, root: Path = ROOT) -> str:
    cases = _load(junit, root)
    if cases is None:
        return f"### {title}\n\nno test report: the job stopped before pytest wrote {junit.name} (see the step log)\n"
    counts: dict[str, int] = {}
    for c in cases:
        counts[c.outcome] = counts.get(c.outcome, 0) + 1
    tally = ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: kv[0] != "passed"))
    lines = [f"### {title}", "", f"{tally or 'no tests'} in {suite_seconds(junit):.0f} s", ""]
    slow = sorted(cases, key=lambda c: -c.seconds)[:top]
    if slow:
        lines += [f"<details><summary>{len(slow)} slowest</summary>", "", "| s | test |", "|---:|---|"]
        lines += [f"| {c.seconds:.1f} | `{c.nodeid}` |" for c in slow]
        lines += ["", "</details>", ""]
    verdicts = classify(cases, _load(rerun, root) if rerun else None)
    if verdicts:
        lines += ["| verdict | test |", "|---|---|"] + [f"| **{v}** | `{n}` |" for n, v in sorted(verdicts.items())]
        lines += ["", "FLAKY = failed, then passed when re-run alone; FAIL = failed again. Either still fails the build "
                  "(no retry masking): fix or quarantine the test in its own change.", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("--title", required=True)
    s.add_argument("junit", type=Path)
    s.add_argument("--rerun", type=Path)
    s.add_argument("--append", type=Path)
    s.add_argument("--step-summary", type=Path)
    s.add_argument("--top", type=int, default=10)
    w = sub.add_parser("weights")
    w.add_argument("--min-seconds", type=float, default=5.0)
    w.add_argument("--out", type=Path, required=True)
    w.add_argument("junit", type=Path, nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "summary":
        md = render(a.title, a.junit, a.rerun, a.top)
        print(md)
        if a.step_summary:
            with a.step_summary.open("a") as f:
                f.write(md + "\n")
        first = _load(a.junit, ROOT)
        verdicts = classify(first, _load(a.rerun, ROOT) if a.rerun else None) if first else {}
        if a.append and verdicts:
            with a.append.open("a") as f:
                f.write("\n== re-run verdicts (the build still fails)\n")
                f.writelines(f"{v} {n}\n" for n, v in sorted(verdicts.items()))
        return 0

    seconds: dict[str, float] = {}
    for path in a.junit:
        for c in parse_junit(path):
            if c.outcome != "skipped" and c.seconds >= a.min_seconds:
                seconds[c.nodeid] = max(seconds.get(c.nodeid, 0.0), round(c.seconds, 1))
    data = {"comment": "known-heavy tests spread first across CI shards (tests/conftest.py); refresh with "
                       "scripts/ci_summary.py weights. Stale entries cost balance only, never a test.",
            "seconds": dict(sorted(seconds.items(), key=lambda kv: (-kv[1], kv[0])))}
    if a.out.exists():   # the unit section is refreshed from a unit run, not from this one
        data = {**json.loads(a.out.read_text()), **data}
    a.out.write_text(json.dumps(data, indent=1) + "\n")
    print(f"{len(seconds)} tests >= {a.min_seconds} s -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
