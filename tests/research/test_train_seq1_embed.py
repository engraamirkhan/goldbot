"""scripts/research/train_seq1_embed.py: the outcome-free SEQ-1 embedding runner reads the xt-v1 store's decision bars
only (never a label value), keeps every row inside learn 2010-17, and refuses sub-hour tfs and non-learn years."""
from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from goldbot.research import dense_labels as D
from tests.research.test_dense_labels import _minutes, _store

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("train_seq1_embed", ROOT / "scripts/research/train_seq1_embed.py")
assert _spec is not None and _spec.loader is not None
R = importlib.util.module_from_spec(_spec)
sys.modules["train_seq1_embed"] = R
_spec.loader.exec_module(R)


def test_the_runner_never_reads_a_label_value():
    src = (ROOT / "scripts/research/train_seq1_embed.py").read_text()
    tree = ast.parse(src)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {
        n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not ({"load_labels", "load_learn", "gross_r", "gross_r_raw", "net_r", "book_learn_read"} & names)


def test_decisions_are_the_xt_v1_skeletons_bars_and_none_ends_after_learn(tmp_path: Path):
    m = _minutes("2017-12-27", 5 * 1440)
    _store(tmp_path, m, "1h", 0, "dukascopy", year=2017)
    dec, keys = R.decisions(tmp_path, "1h", [2017])
    assert len(dec) and list(keys) == ["2017:0"]
    end = pd.DatetimeIndex(dec) + pd.Timedelta(seconds=D.H_BY_TF["1h"])
    assert end.max() <= pd.Timestamp("2018-01-01", tz="UTC")
    with pytest.raises(SystemExit, match="xt-v1"):
        R.decisions(tmp_path, "1h", [2016, 2017])


@pytest.mark.parametrize("argv,match", [(["--tfs", "15m"], "1h"), (["--years", "2017-2018"], "learn")])
def test_the_runner_refuses_sub_hour_tfs_and_years_outside_learn(tmp_path: Path, argv: list[str], match: str):
    with pytest.raises((SystemExit, ValueError), match=match):
        R.main([*argv, "--out", str(tmp_path / "o"), "--raw", str(tmp_path), "--labels", str(tmp_path)])
