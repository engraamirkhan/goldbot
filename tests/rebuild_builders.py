"""Reference builders for the run-manifest rebuild tests (ADR 0106 7: "any past model can be rebuilt exactly from its
logged data version, settings, seed and code sha"): a tiny LightGBM and a tiny torch-free MLP, each a pure function of
its `BuildSpec` (data files, settings, seeds) that returns the artifact's bytes. A trainer's builder follows the same
shape: everything the fit reads comes through the spec, and the bytes are serialised without timestamps or zip
headers, so the same inputs give the same sha256.
"""
from __future__ import annotations

import io
import warnings

import numpy as np
import pandas as pd

from goldbot.research.run_manifest import BuildSpec


def _xy(spec: BuildSpec) -> tuple[pd.DataFrame, np.ndarray]:
    df = pd.read_csv(spec.data_files["rows.csv"])
    return df[["x1", "x2", "x3"]], df["y"].to_numpy(int)


def tiny_lightgbm(spec: BuildSpec) -> bytes:
    import lightgbm as lgb
    X, y = _xy(spec)
    params = {"objective": "binary", "num_leaves": int(spec.settings["num_leaves"]), "learning_rate": 0.1,
              "bagging_fraction": 0.8, "bagging_freq": 1, "feature_fraction": 0.9, "seed": spec.seeds[0],
              "deterministic": True, "force_row_wise": True, "num_threads": 1, "verbose": -1}
    booster = lgb.train(params, lgb.Dataset(X, label=y), num_boost_round=int(spec.settings["rounds"]))
    return booster.model_to_string().encode()


def tiny_mlp(spec: BuildSpec) -> bytes:
    from sklearn.neural_network import MLPClassifier
    X, y = _xy(spec)
    clf = MLPClassifier(hidden_layer_sizes=(int(spec.settings["hidden"]),), random_state=spec.seeds[0],
                        max_iter=int(spec.settings["max_iter"]), alpha=float(spec.settings["weight_decay"]))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")      # ConvergenceWarning on a tiny fit is expected
        clf.fit(X.to_numpy(float), y)
    buf = io.BytesIO()
    for a in [*clf.coefs_, *clf.intercepts_]:
        x = np.ascontiguousarray(a, dtype="<f8")
        buf.write(f"{x.shape}".encode())
        buf.write(x.tobytes())
    return buf.getvalue()


def drifting(spec: BuildSpec) -> bytes:
    """A builder whose output changes on every call, in this process and in any other (the rebuild script runs in a
    child, whose call counter starts again at 1): the rebuild must report the mismatch."""
    import os
    drifting.calls = getattr(drifting, "calls", 0) + 1     # type: ignore[attr-defined]
    return f"pid {os.getpid()} call {drifting.calls}".encode()   # type: ignore[attr-defined]
