"""SEQ-1's primary path: an outcome-free, self-supervised TCN whose 8-dim embedding enters zoo-v2 as one input block
(quant ruling R-c, 2026-11-26: "zoo-v2 may take SEQ-1's 8-dim embedding as a block only if SEQ-1's pretraining is
outcome-free and the embedding is refit per outer fold on that fold's purged training rows (`feature_store.purge`,
embargo >= TCN window + H), learn 2010-17 only; Riley charges 8 inputs; one block decision, K 1." Plus the A8
parity battery on the 8 dimensions.)

The pretext task, outcome-free by construction: inside each decision's own window (the `seq1.bar_windows` input: the
64 closed bars visible at the decision, nothing later), the causal TCN predicts at every position t the NEXT bar of
the window, t + 1 <= the decision bar: its close move and its range in ATR units. The target bars are window bars
already visible at the decision, so no label, no outcome, no P&L and no bar after the decision enters the fit. The
network is the SEQ-1 trunk (`seq1.build_net(head=False)`), a per-position 8-dim linear bottleneck and a 2-output
decoder; the embedding of a decision is the bottleneck at the window's last position (the decision bar).
Causal convolutions make position t blind to t + 1 (tested in test_seq1).

Fit (ADR 0106: dropout, weight decay, a validation stop on a time-split fold with an embargo gap, >= 5 seeds): the
validation loss is the pretext loss on the last 20 % of the fold's training span (`seq1.inner_split`). Seeds: the
embedding served is the pre-named first seed (no selection among seeds); `seed_stability` reports the mean pairwise
linear CKA of the 5 seeds' embeddings on the same rows (a stability report, not a selector).

Folds (`fold_embed`): one outer fold = the caller's train and test rows. Training rows are purged against the test
rows by `feature_store.purge` on [window_from_utc, horizon_end_utc] with embargo = TCN window x tf + H (the longest
tf in the fold), learn 2010-01..2017-12 only. Each fold refits from scratch; test rows get the fold's embedding.

A8 parity on the 8 dims (`parity`): the same decisions embedded from two inputs (two sources, or batch vs serving)
compared per dimension (max abs diff, NaN-mask difference, correlation). `embed_row` equals `embed` on the batch.
Run log (ADR 0106 7): `run_log_of` / `rebuild` reproduce the artifact sha exactly.
"""
from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any

import numpy as np
import pandas as pd
from pydantic import field_serializer, field_validator

from goldbot.base import FrozenRecord
from goldbot.config import tf_seconds
from goldbot.research import feature_store as F
from goldbot.research import run_log as RL
from goldbot.research import seq1 as S
from goldbot.research import windows as WN
from goldbot.research.dense_labels import H_BY_TF

DIM = 8
R_CLOSE, R_HIGH, R_LOW, VALID = (S.CHANNELS.index(c) for c in ("r_close", "r_high", "r_low", "valid"))


class EmbedSpec(FrozenRecord):
    spec_id: str = "seq1-emb-v1"
    base: S.Seq1Spec = S.SPEC
    dim: int = DIM
    pretext: str = "next-bar close move and range inside the window"


SPEC = EmbedSpec()


# ---------------------------------------------------------------------------------------------- rows (no labels)
def rows(decisions: Mapping[tuple[str, str], pd.Series], bars: Mapping[tuple[str, str], pd.DataFrame],
         spec: EmbedSpec = SPEC) -> S.Seq1Data:
    """Unlabelled design rows: every (instrument, tf) decision with its window; y = 0, w = 1 (never read), keys with
    window_from_utc and horizon_end_utc = decision + H (the label horizon the purge must clear, H_BY_TF)."""
    parts = []
    for (inst, tf), dec in sorted(decisions.items()):
        jt = S.judged_tf(tf, spec.base)
        d = pd.Series(pd.DatetimeIndex(pd.to_datetime(dec, utc=True)))
        end = d + pd.Timedelta(seconds=H_BY_TF[tf])
        lo, hi = WN.SPANS[WN.Window.LEARN]
        if len(d) and (d.max() >= WN.HOLDOUT_FROM or end.max() >= WN.HOLDOUT_FROM):
            raise WN.HoldoutSealed("SEQ-1 embedding: a row reaches the holdout")
        if len(d) and (d.min() < lo or end.max() > hi):
            raise ValueError("SEQ-1 embedding fits learn 2010-01..2017-12 only: a row decides or ends outside learn")
        win, ok = S.bar_windows(bars[(inst, tf)], d, spec.base)
        keys = pd.DataFrame({"instrument": inst, "tf": tf, "judged_tf": jt, "decision_utc": d,
                             "window_from_utc": S.window_start(bars[(inst, tf)], d, spec.base).to_numpy(),
                             "horizon_end_utc": end})
        X = np.concatenate([win, S.tf_channels([jt] * len(d), spec.base)], axis=1)
        parts.append(S.Seq1Data(X=X[ok], y=np.zeros((int(ok.sum()), 2)), w=np.ones((int(ok.sum()), 2)),
                                keys=keys.loc[ok].reset_index(drop=True)))
    if not parts:
        raise ValueError("SEQ-1 embedding: no rows")
    return S.Seq1Data(X=np.concatenate([p.X for p in parts]), y=np.concatenate([p.y for p in parts]),
                      w=np.concatenate([p.w for p in parts]),
                      keys=pd.concat([p.keys for p in parts], ignore_index=True))


def pretext_targets(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(targets (n, window - 1, 2), mask (n, window - 1)): at position t the window's bar t + 1 close move and range;
    masked where bar t or t + 1 is padding. Read from the window only."""
    tgt = np.stack([X[:, R_CLOSE, 1:], X[:, R_HIGH, 1:] - X[:, R_LOW, 1:]], axis=2)
    mask = (X[:, VALID, :-1] > 0) & (X[:, VALID, 1:] > 0)
    return tgt.astype(np.float32), mask.astype(np.float32)


# ---------------------------------------------------------------------------------------------- the network
def build(spec: EmbedSpec = SPEC, *, seed: int) -> Any:
    torch = S.torch_module()
    nn = torch.nn
    Module: Any = nn.Module
    trunk = S.build_net(spec.base, seed=seed, head=False)

    class Ssl(Module):
        def __init__(self) -> None:
            super().__init__()
            self.trunk = trunk
            self.bottleneck = nn.Linear(spec.base.hidden, spec.dim)
            self.decoder = nn.Linear(spec.dim, 2)

        def codes(self, x: Any) -> Any:                  # (n, window, dim), causal
            return self.bottleneck(self.trunk.time_major(x))

        def forward(self, x: Any) -> Any:                # predictions for positions 0 .. window - 2
            return self.decoder(self.codes(x))[:, :-1, :]

        def embed(self, x: Any) -> Any:
            return self.codes(x)[:, -1, :]

    return Ssl()


class EmbedArtifact(FrozenRecord):
    """A frozen SEQ-1 embedding fit: spec, seed, float32 weights and their sha256 (as seq1.Seq1Artifact)."""
    spec: EmbedSpec
    seed: int
    torch_version: str
    names: tuple[str, ...]
    shapes: tuple[tuple[int, ...], ...]
    blob: bytes
    n_train: int
    n_val: int
    best_epoch: int
    val_loss: float
    sha256: str
    outcome_free: bool = True

    @field_serializer("blob")
    def _b64(self, v: bytes) -> str:
        return base64.b64encode(v).decode()

    @field_validator("blob", mode="before")
    @classmethod
    def _unb64(cls, v: Any) -> bytes:
        return base64.b64decode(v) if isinstance(v, str) else bytes(v)

    def compute_sha256(self) -> str:
        return S.weights_sha(self.spec.base, self.seed, (self.spec.spec_id, *self.names), self.shapes, self.blob)


def fit(data: S.Seq1Data, *, seed: int, spec: EmbedSpec = SPEC) -> EmbedArtifact:
    """The pretext fit on `data`'s windows only (y and w are never read): masked squared error, Adam with weight
    decay and dropout, the validation stop of seq1 on the pretext loss."""
    b = spec.base
    fit_rows, val_rows = S.inner_split(data, b)
    if len(fit_rows) == 0 or len(val_rows) == 0:
        raise ValueError("SEQ-1 embedding fit: no fit or no validation rows after the embargo")
    with S.TorchState(b.threads) as torch:
        net = build(spec, seed=seed)
        rng = np.random.default_rng(seed)
        X = torch.from_numpy(np.ascontiguousarray(fit_rows.X, dtype=np.float32))
        T, M = (torch.from_numpy(a) for a in pretext_targets(fit_rows.X))
        Xv = torch.from_numpy(np.ascontiguousarray(val_rows.X, dtype=np.float32))
        Tv, Mv = (torch.from_numpy(a) for a in pretext_targets(val_rows.X))
        opt = torch.optim.Adam(net.parameters(), lr=b.lr, weight_decay=b.weight_decay)
        best: tuple[float, int, tuple[list[str], list[tuple[int, ...]], bytes]] | None = None
        since = 0
        for epoch in range(1, b.max_epochs + 1):
            net.train()
            order = rng.permutation(len(fit_rows))
            for a in range(0, len(order), b.batch):
                ix = torch.from_numpy(order[a:a + b.batch])
                m = M[ix]
                tot = m.sum()
                if float(tot) <= 0:
                    continue
                loss = ((net(X[ix]) - T[ix]) ** 2 * m[:, :, None]).sum() / tot
                opt.zero_grad()
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                vl = float(((net(Xv) - Tv) ** 2 * Mv[:, :, None]).sum() / Mv.sum())
            if best is None or vl < best[0]:
                best, since = (vl, epoch, S.state_bytes(net)), 0
            else:
                since += 1
                if since >= b.patience:
                    break
        version = str(torch.__version__)
    assert best is not None
    val_loss, best_epoch, (names, shapes, blob) = best
    return EmbedArtifact(spec=spec, seed=seed, torch_version=version, names=tuple(names), shapes=tuple(shapes),
                         blob=blob, n_train=len(fit_rows), n_val=len(val_rows), best_epoch=best_epoch,
                         val_loss=val_loss,
                         sha256=S.weights_sha(b, seed, (spec.spec_id, *names), shapes, blob))


def _net_of(art: EmbedArtifact) -> Any:
    torch = S.torch_module()
    net = build(art.spec, seed=art.seed)
    state, off = {}, 0
    for name, shape in zip(art.names, art.shapes, strict=True):
        n = int(np.prod(shape)) if shape else 1
        state[name] = torch.from_numpy(np.frombuffer(art.blob, dtype="<f4", count=n, offset=off * 4)
                                       .reshape(shape).copy())
        off += n
    net.load_state_dict(state)
    net.eval()
    return net


def embed(art: EmbedArtifact, X: np.ndarray, *, chunk: int = 4096) -> np.ndarray:
    """The 8-dim embedding of each window, (n, dim)."""
    with S.TorchState(art.spec.base.threads) as torch:
        net = _net_of(art)
        out = []
        with torch.no_grad():
            for a in range(0, len(X), chunk):
                out.append(net.embed(torch.from_numpy(np.ascontiguousarray(X[a:a + chunk], dtype=np.float32)))
                           .numpy())
    return np.concatenate(out).astype(float) if out else np.zeros((0, art.spec.dim))


def embed_row(art: EmbedArtifact, x: np.ndarray) -> np.ndarray:
    """One window -> (dim,): the serving form, equal to `embed` on the batch (tested)."""
    return embed(art, np.asarray(x)[None])[0]


def load(raw: str) -> EmbedArtifact:
    art = EmbedArtifact.model_validate_json(raw)
    if art.compute_sha256() != art.sha256 or not art.outcome_free:
        raise ValueError("SEQ-1 embedding artifact sha mismatch or not outcome-free")
    return art


# ---------------------------------------------------------------------------------------------- folds
def fold_embargo(data: S.Seq1Data, spec: EmbedSpec = SPEC) -> pd.Timedelta:
    """TCN window x tf + H, the longest over the fold's tfs (R-c: "embargo >= TCN window + H")."""
    tfs = sorted(set(data.keys["tf"].astype(str)))
    return max(pd.Timedelta(seconds=spec.base.window * tf_seconds(t) + H_BY_TF[t]) for t in tfs)


def fold_embed(data: S.Seq1Data, train: np.ndarray, test: np.ndarray, *, spec: EmbedSpec = SPEC,
               seed: int | None = None) -> tuple[EmbedArtifact, np.ndarray, np.ndarray]:
    """One outer fold: purge the training rows against the test rows (feature_store.purge, `fold_embargo`), refit
    from scratch on what is left, embed the test rows. Returns (artifact, kept training mask, test embeddings)."""
    tr, te = np.asarray(train, bool), np.asarray(test, bool)
    if (tr & te).any():
        raise ValueError("fold_embed: a row is in both train and test")
    keep = F.purge(data.keys.loc[tr], data.keys.loc[te], label_end="horizon_end_utc", embargo=fold_embargo(data, spec))
    kept = tr.copy()
    kept[np.flatnonzero(tr)[~keep]] = False
    art = fit(data.take(kept), seed=spec.base.seeds[0] if seed is None else seed, spec=spec)
    return art, kept, embed(art, data.X[te])


# ---------------------------------------------------------------------------------------------- reports
def linear_cka(a: np.ndarray, b: np.ndarray) -> float:
    """Linear CKA (Kornblith et al. 2019) of two embeddings of the same rows: 1 = the same up to rotation and scale."""
    a = a - a.mean(axis=0)
    b = b - b.mean(axis=0)
    num = float(np.linalg.norm(a.T @ b) ** 2)
    den = float(np.linalg.norm(a.T @ a) * np.linalg.norm(b.T @ b))
    return num / den if den > 0 else 0.0


class SeedStability(FrozenRecord):
    seeds: tuple[int, ...]
    shas: tuple[str, ...]
    pairwise_cka: tuple[float, ...]
    mean_cka: float


def seed_stability(data: S.Seq1Data, X_eval: np.ndarray, spec: EmbedSpec = SPEC) -> SeedStability:
    """Fit every seed on `data`, embed `X_eval`, report the pairwise linear CKA (ADR 0106: "Results stable across at
    least 5 random seeds"). The served embedding stays the first seed's."""
    arts = [fit(data, seed=s, spec=spec) for s in spec.base.seeds]
    embs = [embed(a, X_eval) for a in arts]
    pairs = [linear_cka(embs[i], embs[j]) for i in range(len(embs)) for j in range(i + 1, len(embs))]
    return SeedStability(seeds=spec.base.seeds, shas=tuple(a.sha256 for a in arts), pairwise_cka=tuple(pairs),
                         mean_cka=float(np.mean(pairs)) if pairs else float("nan"))


class Parity(FrozenRecord):
    max_abs_diff: tuple[float, ...]       # per dimension
    nan_mask_diff: tuple[int, ...]
    corr: tuple[float, ...]


def parity(art: EmbedArtifact, X_a: np.ndarray, X_b: np.ndarray) -> Parity:
    """A8 parity on the 8 dims: the same decisions embedded from input a and input b (two sources, or the history
    store and the serving path)."""
    ea, eb = embed(art, X_a), embed(art, X_b)
    na, nb = np.isnan(ea), np.isnan(eb)
    both = ~(na | nb)
    mad, corr = [], []
    for j in range(ea.shape[1]):
        m = both[:, j]
        mad.append(float(np.abs(ea[m, j] - eb[m, j]).max()) if m.any() else 0.0)
        sa, sb = ea[m, j].std(), eb[m, j].std()
        corr.append(float(np.corrcoef(ea[m, j], eb[m, j])[0, 1]) if m.sum() > 1 and sa > 0 and sb > 0 else 1.0)
    return Parity(max_abs_diff=tuple(mad), nan_mask_diff=tuple(int(x) for x in (na != nb).sum(axis=0)),
                  corr=tuple(corr))


# ---------------------------------------------------------------------------------------------- run log, rebuild
CODE_FILES: tuple[str, ...] = ("goldbot/research/seq1_embed.py", *S.CODE_FILES)


def run_log_of(art: EmbedArtifact, data: S.Seq1Data, results: Mapping[str, Any] | None = None) -> RL.RunLog:
    return RL.new(model="seq1-embed", spec_id=art.spec.spec_id, data=S.data_version(data),
                  settings=art.spec.model_dump(mode="json"), seed=art.seed, code=RL.code_sha(CODE_FILES),
                  artifact_sha=art.sha256, results={"best_epoch": art.best_epoch, "val_loss": art.val_loss,
                                                    "torch_version": art.torch_version, **(results or {})})


def rebuild(log: RL.RunLog, data: S.Seq1Data) -> EmbedArtifact:
    RL.check_inputs(log, data=S.data_version(data), code=RL.code_sha(CODE_FILES))
    art = fit(data, seed=log.seed, spec=EmbedSpec.model_validate(log.settings))
    RL.check_artifact(log, art.sha256)
    return art


__all__ = ["DIM", "SPEC", "EmbedArtifact", "EmbedSpec", "Parity", "SeedStability", "build", "embed", "embed_row",
           "fit", "fold_embargo", "fold_embed", "linear_cka", "load", "parity", "pretext_targets", "rebuild", "rows",
           "run_log_of", "seed_stability"]
