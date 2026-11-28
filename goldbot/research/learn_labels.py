"""The learn-only dl-v1 label route for the history models SEQ-1 and RL-X1 (ML plan 2026-11-25 items 12 and 17;
brief 2026-11-26: "labels only via load_labels on learn 2010-17 ... never confirm/test_old/holdout").

`load_learn` is a train read of `dense_labels.load_labels` with the window fixed to learn 2010-01..2017-12, phase 0
only and no booked read, so confirm and test_old can not be reached from here (load_labels drops any row touching
them on an unbooked read, and refuses the holdout). Every read names its `procedure` (for example `seq-1`, `rl-x1`) and
writes its consumption row to `ledger` (dense_labels.consume, quant L1 on #484). It never bypasses the store's seal: until ADR 0085 A4 records the
tiers file's sha256 (`dense_labels.RECORDED_TIERS_SHA256`) the read raises `SealedStore`.

Weights: one event per decision bar (both sides and every template of a bar share it), uniqueness over the event's
[entry_utc, horizon_end_utc] against the other events of the same instrument x tf (`objective.uniqueness_weights`,
López de Prado ch. 4), the horizon and not the realised exit (dense_labels: "a realised-exit interval would make a
stopped label's weight depend on its outcome"). Truncated labels (`label_ok` False) are dropped.

Exits (D-4): only rows built under the exit freeze xt-v1 are read (`dense_labels.EXIT_FREEZE_SHA256`, stamped on every
row as `exit_freeze_sha`); a row with another stamp is refused (`NotExitFreeze`), and `xt_keys` resolves the one
xt-v1 partition per learn year under the current spec and code hashes.

Costs (D-3): labels are gross. With `costs` (a `sr_v3_train.CostTable`, `sr_v3_store.cost_table(manifest)`), every
read is charged through `sr_v3_store.charge_of_record`, the one route of the sr-v3 trainer: a recorded version charges
gold rows exactly the record's `gold_row_bp` (`srv3_cost_record.apply`, which also re-derives the gate status), an
unrecorded one leaves the rows and their D-3 block as they are. Charged rows also carry `swap_rate`, the row's swap
of record per night (`CostTable.swap_rate`), which RL-X1 charges instead of the research swap spec. Without `costs` the rows keep the w2-point floor and
the D-3 block, so no read from here can become a gate read uncharged.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from goldbot.data.timeutil import epoch_ns
from goldbot.research import dense_labels as D
from goldbot.research import feature_store as F
from goldbot.research import gate_cost as COR
from goldbot.research import objective as O
from goldbot.research import windows as WN


class NotExitFreeze(ValueError):
    """Label rows not built under the D-4 exit freeze xt-v1."""


def _learn_years(years: Iterable[int]) -> list[int]:
    lo, hi = WN.SPANS[WN.Window.LEARN]
    ys = sorted(int(y) for y in years)
    bad = [y for y in ys if not lo.year <= y < hi.year]
    if bad:
        raise ValueError(f"learn_labels reads learn {lo.year}..{hi.year - 1} only, not {bad}")
    return ys


def xt_keys(cache: Path, *, instrument: str, tf: str, years: Iterable[int], phase: int = 0
            ) -> dict[tuple[int, int], str]:
    """{(year, phase): key} of the xt-v1 partitions of the learn `years`: the manifest's spec and code hashes are the
    current ones and its `exit_freeze_sha` is `EXIT_FREEZE_SHA256`. A year without one is absent; two candidates for
    one year refuse (prune the stale one so the read is unambiguous)."""
    import json
    want = (D.spec_hash(), D.code_hash(), D.EXIT_FREEZE_SHA256)
    out: dict[tuple[int, int], str] = {}
    for y in _learn_years(years):
        d = Path(cache) / D.SPEC.spec_id / instrument / tf / f"p{phase}" / str(y)
        mans = [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))]
        hit = [str(m["key"]) for m in mans if (m.get("spec_hash"), m.get("code_hash"), m.get("exit_freeze_sha")) == want]
        if len(hit) > 1:
            raise ValueError(f"dl-v1 {instrument} {tf} p{phase} {y}: {len(hit)} xt-v1 partitions under this spec and "
                             "code; prune the stale one")
        if hit:
            out[(y, phase)] = hit[0]
    return out


def load_learn(cache: Path, *, procedure: str, ledger: Path, instrument: str, tf: str, years: Iterable[int],
               tiers: F.Tiers | None, columns: Sequence[str], keys: Mapping[tuple[int, int], str], templates: Sequence[str] | None = None,
               proxy_source: str | None = None, costs: Any | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Phase-0 learn rows of (instrument, tf) with an event weight `w`; years outside learn are refused first, rows
    not built under xt-v1 after the read. `costs`: the cost table the rows are charged at (module docstring)."""
    from goldbot.research import sr_v3_store as ST
    lo, hi = WN.SPANS[WN.Window.LEARN]
    ys = _learn_years(years)
    lab, meta = D.load_labels(cache, procedure=procedure, ledger=ledger, instrument=instrument, tf=tf, years=ys,
                              tiers=tiers, columns=columns, keys=keys, window=(lo, hi), purpose="train", phases=(0,), templates=templates,
                              proxy_source=proxy_source, booked_read=None)
    if len(lab) and ("exit_freeze_sha" not in lab.columns
                     or (lab["exit_freeze_sha"].astype(str) != D.EXIT_FREEZE_SHA256).any()):
        raise NotExitFreeze(f"learn_labels {instrument} {tf}: rows not built under the D-4 exit freeze xt-v1 (sha256 "
                            f"{D.EXIT_FREEZE_SHA256[:12]}): rebuild the partitions (build_dense_labels.py)")
    lab = lab[lab["label_ok"].astype(bool)].reset_index(drop=True) if len(lab) else lab
    if len(lab):
        lab, meta = ST.charge_of_record(lab, meta, costs, instrument, tf)
        charged = lab["cost_version"].astype(str).str.startswith(COR.COST_RECORD_PREFIX).to_numpy()
        if costs is not None and charged.any():          # the swap of record of each charged row (RL-X1, rl_sim)
            rate = np.full(len(lab), np.nan)
            rate[charged] = costs.swap_rate(lab[charged].assign(instrument=instrument, tf=tf))
            lab = lab.assign(swap_rate=rate)
    meta = {**meta, "learn_only": True}
    if not len(lab):
        return lab.assign(w=pd.Series(dtype=float)), meta
    ev = lab.drop_duplicates("decision_utc")[["decision_utc", "entry_utc", "horizon_end_utc"]]
    w = O.uniqueness_weights(epoch_ns(pd.DatetimeIndex(ev["entry_utc"])),
                             epoch_ns(pd.DatetimeIndex(ev["horizon_end_utc"])))
    lab = lab.merge(ev[["decision_utc"]].assign(w=w), on="decision_utc", how="left")
    return lab, meta
