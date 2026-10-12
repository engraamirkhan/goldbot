"""Write a quarter's pre-registered queued rows and contingent slots into the trial registry (BACKLOG 47).

  python scripts/preregister_quarter.py --quarter 2027Q1 --from docs/research/preregistration-2027Q1.yaml \
      --registry registry.jsonl --bars raw/ [--dry-run] [--allow-late REASON]

`--bars` is the folder of the data-v1 yearly 1m files: the sha256 digests of the research window's files (years up to
research.holdout_from's) are pinned per row, from the year of the row's bars_from (every file for a specialist row),
with the pinned years in `plan.data_years`, and verified by the runners before any look (BACKLOG 50); a write without
them is refused. `--from` is the reviewed YAML extract of the pre-registration (a `.md` path is read as
the `.yaml` beside it). Every row is checked against the pre-registration's text, the registry's rules, the quarter's
trial budget and the reservation in config/settings.yaml before anything is written
(goldbot/research/preregistration.py). `--dry-run` prints the rows and writes nothing. A write is idempotent, goes
through `TrialRegistry.preregister` / `reserve_slot` under the registry lock, and is refused on or after the
quarter's first day (00:00 UTC) unless `--allow-late` gives a reason, in which case rows are written as ad-hoc
pre-registrations naming their rule and no slot is written. A pre-registration is not a trial: nothing here spends the budget or writes an outcome. The shared copy on release research-v1 is merged
before and after by the `preregister-quarter` workflow (goldbot.research.registry_sync), as research.yml does.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goldbot.config import load_settings  # noqa: E402
from goldbot.data.release import bar_file_digests  # noqa: E402
from goldbot.research.director import reserved_trials  # noqa: E402
from goldbot.research.preregistration import (  # noqa: E402
    QuarterSpec,
    SpecError,
    plan_quarter,
    render_plan,
    write_quarter,
)
from goldbot.research.registry import TrialRegistry  # noqa: E402
from goldbot.research.registry_sync import read_rows  # noqa: E402


def doc_text(source: str, commit: str | None) -> str:
    """The pre-registration the spec's rules are checked against. With a freeze commit, its text AT that commit
    (`git show <commit>:<source>`), so the rules written are the frozen ones and a commit that does not exist, or does
    not hold the file, is refused; and the commit must be an ancestor of HEAD (`git merge-base --is-ancestor`), so a
    commit on an unmerged branch, whose rules were never reviewed into this history, is refused too (quant review,
    2026-10-11). Without one (dry runs before the freeze), the working tree's file."""
    p = Path(source)
    if commit is None:
        return (p if p.is_absolute() or p.exists() else ROOT / p).read_text()
    rel = p.resolve().relative_to(ROOT) if p.is_absolute() else p
    res = subprocess.run(["git", "-C", str(ROOT), "show", f"{commit}:{rel.as_posix()}"], capture_output=True,
                         text=True)
    if res.returncode != 0:
        raise SystemExit(f"freeze_commit {commit}: cannot read {rel} at that commit ({res.stderr.strip()}); the "
                         f"commit must exist in this checkout (fetch-depth 0) and hold the pre-registration")
    anc = subprocess.run(["git", "-C", str(ROOT), "merge-base", "--is-ancestor", commit, "HEAD"], capture_output=True,
                         text=True)
    if anc.returncode != 0:
        why = "not an ancestor of HEAD" if anc.returncode == 1 else f"ancestry not checked ({anc.stderr.strip()})"
        raise SystemExit(f"freeze_commit {commit}: {why}; the freeze commit must be in the history the spec is "
                         f"written from (merged into this branch), not on another branch")
    return res.stdout


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--quarter", required=True, help="the quarter the rows are queued for, e.g. 2027Q1")
    ap.add_argument("--from", dest="spec", required=True, type=Path, help="the reviewed spec (YAML)")
    ap.add_argument("--registry", default="registry.jsonl", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="print the rows and slots; write nothing")
    ap.add_argument("--allow-late", default=None, metavar="REASON",
                    help="write after the quarter started, as ad-hoc rows naming their rule (no slots); states why")
    ap.add_argument("--bars", default=None, type=Path,
                    help="folder of the data-v1 yearly 1m files: their research-window sha256 digests are pinned")
    args = ap.parse_args(argv)
    spec_path = args.spec.with_suffix(".yaml") if args.spec.suffix == ".md" else args.spec
    try:
        qs = QuarterSpec.model_validate(yaml.safe_load(spec_path.read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise SystemExit(f"{spec_path}: {exc}") from None
    research = load_settings().research
    digests = None
    if args.bars is not None:
        last = research.holdout_from.year if research.holdout_from is not None else None
        digests = bar_file_digests(args.bars, to_year=last)
        if not digests:
            raise SystemExit(f"--bars {args.bars}: no xauusd_1m_dukascopy_<year>.parquet files"
                             + (f" up to {last}" if last else ""))
    try:
        plan = plan_quarter(qs, research=research, doc_text=doc_text(qs.source, qs.freeze_commit),
                            quarter=args.quarter, data_digests=digests)
    except (OSError, SpecError) as exc:
        raise SystemExit(f"{spec_path}: {exc}") from None
    if args.dry_run:                          # reads only: a missing registry stays missing
        print(render_plan(plan, read_rows(args.registry)))
        return 0
    reg = TrialRegistry(args.registry)
    try:
        actions = write_quarter(reg, plan, allow_late=args.allow_late)
    except SpecError as exc:
        raise SystemExit(str(exc)) from None
    for a in actions:
        print(f"{a.id:10s} {a.action:8s} {a.detail}")
    res = reserved_trials(reg._rows(), plan.quarter, research.reserved_for(plan.quarter))
    print(f"{plan.quarter} reservation: setting {res.setting}, pending {res.pending}, run {res.run}, withdrawn "
          f"{res.withdrawn}, expired {res.expired} -> reserved {res.reserved}; trials in the registry: {reg.n_trials}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
