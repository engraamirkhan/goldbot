"""D-12-xt gate-read verifier (quant F3 / R2 on PR #525), a leaf module: stdlib and pandas only, plus
`goldbot.config.ROOT`. `sr_v3.rho_inputs` sits on the obj-v1 guarded fit path (`objective.GUARDED_MODULES`), whose
static guard follows every import, lazy ones included; `data_sufficiency` reaches `dense_labels` and from there
banned metrics, so the verifier lives here and both import it. The hold map is the dl-v1 H_BY_TF in seconds (a
test ties it to `data_sufficiency.LABEL_H_S`)."""
from __future__ import annotations

from typing import Any

H_S: dict[str, int] = {"1m": 3_600, "5m": 14_400, "15m": 28_800, "30m": 57_600, "1h": 86_400, "2h": 172_800,
                       "4h": 432_000, "1d": 864_000, "1w": 1_728_000}


def hold_bucket(tf: str) -> str:
    h = H_S[tf]
    return "le60m" if h <= 3_600 else ("1h-4h" if h <= 14_400 else "gt4h")


def verify_inst_by_tf(full: dict[str, Any], path: object, tol: float = 1e-4) -> None:
    """Quant F3 on #525: a gate read recomputes, from the file's own cells file (sha256 checked), the re-run day-mean
    rho_u,inst per class x tf (max cross_agg.hi95 over the non-`both_gross` variants, capped at 1, cumulative max over
    holds <= the tf) and asserts `binding_by_tf` == max(that, the D-12 bucket binding of the `binds_with` file, sha256
    checked). ValueError names the first cell that differs."""
    import hashlib
    import json
    import math

    import pandas as pd

    from goldbot.config import ROOT
    bt = (full.get("binding_by_tf") or {}).get("rho_u_inst_day_mean")
    cf, bw = full.get("cells_file") or {}, full.get("binds_with") or {}
    if not bt or not cf.get("path") or not bw.get("path"):
        raise ValueError(f"rho file {path!s}: a gate read needs binding_by_tf, cells_file and binds_with (#525 F3)")
    for what, rel, sha in (("cells_file", cf["path"], cf.get("sha256")), ("binds_with", bw["path"], bw.get("sha256"))):
        f = ROOT / str(rel)
        if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != sha:
            raise ValueError(f"rho file {path!s}: {what} {rel!r} is missing or differs from its sha256")
    d12 = json.loads((ROOT / str(bw["path"])).read_text())["binding"]["rho_u_inst_day_mean"]
    t = pd.read_csv(ROOT / str(cf["path"]), compression="gzip", usecols=["class", "tf", "outcome", "cross_agg.hi95"])
    t = t[(t["outcome"] != "both_gross") & t["cross_agg.hi95"].notna()]
    best = t.groupby(["class", "tf"])["cross_agg.hi95"].max()
    for cls in sorted(set(k.split("|")[0] for k in bt)):
        cum = -math.inf
        for tf in H_S:
            k = f"{cls}|{tf}"
            if (cls, tf) in best.index:
                cum = max(cum, min(1.0, float(best[(cls, tf)])))
            if k not in bt:
                continue
            want = max(cum, float((d12.get(f"{cls}|{hold_bucket(tf)}") or {}).get("max", -math.inf)))
            got = float(bt[k]["max"])
            if not abs(min(1.0, want) - got) <= tol:
                raise ValueError(f"rho file {path!s}: binding_by_tf rho_u_inst {k} = {got}, recomputed {want} (#525 F3)")


__all__ = ["H_S", "hold_bucket", "verify_inst_by_tf"]
