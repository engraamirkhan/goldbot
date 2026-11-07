"""The ONE catalogue screen run (WS-3). Subcommands, in order:

  counts      feasibility pre-screen: explore EVENT COUNTS per unit (no fills, prices or returns are computed); a unit
              whose explore events are below its n floor cannot pass and is listed 'not run, no K' in the prereg
  enumerate   the unit list, one config hash each, the budget assert (Q4 used + units + reserved <= the quarter cap,
              else abort), optional --write-registry: one PREREGISTERED registry row per unit, BEFORE any return
  explore     every registered unit on explore 2010-2017 (+ learn_pre 2003-05..2005), 8 workers; refuses to start
              unless every unit has its registry row and the catalogue code equals the frozen commit; writes one
              'screened' trial row per unit (the quarter's budget is spent here) and the null void check
  confirm     explore passers only, 2018-01..2025-09, read ONCE (ledger claim), BH-FDR q 0.10
  test_old    CONFIRM passers only, 2006-02..2009

Outputs (aggregates only) go to $CATALOGUE_OUT (default ~/tmp/catalogue_out); nothing at or after 2025-10-01 is read."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

import numpy as np

from goldbot.research.catalogue import dispatcher as D
from goldbot.research.catalogue import stats as S
from goldbot.research.catalogue.data import (
    DAY,
    FINE_INSTRUMENTS,
    INTRADAY,
    TF_NS,
    Bars,
    _gold_minutes,
    _resample_minutes,
    window,
)
from goldbot.research.registry import PREREGISTERED, TrialRegistry, config_hash, quarter_of

ROOT = Path(__file__).resolve().parents[3]
QUARTER = "2026Q4"
RESERVED_OTHER_WS = 12          # WS-5 arms (<= 6) + WS-6 N-3/N-1a (3) + WS-7 N-9/N-2 (3), plan k_cost
PLAN_USED_FLOOR = 3173          # the plan's Q4 used count (registry rows incl. sprint-3000 pre-registrations)
FAMILY_PREFIX = "catalogue:"
FEATURE_VERSION = "catalogue-screen-2026-11-07"
RULE_REF = "docs/research/prereg-2026-11-07-catalogue-screen.md"
AGENT = "strategy-researcher"
READS_LEDGER = "catalogue_reads.jsonl"
EX2009_FAMILIES = ("S11", "S15", "S16", "S27", "S30")      # quant T4: TEST-OLD also reported without 2009
CODE_PATH = "goldbot/research/catalogue"


def out_dir() -> Path:
    p = Path(os.environ.get("CATALOGUE_OUT", str(Path.home() / "tmp" / "catalogue_out")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_registry() -> Path:
    env = os.environ.get("GOLDBOT_REGISTRY")
    if env:
        return Path(env)
    from goldbot.research.catalogue.data import raw_dir
    return raw_dir().parent / "state" / "research_registry.jsonl"


def unit_config(u: dict[str, Any]) -> dict[str, Any]:
    return {"catalogue": u["unit"], "specs_sha256": D.specs_sha256(), "unit": u}


def registered_units(dropped: set[str]) -> list[dict[str, Any]]:
    return [u for u in D.units() if u["unit"] not in dropped]


def _dropped() -> set[str]:
    f = Path(__file__).with_name("infeasible.json")
    return set(json.loads(f.read_text())["units"]) if f.exists() else set()


# ------------------------------------------------------------------ budget
def q4_used(reg: TrialRegistry) -> tuple[int, int]:
    """(rows stamped in the quarter that count in K: trials + pre-registrations, minus withdrawn; the plan floor)."""
    n = 0
    for r in reg._rows():
        try:
            ts = datetime.fromisoformat(str(r.get("ts")))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if quarter_of(ts) == QUARTER and r.get("status") != "withdrawn":
            n += 1
    return n, PLAN_USED_FLOOR


def quarter_cap() -> int:
    from goldbot.config import load_settings
    r = load_settings().research
    return int(r.trial_budget_overrides.get(QUARTER, r.trial_budget_quarter))


class BudgetAbort(SystemExit):
    pass


def assert_budget(n_units: int, used: int, cap: int, reserved: int = RESERVED_OTHER_WS) -> int:
    """Abort unless used + units + reserved <= cap; returns the headroom left."""
    left = cap - used - n_units - reserved
    if left < 0:
        raise BudgetAbort(f"trial budget: {QUARTER} cap {cap}, used {used}, this screen {n_units}, reserved "
                          f"{reserved} -> over by {-left}. Abort (raising the budget is an owner decision).")
    return left


# ------------------------------------------------------------------ provenance
def head() -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def code_matches(commit: str) -> bool:
    r = subprocess.run(["git", "diff", "--quiet", commit, "--", CODE_PATH], cwd=ROOT)
    st = subprocess.run(["git", "status", "--porcelain", "--", CODE_PATH], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0 and not st.stdout.strip()


# ------------------------------------------------------------------ cache prebuild
def prebuild(win: str, all_units: list[dict[str, Any]], *, allow_test_old: bool = False) -> None:
    need = set()
    for u in all_units:
        if u.get("xs"):
            need |= {(i, t) for i in u["instruments"] for t in u["timeframes"]}
        else:
            need |= set(D.cells(u))
    gold_intra = sorted(t for i, t in need if i == "XAUUSD" and t in INTRADAY and t != "4h")
    cdir = D.cache_dir() / win
    cdir.mkdir(parents=True, exist_ok=True)
    if gold_intra and not all((cdir / f"XAUUSD_{t}.npz").exists() for t in gold_intra):
        import pandas as pd

        from goldbot.research.catalogue.data import INTRADAY_WARMUP_NS, _cut, _frame_to_bars, assert_sealed
        w = window(win, allow_test_old=allow_test_old)
        y0 = pd.Timestamp(max(w.data_from, w.dec_from - INTRADAY_WARMUP_NS), unit="ns").year
        y1 = pd.Timestamp(w.end - 1, unit="ns").year
        m = _gold_minutes(range(y0, y1 + 1))
        for t in gold_intra:
            df = _cut(_resample_minutes(m, t), w, t)
            b = _frame_to_bars("XAUUSD", t, df)
            assert_sealed(b.ta, b.t)
            np.savez(cdir / f"XAUUSD_{t}.npz", t=b.t, ta=b.ta, o=b.o, h=b.h, l=b.l, c=b.c, spr=b.spr, co=b.co,
                     seg=b.seg, vol=b.vol)
    for i, t in sorted(need):
        try:
            D.get_bars(i, t, win, allow_test_old=allow_test_old)
        except FileNotFoundError:
            pass
    if any(i in FINE_INSTRUMENTS for i, _ in need):
        D.get_minutes(win, allow_test_old=allow_test_old)       # 1m fill bars (C10), built once before the workers


# ------------------------------------------------------------------ workers
_CTX: dict[str, Any] = {}


def _init(wins: list[str], allow_test_old: bool) -> None:
    for w in wins:
        _CTX[w] = D.build_ctx(w, allow_test_old=allow_test_old)


def _counts_work(uid: str) -> tuple[str, dict[str, int]]:
    u = D.unit_by_id(uid)
    return uid, D.unit_event_counts(u, "explore", _CTX["explore"])


def _explore_work(uid: str) -> dict[str, Any]:
    t0 = time.time()
    u = D.unit_by_id(uid)
    tr, ctrl, cnt = D.unit_trades(u, "explore", _CTX["explore"])
    pre_tr, _, _ = D.unit_trades(u, "learn_pre", _CTX["learn_pre"], control=ctrl)
    s = S.summarize(tr)
    pre = S.summarize(pre_tr)
    gates = S.explore_gates(s, u["timeframes"], pre)
    d = S.daily(tr).to_numpy() if len(tr) else np.zeros(0)
    tr.to_parquet(out_dir() / "trades" / f"explore_{uid}.parquet") if len(tr) else None
    return {"unit": uid, "family": u["family"], "control": ctrl, "n_events": cnt["events"],
            "n_trades": cnt["trades"], "n_risk_floor": cnt["risk_floor"], "n_fine_miss": cnt["fine_miss"],
            "excluded_no_1m": bool(cnt["excluded_no_1m"]), "no_1m_cells": cnt["no_1m_cells"], "explore": s,
            "learn_pre": {k: pre.get(k) for k in ("n", "excess", "net", "t_excess")}, "gates": gates,
            "pass": bool(gates) and all(gates.values()), "daily_len": int(len(d)), "secs": round(time.time() - t0, 1)}


def _window_work(args: tuple[str, str, str]) -> dict[str, Any]:
    uid, win, ctrl = args
    u = D.unit_by_id(uid)
    tr, _, _ = D.unit_trades(u, win, _CTX[win], control=ctrl, allow_test_old=(win == "test_old"))
    s = S.summarize(tr)
    d = S.daily(tr)
    block = max(S.BOOT_BLOCK, int(s.get("lag_L", 1)))                       # quant C2
    p = S.block_boot_p(d.to_numpy(), block=block,
                       seed=int.from_bytes(uid.encode()[:4].ljust(4, b"0"), "big")) if len(d) else 1.0
    if len(tr):
        tr.to_parquet(out_dir() / "trades" / f"{win}_{uid}.parquet")
    y2025 = {k: v for k, v in s.get("years", {}).items() if k == 2025}
    out = {"unit": uid, "window": win, "summary": s, "boot_p": p, "boot_block": block, "y2025": y2025}
    if win == "test_old" and u["family"] in EX2009_FAMILIES and len(tr):       # T4: reported, not gated
        yr = S.year_of_day(tr["t_fill"].to_numpy(np.int64) // DAY)
        s2 = S.summarize(tr[yr != 2009])
        out["ex_2009"] = {k: s2.get(k) for k in ("n", "net", "excess", "t_excess")}
    return out


def _pool(fn: Callable[[Any], Any], items: list[Any], wins: list[str], workers: int,
          allow_test_old: bool = False) -> Iterator[Any]:
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(wins, allow_test_old)) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for f in as_completed(futs):
            yield f.result()


# ------------------------------------------------------------------ commands
def _floor(u: dict[str, Any]) -> int:
    return S.N_MIN_DAILY if all(t in ("1d", "1w") for t in u["timeframes"]) else S.N_MIN_INTRADAY


def cmd_counts(a: argparse.Namespace) -> None:
    us = [u for u in D.units() if not a.only or u["unit"].startswith(a.only)]
    sfx = "_" + a.only if a.only else ""
    prebuild("explore", us)
    res = {}
    for uid, c in _pool(_counts_work, [u["unit"] for u in us], ["explore"], a.workers):
        res[uid] = c
        print(f"{uid:24s} events {sum(c.values()):8d}", flush=True)
    (out_dir() / f"counts_explore{sfx}.json").write_text(json.dumps(res, indent=1))
    drops = []
    for u in us:
        total = sum(res[u["unit"]].values())
        floor = S.N_MIN_DAILY if all(t in ("1d", "1w") for t in u["timeframes"]) else S.N_MIN_INTRADAY
        if total < floor and u["family"] != "NULL":
            drops.append(u["unit"])
    print("infeasible (events < n floor):", drops)
    (out_dir() / f"infeasible_proposed{sfx}.json").write_text(json.dumps({"units": drops}, indent=1))


def cmd_enumerate(a: argparse.Namespace) -> None:
    dropped = _dropped()
    us = registered_units(dropped)
    reg = TrialRegistry(a.registry)
    used_rows, floor = q4_used(reg)
    used = max(used_rows, floor) + a.extra_used
    cap = quarter_cap()
    left = assert_budget(len(us), used, cap)
    rows = [{"unit": u["unit"], "family": FAMILY_PREFIX + u["family"], "config_hash": config_hash(unit_config(u))}
            for u in us]
    doc = {"specs_sha256": D.specs_sha256(), "harness_commit": head(), "quarter": QUARTER, "cap": cap,
           "used": used, "used_rows_local": used_rows, "plan_floor": floor, "reserved_other_ws": RESERVED_OTHER_WS,
           "n_units": len(us), "n_real": sum(u["family"] != "NULL" for u in us),
           "n_null": sum(u["family"] == "NULL" for u in us), "headroom_after": left,
           "k_registry_after": max(reg.n_trials_effective, used) + len(us), "dropped_infeasible": sorted(dropped),
           "units": rows}
    Path(a.units_out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.units_out).write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "units"}, indent=1))
    if a.write_registry:
        written = 0
        with reg.locked():
            have = {(r.get("family"), r.get("config_hash")) for r in reg._rows() if r.get("status") == PREREGISTERED}
            for u in us:
                fam, cfg = FAMILY_PREFIX + u["family"], unit_config(u)
                if (fam, config_hash(cfg)) in have:
                    continue
                reg.preregister(agent_id=AGENT, family=fam, config=cfg, feature_version=FEATURE_VERSION,
                                rationale=f"catalogue screen unit {u['unit']} (WS-3, consolidated plan 2026-11-07)",
                                reading_rule=RULE_REF + " section 'Reading rule'", queue=False, rule_ref=RULE_REF,
                                plan={"harness_commit": doc["harness_commit"], "specs_sha256": doc["specs_sha256"]})
                written += 1
        print(f"registry rows written: {written}")


def _check_ready(a: argparse.Namespace, us: list[dict[str, Any]]) -> TrialRegistry:
    units_doc = json.loads(Path(a.units_out).read_text())
    if not code_matches(units_doc["harness_commit"]):
        raise SystemExit(f"catalogue code differs from the frozen commit {units_doc['harness_commit']}: refused")
    if units_doc["specs_sha256"] != D.specs_sha256():
        raise SystemExit("specs.yaml differs from the pre-registered unit list: refused")
    reg = TrialRegistry(a.registry)
    have = {(r.get("family"), r.get("config_hash")) for r in reg._rows() if r.get("status") == PREREGISTERED}
    missing = [u["unit"] for u in us if (FAMILY_PREFIX + u["family"], config_hash(unit_config(u))) not in have]
    if missing:
        raise SystemExit(f"{len(missing)} units have no pre-registration row (e.g. {missing[:3]}): refused")
    return reg


def _claim(win: str, resume: bool = False) -> None:
    """One read per window. `resume` continues a crashed read of the SAME frozen code (same HEAD), recorded."""
    led = default_registry().with_name(READS_LEDGER)
    prior = [json.loads(x) for x in led.read_text().splitlines() if x.strip()] if led.exists() else []
    prior = [r for r in prior if r.get("window") == win]
    if prior and not (resume and all(r.get("head") == head() for r in prior)):
        raise SystemExit(f"the {win} window was already read by the catalogue screen: a second read is refused")
    with open(led, "a") as fh:
        fh.write(json.dumps({"window": win, "ts": datetime.now(timezone.utc).isoformat(), "head": head(),
                             "resume": bool(prior)}) + "\n")


def cmd_explore(a: argparse.Namespace) -> None:
    us = registered_units(_dropped())
    reg = _check_ready(a, us)
    (out_dir() / "trades").mkdir(parents=True, exist_ok=True)
    _claim("explore", a.resume)
    for w in ("explore", "learn_pre"):
        prebuild(w, us)
    res = []
    for r in _pool(_explore_work, [u["unit"] for u in us], ["explore", "learn_pre"], a.workers):
        res.append(r)
        s = r["explore"]
        print(f"{r['unit']:24s} trades {s.get('n', 0):7d} events {r['n_events']:7d} ex {s.get('excess', float('nan')):+.3f} "
              f"t {s.get('t_excess', float('nan')):+.2f} net {s.get('net', float('nan')):+.3f} "
              f"{'PASS' if r['pass'] else ''} {r['secs']}s", flush=True)
    res.sort(key=lambda r: r["unit"])
    nulls = [r for r in res if r["family"] == "NULL"]
    nv = S.null_void([{"unit": r["unit"], "pass": r["pass"], "n_events": r["n_events"],
                       "floor": _floor(D.unit_by_id(r["unit"]))} for r in nulls])
    units_doc = json.loads(Path(a.units_out).read_text())
    k_reg = int(units_doc["k_registry_after"])        # program K: Q4 rows incl. sprint-3000 pre-registrations + units
    for r in res:
        if r["explore"].get("n"):
            tr = _load_trades("explore", r["unit"])
            d = S.daily(tr).to_numpy()
            r["dsr_registry_k"] = S.deflated_sharpe(d, k_reg)
            r["dsr_k_cells"] = S.deflated_sharpe(d, len(us))
    summary = {"window": "explore", "head": head(), "n_units": len(us), "null_units": len(nulls),
               "null_void": nv, "void": nv["void"],
               "passers": [r["unit"] for r in res if r["pass"] and r["family"] != "NULL"], "k_registry": k_reg,
               "excluded_no_1m": [r["unit"] for r in res if r.get("excluded_no_1m")],          # quant C10: listed
               "results": res}
    (out_dir() / "explore.json").write_text(json.dumps(summary, indent=1, default=float))
    with reg.locked():
        for r in res:
            u = D.unit_by_id(r["unit"])
            reg.record(agent_id=AGENT, family=FAMILY_PREFIX + u["family"], config=unit_config(u),
                       feature_version=FEATURE_VERSION, rationale=f"catalogue screen explore {u['unit']}",
                       results={"explore": _slim(r)}, status="screened", budget_quarter=QUARTER,
                       preregistration=_prereg_row(reg, u), rule_ref=RULE_REF)
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=1))


def _prereg_row(reg: TrialRegistry, u: dict[str, Any]) -> dict[str, Any] | None:
    h = config_hash(unit_config(u))
    for r in reg._rows():
        if r.get("status") == PREREGISTERED and r.get("config_hash") == h and r.get("family") == FAMILY_PREFIX + u[
                "family"]:
            return r
    return None


def _slim(r: dict[str, Any]) -> dict[str, Any]:
    s = r["explore"]
    return {"n": s.get("n"), "n_trades": r.get("n_trades"), "n_events": r.get("n_events"),
            "n_risk_floor": r.get("n_risk_floor"), "excluded_no_1m": r.get("excluded_no_1m"), "gross": s.get("gross"),
            "cost": s.get("cost"), "net": s.get("net"),
            "excess": s.get("excess"), "t_excess": s.get("t_excess"), "pass": r["pass"], "gates": r["gates"],
            "control": r["control"]}


def _load_trades(win: str, uid: str) -> Any:
    import pandas as pd
    f = out_dir() / "trades" / f"{win}_{uid}.parquet"
    return pd.read_parquet(f) if f.exists() else pd.DataFrame(columns=["t_fill", "excess"])


def cmd_confirm(a: argparse.Namespace) -> None:
    us = registered_units(_dropped())
    _check_ready(a, us)
    ex = json.loads((out_dir() / "explore.json").read_text())
    if ex["void"]:
        raise SystemExit("explore run is VOID (null passes above the bound): confirm not read")
    passers = ex["passers"]
    ctrl = {r["unit"]: r["control"] for r in ex["results"]}
    if not passers:
        print("no explore passers: CONFIRM not read; the line closes")
        return
    _claim("confirm", a.resume)
    prebuild("confirm", [D.unit_by_id(u) for u in passers])
    res = list(_pool(_window_work, [(u, "confirm", ctrl[u]) for u in passers], ["confirm"], a.workers))
    res.sort(key=lambda r: r["unit"])
    p = np.array([r["boot_p"] for r in res])
    rej = S.bh(p)
    for r, ok in zip(res, rej):
        g = S.confirm_gates(r["summary"])
        g["bh"] = bool(ok)
        r["gates"] = g
        r["pass"] = all(g.values())
    out = {"window": "confirm", "head": head(), "m": len(res), "q": S.BH_Q,
           "passers": [r["unit"] for r in res if r["pass"]], "results": res}
    (out_dir() / "confirm.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))


def cmd_test_old(a: argparse.Namespace) -> None:
    from goldbot.research.windows import allow_test_old_for
    cf = json.loads((out_dir() / "confirm.json").read_text())
    passers = cf["passers"]
    if not passers:
        print("no CONFIRM passers: TEST-OLD stays sealed")
        return
    ex = json.loads((out_dir() / "explore.json").read_text())
    ctrl = {r["unit"]: r["control"] for r in ex["results"]}
    for u in passers:
        allow_test_old_for(u, passers)
    _claim("test_old", a.resume)
    prebuild("test_old", [D.unit_by_id(u) for u in passers], allow_test_old=True)
    res = list(_pool(_window_work, [(u, "test_old", ctrl[u]) for u in passers], ["test_old"], a.workers,
                     allow_test_old=True))
    for r in res:
        r["gates"] = S.test_old_gates(r["summary"])
        r["pass"] = all(r["gates"].values())
    out = {"window": "test_old", "head": head(), "passers": [r["unit"] for r in res if r["pass"]], "results": res}
    (out_dir() / "test_old.json").write_text(json.dumps(out, indent=1, default=float))
    print(json.dumps({k: v for k, v in out.items() if k != "results"}, indent=1))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m goldbot.research.catalogue.run")
    ap.add_argument("cmd", choices=["counts", "enumerate", "explore", "confirm", "test_old"])
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--registry", default=str(default_registry()))
    ap.add_argument("--units-out", default=str(ROOT / "docs" / "research" / "catalogue-units-2026-11-07.json"))
    ap.add_argument("--write-registry", action="store_true")
    ap.add_argument("--extra-used", type=int, default=0, help="Q4 rows known to exist outside the local registry")
    ap.add_argument("--resume", action="store_true", help="continue a crashed read of the same frozen commit")
    ap.add_argument("--only", default="", help="counts: only units whose id starts with this prefix")
    a = ap.parse_args(argv)
    {"counts": cmd_counts, "enumerate": cmd_enumerate, "explore": cmd_explore, "confirm": cmd_confirm,
     "test_old": cmd_test_old}[a.cmd](a)


if __name__ == "__main__":
    main()
    sys.exit(0)

_ = (TF_NS, Bars)
