"""The learning KPIs for the Friday #34 report (plan 2026-11-25 §1b, K1, K2 and K4; W1-C), computed from the
brain's aux.db read-only (`mode=ro`: never a write, never a migration).

  K1 usable rows   per (broker, asset class, tf), all 9 timeframes x every class, empty cells shown as 0 (owner
                   rules A1, A3): candidates, r2 labels, and labels with an admitted point-in-time snapshot ("usable":
                   not quarantined, not void for r2, not excluded as evidence, snapshot ok and `written`, or `nominal`
                   from the self-retrain's NOMINAL_FROM). Stock (everything to `--to`) and flow (the last `--days`).
                   The nominal-snapshot audit is not re-run here, so usable is an upper bound of what sr-v2 admits.
                   History rows (U_hist) are reported as 0 with the reason until a history-pretrain store exists.
  K2 influence     over the last `--days`: % of candidates a model scored, % it changed (goldbot/ops/checks/
                   learning.py `influence`), and the effect of the changed decisions in R (r2 replay label) and in
                   account money (R x the arm's median loss at the stop of its fills), with a 95 % CI clustered by
                   session day. "Changed" is a model's block (MODEL_BLOCK_PREFIXES): removing a candidate of label R
                   moves the book by -R. Influence is always shown next to its money (PO: Goodhart on influence %).
  K4 liveness      every learner and watcher with its verdict (the learning_liveness health check's own judgement).
  drift            PSI of inputs and of scores, live against training, for every self-retrain model in shadow or
                   active (goldbot/research/drift.py `scored_model_drift`); training = the arm's admitted rows up
                   to the model's cutoff (before the nominal audit), live = after it.

On the brain (read-only; prints JSON with --json, else a short markdown report):

    sudo -n -u goldbot /opt/goldbot/.venv/bin/python scripts/learning_kpis.py --state-dir /opt/goldbot/state
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.config import load_settings
from goldbot.instruments import ASSET_CLASS_ORDER
from goldbot.ops.checks import learning
from goldbot.ops.class_breakdown import asset_class_of
from goldbot.research import drift
from goldbot.research import self_retrain as sr
from goldbot.research.exploration_data import REPLAY_V2

# owner rule A3 (every tf, keep all): the nine timeframes, until the single tf source (plan W2-1) replaces this tuple
TIMEFRAMES: tuple[str, ...] = ("1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w")
Z95 = 1.96


def connect(state_dir: Path) -> sqlite3.Connection:
    path = state_dir / "aux.db"
    if not path.exists():
        raise SystemExit(f"no aux.db under {state_dir}")
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)


def candidates(con: sqlite3.Connection, until: pd.Timestamp) -> pd.DataFrame:
    """Every candidate decided before `until` with its r2 label, admission flags and money per R inputs."""
    q = """
        SELECT o.candidate_id, COALESCE(o.broker, '?') AS broker, o.arm_id, o.tf, o.decision_ns, o.session_day,
               o.blocked_reason, o.meta_p, o.model_version, o.bandit_version,
               o.quarantine IS NULL
                 AND NOT EXISTS (SELECT 1 FROM exploration_label_void v
                                 WHERE v.candidate_id = o.candidate_id AND v.replay_version = :rv)
                 AND NOT EXISTS (SELECT 1 FROM evidence_exclusions e WHERE e.client_order_id = o.candidate_id)
                 AS admitted,
               r.sim_r,
               s.status = 'ok' AND (s.pit = 'written' OR (s.pit = 'nominal' AND s.decision_ns >= :nominal))
                 AS snapshot
        FROM exploration_obs o
        LEFT JOIN exploration_replay r ON r.candidate_id = o.candidate_id AND r.replay_version = :rv
        LEFT JOIN exploration_feature_snapshots s ON s.candidate_id = o.candidate_id
        WHERE o.decision_ns < :until"""
    df = pd.read_sql_query(q, con, params={"rv": REPLAY_V2, "nominal": int(sr.NOMINAL_FROM.value),
                                           "until": int(until.value)})
    df["asset_class"] = [asset_class_of(a) for a in df["arm_id"]]
    df["labelled"] = df["admitted"].fillna(0).astype(bool) & df["sim_r"].notna()
    df["usable"] = df["labelled"] & df["snapshot"].fillna(0).astype(bool)
    return df


def usable_rows(df: pd.DataFrame, *, since: pd.Timestamp) -> list[dict[str, Any]]:
    """K1 per (broker, class, tf): every broker seen x every class x the nine tfs, zeros kept."""
    brokers = sorted(set(df["broker"])) or ["icm"]
    flow = df[df["decision_ns"] >= int(since.value)]
    out: list[dict[str, Any]] = []
    for b in brokers:
        for c in ASSET_CLASS_ORDER:
            for tf in TIMEFRAMES:
                cell = df[(df["broker"] == b) & (df["asset_class"] == c) & (df["tf"] == tf)]
                fc = flow[(flow["broker"] == b) & (flow["asset_class"] == c) & (flow["tf"] == tf)]
                labels, usable = int(cell["labelled"].sum()), int(cell["usable"].sum())
                out.append({"broker": b, "asset_class": c, "tf": tf, "candidates": len(cell), "labels": labels,
                            "usable": usable, "conversion": round(usable / labels, 3) if labels else None,
                            "flow_candidates": len(fc), "flow_labels": int(fc["labelled"].sum()),
                            "flow_usable": int(fc["usable"].sum()), "history_rows": 0})
    return out


def gbp_per_r(con: sqlite3.Connection) -> tuple[dict[str, float], float | None]:
    """Median loss at the stop (account money) per arm over its fills, and over all fills (the fallback)."""
    rows = con.execute("SELECT arm_id, json_extract(body, '$.stop_loss') FROM exploration_trades "
                       "WHERE json_extract(body, '$.stop_loss') > 0").fetchall()
    if not rows:
        return {}, None
    df = pd.DataFrame(rows, columns=["arm_id", "stop_loss"])
    per_arm = {str(k): float(v) for k, v in df.groupby("arm_id")["stop_loss"].median().items()}
    return per_arm, float(df["stop_loss"].median())


def influence_effect(con: sqlite3.Connection, df: pd.DataFrame, *, since: pd.Timestamp,
                     until: pd.Timestamp) -> dict[str, Any]:
    """K2: scored / changed shares and the changed decisions' effect in R and money, day-clustered 95 % CI."""
    inf = learning.influence(con, since=since, until=until)
    out: dict[str, Any] = {"candidates": inf.n, "scored": inf.scored, "changed": inf.changed,
                           "scored_pct": round(inf.scored_pct, 2), "changed_pct": round(inf.changed_pct, 2),
                           "effect_r": None, "effect_r_ci": None, "effect_money": None, "effect_money_ci": None}
    win = df[(df["decision_ns"] >= int(since.value)) & (df["decision_ns"] < int(until.value))]
    reason = win["blocked_reason"].fillna("")
    changed = win[reason.str.startswith(learning.MODEL_BLOCK_PREFIXES) & win["labelled"]]
    out["changed_labelled"] = len(changed)
    if changed.empty:
        return out
    per_arm, overall = gbp_per_r(con)
    money_per_r = changed["arm_id"].map(per_arm).fillna(overall if overall is not None else np.nan)
    eff = changed.assign(r=-changed["sim_r"].astype(float), money=-changed["sim_r"].astype(float) * money_per_r)
    for col, key in (("r", "effect_r"), ("money", "effect_money")):
        days = eff.groupby("session_day")[col].sum(min_count=1).dropna()
        if days.empty:
            continue
        total = float(days.sum())
        se = float(np.sqrt(len(days) * days.var(ddof=1))) if len(days) > 1 else None
        out[key] = round(total, 2)
        out[f"{key}_ci"] = [round(total - Z95 * se, 2), round(total + Z95 * se, 2)] if se is not None else None
    return out


def liveness(con: sqlite3.Connection, state_dir: Path, settings: Any, now: pd.Timestamp) -> list[dict[str, Any]]:
    """K4: the learning_liveness check's verdict per learner and watcher."""
    reads = {spec.name: learning.read_learner(con, spec) for spec in learning.LEARNERS}
    n_models, since = learning.models_to_watch(con)
    watchers = learning.watcher_reports(state_dir, settings)
    verdicts = learning.judge_all(reads, settings=settings, now=now, watchers=watchers, models_to_watch=n_models,
                                  models_since=since)
    return [v.model_dump() for v in verdicts]


def _features(con: sqlite3.Connection, broker: str, arm_id: str) -> pd.DataFrame:
    """The arm's candidates with an admitted snapshot: decision time, side and the self-retrain FEATURES."""
    rows = con.execute(
        "SELECT o.decision_ns, o.side, v.features FROM exploration_obs o "
        "JOIN exploration_feature_snapshots s ON s.candidate_id = o.candidate_id "
        "JOIN exploration_feature_vectors v ON v.sha256 = s.vector_sha256 "
        "WHERE o.broker = ? AND o.arm_id = ? AND s.status = 'ok' "
        "AND (s.pit = 'written' OR (s.pit = 'nominal' AND s.decision_ns >= ?))",
        (broker, arm_id, int(sr.NOMINAL_FROM.value))).fetchall()
    if not rows:
        return pd.DataFrame(columns=["decision_ns", "side", *sr.FEATURES])
    vals = pd.DataFrame([json.loads(f) for *_, f in rows]).reindex(columns=list(sr.FEATURES))
    head = pd.DataFrame([r[:2] for r in rows], columns=["decision_ns", "side"])
    return pd.concat([head, vals], axis=1)


def model_drift(con: sqlite3.Connection, settings: Any) -> list[dict[str, Any]]:
    """PSI of inputs and scores for every self-retrain model in shadow or active (training rows vs later rows)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'self_retrain_models'").fetchone():
        return []
    q = ", ".join("?" for _ in learning.WATCH_STATUSES)
    models = con.execute(f"SELECT model_version, broker, arm_id, status, cutoff_ns, model FROM self_retrain_models "
                         f"WHERE status IN ({q})", learning.WATCH_STATUSES).fetchall()
    out: list[dict[str, Any]] = []
    for version, broker, arm_id, status, cutoff_ns, text in models:
        rows = _features(con, broker, arm_id)
        model = sr.LogitModel.model_validate_json(text)
        ref, live = rows[rows["decision_ns"] <= cutoff_ns], rows[rows["decision_ns"] > cutoff_ns]
        d = drift.scored_model_drift(version, sr.design(ref), sr.design(live), model.score, settings.drift)
        out.append({"broker": broker, "arm_id": arm_id, "status": status, **d.model_dump()})
    return out


def build(state_dir: Path, *, until: pd.Timestamp, days: int) -> dict[str, Any]:
    settings = load_settings()
    since = until - pd.Timedelta(days=days)
    con = connect(state_dir)
    try:
        df = candidates(con, until)
        return {"until": until.isoformat(), "days": days, "k1_usable_rows": usable_rows(df, since=since),
                "k2_influence": influence_effect(con, df, since=since, until=until),
                "k4_liveness": liveness(con, state_dir, settings, until), "drift": model_drift(con, settings),
                "history_rows_note": "0: no history-pretrain store yet (ladder L1/L3; plan W1-E, W3-2)"}
    finally:
        con.close()


def markdown(r: dict[str, Any]) -> str:
    """The #34 top lines: K1 totals and the class x tf matrix of usable stock, K2, K4, drift."""
    k1 = pd.DataFrame(r["k1_usable_rows"])
    lines = [f"## Learning KPIs to {r['until']} (flow: last {r['days']} d)", ""]
    for b, g in k1.groupby("broker"):
        lab, use = int(g["labels"].sum()), int(g["usable"].sum())
        conv = f"{100 * use / lab:.1f} %" if lab else "-"
        lines.append(f"- K1 {b}: {use} usable of {lab} labels ({conv}); flow {int(g['flow_usable'].sum())} usable "
                     f"of {int(g['flow_labels'].sum())} labels; history rows 0")
        m = g.pivot_table(index="asset_class", columns="tf", values="usable", aggfunc="sum").reindex(
            index=list(ASSET_CLASS_ORDER), columns=list(TIMEFRAMES)).fillna(0).astype(int)
        lines += ["", f"usable rows (stock), {b}:", "", "| class | " + " | ".join(TIMEFRAMES) + " |",
                  "|---|" + "---:|" * len(TIMEFRAMES)]
        lines += [f"| {c} | " + " | ".join(str(v) for v in row) + " |" for c, row in m.iterrows()]
        lines.append("")
    k2 = r["k2_influence"]
    money = "-" if k2["effect_money"] is None else f"{k2['effect_money']:+.2f} (95 % CI {k2['effect_money_ci']})"
    lines.append(f"- K2 influence: {k2['scored_pct']} % scored, {k2['changed_pct']} % changed of {k2['candidates']} "
                 f"candidates; effect of changed decisions {money}")
    dead = [v for v in r["k4_liveness"] if v["status"] != "ok"]
    lines.append(f"- K4 liveness: {len(dead)} of {len(r['k4_liveness'])} learners/watchers dead or off")
    lines += [f"  - {v['status'].upper()} {v['reason']}" for v in dead]
    for d in r["drift"]:
        top = ", ".join(f"{k} {v}" for k, v in sorted(d["psi"].items(), key=lambda kv: -kv[1])[:3]) or "-"
        lines.append(f"- drift {d['broker']} {d['arm_id']} ({d['status']}): score PSI {d['psi_score']}, top inputs "
                     f"{top}; {'; '.join(d['notes'])}")
    if not r["drift"]:
        lines.append("- drift: no self-retrain model in shadow or active")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--state-dir", type=Path, default=Path("state"))
    ap.add_argument("--to", default=None, help="UTC end (default now; the Friday 21:00 cutoff for the report)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    until = pd.Timestamp(a.to, tz="UTC") if a.to else pd.Timestamp.now("UTC")
    r = build(a.state_dir, until=until, days=a.days)
    sys.stdout.write((json.dumps(r, indent=1, default=str) if a.json else markdown(r)) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
