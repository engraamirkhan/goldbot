"""Write a quarter's pre-registered queued rows and contingent slots into the trial registry (BACKLOG 47).

  python scripts/preregister_quarter.py --quarter 2027Q1 --from docs/research/preregistration-2027Q1.yaml \
      --registry registry.jsonl [--dry-run] [--allow-late REASON]

`--from` is the reviewed YAML extract of the pre-registration (a `.md` path is read as the `.yaml` beside it). Every row
is checked against the pre-registration's text, the registry's rules, the quarter's trial budget and the reservation
in config/settings.yaml before anything is written (goldbot/research/preregistration.py). `--dry-run` prints the rows
and writes nothing. A write is idempotent, goes through `TrialRegistry.preregister` / `reserve_slot` under the
registry lock, and is refused on or after the quarter's first day (00:00 UTC) unless `--allow-late` gives a reason, in
which case rows are written as ad-hoc pre-registrations naming their rule and no slot is written. A pre-registration
is not a trial: nothing here spends the budget or writes an outcome. The shared copy on release research-v1 is merged
before and after by the `preregister-quarter` workflow (goldbot.research.registry_sync), as research.yml does.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goldbot.config import load_settings  # noqa: E402
from goldbot.research.director import reserved_trials  # noqa: E402
from goldbot.research.preregistration import (  # noqa: E402
    QuarterSpec,
    SpecError,
    plan_quarter,
    render_plan,
    write_quarter,
)
from goldbot.research.registry import TrialRegistry  # noqa: E402


def _source(path: str) -> Path:
    """The pre-registration a spec quotes: as given, else relative to the repo root."""
    p = Path(path)
    return p if p.is_absolute() or p.exists() else ROOT / p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--quarter", required=True, help="the quarter the rows are queued for, e.g. 2027Q1")
    ap.add_argument("--from", dest="spec", required=True, type=Path, help="the reviewed spec (YAML)")
    ap.add_argument("--registry", default="registry.jsonl", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="print the rows and slots; write nothing")
    ap.add_argument("--allow-late", default=None, metavar="REASON",
                    help="write after the quarter started, as ad-hoc rows naming their rule (no slots); states why")
    args = ap.parse_args(argv)
    spec_path = args.spec.with_suffix(".yaml") if args.spec.suffix == ".md" else args.spec
    try:
        qs = QuarterSpec.model_validate(yaml.safe_load(spec_path.read_text()))
    except (OSError, yaml.YAMLError, ValidationError) as exc:
        raise SystemExit(f"{spec_path}: {exc}") from None
    research = load_settings().research
    try:
        plan = plan_quarter(qs, research=research, doc_text=_source(qs.source).read_text(), quarter=args.quarter)
    except (OSError, SpecError) as exc:
        raise SystemExit(f"{spec_path}: {exc}") from None
    reg = TrialRegistry(args.registry)
    if args.dry_run:
        print(render_plan(plan, reg._rows()))
        return 0
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
