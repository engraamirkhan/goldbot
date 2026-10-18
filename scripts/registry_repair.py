"""Put a cell's real row in place of the `failed` row the record step wrote for it (research readout 2026-10-18, D-1).

The record step (goldbot.research.cells.record) refused the rows of cells whose plan said 3.0 where the matrix handed
the cell 3: the agent ids differed, so it wrote a `failed` trial row instead. The cells did run, and their artifacts
hold the rows. For each failed row whose cell artifact holds valid rows (the record step's own checks, now matching
canonical variants), this writes the cell's row in its place:

  - the same trial number and the same K: one failed row is replaced by exactly one cell row, under the failed row's
    timestamp (the slot it fills in the time-ordered numbering); the cell's own timestamp is kept as `repair.cell_ts`;
  - `repair.replaces` names the failed row, so every later union (registry_sync.merge_rows) drops it from any copy,
    including a stale one such as the VPS's; only a failed row can be replaced;
  - the row comes from a run that finished before the failed row was written, and from exactly one run.

A failed row with no artifact, an ambiguous one, or more than one row stays as it is (a look at the data is a trial).
The deflated Sharpe of a replaced row is restated on the registry's K, as the record step does.

  python scripts/registry_repair.py REGISTRY RUN_DIR [RUN_DIR ...]            dry run: prints the diff, writes nothing
  python scripts/registry_repair.py REGISTRY RUN_DIR [RUN_DIR ...] --apply    writes the repaired rows to REGISTRY

RUN_DIR is `gh run download <run id> -D <dir>/<run id>` of a research.yml run (cell-<idx>/rows.jsonl), named by its run
id. Each run is checked with `gh run view` (a successful `research` run) and every repair row records `run_id`, the
run's `head_sha` and the `rows_sha256` of the rows file it came from; a run that fails the check refuses the repair. Publishing is a separate, reviewed step: `scripts/registry_release.sh save REGISTRY`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from goldbot.base import FrozenRecord  # noqa: E402
from goldbot.research.cells import FAILED, _read_cell_rows, restate_dsr  # noqa: E402
from goldbot.research.registry import is_trial, n_trials_effective  # noqa: E402
from goldbot.research.registry_sync import KEY_FIELDS, _key, merge_rows, read_rows, write_rows  # noqa: E402

REASON = ("the record step refused this cell's rows: its plan said a whole float (e.g. 3.0) that the matrix handed the "
          "cell as an int, so the planned agent id differed (readout 2026-10-18, D-1)")


WORKFLOW = "research"           # .github/workflows/research.yml `name:`
_RUN_ID = re.compile(r"^[0-9]+$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
RunInfo = Callable[[str], dict[str, Any]]


def run_info(run_id: str) -> dict[str, Any]:
    """`gh run view <id> --json headSha,conclusion,workflowName` of the run an artifact came from."""
    out = subprocess.run(["gh", "run", "view", run_id, "--json", "headSha,conclusion,workflowName"],
                         capture_output=True, text=True, check=False, timeout=60)
    if out.returncode != 0:
        raise ValueError(f"run {run_id}: gh run view failed: {out.stderr.strip()}")
    info: dict[str, Any] = json.loads(out.stdout)
    return info


def _verified(runs: list[Path], verify: RunInfo) -> dict[Path, str]:
    """Each run folder's head commit, once its name is the run id of a successful research.yml run (quant review of
    ADR 0033: a repair row names the run, its commit and the digest of the rows file it came from). ValueError
    otherwise: nothing is repaired from an artifact of unknown origin."""
    out: dict[Path, str] = {}
    for run in runs:
        if not _RUN_ID.match(run.name):
            raise ValueError(f"{run}: the folder must be named by its run id (gh run download <run id> -D <run id>)")
        info = verify(run.name)
        sha = str(info.get("headSha") or "")
        if info.get("workflowName") != WORKFLOW or info.get("conclusion") != "success" or not _SHA.match(sha):
            raise ValueError(f"run {run.name} is not a successful {WORKFLOW} run with a commit: {info}")
        out[run] = sha
    return out


class Outcome(FrozenRecord):
    """One failed row: replaced by the cell's row (`agent_id` set), or left as it is with the reason."""
    trial: int
    failed_agent_id: str
    agent_id: str = ""
    source: str = ""
    status: str = ""
    why: str = ""


class Repair(FrozenRecord):
    rows: list[dict[str, Any]]       # the registry after the repair (unchanged when nothing is replaced)
    replaced: list[Outcome]
    left: list[Outcome]
    k_before: int
    k_after: int


def _when(ts: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(ts))
    except ValueError:
        return None


def _candidates(failed: dict[str, Any], runs: list[Path], known: set[tuple[str, str, str, str]]
                ) -> tuple[dict[str, tuple[Path, Path, list[dict[str, Any]]]], list[str]]:
    """The valid rows each run left for the failed row's cell, keyed by row keys (the same artifact downloaded twice is
    one candidate), and why the other runs were not taken."""
    cell = (failed.get("results") or {}).get("cell")
    if not isinstance(cell, dict) or not {"idx", "specialist", "pooled", "variant"} <= set(cell):
        return {}, ["the failed row names no cell"]
    written = _when(failed.get("ts"))
    found: dict[str, tuple[Path, Path, list[dict[str, Any]]]] = {}
    notes: list[str] = []
    for run in runs:
        path = run / f"cell-{cell['idx']}" / "rows.jsonl"
        if not path.is_file():
            continue
        rows, bad = _read_cell_rows(path, cell)
        if bad or not rows:
            notes.append(f"{run.name}: {bad or 'no rows'}")
            continue
        when = [_when(r.get("ts")) for r in rows]
        if written is None or any(w is None or w >= written for w in when):
            notes.append(f"{run.name}: written after the failed row (another run)")
            continue
        if any(_key(r) in known for r in rows):
            notes.append(f"{run.name}: already in the registry")
            continue
        found.setdefault("|".join(sorted("/".join(_key(r)) for r in rows)), (run, path, rows))
    return found, notes


Digests = Callable[[str], dict[str, str]]


def artifact_digests(run_id: str) -> dict[str, str]:
    """{artifact name: digest} the run uploaded (`gh api .../actions/runs/<id>/artifacts`); {} when gh cannot say. GitHub
    digests the artifact zip, not rows.jsonl, so it is recorded on the repair row for the reviewer, not compared."""
    out = subprocess.run(["gh", "api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/artifacts"],
                         capture_output=True, text=True, check=False, timeout=60)
    if out.returncode != 0:
        return {}
    try:
        return {str(a["name"]): str(a["digest"]) for a in json.loads(out.stdout).get("artifacts", []) if a.get("digest")}
    except (ValueError, KeyError, TypeError, AttributeError):
        return {}


def repair(published: list[dict[str, Any]], runs: list[Path], verify: RunInfo | None = None,
           expect_k: int | None = None, digests: Digests | None = None) -> Repair:
    """The registry with every repairable failed row replaced (see the module docstring); `published` is not mutated.
    Every run folder must be a successful research.yml run (`verify`, gh by default). K may never fall, and with
    `expect_k` (the operator's figure from the evaluator's reading, never derived from the artifact) it must land on
    it exactly: a discovery row's own K_eff is not evidence of what K should be."""
    heads = _verified(runs, verify if verify is not None else run_info)   # looked up at call time (tests patch it)
    known = {_key(r) for r in published}
    fixes: list[dict[str, Any]] = []
    replaced: list[Outcome] = []
    left: list[Outcome] = []
    for f in published:
        if f.get("status") != FAILED or "repair" in f:
            continue
        found, notes = _candidates(f, runs, known)
        trial, was = int(f["trial"]), str(f["agent_id"])
        if len(found) != 1:
            why = f"cell rows in {len(found)} runs" if found else ("; ".join(notes) or "no artifact for its cell")
            left.append(Outcome(trial=trial, failed_agent_id=was, why=why))
            continue
        run, path, rows = next(iter(found.values()))
        source = f"{run.name}/{path.parent.name}"
        trials = [r for r in rows if is_trial(r)]
        if len(trials) != 1:
            left.append(Outcome(trial=trial, failed_agent_id=was, why=f"the cell left {len(trials)} rows; replacing one "
                                                                         "failed row with them would change K"))
            continue
        row = trials[0]
        # a pre-registration the cell wrote before its look (a discovery run's) is not a trial: it comes along under
        # its own timestamp and takes no slot (merge_rows numbers it after the trial before it)
        fixes += [r for r in rows if not is_trial(r)]
        fixes.append({**row, "ts": f["ts"], "trial": f["trial"],
                      "repair": {"replaces": {k: f[k] for k in KEY_FIELDS}, "trial": f["trial"],
                                 "cell_ts": row["ts"], "source": source, "run_id": run.name,
                                 "head_sha": heads[run], "rows_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                 "artifact_digest": (digests(run.name) if digests else {}).get(path.parent.name, ""),
                                 "reason": REASON}})
        replaced.append(Outcome(trial=trial, failed_agent_id=was, agent_id=str(row["agent_id"]), source=source,
                                status=str(row["status"])))
    k_before = n_trials_effective(published)
    if not fixes:
        return Repair(rows=published, replaced=[], left=left, k_before=k_before, k_after=k_before)
    merged = merge_rows(published, fixes)
    k = n_trials_effective(merged)
    mine = {_key(r) for r in fixes}
    final = [restate_dsr(r, k) if _key(r) in mine else r for r in merged]
    numbers = {_key(r): r["trial"] for r in final}
    swapped = [r for r in fixes if is_trial(r)]
    moved = [o.trial for o, r in zip(replaced, swapped, strict=True) if numbers[_key(r)] != o.trial]
    if moved or sum(map(is_trial, final)) != sum(map(is_trial, published)) or k < k_before:
        raise ValueError(f"the repair would renumber trials {moved}, change the trial count or lower K "
                         f"({k_before} -> {k}): refusing")
    if expect_k is not None and k != expect_k:
        raise ValueError(f"K would be {k} after the repair, not the expected {expect_k}: refusing")
    return Repair(rows=final, replaced=replaced, left=left, k_before=k_before, k_after=k)


def summary(r: Repair) -> str:
    lines = [f"#{o.trial} failed {o.failed_agent_id} -> {o.status} {o.agent_id} (from {o.source})" for o in r.replaced]
    lines += [f"#{o.trial} failed {o.failed_agent_id}: left as it is ({o.why})" for o in r.left]
    lines.append(f"{len(r.replaced)} replaced, {len(r.left)} left failed; {len(r.rows)} rows, K {r.k_before} -> "
                 f"{r.k_after}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("registry", type=Path, help="a local copy (scripts/registry_release.sh fetch REGISTRY)")
    ap.add_argument("runs", type=Path, nargs="+", help="downloaded research.yml run artifacts (cell-<idx>/)")
    ap.add_argument("--apply", action="store_true", help="write the repaired rows to REGISTRY (default: dry run)")
    ap.add_argument("--expect-k", type=int, default=None,
                    help="the registry K the repair must land on (from the evaluator's reading); required with --apply")
    a = ap.parse_args()
    if a.apply and a.expect_k is None:
        sys.exit("::error::--apply needs --expect-k N: the K after the repair, taken from the evaluator, not the artifact")
    try:
        out = repair(read_rows(a.registry), a.runs, expect_k=a.expect_k, digests=artifact_digests)
    except ValueError as exc:
        sys.exit(f"::error::{exc}")
    print(summary(out))
    if a.apply and out.replaced:
        write_rows(a.registry, out.rows)
        print(f"wrote {a.registry}; publish after review with: scripts/registry_release.sh save {a.registry}")
    else:
        print("dry run: nothing written (--apply writes REGISTRY)")


if __name__ == "__main__":
    main()
