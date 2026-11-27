"""Run records of the history models SEQ-1 (family A), its embedding (A, a zoo-v2 input block) and RL-X1 (D), in the
one format of record: `run_manifest` (ADR 0106 decision 7, ADR 0109: "any past model can be rebuilt exactly from its
logged data version, settings, seed and code sha").

Each fit goes through a builder (`build_seq1`, `build_seq1_embed`, `build_rlx1`: pure functions of a
`run_manifest.BuildSpec` returning the artifact's exact bytes) and `run_manifest.fit_and_record`, so the recorded
artifact is the one `run_manifest.rebuild` reproduces. The design the fit reads is written once as an .npz under the
caller's data root (local, never committed: ADR 0048) and enters the manifest with its sha256. Every record names
its run by a `manifest_key` fixed before the fit; the seeds are `seed_list(manifest_key)` (`seq1_spec`, `embed_spec`;
quant Q1 on #494), SEQ-1 serves the seeds' ensemble and the embedding and RL-X1 seed index 0, and `label_end_max`
is the training rows' last horizon end (Q4).

Serving verdicts (`model_quality`, ADR 0109): SEQ-1's served form is the seed bundle; `seq1_quality` turns a
walk-forward read into `seed_stability` (every seed's obj-v1 S > 0, >= 5 seeds) and `beats_baseline` against the GBT
run manifest on the same judged rows, after costs (ADR 0106 3). The embedding and RL-X1 record no verdict, so
`require_servable` refuses both: the embedding is an input block (quant R-c), RL-X1 is never live.

Designs and episodes are learn-window reads (learn_labels); a file whose rows reach the sealed holdout is refused at
load (`holdout_guard.refuse`), so a hand-edited or mis-built design can never feed a fit or a rebuild.

This module is the only one that imports run_manifest / model_quality for these models: seq1 stays on the obj-v1
guarded list (objective.GUARDED_MODULES), which model_quality's import of model_zoo would break.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data import holdout_guard
from goldbot.data.timeutil import epoch_ns
from goldbot.research import model_quality as MQ
from goldbot.research import rl_exits as X
from goldbot.research import run_manifest as RM
from goldbot.research import seq1 as S
from goldbot.research import seq1_embed as E
from goldbot.research.rl_sim import SimCosts

KEY_STR = ("instrument", "tf", "judged_tf")
KEY_TS = ("decision_utc", "window_from_utc", "horizon_end_utc")


# ---------------------------------------------------------------------------------------------- designs on disk
def design_sha(data: S.Seq1Data) -> str:
    """sha256 of the design file's content (arrays and keys), for its file name."""
    import hashlib
    h = hashlib.sha256()
    for a in (data.X, data.y, data.w):
        h.update(np.ascontiguousarray(a).tobytes())
    h.update(data.keys.to_csv(index=False).encode())
    return h.hexdigest()


def save_design(data: S.Seq1Data, path: Path | str) -> Path:
    """The design as an .npz (no pickles): arrays, string keys, UTC keys as int64 ns."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    k = data.keys
    arrays: dict[str, Any] = {"X": data.X, "y": data.y, "w": data.w}
    arrays |= {f"k_{c}": k[c].astype(str).to_numpy(dtype=str) for c in KEY_STR}
    arrays |= {f"t_{c}": epoch_ns(pd.DatetimeIndex(pd.to_datetime(k[c], utc=True))) for c in KEY_TS if c in k.columns}
    with open(p, "wb") as fh:
        np.savez(fh, **arrays)
    return p


def load_design(path: Path | str) -> S.Seq1Data:
    with np.load(path, allow_pickle=False) as z:
        keys = pd.DataFrame({c: z[f"k_{c}"].astype(str) for c in KEY_STR})
        for c in KEY_TS:
            if f"t_{c}" in z.files:
                keys[c] = pd.to_datetime(z[f"t_{c}"], unit="ns", utc=True)
                holdout_guard.refuse(keys, what=f"history design {Path(path).name}", col=c)
        return S.Seq1Data(X=z["X"], y=z["y"], w=z["w"], keys=keys)


def save_episodes(eps: Sequence[X.Episode], path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lens = np.array([len(e) for e in eps], dtype=np.int64)
    arrays: dict[str, Any] = {f: np.concatenate([getattr(e, f) for e in eps]) if eps else np.zeros(0)
                              for f in ("open_r", "high_r", "low_r", "close_r", "swap_r")}
    arrays |= {"lens": lens, "k": np.array([e.k for e in eps]), "weight": np.array([e.weight for e in eps]),
               "day": np.array([e.day for e in eps], dtype=str)}
    with open(p, "wb") as fh:
        np.savez(fh, **arrays)
    return p


def load_episodes(path: Path | str) -> list[X.Episode]:
    with np.load(path, allow_pickle=False) as z:
        holdout_guard.refuse(pd.to_datetime(z["day"].astype(str), utc=True), what=f"history episodes {Path(path).name}")
        ends = np.cumsum(z["lens"])
        starts = ends - z["lens"]
        return [X.Episode(**{f: z[f][a:b] for f in ("open_r", "high_r", "low_r", "close_r", "swap_r")},
                          k=float(z["k"][i]), weight=float(z["weight"][i]), day=str(z["day"][i]))
                for i, (a, b) in enumerate(zip(starts, ends, strict=True))]


def _one(spec: RM.BuildSpec) -> str:
    if len(spec.data_files) != 1:
        raise ValueError("a history-model build reads exactly one design file")
    return next(iter(spec.data_files.values()))


# ---------------------------------------------------------------------------------------------- builders
def build_seq1(spec: RM.BuildSpec) -> bytes:
    """The SEQ-1 seed bundle, one artifact per manifest seed."""
    sp = S.Seq1Spec.model_validate(spec.settings["spec"]).model_copy(update={"seeds": tuple(spec.seeds)})
    return S.bundle_bytes(S.fit_bundle(load_design(_one(spec)), sp))


def build_seq1_embed(spec: RM.BuildSpec) -> bytes:
    sp = E.EmbedSpec.model_validate(spec.settings["spec"])
    return E.fit(load_design(_one(spec)), seed=spec.seeds[0], spec=sp).model_dump_json().encode()


def build_rlx1(spec: RM.BuildSpec) -> bytes:
    sp = X.RlSpec.model_validate(spec.settings["spec"]).model_copy(update={"seed": spec.seeds[0]})
    costs = SimCosts.model_validate(spec.settings["costs"])
    eps = load_episodes(_one(spec))
    return X.fit(X.transitions(eps, sp, costs), sp, n_episodes=len(eps)).model_dump_json().encode()


# ---------------------------------------------------------------------------------------------- fit and record
def _ends(data: S.Seq1Data) -> tuple[pd.Timestamp, pd.Timestamp]:
    """(train_end, scaler_fit_end): the last label end the fit used, and the last decision whose window it read (the
    inputs are normalised inside each window, so no transform sees past a decision)."""
    return (pd.Timestamp(pd.DatetimeIndex(data.keys["horizon_end_utc"]).max()),
            pd.Timestamp(pd.DatetimeIndex(data.keys["decision_utc"]).max()))


def seq1_spec(manifest_key: str, spec: S.Seq1Spec = S.SPEC) -> S.Seq1Spec:
    """`spec` with its seeds set by the run's key (quant Q1 on #494: "Seeds are the deterministic list
    `seed_list(manifest_key)` ... fixed before the first fit"): the walk-forward read and the recorded fit use it, so
    seed_stability covers exactly the manifest's seeds."""
    return spec.model_copy(update={"seeds": tuple(MQ.seed_list(manifest_key, len(spec.seeds)))})


def embed_spec(manifest_key: str, spec: E.EmbedSpec = E.SPEC) -> E.EmbedSpec:
    """`spec` with its base seeds set by the run's key (Q1); the served embedding is seed index 0 (quant R-c)."""
    return spec.model_copy(update={"base": seq1_spec(manifest_key, spec.base)})


def record_seq1(data: S.Seq1Data, *, manifest_key: str, data_root: Path | str, data_version: str, root: Path | str,
                spec: S.Seq1Spec = S.SPEC, unit: str = "", fold_keys: Sequence[str] = (),
                quality: Sequence[MQ.QualityVerdict] = (), results: Mapping[str, Any] | None = None,
                repo: Path | str = RM.ROOT, schedule: RM.Schedule = "adhoc") -> tuple[S.Seq1Bundle, RM.RunManifest]:
    """Fit and record the SEQ-1 seed bundle; it serves as the seeds' ensemble (declared before the fit)."""
    sp = seq1_spec(manifest_key, spec)
    f = save_design(data, Path(data_root) / f"seq1-design-{design_sha(data)[:16]}.npz")
    train_end, scaler_end = _ends(data)
    raw, m = RM.fit_and_record("goldbot.research.history_runs:build_seq1", model="seq1:tcn", family="A",
                               data_version=data_version, data_root=data_root, data_files=[f.name],
                               settings={"spec": sp.model_dump(mode="json")}, manifest_key=manifest_key,
                               served="ensemble", seeds=list(sp.seeds), train_end=train_end,
                               scaler_fit_end=scaler_end, label_end_max=train_end, unit=unit, fold_keys=fold_keys,
                               results=results, quality=quality, schedule=schedule, spec_id=sp.spec_id, root=root,
                               repo=repo)
    return S.load_bundle(raw), m


def record_seq1_embed(data: S.Seq1Data, *, manifest_key: str, data_root: Path | str, data_version: str,
                      root: Path | str, spec: E.EmbedSpec = E.SPEC, results: Mapping[str, Any] | None = None,
                      repo: Path | str = RM.ROOT) -> tuple[E.EmbedArtifact, RM.RunManifest]:
    """Fit and record the served embedding: seed index 0 of `seed_list(manifest_key)` (no selection among seeds)."""
    sp = embed_spec(manifest_key, spec)
    f = save_design(data, Path(data_root) / f"seq1-embed-design-{design_sha(data)[:16]}.npz")
    train_end, scaler_end = _ends(data)
    raw, m = RM.fit_and_record("goldbot.research.history_runs:build_seq1_embed", model="seq1:embed", family="A",
                               data_version=data_version, data_root=data_root, data_files=[f.name],
                               settings={"spec": sp.model_dump(mode="json")}, manifest_key=manifest_key,
                               served="seed0", seeds=[sp.base.seeds[0]], train_end=train_end,
                               scaler_fit_end=scaler_end, label_end_max=train_end, results=results,
                               spec_id=sp.spec_id, root=root, repo=repo)
    return E.load(raw.decode()), m


def record_rlx1(eps: Sequence[X.Episode], *, manifest_key: str, train_end: pd.Timestamp, data_root: Path | str,
                data_version: str, root: Path | str, spec: X.RlSpec = X.SPEC, costs: SimCosts | None = None,
                results: Mapping[str, Any] | None = None, repo: Path | str = RM.ROOT
                ) -> tuple[X.ExitPolicy, RM.RunManifest]:
    """`train_end`: the last horizon end of the training episodes (the caller's entries), also their
    `label_end_max`. The policy's seed (shuffled-state control, bootstrap) is seed_list(manifest_key)[0] (Q1)."""
    import hashlib
    sp = spec.model_copy(update={"seed": MQ.seed_list(manifest_key, 1)[0]})
    tmp = save_episodes(eps, Path(data_root) / "rlx1-episodes.tmp.npz")
    name = f"rlx1-episodes-{hashlib.sha256(tmp.read_bytes()).hexdigest()[:16]}.npz"
    f = tmp.replace(Path(data_root) / name)
    c = costs or SimCosts.w2()
    raw, m = RM.fit_and_record("goldbot.research.history_runs:build_rlx1", model="rlx1:fqi", family="D",
                               data_version=data_version, data_root=data_root, data_files=[f.name],
                               settings={"spec": sp.model_dump(mode="json"), "costs": c.model_dump(mode="json")},
                               manifest_key=manifest_key, served="seed0", seeds=[sp.seed], train_end=train_end,
                               scaler_fit_end=train_end, label_end_max=train_end, results=results,
                               spec_id=sp.spec_id, root=root, repo=repo)
    return X.load(raw.decode()), m


# ---------------------------------------------------------------------------------------------- serving verdicts
def seq1_quality(rd: S.Seq1Read, rows: pd.DataFrame, *, gbt_net: pd.Series | np.ndarray, gbt_manifest_id: str,
                 horizon_days: pd.Series | np.ndarray, train_end: pd.Timestamp, fold_keys: Sequence[str],
                 trial: int) -> list[MQ.QualityVerdict]:
    """The bundle's verdicts from a learn-window walk-forward OOF read (quant Q2/Q3 on #494): seed stability on each
    seed's obj-v1 S, and the paired after-cost comparison with the unit's GBT baseline on the same judged rows
    (`gbt_net`: the GBT's net R per row, 0 where it does not keep; `horizon_days`: each row's label horizon in session
    days; `train_end`, `fold_keys`: the folds both were fitted on; `trial`: the registry trial booked for this read).
    Both carry the rows_key of the same rows. The beats_baseline pass is unadjusted: `MQ.holm_adjust` over the unit's
    A, B and D reads of the cycle sets the verdict that serves."""
    idx = pd.RangeIndex(len(rows))
    net = rows["net_r"].to_numpy(dtype=float)
    model_net = pd.Series(np.where(rd.keep_ensemble, net, 0.0), index=idx)
    base = pd.Series(np.asarray(gbt_net, dtype=float), index=idx)
    days = pd.Series(pd.DatetimeIndex(pd.to_datetime(rows["day"], utc=True)).strftime("%Y-%m-%d"), index=idx)
    hz = pd.Series(np.asarray(horizon_days, dtype=float), index=idx)
    return [MQ.seed_stability(dict(zip(rd.seeds, rd.s_by_seed, strict=True)), rows=MQ.rows_key(idx)),
            MQ.beats_baseline(model_net, base, days, hz, baseline_manifest_id=gbt_manifest_id, train_end=train_end,
                              fold_keys=fold_keys, trial=trial)]
