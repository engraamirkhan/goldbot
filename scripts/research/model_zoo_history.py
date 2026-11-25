"""H-101 model zoo and fusion on the retired gold arms (docs/research/prereg-2026-11-25-model-zoo-fusion.md, zoo-v1).

  count     Stage 0, outcome-blind (0 K): candidates per arm and block on learn 2012-17 and confirm, no label computed,
            and the ADR 0085 power verdict (section 4a). With --registry a refusal is written as NOT STARTED (power).
  learn     Stage L (K = 8): needs a started Stage 0 record; budget checked, the leakage checks run, the 8 K rows booked
            BEFORE the read; then the walk-forward, the Stage L read and the confirm models frozen and hashed.
  confirm   Stage C, read once: needs a CONTINUE Stage L record and refits the frozen models to the same hash.
  test_old  Stage T, read once, winner only: needs a Stage C winner (`windows.allow_test_old_for`).

Gold only (card 2.4): any --instrument but XAUUSD (metal) is refused. Market data, labels, features and records stay
local (--cache, default ~/tmp/history-replay; the VM's data dir in production): never committed, never published.
Multiprocessing by (arm, year); rule 2026-11-23: the label stages run on the goldbot-ops VM, not the Mac.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from goldbot.config import load_settings  # noqa: E402
from goldbot.data.timeutil import epoch_ns  # noqa: E402
from goldbot.research import history_replay as H  # noqa: E402
from goldbot.research import model_zoo as Z  # noqa: E402
from goldbot.research import self_retrain as sr  # noqa: E402
from goldbot.research import windows as W  # noqa: E402

WORKERS = 5
LEARN = (pd.Timestamp("2010-01-01", tz="UTC"), Z.CONFIRM_FROM)


def _years(lo: pd.Timestamp, hi: pd.Timestamp) -> list[int]:
    return list(range(lo.year, (hi - pd.Timedelta(1, "ns")).year + 1))


def _arm(arm_id: str) -> Any:
    return H.load_arm(arm_id, replay_as=Z.REPLAY_AS.get(arm_id))


def _count_job(job: tuple[str, str, str, int, pd.Timestamp, pd.Timestamp]) -> pd.DataFrame | str:
    """One (arm, year) of outcome-blind candidates; a rule that cannot be replayed returns its error (label-blind
    drop)."""
    arm_id, bars, cal, year, lo, hi = job
    try:
        arm = _arm(arm_id)
        a, b = H.year_span(lo, hi, year)
        return H.candidates(arm_id, arm, H.year_bars(Path(bars), a, b), a, b, H.load_blackout(Path(cal)))
    except Exception as exc:  # noqa: BLE001 - recorded as a label-blind drop of the arm
        return f"{arm_id} {year}: {type(exc).__name__}: {exc}"


def _sha(*parts: str | bytes) -> str:
    h = hashlib.sha256()
    for x in parts:
        h.update(x.encode() if isinstance(x, str) else x)
    return h.hexdigest()


def feature_key(tf: str, year: int, decisions: pd.DatetimeIndex) -> str:
    """Feature cache key: sha256 of the spec, the replay/feature/label code blob SHAs, tf, year and the decisions."""
    ns = np.ascontiguousarray(epoch_ns(decisions) if len(decisions) else np.zeros(0, np.int64))
    return _sha(Z.SPEC_VERSION, H.code_hash(), tf, str(year), ns.tobytes())[:24]


def _label_key(arm_id: str, year: int, lo: pd.Timestamp, hi: pd.Timestamp) -> str:
    return _sha(Z.SPEC_VERSION, H.code_hash(), arm_id, str(year), str(lo), str(hi))[:24]


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _rows_sha(rows: pd.DataFrame) -> str:
    cols = ["candidate_id", "decision_utc", "label_exit_utc", "r", "r_mid", *sr.FEATURES]
    return _sha(pd.util.hash_pandas_object(rows.reindex(columns=cols), index=False).to_numpy().tobytes())


def _read_families(registry: Path | None, ledger: Path) -> set[str]:
    """Families already read on test_old: EVAL-2's reads ledger and the read-once window ledger."""
    from goldbot.research.promotion_confirm import eval2_test_old_families
    fam = eval2_test_old_families(registry.with_name("catalogue_reads.jsonl")) if registry is not None else set()
    if ledger.exists():
        rows = [json.loads(x) for x in ledger.read_text().splitlines() if x.strip()]
        fam |= {str(r["family"]) for r in rows if r.get("window") == W.Window.TEST_OLD.value}
    return fam


def _table(t: pd.DataFrame) -> dict[str, dict[str, int]]:
    """A (gate, key) -> {rows, days} table as JSON keys "gate:key"."""
    keys = [f"{g}:{k}" for g, k in zip(t.index.get_level_values(0), t.index.get_level_values(1), strict=True)]
    return {key: {"rows": int(r), "days": int(d)} for key, r, d in zip(keys, t["rows"], t["days"], strict=True)}


def count(a: argparse.Namespace) -> dict[str, Any]:
    spans = [(Z.JUDGED_BLOCKS[0], pd.Timestamp(f"{Z.JUDGED_BLOCKS[0]}-01-01", tz="UTC"), Z.CONFIRM_FROM),
             (2018, Z.CONFIRM_FROM, Z.CONFIRM_TO)]
    jobs = [(arm, a.bars, a.calendar, y, lo, hi) for arm in Z.ARMS for _, lo, hi in spans for y in _years(lo, hi)]
    with ProcessPoolExecutor(a.workers) as ex:
        got = list(ex.map(_count_job, jobs))
    errors = [g for g in got if isinstance(g, str)]
    dropped = sorted({e.split(" ", 1)[0] for e in errors})
    frames = [g for g in got if isinstance(g, pd.DataFrame) and len(g) and str(g["arm"].iloc[0]) not in dropped]
    cands = pd.concat(frames, ignore_index=True)
    cands = cands.assign(label_exit_utc=pd.to_datetime(cands["decision_utc"], utc=True))   # outcome-blind: no exit read
    counts = Z.count_table(cands)
    rules = {arm: _arm(arm).rule for arm in Z.ARMS if arm not in dropped}
    reg = Path(a.registry) if a.registry else None
    excluded = Z.test_old_excluded(rules, _read_families(reg, Path(a.ledger)))
    settings = load_settings()
    if reg is not None:
        from goldbot.research.registry import TrialRegistry
        verdict = Z.stage0_record(TrialRegistry(reg), counts, excluded=excluded, settings=settings,
                                  agent_id="research-director")
    else:
        verdict, _ = Z.stage0(counts, excluded=excluded, settings=settings)
    by = counts.groupby(["gate", "arm"]).agg(rows=("session_day", "size"), days=("session_day", "nunique"))
    blocks = counts.groupby(["gate", "block"]).agg(rows=("session_day", "size"), days=("session_day", "nunique"))
    out = {"spec": Z.SPEC_VERSION, "stage": "0", "started": verdict.started, "verdict": verdict.model_dump(mode="json"),
           "dropped_arms": dropped, "drop_errors": errors, "test_old_excluded": excluded,
           "by_arm": _table(by), "by_block": _table(blocks),
           "recorded_in_registry": reg is not None, "notes": list(Z.STAGE0_NOTES)}
    _write(a, "stage0.json", out)
    return out


# ---------------------------------------------------------------------------------------------- labelled rows
def _label_job(job: tuple[str, str, str, int, pd.Timestamp, pd.Timestamp, str]) -> pd.DataFrame:
    arm_id, bars, cal, year, lo, hi, cache = job
    part = Path(cache) / "labels" / f"{arm_id}_{year}_{_label_key(arm_id, year, lo, hi)}.parquet"
    if part.exists():
        return pd.read_parquet(part)
    arm = _arm(arm_id)
    a, b = H.year_span(lo, hi, year)
    out = H.label(arm_id, arm, H.year_bars(Path(bars), a, b), a, b, H.load_blackout(Path(cal)))
    out = out.assign(tf=arm.timeframe)
    part.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(part, index=False)
    return out


def _feature_job(job: tuple[str, int, str, str, pd.DatetimeIndex]) -> pd.DataFrame:
    tf, year, bars, cache, decisions = job
    part = Path(cache) / "features" / f"{tf}_{year}_{feature_key(tf, year, decisions)}.parquet"
    if part.exists():
        return pd.read_parquet(part)
    a, b = pd.Timestamp(f"{year}-01-01", tz="UTC"), pd.Timestamp(f"{year + 1}-01-01", tz="UTC")
    f = H.features_at(H.history_minutes(H.year_bars(Path(bars), a, min(b, W.HOLDOUT_FROM))), tf, decisions)
    f = f.assign(tf=tf)
    part.parent.mkdir(parents=True, exist_ok=True)
    f.to_parquet(part, index=False)
    return f


def build_rows(a: argparse.Namespace, lo: pd.Timestamp, hi: pd.Timestamp, arms: list[str]) -> pd.DataFrame:
    """Labelled candidates of `arms` decided in [lo, hi) with the 40 inputs (features per distinct (tf, decision))."""
    jobs = [(arm, a.bars, a.calendar, y, lo, hi, a.cache) for arm in arms for y in _years(lo, hi)]
    with ProcessPoolExecutor(a.workers) as ex:
        lab = pd.concat([f for f in ex.map(_label_job, jobs) if len(f)], ignore_index=True)
    lab["decision_utc"] = pd.to_datetime(lab["decision_utc"], utc=True)
    keys = lab[["tf", "decision_utc"]].drop_duplicates()
    keys = keys.assign(year=keys["decision_utc"].dt.year)
    fjobs = [(str(tf), int(y), a.bars, a.cache, pd.DatetimeIndex(sorted(keys.loc[(keys["tf"] == tf) & (keys["year"] == y),
                                                                             "decision_utc"])))
             for tf, y in sorted(set(zip(keys["tf"].astype(str), keys["year"].astype(int), strict=True)))]
    with ProcessPoolExecutor(a.workers) as ex:
        feats = pd.concat(list(ex.map(_feature_job, fjobs)), ignore_index=True)
    feats["decision_utc"] = pd.to_datetime(feats["decision_utc"], utc=True)
    rows = lab.merge(feats.reindex(columns=["tf", "decision_utc", "available_utc_max", "memory_from_utc",
                                            *sr.FEATURES]), on=["tf", "decision_utc"], how="left")
    try:
        Z.check_inputs(rows)              # point in time (a missing snapshot fails) and input coverage, before any read
    except ValueError as exc:
        raise SystemExit(f"refused before any read (card section 3): {exc}") from exc
    return rows.sort_values(["decision_utc", "candidate_id"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------- stages
def _write(a: argparse.Namespace, name: str, payload: dict[str, Any]) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / name).write_text(json.dumps(payload, indent=2, default=str))


def _read(a: argparse.Namespace, name: str, why: str) -> dict[str, Any]:
    p = Path(a.out) / name
    if not p.exists():
        raise SystemExit(f"{why}: {p} is missing")
    return dict(json.loads(p.read_text()))


def _arms_used(stage0: dict[str, Any]) -> list[str]:
    return [arm for arm in Z.ARMS if arm not in set(stage0["dropped_arms"])]


def _same_ledger(a: argparse.Namespace, s: dict[str, Any]) -> None:
    if str(s.get("ledger")) != str(a.ledger):
        raise SystemExit(f"read-once ledger mismatch: Stage L recorded {s.get('ledger')}, this run would use {a.ledger}")


def _not_done(a: argparse.Namespace, name: str) -> None:
    if (Path(a.out) / name).exists():
        raise SystemExit(f"{name} already exists: this stage was already read once")


def learn(a: argparse.Namespace) -> dict[str, Any]:
    """Stage L. Learn rows whose horizon or exit reaches 2018-01-01 are dropped (read_rows) before anything is read,
    so no 2017 candidate resolved in confirm is judged. Early-2010 features read late-2009 minutes as indicator state
    (prices only; no test_old label is computed)."""
    from goldbot.research.power_first import PowerVerdict
    from goldbot.research.registry import TrialRegistry, quarter_of
    _not_done(a, "stage_l.json")
    reg, settings = TrialRegistry(Path(a.registry)), load_settings()
    booked_before = [r for r in reg.by_family(Z.REGISTRY_FAMILY) if (r.get("config") or {}).get("spec") == Z.SPEC_VERSION]
    if booked_before and W.Window.LEARN not in W.reads_of(a.ledger, Z.REGISTRY_FAMILY):
        raise SystemExit(f"{Z.SPEC_VERSION} Stage L rows are already in the registry: Stage L was already read")
    s0 = _read(a, "stage0.json", "Stage L needs the Stage 0 record")
    verdict = PowerVerdict.model_validate(s0["verdict"])
    if not (s0["started"] and verdict.started and s0["recorded_in_registry"]):
        raise SystemExit("Stage 0 did not start (power) or was not run against the registry: no Stage L, 0 K")
    if quarter_of() != Z.ALLOCATED_QUARTER:
        raise SystemExit(f"H-101's K is allocated from {Z.ALLOCATED_QUARTER}, not {quarter_of()}: no Stage L")
    reg.check_budget(len(Z.CANDIDATES), settings.research.budget_for(quarter_of()))     # fail fast, nothing read yet
    rows, dropped = Z.read_rows(build_rows(a, *LEARN, _arms_used(s0)), *LEARN)
    coverage = Z.check_inputs(rows)
    leak = Z.shuffled_label_check(rows)
    if not leak["ok"]:
        raise SystemExit(f"leakage check failed (card section 3.1): no read, 0 K: {leak}")
    provenance = {"git_sha": _git_sha(), "learn_rows_sha256": _rows_sha(rows)}
    key = f"{Z.SPEC_VERSION}:{provenance['learn_rows_sha256']}"
    W.record_read_once(a.ledger, Z.REGISTRY_FAMILY, W.Window.LEARN, ref=provenance, resume_key=key)
    booked = Z.book_stage_l(reg, settings, stage0=verdict, agent_id="research-director", provenance=provenance)
    rows.to_parquet(Path(a.cache) / "learn_rows.parquet", index=False)
    wf = Z.walk_forward(rows)
    wf.oof.to_parquet(Path(a.cache) / "learn_oof.parquet", index=False)
    res = Z.stage_l(Z.judged(wf.oof)) | {"unjudged_by_arm": Z.unjudged(wf.oof)}
    frozen = Z.freeze(rows, wf.oof, t0=Z.CONFIRM_FROM)
    fh = Z.frozen_hash(frozen)
    Z.write_back_stage_l(reg, res, frozen_hash=fh)
    out = {"stage": "L", "spec": Z.SPEC_VERSION, "k_booked": len(booked), "leakage": leak, **res,
           "frozen_hash": fh, "arms": _arms_used(s0), "ledger": str(a.ledger), "dropped_at_learn_edge": dropped,
           "inputs": coverage, "note": f"{coverage['informative_inputs']} informative inputs: "
           f"{', '.join(Z.STRUCTURAL_NAN)} are always NaN on the engine's 90k-minute memory", **provenance}
    _write(a, "stage_l.json", out)
    return out


def _frozen_from_cache(a: argparse.Namespace, stage_l: dict[str, Any]) -> Z.Frozen:
    rows = pd.read_parquet(Path(a.cache) / "learn_rows.parquet")
    oof = pd.read_parquet(Path(a.cache) / "learn_oof.parquet")
    frozen = Z.freeze(rows, oof, t0=Z.CONFIRM_FROM)
    if Z.frozen_hash(frozen) != stage_l["frozen_hash"]:
        raise SystemExit("the confirm models do not match the hash frozen at Stage L: refused")
    return frozen


def confirm(a: argparse.Namespace) -> dict[str, Any]:
    s = _read(a, "stage_l.json", "Stage C needs the Stage L record")
    _same_ledger(a, s)
    _not_done(a, "stage_c.json")
    if s.get("verdict") != "CONTINUE" or not s.get("frozen_hash"):
        raise SystemExit("Stage L did not CONTINUE (or no frozen-model hash): confirm stays closed")
    frozen = _frozen_from_cache(a, s)
    W.record_read_once(a.ledger, Z.REGISTRY_FAMILY, W.Window.CONFIRM, ref={"frozen_hash": s["frozen_hash"]},
                       resume_key=str(s["frozen_hash"]))
    rows, dropped = Z.read_rows(build_rows(a, Z.CONFIRM_FROM, Z.CONFIRM_TO, list(s["arms"])), Z.CONFIRM_FROM,
                                Z.CONFIRM_TO)
    j = pd.concat([rows, Z.score(frozen, rows)], axis=1)
    unj = Z.unjudged(j.assign(block=0), blocks=(0,))
    j = Z.judged(j.assign(block=0), blocks=(0,))
    out = {"stage": "C", "dropped_at_holdout": dropped, "n_rows": len(j), "unjudged_by_arm": unj,
           **Z.stage_c(j, continuing=list(s["continuing"]), best_base=str(s["best_base"]))}
    _write(a, "stage_c.json", out)
    return out


def test_old(a: argparse.Namespace) -> dict[str, Any]:
    s = _read(a, "stage_l.json", "Stage T needs the Stage L record")
    _same_ledger(a, s)
    _not_done(a, "stage_t.json")
    c = _read(a, "stage_c.json", "Stage T needs the Stage C record")
    winner = c.get("winner")
    passed = [f"{Z.REGISTRY_FAMILY}:{winner}"] if winner else []
    allow = W.allow_test_old_for(f"{Z.REGISTRY_FAMILY}:{winner}", passed)
    frozen = _frozen_from_cache(a, s)
    rules = {arm: _arm(arm).rule for arm in s["arms"]}
    excluded = Z.test_old_excluded(rules, _read_families(Path(a.registry), Path(a.ledger)))   # EVAL-2 ledger always
    left = [arm for arm in s["arms"] if arm not in excluded]
    if len(left) < Z.MIN_ARMS:
        out = {"stage": "T", "winner": winner, "excluded": excluded, **Z.decide_t({}, arms_left=len(left))}
        _write(a, "stage_t.json", out)
        return out
    W.record_read_once(a.ledger, Z.REGISTRY_FAMILY, W.Window.TEST_OLD, ref={"winner": winner, "allow": allow},
                       resume_key=f"{winner}:{s['frozen_hash']}")
    rows, dropped = Z.read_rows(build_rows(a, Z.TEST_OLD_FROM, Z.TEST_OLD_TO, left), Z.TEST_OLD_FROM, Z.TEST_OLD_TO)
    early = int((pd.to_datetime(rows["memory_from_utc"], utc=True) < Z.TEST_OLD_FROM).sum())
    j = pd.concat([rows, Z.score(frozen, rows)], axis=1)
    j = Z.judged(j.assign(block=0), blocks=(0,))
    out = {"stage": "T", "excluded": excluded, "dropped_at_edge": dropped,
           "memory_before_test_old": {"rows": early, "share": early / len(rows) if len(rows) else 0.0,
                                      "note": "feature memory reaching before 2008-02-01 (prices only), disclosed"},
           **Z.stage_t(j, winner=str(winner), arms_left=len(left))}
    _write(a, "stage_t.json", out)
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("count", "learn", "confirm", "test_old"))
    ap.add_argument("--instrument", default=Z.GOLD)
    ap.add_argument("--bars", default=str(ROOT / "raw"))
    ap.add_argument("--calendar", default=None, help="calendar-v1 files (default: <bars>/calendar)")
    ap.add_argument("--registry", default=None, help="the research registry of record (Stage 0 refusal, Stage L K)")
    ap.add_argument("--cache", default=str(H.CACHE / "model-zoo"))
    ap.add_argument("--out", default=str(H.CACHE / "model-zoo" / "records"))
    ap.add_argument("--workers", type=int, default=WORKERS)
    a = ap.parse_args(argv)
    Z.gold_only(a.instrument)                         # before anything is read
    if a.mode != "count" and not a.registry:
        raise SystemExit(f"{a.mode} reads labels: --registry (the registry of record) is required")
    a.calendar = a.calendar or str(Path(a.bars) / "calendar")
    # the read-once ledger lives beside the registry, never elsewhere (count without a registry only reads it)
    a.ledger = str(Path(a.registry).with_name(W.READS_LEDGER) if a.registry else Path(a.out) / W.READS_LEDGER)
    Path(a.cache).mkdir(parents=True, exist_ok=True)
    out = {"count": count, "learn": learn, "confirm": confirm, "test_old": test_old}[a.mode](a)
    print(json.dumps(out if a.mode != "count" else {k: out[k] for k in ("started", "dropped_arms", "test_old_excluded",
                                                                           "by_block")} | {
        "gates": out["verdict"]["gates"], "joint_power": out["verdict"]["joint_power"],
        "reasons": out["verdict"]["reasons"]}, indent=2, default=str))


if __name__ == "__main__":
    np.seterr(all="ignore")
    main()
