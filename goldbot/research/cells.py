"""Parallel research trials with one serialized registry write (.github/workflows/research.yml).

The shared trial registry (release research-v1) is the sole source of N for the deflated Sharpe, and it has one writer
at a time (`concurrency: research-registry`). Running each walk-forward inside that lock made trials run one by one,
up to two hours each. The workflow now splits the run in three:

  plan     `plan` turns the dispatch inputs (the single-trial ones, or a `batch`) into cells: one trial each.
  trials   one matrix cell per trial: fetches the bars and a READ-ONLY snapshot of the registry, runs
           scripts/research_pass.py on a copy of it, and hands back only the rows it added (`new_rows`), with its
           report and log, as an artifact. No write scope, no lock.
  record   under the lock, with the fresh registry: `record` merges every cell's rows (registry_sync's keyed union, so a
           row counts once however often the job is re-run), restates every deflated Sharpe on the FINAL trial count
           (`restate_dsr`) and writes the reports. A cell that crashed, timed out or left no artifact still records a row
           with status "failed": a look at the data is a trial, so nothing vanishes from N. A cell refused before any look
           (budget, queue, holdout rule; a SystemExit message with no traceback) records nothing and is reported.

Why a cell's own deflated Sharpe is provisional: it ran against the snapshot, so it counted the snapshot's trials plus
itself, not the cells running beside it. `restate_dsr` recomputes it from the stored inputs (`metrics.DsrInputs`,
`DaySummedDsr`) on `n_trials_effective` of the merged registry, which is at least as large as any count a cell saw,
and redoes the gate's DSR verdict (`gates.dsr_check`), so the stored `gates.passed` is never judged on a stale count.
The per-cell reports are quoted under a banner that says so.

Budget: each cell checks the quarter's budget against its snapshot, so cells running together can overshoot it by up to
the batch size; the report states the quarter's count after the record. Size a batch to the remaining budget.

  python -m goldbot.research.cells plan                         env: the dispatch inputs; prints the cells as JSON
  python -m goldbot.research.cells argv CELL_JSON               NUL-separated research_pass.py arguments of one cell
  python -m goldbot.research.cells delta SNAPSHOT WORKING OUT   the rows a cell added
  python -m goldbot.research.cells record PLAN_JSON ARTIFACTS REGISTRY REPORTS_JSON
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from goldbot.base import FrozenRecord, load_record
from goldbot.research.gates import dsr_check
from goldbot.research.metrics import DaySummedDsr, DsrInputs
from goldbot.research.registry import TrialRegistry, n_trials_effective, quarter_of, quarter_trials
from goldbot.research.registry_sync import _key, merge_rows, read_rows, write_rows

MAX_CELLS = 64                  # one dispatch; GitHub allows 256 matrix jobs, the quarter's trial budget far fewer
MAX_COMMENT = 60_000            # GitHub's issue comment limit is 65,536 characters
ERROR_TAIL = 4_000
FAILED = "failed"
ITEM_KEYS = frozenset({"specialist", "variants", "pooled", "timeframe", "from_year", "to_year", "rationale",
                       "score_holdout", "skip_screen", "macro", "discover", "families", "discover_config", "rule_ref",
                       "lot_fit"})
FAMILIES = ("", "column", "feature", "family", "cluster")
_NAME = re.compile(r"^[a-z0-9_]+$")
_TF = re.compile(r"^[0-9]+[mhd]$")
_FLAGS = ("score_holdout", "skip_screen", "macro", "discover", "lot_fit")


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if str(v).lower() in ("true", "false", ""):
        return str(v).lower() == "true"
    raise ValueError(f"expected true or false, got {v!r}")


def _variants(raw: Any) -> list[Any]:
    out = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(out, (dict, str)):
        out = [out]
    if not isinstance(out, list) or not out or not all(isinstance(v, (dict, str)) for v in out):
        raise ValueError('variants must be a JSON list of objects or preset names, e.g. [{}, {"band_z": 1.5}]')
    return out


def _json_object(raw: str, what: str) -> str:
    try:
        ok = isinstance(json.loads(raw), dict)
    except ValueError:
        ok = False
    if not ok:
        raise ValueError(f"{what} must be a JSON object")
    return raw


def plan(env: Mapping[str, str]) -> list[dict[str, Any]]:
    """The cells of one dispatch: the single-trial inputs (SPECIALIST, VARIANTS, POOLED, ... as research.yml's inputs), or
    each object of BATCH (a JSON list; a key it leaves out takes the dispatch input's value). One cell per variant; a
    pooled object is one cell (it ignores specialist and variants). Every value is validated here, before a runner
    starts: a name is [a-z0-9_]+, a timeframe is like 15m, flags are booleans, years are integers. ValueError otherwise."""
    defaults: dict[str, Any] = {
        "specialist": env.get("SPECIALIST", "session_open"), "variants": env.get("VARIANTS", "[{}]") or "[{}]",
        "pooled": env.get("POOLED", ""), "from_year": env.get("FROM_YEAR", "2010"), "to_year": env.get("TO_YEAR", "2026"),
        "rationale": env.get("RATIONALE", ""), "families": env.get("FAMILIES", ""),
        "discover_config": env.get("DISCOVER_CONFIG", "{}") or "{}", "rule_ref": env.get("RULE_REF", ""),
        "score_holdout": env.get("SCORE_HOLDOUT", "false"), "skip_screen": env.get("SKIP_SCREEN", "false"),
        "macro": env.get("MACRO", "false"), "discover": env.get("DISCOVER", "false"), "timeframe": "",
        "lot_fit": env.get("LOT_FIT", "false"),
    }
    raw_batch = (env.get("BATCH") or "").strip()
    if raw_batch:
        items = json.loads(raw_batch)
        if not isinstance(items, list) or not items or not all(isinstance(i, dict) for i in items):
            raise ValueError("batch must be a non-empty JSON list of objects")
    else:
        items = [{}]
    out: list[dict[str, Any]] = []
    for item in items:
        if (bad := sorted(set(item) - ITEM_KEYS)):
            raise ValueError(f"unknown batch keys {bad}; allowed: {sorted(ITEM_KEYS)}")
        spec = {**defaults, **item}
        spec["specialist"] = str(spec["specialist"])
        spec["pooled"] = str(spec["pooled"])
        spec["timeframe"] = str(spec["timeframe"])
        if not _NAME.match(spec["specialist"]):
            raise ValueError(f"bad specialist {spec['specialist']!r}")
        if spec["pooled"] and not _TF.match(spec["pooled"]):
            raise ValueError(f"bad pooled timeframe {spec['pooled']!r}")
        if spec["timeframe"] and not _TF.match(spec["timeframe"]):
            raise ValueError(f"bad timeframe {spec['timeframe']!r}")
        if spec["families"] not in FAMILIES:
            raise ValueError(f"bad families {spec['families']!r}")
        for k in _FLAGS:
            spec[k] = _truthy(spec[k])
        spec["from_year"], spec["to_year"] = int(spec["from_year"]), int(spec["to_year"])
        spec["discover_config"] = _json_object(str(spec["discover_config"]), "discover_config")
        spec["rationale"], spec["rule_ref"] = str(spec["rationale"]), str(spec["rule_ref"])
        variants = [{}] if spec["pooled"] else _variants(spec["variants"])
        for v in variants:
            if spec["timeframe"] and not spec["pooled"]:
                if not isinstance(v, dict):
                    raise ValueError("timeframe applies to object variants; put it in the preset or the object")
                v = {**v, "timeframe": spec["timeframe"]} if "timeframe" not in v else v
            out.append({**{k: spec[k] for k in ("specialist", "pooled", "timeframe", "from_year", "to_year", "rationale",
                                                "families", "discover_config", "rule_ref", *_FLAGS)},
                        "variant": v, "idx": len(out)})
    if len(out) > MAX_CELLS:
        raise ValueError(f"{len(out)} cells; a dispatch runs at most {MAX_CELLS} (the quarter's trial budget is the real cap)")
    return out


def argv(cell: Mapping[str, Any]) -> list[str]:
    """research_pass.py arguments of one cell. Every value goes in the equals form: as a separate argument a value could
    itself be an option (--families takes an optional value, so families=--registry=/tmp/x would move where the trial row
    is written), and the plan has already restricted the values that are not free text."""
    out = [f"--from-year={cell['from_year']}", f"--to-year={cell['to_year']}", f"--rationale={cell['rationale']}"]
    out += [f"--pooled={cell['pooled']}"] if cell["pooled"] else \
        [f"--specialist={cell['specialist']}", f"--variants={json.dumps([cell['variant']])}"]
    if cell["discover"]:
        out += ["--discover", f"--discover-config={cell['discover_config']}"]
        if cell["families"]:
            out.append(f"--families={cell['families']}")
    if cell["rule_ref"]:
        out.append(f"--rule-ref={cell['rule_ref']}")
    out += [f for f, on in (("--score-holdout", cell["score_holdout"]), ("--skip-screen", cell["skip_screen"])) if on]
    if cell["macro"]:
        out.append("--macro=macro")
    if cell["lot_fit"]:                    # H-27 overlay, reporting only; the account comes from settings
        out.append("--lot-fit")
    return out


def new_rows(snapshot: Path, working: Path) -> list[dict[str, Any]]:
    """The rows a cell added to its copy of the snapshot, in the order it wrote them (keyed as registry_sync keys rows)."""
    seen = {_key(r) for r in read_rows(snapshot)}
    return [r for r in read_rows(working) if _key(r) not in seen]


# ----------------------------------------------------------------------------------------------- DSR restatement
def _restate_summary(summary: dict[str, Any], k: int) -> dict[str, Any]:
    """A trade summary (`metrics.summarize`, plus its day-summed record) with its deflated Sharpe on `k` trials, from the
    stored inputs; a summary without inputs (below the trade floor, or written before they were stored) is unchanged."""
    out = copy.deepcopy(summary)
    raw = out.get("dsr_inputs")
    if raw is not None:
        try:
            out["dsr"] = load_record(DsrInputs, raw, source="trial_registry").dsr(k)
        except ValidationError:
            return summary
    day = out.get("day_summed")
    if isinstance(day, dict):
        try:
            out["day_summed"] = load_record(DaySummedDsr, day, source="trial_registry").at(k).model_dump()
        except ValidationError:
            pass
    return out


def _regate(gates: dict[str, Any], summary: dict[str, Any], subject: str) -> dict[str, Any]:
    checks = [dsr_check(summary, subject) if c.get("name") == "dsr" else c for c in gates.get("checks", [])]
    return {**gates, "checks": checks, "passed": all(c["passed"] for c in checks)}


def restate_dsr(row: dict[str, Any], k: int) -> dict[str, Any]:
    """The row with every deflated Sharpe recomputed on `k` trials (the registry's `n_trials_effective` once every cell is
    in) and the gate's DSR verdict redone on it. Only a walk-forward row ("evaluated" or "holdout") is restated; the run's
    own value is kept as `results.dsr_at_run` and the count used as `results.dsr_n_trials`. The input row is not mutated."""
    results = row.get("results")
    if row.get("status") not in ("evaluated", "holdout") or not isinstance(results, dict):
        return row
    mf = results.get("model_filtered")
    if not isinstance(mf, dict) or mf.get("dsr_inputs") is None:
        return row
    res = copy.deepcopy(results)
    res["dsr_at_run"] = mf.get("dsr")
    res["dsr_n_trials"] = int(k)
    res["model_filtered"] = _restate_summary(mf, k)
    if isinstance(res.get("all_candidates"), dict):
        res["all_candidates"] = _restate_summary(res["all_candidates"], k)
    if isinstance(res.get("gates"), dict):
        res["gates"] = _regate(res["gates"], res["model_filtered"], "model-filtered")
    ro = res.get("rule_only_gates")
    if isinstance(ro, dict) and isinstance(ro.get("summary"), dict):
        summary = _restate_summary(ro["summary"], k)
        res["rule_only_gates"] = {**_regate(ro, summary, "rule-only"), "summary": summary}
    return {**row, "results": res}


# ----------------------------------------------------------------------------------------------- the record step
class Recorded(FrozenRecord):
    """What `record` did, for the log and the report."""
    recorded: int                    # rows merged from cells that ran
    failed: int                      # failed rows written for cells that left nothing
    refused: int                     # cells refused before any look (no row)
    n_trials_effective: int          # the final count every DSR was restated on
    quarter_used: int                # trial rows stamped in the current quarter after the record
    reports: list[dict[str, str]]    # one {"title", "body"} per issue to comment on


def _title(cell: Mapping[str, Any]) -> str:
    return f"research: {'pooled_' + cell['pooled'] if cell['pooled'] else cell['specialist']}"


def _label(cell: Mapping[str, Any]) -> str:
    what = f"pooled {cell['pooled']}" if cell["pooled"] else f"{cell['specialist']} {json.dumps(cell['variant'])}"
    return f"cell {cell['idx']}: {what}"


def _failed_row(cell: Mapping[str, Any], error: str) -> dict[str, Any]:
    """A `failed` trial row for a cell that left no row: the family and config it would have run when they resolve (the
    queue and `passed_gates` match on them), else the cell's own description. Never passes a gate (status is not
    "evaluated") and carries no results to read, only the error."""
    from goldbot.research.pipeline import pooled_family, pooled_members
    from goldbot.research.preregistration import resolve_variant
    from goldbot.specialists import SPECIALISTS
    agent, family, config = f"cell-{cell['specialist']}", cell["specialist"], {"variant": cell["variant"]}
    try:
        if cell["pooled"]:
            specs = [SPECIALISTS[f]() for f in pooled_members(cell["pooled"])]
            family, config = pooled_family(cell["pooled"]), {"timeframe": cell["pooled"],
                                                            "families": {s.family: s.config for s in specs}}
            agent = f"pooled-{cell['pooled']}"
        else:
            spec = SPECIALISTS[cell["specialist"]](**resolve_variant(cell["specialist"], cell["variant"]))
            agent, config = spec.agent_id, spec.config
    except (ValueError, KeyError, TypeError):
        pass
    with tempfile.TemporaryDirectory() as tmp:
        reg = TrialRegistry(Path(tmp) / "registry.jsonl")
        return reg.record(agent_id=agent, family=family, config=config, feature_version="unknown",
                          rationale=f"{cell['rationale']} | cell failed before recording a result",
                          results={"error": error, "cell": {k: cell[k] for k in ("idx", "specialist", "pooled", "variant")}},
                          status=FAILED, budget_quarter=quarter_of())


def _read(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def _tail(text: str, n: int) -> str:
    return text[-n:] if len(text) > n else text


def record(cells_plan: list[dict[str, Any]], artifacts: Path, registry: Path) -> Recorded:
    """Merge every planned cell's rows into `registry` (the fresh copy, fetched under the lock) and restate their DSR
    on the final count. `artifacts/cell-<idx>/` holds `rows.jsonl`, `status.json` ({"exit": code}), `research.log` and
    `report.md` of a cell that ran; a missing directory is a cell that never reported."""
    published = read_rows(registry)
    added: list[dict[str, Any]] = []
    notes: dict[int, tuple[str, str]] = {}          # idx -> (outcome, text)
    n_failed = n_refused = 0
    for cell in cells_plan:
        d = artifacts / f"cell-{cell['idx']}"
        rows = read_rows(d / "rows.jsonl")
        log = _read(d / "research.log")
        try:
            code = int(json.loads(_read(d / "status.json") or "{}").get("exit", -1))
        except ValueError:
            code = -1
        if rows:
            added += rows
            notes[cell["idx"]] = ("ok" if code == 0 else "recorded, then failed", "")
        elif code > 0 and log and "Traceback" not in log:
            n_refused += 1                         # a SystemExit message: refused before any look at the data
            notes[cell["idx"]] = ("refused (no look, no trial)", _tail(log, ERROR_TAIL))
        else:
            n_failed += 1
            why = _tail(log, ERROR_TAIL) if log else ("no log and no rows: the cell timed out, was cancelled, lost its runner or failed before the "
                                     "walk-forward started")
            added.append(_failed_row(cell, why))
            notes[cell["idx"]] = ("FAILED (recorded as a trial)", why)
    keys = {_key(r) for r in added}
    merged = merge_rows(published, added)
    k = n_trials_effective(merged)
    final = [restate_dsr(r, k) if _key(r) in keys else r for r in merged]
    write_rows(registry, final)
    mine = {_key(r): r for r in final if _key(r) in keys}
    return Recorded(recorded=sum(1 for r in added if r.get("status") != FAILED), failed=n_failed, refused=n_refused,
                    n_trials_effective=k, quarter_used=quarter_trials(final, quarter_of()),
                    reports=_reports(cells_plan, artifacts, notes, mine, k, quarter_trials(final, quarter_of())))


def _fmt(v: Any) -> str:
    return "n/a" if v is None else f"{float(v):.3f}"


def _dsr(row: dict[str, Any]) -> str:
    res = row.get("results") or {}
    if "dsr_n_trials" not in res:
        return "n/a"
    gates = (res.get("gates") or {}).get("passed")
    return f"{_fmt(res.get('dsr_at_run'))} -> {_fmt((res.get('model_filtered') or {}).get('dsr'))}" + \
        ("" if gates is None else f", gates {'pass' if gates else 'fail'}")


def _reports(cells_plan: list[dict[str, Any]], artifacts: Path, notes: dict[int, tuple[str, str]],
             mine: dict[Any, dict[str, Any]], k: int, used: int) -> list[dict[str, str]]:
    by_cell: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells_plan:
        for r in read_rows(artifacts / f"cell-{cell['idx']}" / "rows.jsonl"):
            if _key(r) in mine:
                by_cell[cell["idx"]].append(mine[_key(r)])
    bodies: dict[str, list[str]] = defaultdict(list)
    for cell in cells_plan:
        outcome, text = notes[cell["idx"]]
        rows = by_cell.get(cell["idx"], [])
        trials = ", ".join(f"#{r['trial']} ({r['status']})" for r in rows) or "none"
        lines = [f"### {_label(cell)}", f"- outcome: {outcome}; registry trial: {trials}"]
        if rows:
            lines.append(f"- deflated Sharpe, at the run -> restated on K = {k}: " + "; ".join(_dsr(r) for r in rows))
        failed_row = next((r for r in mine.values() if (r.get("results") or {}).get("cell", {}).get("idx") == cell["idx"]
                           and r.get("status") == FAILED), None)
        if failed_row is not None:
            lines.append(f"- registry trial #{failed_row['trial']} (failed)")
        if text:
            lines += ["", "```", text, "```"]
        report = _read(artifacts / f"cell-{cell['idx']}" / "report.md")
        if report:
            lines += ["", "<details><summary>cell report (provisional figures)</summary>", "", report, "", "</details>"]
        bodies[_title(cell)].append("\n".join(lines))
    head = (f"Parallel run: {len(cells_plan)} cells. **Provisional:** the deflated Sharpe and the trial numbers inside each "
            f"cell report were computed on the registry snapshot the cell started from (its trials plus the cell itself), "
            f"not on the cells that ran beside it. The record job restated every DSR on the final count, K = {k} "
            f"(`n_trials_effective`), and redid the gate verdict; the restated values are the ones in the registry. "
            f"Trial rows stamped in {quarter_of()}: {used}.")
    return [{"title": t, "body": _tail("\n\n".join([head, *parts]), MAX_COMMENT)} for t, parts in bodies.items()]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    p = sub.add_parser("argv")
    p.add_argument("cell")
    p = sub.add_parser("delta")
    p.add_argument("snapshot", type=Path)
    p.add_argument("working", type=Path)
    p.add_argument("out", type=Path)
    p = sub.add_parser("record")
    p.add_argument("plan", type=Path)
    p.add_argument("artifacts", type=Path)
    p.add_argument("registry", type=Path)
    p.add_argument("reports", type=Path)
    a = ap.parse_args()
    if a.cmd == "plan":
        try:
            print(json.dumps(plan(os.environ), separators=(",", ":")))
        except ValueError as exc:
            sys.exit(f"::error::{exc}")
    elif a.cmd == "argv":
        sys.stdout.write("".join(x + "\0" for x in argv(json.loads(a.cell))))
    elif a.cmd == "delta":
        write_rows(a.out, new_rows(a.snapshot, a.working))
    else:
        out = record(json.loads(a.plan.read_text()), a.artifacts, a.registry)
        a.reports.write_text(json.dumps(out.reports))
        print(out.model_dump_json(exclude={"reports"}))


if __name__ == "__main__":
    main()
