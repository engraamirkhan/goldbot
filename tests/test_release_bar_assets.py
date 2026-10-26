"""The gold bar loader reads only gold files from release data-v1 (a second instrument on the same pipeline, or any other
Parquet on the release, must never be loaded into bars_1m as gold)."""
from pathlib import Path

from goldbot.data import release


def test_sync_release_bars_loads_only_the_gold_yearly_files(monkeypatch, tmp_path):
    assets = [{"name": n, "size": 1, "url": n} for n in (
        "xauusd_1m_dukascopy_2010.parquet", "xagusd_1m_dukascopy_2010.parquet", "costs.parquet",
        "xauusd_1m_dukascopy_2011.parquet")]
    monkeypatch.setattr(release, "list_assets", lambda token=None, repo=release.REPO, tag="data-v1": assets)
    monkeypatch.setattr(release, "download", lambda a, d, token=None: Path(d) / a["name"])
    seen: list[str] = []

    def load(store, files, **kw):
        seen.extend(f.name for f in files)
        return {}

    monkeypatch.setattr(release, "load_into_store", load)
    release.sync_release_bars(object(), raw_dir=tmp_path)  # type: ignore[arg-type]
    assert seen == ["xauusd_1m_dukascopy_2010.parquet", "xauusd_1m_dukascopy_2011.parquet"]
    seen.clear()
    release.sync_release_bars(object(), years=(2011, 2011), raw_dir=tmp_path)  # type: ignore[arg-type]
    assert seen == ["xauusd_1m_dukascopy_2011.parquet"]
