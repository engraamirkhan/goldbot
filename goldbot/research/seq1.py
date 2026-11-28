"""SEQ-1: the pooled temporal convolutional network over raw bar windows (ML plan 2026-11-25, SYNTH-ml ruling R6 and
item 17: "SEQ-1 pooled TCN (about 60k params, L = 64), judged at 1h-1d, 1w pooled into 1d"; "Seed SD < MIN_EDGE/2,
otherwise INCONCLUSIVE. A null retires the sequence family."). Conditional on R6 ("it runs only if the quant rules
that ... count as new information under R-20, and the data, per-instrument cost and torch dependencies are in
place"): this module is the code; no K is booked and no label is read here.

Quant ruling R-c (2026-11-26): "zoo-v2 may take SEQ-1's 8-dim embedding as a block only if SEQ-1's pretraining is
outcome-free and the embedding is refit per outer fold on that fold's purged training rows ... An embedding trained
or fine-tuned on labels needs nested cross-fitting and its own card; it is not admitted here." The primary path is
therefore `seq1_embed` (self-supervised, outcome-free). The label-supervised head below (`fit`, `read`) is a separate
shadow-only model that needs its own card before any read; it shares the windows and the trunk.

The card, as coded:
- Input (`bar_windows`): the last `window` (64) closed decision bars visible at the decision (`visible_at` <=
  decision_utc; a bar still open at the decision never enters, a bar after it can not), per bar the open, high, low and
  close as log moves from the previous bar's close, in units of the ATR share at the decision (Wilder ATR14 of mid,
  known at the decision bar's close), clipped at +-10, plus a `valid` channel (0 on left padding). Bid/ask bars are
  read at mid. Constant channels one-hot the judged tf (`judged_tf`: 1w pools into 1d). Sub-hour timeframes are
  refused (R11: "No judged direction model at any tf whose break-even IC exceeds the best replicated IC").
- Network (`build_net`): causal 1-D convolutions only (left padding, dilations 1..32, kernel 3, residual blocks,
  40 channels; `param_count` about 58k), the last position's state -> two outputs, E[gross R | x] for a long and a
  short under the judged head T2. Every output at position t reads positions <= t (tested).
- Objective (obj-v1, ADR 0098 R-I, `objective.py`): "The learner predicts E[gross R | x] (clipped at +-10 R;
  uniqueness weights only, squared-error loss ...), the cost of record cost(x) enters as a known offset, and a
  candidate is kept iff E[gross R | x] - cost(x) > 0" (`objective.keep_rule`). Fixed recipe, no grid: Adam at a
  fixed rate, fixed batch, weight decay and dropout.
- Regularisation (ADR 0106, "Regularise deep models (dropout, early stopping, weight decay) and train several seeds";
  "Split by time, never randomly, with a gap between train, validation and test periods"): the fit holds out the last
  `val_share` of its own training span by time (`inner_split`, the same purge and embargo as `split`) and keeps the
  epoch with the lowest validation loss, stopping after `patience` epochs without improvement (`max_epochs` cap).
  The validation loss is the obj-v1 training loss itself (uniqueness-weighted squared error on clipped gross R), never
  AUC, accuracy or a library default; this is how ADR 0106's early stopping meets ADR 0098's ban on "library defaults
  that select on their own metric" (for the quant to confirm on the card).
- Normalisation from past data only (ADR 0106): every input is scaled inside its own window by the ATR known at the
  decision; nothing is fitted across rows, so no future row moves a past input.
- Served form and baseline (ADR 0106 3: "Every deep model (A, B) must beat the gradient-boosted baseline (C) out of
  sample after costs or it is not deployed"; ADR 0109): the served model is the seed ensemble (`Seq1Bundle`, mean
  prediction); `read` returns its keep on the judged rows, and `history_runs.seq1_quality` turns the read into
  `model_quality.seed_stability` and `model_quality.beats_baseline` (against the GBT run manifest) verdicts for its
  run manifest (`run_manifest`, one record format). This module imports neither: it stays on the obj-v1 guarded list.
- Determinism: torch on CPU, `use_deterministic_algorithms`, fixed thread count and seed; the artifact
  (`Seq1Artifact`) holds the float32 weights and their sha256 (over spec, seed, names, shapes and bytes); loading
  recomputes it and refuses a mismatch. `score` and `score_row` agree (tested).
- Seeds (`seed_check`): every `seeds` member is fitted; the per-candidate S of each is read; "seed SD < MIN_EDGE/2,
  otherwise INCONCLUSIVE" with MIN_EDGE = objective.MIN_S_R (0.05 R), the descriptive battery's rule
  (diagnostics.seed_spread, which a guarded module may not import: parity is tested).
- Windows: rows are learn 2010-01..2017-12 only (`design` refuses any decision or label end outside it, the holdout
  first); `split` purges a training label unless it ends max(1 d, its hold) before the test start
  (`windows.label_embargo`). Confirm, test_old and holdout are never read here.

torch is the optional `dl` group (`uv sync --extra dl`, CPU wheels): it is imported inside the functions that need
it, so this module, its numpy half and the core suite run without it.
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd
from pydantic import ConfigDict, field_serializer, field_validator

from goldbot.base import FrozenRecord
from goldbot.data.timeutil import epoch_ns
from goldbot.research import gate_cost as COR
from goldbot.research import objective as O
from goldbot.research import windows as WN

SPEC_ID = "seq1-v1"
CHANNELS: tuple[str, ...] = ("r_open", "r_high", "r_low", "r_close", "valid")
SUB_HOUR = frozenset({"1m", "5m", "15m", "30m"})
POOLED_INTO: dict[str, str] = {"1w": "1d"}
INPUT_CLIP = 10.0


class Seq1Spec(FrozenRecord):
    spec_id: str = SPEC_ID
    objective_id: Literal["obj-v1"] = O.OBJECTIVE_ID
    window: int = 64
    judged_tfs: tuple[str, ...] = ("1h", "2h", "4h", "1d")
    template: str = "T2"                 # dense_labels.SPEC.judged_head
    hidden: int = 40
    kernel: int = 3
    dilations: tuple[int, ...] = (1, 2, 4, 8, 16, 32)
    atr_n: int = 14
    dropout: float = 0.1
    max_epochs: int = 30
    patience: int = 3                    # epochs without a lower validation loss before the fit stops
    val_share: float = 0.2               # the last 20 % of the training span's decisions, after an embargo gap
    batch: int = 512
    lr: float = 1e-3
    weight_decay: float = 1e-4
    seeds: tuple[int, ...] = (20261126, 20261127, 20261128, 20261129, 20261130)
    threads: int = 4
    min_edge: float = O.MIN_S_R


SPEC = Seq1Spec()


def judged_tf(tf: str, spec: Seq1Spec = SPEC) -> str:
    """The judged unit of a timeframe: itself for 1h..1d, 1d for 1w; sub-hour and unknown tfs are refused."""
    if tf in SUB_HOUR:
        raise ValueError(f"SEQ-1 is judged at 1h-1d only (R11): {tf} is refused")
    j = POOLED_INTO.get(tf, tf)
    if j not in spec.judged_tfs:
        raise ValueError(f"SEQ-1 judges {', '.join(spec.judged_tfs)} (1w pooled into 1d), not {tf}; 1h minimum")
    return j


def receptive_field(spec: Seq1Spec = SPEC) -> int:
    """Bars one output reads: 1 + 2 convolutions x (kernel - 1) x each dilation."""
    return 1 + 2 * (spec.kernel - 1) * sum(spec.dilations)


def n_inputs(spec: Seq1Spec = SPEC) -> int:
    return len(CHANNELS) + len(spec.judged_tfs)


# ---------------------------------------------------------------------------------------------- windows (numpy)
def _mid(bars: pd.DataFrame, k: str) -> np.ndarray:
    if k in bars.columns:
        return bars[k].to_numpy(dtype=float)
    return (bars[f"bid_{k}"].to_numpy(dtype=float) + bars[f"ask_{k}"].to_numpy(dtype=float)) / 2


def _ns(ts: pd.Series | pd.DatetimeIndex | Sequence[Any]) -> np.ndarray:
    return epoch_ns(pd.DatetimeIndex(pd.to_datetime(pd.Series(ts) if not isinstance(ts, pd.Series) else ts,
                                                    utc=True)))


def bar_windows(bars: pd.DataFrame, decision_utc: pd.Series, spec: Seq1Spec = SPEC) -> tuple[np.ndarray, np.ndarray]:
    """(windows (n, len(CHANNELS), window) float32, ok (n,)) for one instrument x tf. A decision reads the bars whose
    `visible_at` is at or before it, the last `window` of them; ok is False before the ATR warm-up."""
    b = bars.sort_values("visible_at", kind="stable").reset_index(drop=True)
    vis = _ns(b["visible_at"])
    o, h, lo, c = (_mid(b, k) for k in ("open", "high", "low", "close"))
    prev = np.concatenate(([np.nan], c[:-1]))
    tr = np.fmax(np.fmax(h - lo, np.abs(h - prev)), np.abs(lo - prev))
    atr = pd.Series(tr).ewm(alpha=1 / spec.atr_n, adjust=False, min_periods=spec.atr_n).mean().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        moves = np.stack([np.log(o / prev), np.log(h / prev), np.log(lo / prev), np.log(c / prev)], axis=1)
    dec = _ns(decision_utc)
    last = np.searchsorted(vis, dec, side="right") - 1
    L = spec.window
    out = np.zeros((len(dec), len(CHANNELS), L), dtype=np.float32)
    ok = np.zeros(len(dec), dtype=bool)
    for i, j in enumerate(last):
        if j < 0 or not np.isfinite(atr[j]) or atr[j] <= 0 or c[j] <= 0:
            continue
        scale = atr[j] / c[j]
        a = max(0, j - L + 1)
        m = moves[a:j + 1] / scale
        good = np.isfinite(m).all(axis=1)
        seg = np.where(good[:, None], np.clip(m, -INPUT_CLIP, INPUT_CLIP), 0.0)
        n = len(seg)
        out[i, :4, L - n:] = seg.T
        out[i, 4, L - n:] = good
        ok[i] = True
    return out, ok


def window_start(bars: pd.DataFrame, decision_utc: pd.Series, spec: Seq1Spec = SPEC) -> pd.Series:
    """The open (`ts_utc`) of the first bar a decision's window reads (its `window_from_utc` for the purge)."""
    b = bars.sort_values("visible_at", kind="stable").reset_index(drop=True)
    last = np.searchsorted(_ns(b["visible_at"]), _ns(decision_utc), side="right") - 1
    first = np.clip(last - spec.window, 0, None)        # the window's first bar's previous close is read too
    ts = pd.DatetimeIndex(pd.to_datetime(b["ts_utc"], utc=True))
    return pd.Series(ts[first]).where(last >= 0)


def tf_channels(judged: Sequence[str], spec: Seq1Spec = SPEC) -> np.ndarray:
    """Constant one-hot channels of the judged tf, (n, len(judged_tfs), window) float32."""
    out = np.zeros((len(judged), len(spec.judged_tfs), spec.window), dtype=np.float32)
    for i, t in enumerate(judged):
        out[i, spec.judged_tfs.index(t), :] = 1.0
    return out


class Seq1Data(FrozenRecord):
    """Design of one span: X (n, n_inputs, window), y = clipped gross R (n, 2: long, short), w = the fit weight per
    side (0 where the side has no label), keys (instrument, tf, judged_tf, decision_utc, window_from_utc,
    horizon_end_utc)."""
    X: np.ndarray
    y: np.ndarray
    w: np.ndarray
    keys: pd.DataFrame

    def __len__(self) -> int:
        return len(self.keys)

    def take(self, mask: np.ndarray) -> Seq1Data:
        m = np.asarray(mask, dtype=bool)
        return Seq1Data(X=self.X[m], y=self.y[m], w=self.w[m], keys=self.keys.loc[m].reset_index(drop=True))


def _refuse_outside_learn(labels: pd.DataFrame) -> None:
    d = pd.DatetimeIndex(pd.to_datetime(labels["decision_utc"], utc=True))
    e = pd.DatetimeIndex(pd.to_datetime(labels["horizon_end_utc"], utc=True))
    if len(d) and (d.max() >= WN.HOLDOUT_FROM or e.max() >= WN.HOLDOUT_FROM):
        raise WN.HoldoutSealed("SEQ-1: a label row reaches the holdout (2025-10-01 onward)")
    lo, hi = WN.SPANS[WN.Window.LEARN]
    if len(d) and (d.min() < lo or e.max() > hi):
        raise ValueError("SEQ-1 trains and reads learn 2010-01..2017-12 only: a row decides or ends outside learn")


def design(labels: pd.DataFrame, bars: Mapping[tuple[str, str], pd.DataFrame], spec: Seq1Spec = SPEC) -> Seq1Data:
    """Rows of the judged template (both sides on one window) joined to their (instrument, tf) bars. `labels`: long
    dl-v1 rows with instrument, tf, decision_utc, horizon_end_utc, side, template, gross_r_raw and the fit weight `w`
    (`learn_labels.load_learn`). Rows without a window (warm-up) are dropped."""
    lab = labels[labels["template"].astype(str) == spec.template]
    _refuse_outside_learn(lab)
    parts: list[Seq1Data] = []
    for (inst, tf), g in lab.groupby(["instrument", "tf"], sort=True):
        jt = judged_tf(str(tf), spec)
        wide = g.pivot_table(index=["decision_utc", "horizon_end_utc"], columns="side",
                             values=["gross_r_raw", "w"], aggfunc="first").sort_index()
        dec = pd.Series(wide.index.get_level_values("decision_utc"))
        win, ok = bar_windows(bars[(str(inst), str(tf))], dec, spec)
        y = np.zeros((len(wide), 2))
        w = np.zeros((len(wide), 2))
        for j, side in enumerate((1, -1)):
            if ("gross_r_raw", side) in wide.columns:
                g_r = wide[("gross_r_raw", side)].to_numpy(dtype=float)
                ws = wide[("w", side)].to_numpy(dtype=float)
                fin = np.isfinite(g_r) & np.isfinite(ws)
                y[:, j] = np.where(fin, O.clip_r(np.nan_to_num(g_r)), 0.0)
                w[:, j] = np.where(fin, ws, 0.0)
        ok &= w.sum(axis=1) > 0
        keys = pd.DataFrame({"instrument": str(inst), "tf": str(tf), "judged_tf": jt, "decision_utc": dec,
                             "window_from_utc": window_start(bars[(str(inst), str(tf))], dec, spec).to_numpy(),
                             "horizon_end_utc": wide.index.get_level_values("horizon_end_utc")})
        X = np.concatenate([win, tf_channels([jt] * len(wide), spec)], axis=1)
        parts.append(Seq1Data(X=X[ok], y=y[ok], w=w[ok], keys=keys.loc[ok].reset_index(drop=True)))
    if not parts:
        raise ValueError(f"SEQ-1: no {spec.template} rows")
    return Seq1Data(X=np.concatenate([p.X for p in parts]), y=np.concatenate([p.y for p in parts]),
                    w=np.concatenate([p.w for p in parts]),
                    keys=pd.concat([p.keys for p in parts], ignore_index=True))


def split(data: Seq1Data, at: pd.Timestamp) -> tuple[Seq1Data, Seq1Data]:
    """(train, test): test decides at or after `at`; a training label must end at least max(1 d, its own hold)
    before `at` (`windows.label_embargo`: no still-open label and no day-scale serial dependence crosses the cut)."""
    t = pd.Timestamp(at)
    d = pd.DatetimeIndex(pd.to_datetime(data.keys["decision_utc"], utc=True))
    e = pd.DatetimeIndex(pd.to_datetime(data.keys["horizon_end_utc"], utc=True))
    emb = np.array([WN.label_embargo(x).value for x in (e - d)], dtype=np.int64)
    train = epoch_ns(e) + emb <= t.value
    return data.take(train), data.take(np.asarray(d >= t))


# ---------------------------------------------------------------------------------------------- seeds
class SeedCheck(FrozenRecord):
    s_by_seed: tuple[float, ...]
    sd: float
    threshold: float
    inconclusive: bool


def seed_check(s_by_seed: Sequence[float], *, min_edge: float = O.MIN_S_R) -> SeedCheck:
    """"Seed SD < MIN_EDGE/2, otherwise INCONCLUSIVE" (sample SD over seeds; one seed is never conclusive)."""
    v = np.asarray(list(s_by_seed), dtype=float)
    sd = float(v.std(ddof=1)) if len(v) > 1 else float("nan")
    return SeedCheck(s_by_seed=tuple(float(x) for x in v), sd=sd, threshold=min_edge / 2,
                     inconclusive=not (sd < min_edge / 2))


# ---------------------------------------------------------------------------------------------- the network (torch)
def torch_module() -> Any:
    try:
        import torch
    except ImportError as e:     # the optional dl group
        raise ImportError("SEQ-1 needs torch: uv sync --extra dl (CPU wheels)") from e
    return torch


def build_net(spec: Seq1Spec = SPEC, *, seed: int, head: bool = True) -> Any:
    """The TCN, initialised from `seed`: an input projection, residual blocks of two causal dilated convolutions (left
    padding only) and, with `head`, a linear head on the last position. `net.sequence(x)` returns every position's
    state (n, hidden, window). Internally time-major (n, window, channels) with every convolution written as one
    matrix product over its stacked taps: torch's CPU convolution kernels without oneDNN (macOS arm64 wheels) run
    about 30x slower than the same arithmetic as a matrix product (measured 2026-11-26)."""
    torch = torch_module()
    nn, F = torch.nn, torch.nn.functional
    Module: Any = nn.Module          # torch is optional: typed as Any so mypy runs with or without it
    torch.manual_seed(seed)

    class Causal(Module):
        """y[t] = sum_i W_i x[t - (kernel - 1 - i) d] on time-major input: the dilated taps stacked on the channel
        axis and mixed by one linear map."""

        def __init__(self, c: int, d: int) -> None:
            super().__init__()
            self.d, self.pad = d, (spec.kernel - 1) * d
            self.mix = nn.Linear(spec.kernel * c, c)

        def forward(self, x: Any) -> Any:
            L = x.shape[1]
            xp = F.pad(x, (0, 0, self.pad, 0))
            taps = [xp[:, i * self.d:i * self.d + L, :] for i in range(spec.kernel)]
            return self.mix(torch.cat(taps, dim=2))

    class Block(Module):
        def __init__(self, c: int, d: int) -> None:
            super().__init__()
            self.a, self.b = Causal(c, d), Causal(c, d)
            self.drop = nn.Dropout(spec.dropout)

        def forward(self, x: Any) -> Any:
            return torch.relu(x + self.drop(self.b(self.drop(torch.relu(self.a(x))))))

    class Tcn(Module):
        def __init__(self) -> None:
            super().__init__()
            self.inp = nn.Linear(n_inputs(spec), spec.hidden)
            self.blocks = nn.ModuleList([Block(spec.hidden, d) for d in spec.dilations])
            self.head: Any = nn.Linear(spec.hidden, 2) if head else None

        def time_major(self, x: Any) -> Any:
            z = self.inp(x.transpose(1, 2))
            for blk in self.blocks:
                z = blk(z)
            return z

        def sequence(self, x: Any) -> Any:
            return self.time_major(x).transpose(1, 2)

        def forward(self, x: Any) -> Any:
            return self.head(self.time_major(x)[:, -1, :])

    return Tcn()


def param_count(spec: Seq1Spec = SPEC) -> int:
    return int(sum(p.numel() for p in build_net(spec, seed=0).parameters()))


class Seq1Artifact(FrozenRecord):
    """A frozen SEQ-1 fit: spec, seed, float32 weights (`blob`, in `names` order with `shapes`) and their sha256.
    Loading refuses an artifact whose sha does not match its contents, or another objective id (ADR 0098 4)."""
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True, extra="forbid")
    objective_id: Literal["obj-v1"] = O.OBJECTIVE_ID
    spec: Seq1Spec
    seed: int
    torch_version: str
    names: tuple[str, ...]
    shapes: tuple[tuple[int, ...], ...]
    blob: bytes
    n_train: int
    n_val: int
    n_eff: float
    best_epoch: int
    val_loss: float
    sha256: str

    @field_serializer("blob")
    def _b64(self, v: bytes) -> str:
        return base64.b64encode(v).decode()

    @field_validator("blob", mode="before")
    @classmethod
    def _unb64(cls, v: Any) -> bytes:
        return base64.b64decode(v) if isinstance(v, str) else bytes(v)

    def compute_sha256(self) -> str:
        return weights_sha(self.spec, self.seed, self.names, self.shapes, self.blob)


def weights_sha(spec: Seq1Spec, seed: int, names: Sequence[str], shapes: Sequence[Sequence[int]], blob: bytes) -> str:
    h = hashlib.sha256()
    h.update(spec.model_dump_json().encode())
    h.update(json.dumps({"seed": seed, "names": list(names), "shapes": [list(s) for s in shapes]}).encode())
    h.update(blob)
    return h.hexdigest()


OMP_MODULES: tuple[str, ...] = ("sklearn", "lightgbm", "xgboost")     # each ships its own libomp on macOS


def omp_before_torch(modules: Sequence[str] | None = None) -> list[str]:
    """The OpenMP-linked packages imported before torch, in import order (`sys.modules` keeps it): on macOS each
    bundles its own libomp, and torch's threads started beside another runtime crash the process (measured 2026-11-28:
    SIGSEGV in the first multi-threaded SEQ-1 epoch once feature_store had loaded sklearn and lightgbm). [] when torch
    is not imported yet or came first."""
    order = [m for m in (list(sys.modules) if modules is None else modules) if "." not in m]
    if "torch" not in order:
        return []
    first = order.index("torch")
    return [m for m in order[:first] if m in OMP_MODULES]


class TorchState:
    """Deterministic CPU settings for one fit or score, restored afterwards. More than one thread on macOS after another
    OpenMP runtime (`omp_before_torch`) is refused with the fix named, never left to segfault: entry scripts import
    torch before goldbot (scripts/research/train_seq1_embed.py, rebuild_model.py)."""

    def __init__(self, threads: int) -> None:
        self.torch, self.threads = torch_module(), threads

    def __enter__(self) -> Any:
        if self.threads > 1 and sys.platform == "darwin" and (clash := omp_before_torch()):
            raise RuntimeError(f"SEQ-1 with {self.threads} threads: {', '.join(clash)} loaded its OpenMP runtime "
                               "before torch (macOS: two runtimes crash the process); import torch first in the entry "
                               "script, or fit with threads=1")
        t = self.torch
        self.prev = (t.are_deterministic_algorithms_enabled(), t.get_num_threads())
        t.use_deterministic_algorithms(True)
        t.set_num_threads(self.threads)
        return t

    def __exit__(self, *exc: object) -> None:
        self.torch.use_deterministic_algorithms(self.prev[0])
        self.torch.set_num_threads(self.prev[1])


def inner_split(data: Seq1Data, spec: Seq1Spec = SPEC) -> tuple[Seq1Data, Seq1Data]:
    """(fit rows, validation rows) inside one training span: validation = decisions in the last `val_share` of the
    span's time, fit = rows purged and embargoed before it (`split`)."""
    d = pd.DatetimeIndex(pd.to_datetime(data.keys["decision_utc"], utc=True))
    at = d.min() + (d.max() - d.min()) * (1 - spec.val_share)
    return split(data, at)


def _weights(w: np.ndarray) -> np.ndarray:
    flat = w.reshape(-1).astype(float).copy()
    if (flat > 0).any():
        flat[flat > 0] = O.normalise_weights(flat[flat > 0])
    return flat.reshape(w.shape)


def state_bytes(net: Any) -> tuple[list[str], list[tuple[int, ...]], bytes]:
    names, shapes, chunks = [], [], []
    for name, p in sorted(net.state_dict().items()):
        arr = p.detach().cpu().numpy().astype("<f4")
        names.append(name)
        shapes.append(tuple(int(s) for s in arr.shape))
        chunks.append(arr.tobytes())
    return names, shapes, b"".join(chunks)


def fit(data: Seq1Data, *, seed: int, spec: Seq1Spec = SPEC) -> Seq1Artifact:
    """One seed's fit: weighted squared error on clipped gross R per side (obj-v1 R-I; weights normalised by
    `objective.normalise_weights`), Adam with weight decay and dropout, epochs in a seeded order on the fit rows of
    `inner_split`; the weights of the epoch with the lowest validation loss are kept (patience `patience`)."""
    fit_rows, val_rows = inner_split(data, spec)
    if len(fit_rows) == 0 or len(val_rows) == 0:
        raise ValueError("SEQ-1 fit: the training span leaves no fit or no validation rows after the embargo")
    with TorchState(spec.threads) as torch:
        net = build_net(spec, seed=seed)
        rng = np.random.default_rng(seed)
        X = torch.from_numpy(np.ascontiguousarray(fit_rows.X, dtype=np.float32))
        Y = torch.from_numpy(fit_rows.y.astype(np.float32))
        W = torch.from_numpy(_weights(fit_rows.w).astype(np.float32))
        Xv = torch.from_numpy(np.ascontiguousarray(val_rows.X, dtype=np.float32))
        Yv = torch.from_numpy(val_rows.y.astype(np.float32))
        Wv = torch.from_numpy(_weights(val_rows.w).astype(np.float32))
        opt = torch.optim.Adam(net.parameters(), lr=spec.lr, weight_decay=spec.weight_decay)
        best: tuple[float, int, tuple[list[str], list[tuple[int, ...]], bytes]] | None = None
        since = 0
        for epoch in range(1, spec.max_epochs + 1):
            net.train()
            order = rng.permutation(len(fit_rows))
            for a in range(0, len(order), spec.batch):
                ix = torch.from_numpy(order[a:a + spec.batch])
                wb = W[ix]
                tot = wb.sum()
                if float(tot) <= 0:
                    continue
                loss = (wb * (net(X[ix]) - Y[ix]) ** 2).sum() / tot
                opt.zero_grad()
                loss.backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                vl = float((Wv * (net(Xv) - Yv) ** 2).sum() / Wv.sum())
            if best is None or vl < best[0]:
                best, since = (vl, epoch, state_bytes(net)), 0
            else:
                since += 1
                if since >= spec.patience:
                    break
        version = str(torch.__version__)
    assert best is not None
    val_loss, best_epoch, (names, shapes, blob) = best
    w_all = fit_rows.w.reshape(-1)
    return Seq1Artifact(spec=spec, seed=seed, torch_version=version, names=tuple(names), shapes=tuple(shapes),
                        blob=blob, n_train=len(fit_rows), n_val=len(val_rows), n_eff=O.kish_n_eff(w_all[w_all > 0]),
                        best_epoch=best_epoch, val_loss=val_loss,
                        sha256=weights_sha(spec, seed, names, shapes, blob))


# ---------------------------------------------------------------------------------------------- the served form
class Seq1Bundle(FrozenRecord):
    """The seed ensemble of one fit: one artifact per seed of the spec, scored as their mean (ADR 0106: "train several
    seeds per model"; ADR 0109's serving rule reads seed stability over the run manifest's own seeds, >= 5). The bytes
    a run manifest records are `bundle_bytes` (research/history_runs.py)."""
    artifacts: tuple[Seq1Artifact, ...]

    @property
    def seeds(self) -> tuple[int, ...]:
        return tuple(a.seed for a in self.artifacts)


def fit_bundle(data: Seq1Data, spec: Seq1Spec = SPEC) -> Seq1Bundle:
    return Seq1Bundle(artifacts=tuple(fit(data, seed=s, spec=spec) for s in spec.seeds))


def score_bundle(bundle: Seq1Bundle, X: np.ndarray) -> np.ndarray:
    """Mean E[gross R | x] over the bundle's seeds, (n, 2)."""
    return np.mean([score(a, X) for a in bundle.artifacts], axis=0)


def bundle_bytes(bundle: Seq1Bundle) -> bytes:
    return bundle.model_dump_json().encode()


def load_bundle(raw: bytes | str) -> Seq1Bundle:
    """Strict: every artifact's sha must match its weights (a field this release does not know would change the
    bytes the run manifest hashed, so it is refused, never dropped)."""
    b = Seq1Bundle.model_validate_json(raw)
    bad = [a.seed for a in b.artifacts if a.compute_sha256() != a.sha256]
    if bad:
        raise ValueError(f"SEQ-1 bundle: artifact sha mismatch for seeds {bad}")
    return b


def _net_of(art: Seq1Artifact) -> Any:
    torch = torch_module()
    net = build_net(art.spec, seed=art.seed)
    state, off = {}, 0
    for name, shape in zip(art.names, art.shapes, strict=True):
        n = int(np.prod(shape)) if shape else 1
        arr = np.frombuffer(art.blob, dtype="<f4", count=n, offset=off * 4).reshape(shape)
        state[name] = torch.from_numpy(arr.copy())
        off += n
    net.load_state_dict(state)
    net.eval()
    return net


def score(art: Seq1Artifact, X: np.ndarray, *, chunk: int = 4096) -> np.ndarray:
    """E[gross R | x] per row and side (n, 2: long, short)."""
    with TorchState(art.spec.threads) as torch:
        net = _net_of(art)
        out = []
        with torch.no_grad():
            for a in range(0, len(X), chunk):
                out.append(net(torch.from_numpy(np.ascontiguousarray(X[a:a + chunk], dtype=np.float32))).numpy())
    return np.concatenate(out).astype(float) if out else np.zeros((0, 2))


def score_row(art: Seq1Artifact, x: np.ndarray) -> np.ndarray:
    """One window (n_inputs, window) -> (2,): the serving form, equal to `score` on the batch (tested)."""
    return score(art, np.asarray(x)[None])[0]


def save(art: Seq1Artifact, path: Path | str) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(art.model_dump_json())
    return p


def load(path: Path | str) -> Seq1Artifact:
    art = Seq1Artifact.model_validate_json(Path(path).read_text())
    if art.compute_sha256() != art.sha256:
        raise ValueError(f"{path}: SEQ-1 artifact sha mismatch (the weights are not the ones recorded)")
    return art


# ---------------------------------------------------------------------------------------------- the read
def judged_rows(data: Seq1Data, *, cost: np.ndarray, cost_version: np.ndarray | Sequence[str],
                net: np.ndarray | None = None) -> pd.DataFrame:
    """Long judged rows (one per row x side with a label): row, side column (0 long, 1 short), gross R, net R, the
    decision-time cost(x), the cost version it was charged at and the session day. `cost` (n, 2) is cost(x) in R
    (`decision_cost.cost_x` / `dense_labels.net_r` with the stage's priors, at the rows' cost of record);
    `cost_version` (n,) is each design row's `cost_version` (learn_labels with costs); `net` defaults to gross - cost.
    `read` refuses any row not charged at one D-3 cost of record (quant 1 on #489)."""
    c = np.asarray(cost, dtype=float)
    cv = np.asarray(cost_version, dtype=object)
    if c.shape != data.y.shape or cv.shape != (len(data),):
        raise ValueError(f"judged_rows: cost {c.shape} and cost_version {cv.shape} must match the design {data.y.shape}")
    n = (data.y - c) if net is None else np.asarray(net, dtype=float)
    i, j = np.nonzero(data.w > 0)
    day = pd.DatetimeIndex(pd.to_datetime(data.keys["decision_utc"], utc=True)).normalize()
    return pd.DataFrame({"row": i, "col": j, "gross_r": data.y[i, j], "net_r": n[i, j], "cost_x": c[i, j],
                         "cost_version": cv[i].astype(str), "day": np.asarray(day)[i],
                         "judged_tf": data.keys["judged_tf"].to_numpy()[i]})


def design_sha(data: Seq1Data) -> str:
    """sha256 of a design's content (arrays and keys): history_runs names its design files by it."""
    h = hashlib.sha256()
    for a in (data.X, data.y, data.w):
        h.update(np.ascontiguousarray(a).tobytes())
    h.update(data.keys.to_csv(index=False).encode())
    return h.hexdigest()


def read_key(spec: Seq1Spec, cost_version: str, train: Seq1Data, test: Seq1Data, rows: pd.DataFrame) -> str:
    """The id a SEQ-1 read is booked under (quant B2 on #489, read_key style of sr_v3_store): sha256 over the spec, the
    cost version and the sha of the train design, the test design and the judged rows. A booking covers exactly that
    read: another spec, design, cost or row set is another read."""
    rows_sha = hashlib.sha256(pd.util.hash_pandas_object(rows, index=False).to_numpy().tobytes()
                              + ",".join(map(str, rows.columns)).encode()).hexdigest()
    blob = {"spec": spec.model_dump(mode="json"), "cost_version": cost_version, "train": design_sha(train),
            "test": design_sha(test), "rows": rows_sha}
    return hashlib.sha256(json.dumps(blob, sort_keys=True).encode()).hexdigest()


class Seq1Read(FrozenRecord):
    """One walk-forward read inside learn: S per seed (objective.read on the pooled 1h-1d unit), the seed check,
    the verdict and per-tf S (descriptive: "Only the pooled result decides")."""
    spec_id: str
    seeds: tuple[int, ...]
    shas: tuple[str, ...]
    s_by_seed: tuple[float, ...]
    seed: SeedCheck
    verdict: Literal["READ", "INCONCLUSIVE"]
    s_mean: float
    keep_share: float
    per_tf: dict[str, float]
    keep_ensemble: np.ndarray                           # per judged row: keep_rule on the seeds' mean prediction
    reads: tuple[O.ObjectiveRead, ...]
    cost_version: str                                   # the one D-3 cost of record every judged row was charged at
    booked_read: str                                    # the read id booked in the read-once ledger before scoring
    best_epoch_by_seed: tuple[int, ...]                 # ADR 0107 1: best_epoch recorded per seed (and fold: per read)


def read(train: Seq1Data, test: Seq1Data, rows: pd.DataFrame, *, booked_read: str, family: str, ledger: Path | str,
         spec: Seq1Spec = SPEC, boot: int = O.BOOT) -> Seq1Read:
    """Fit every seed on `train`, keep test rows by `objective.keep_rule` (pred - cost(x) > 0), read obj-v1 S on the
    pooled unit per seed; INCONCLUSIVE when the seed SD is not below MIN_EDGE / 2. `keep_ensemble` is the served
    bundle's keep (the seeds' mean prediction), the rows `history_runs.seq1_quality` compares with the GBT baseline.

    A gate read (quant 1, B2 on #489): refused before any fit or score unless `booked_read` is this read's `read_key`
    and is booked under `family` in `ledger` (`windows.require_booked_read`; the family is registered with the signed study plan) and every judged row
    is charged at one D-3 cost of record (`gate_cost.require_cost_of_record`). `fit` / `fit_bundle` stay fit-only
    (K 0)."""
    if "cost_version" not in rows.columns:
        raise COR.CostNotOfRecord("SEQ-1 read: no costs: the judged rows carry no cost_version (judged_rows)")
    version = COR.require_cost_of_record(rows["cost_version"].to_numpy(), "SEQ-1 read")
    key = read_key(spec, version, train, test, rows)
    if booked_read != key:
        raise WN.ReadNotBooked(f"SEQ-1 read: booked id {booked_read[:12]} is not this read's read_key {key[:12]} "
                               "(spec, cost version, designs and rows are bound to the booking)")
    WN.require_booked_read(ledger, family, booked_read)
    reads, shas, per_tf_s, preds, epochs = [], [], [], [], []
    for sd in spec.seeds:
        art = fit(train, seed=sd, spec=spec)
        pred = score(art, test.X)[rows["row"].to_numpy(), rows["col"].to_numpy()]
        keep = O.keep_rule(pred, rows["cost_x"].to_numpy())
        day = rows["day"].to_numpy()
        rd = O.read(rows["net_r"].to_numpy(), rows["gross_r"].to_numpy(), keep, day,
                    cost_x=rows["cost_x"].to_numpy(), boot=boot)
        reads.append(rd)
        shas.append(art.sha256)
        epochs.append(art.best_epoch)
        preds.append(pred)
        tfs = rows["judged_tf"].to_numpy()
        per_tf_s.append({str(t): O.s_statistic(O.clip_r(rows["net_r"].to_numpy()[tfs == t]),
                                               keep[tfs == t].astype(float)) for t in np.unique(tfs)})
    s = [r.s for r in reads]
    chk = seed_check(s, min_edge=spec.min_edge)
    per_tf = {t: float(np.mean([p[t] for p in per_tf_s])) for t in per_tf_s[0]}
    return Seq1Read(spec_id=spec.spec_id, seeds=spec.seeds, shas=tuple(shas), s_by_seed=tuple(s), seed=chk,
                    verdict="INCONCLUSIVE" if chk.inconclusive else "READ", s_mean=float(np.mean(s)),
                    keep_share=float(np.mean([r.keep_share for r in reads])), per_tf=per_tf,
                    keep_ensemble=O.keep_rule(np.mean(preds, axis=0), rows["cost_x"].to_numpy()),
                    reads=tuple(reads), cost_version=version, booked_read=booked_read,
                    best_epoch_by_seed=tuple(epochs))


__all__ = ["OMP_MODULES", "omp_before_torch", "CHANNELS", "SPEC", "Seq1Artifact", "Seq1Data", "Seq1Read", "Seq1Spec", "SeedCheck", "bar_windows",
           "Seq1Bundle", "TorchState", "build_net", "bundle_bytes", "design_sha", "fit_bundle", "read_key", "load_bundle", "score_bundle", "design", "fit", "inner_split", "judged_rows", "judged_tf", "load", "n_inputs", "param_count", "read",
           "receptive_field", "save", "score", "score_row", "seed_check", "split", "state_bytes", "tf_channels", "torch_module",
           "weights_sha", "window_start"]
