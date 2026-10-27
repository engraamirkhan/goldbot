"""The Saturday refresh downloads inside the data store, not the service's working directory (read-only under
ProtectSystem=strict on the brain: "[Errno 30] Read-only file system: 'raw'", 2026-10-17)."""
from pathlib import Path

from goldbot.data import release
from goldbot.data.store import Store


def test_sync_release_downloads_inside_the_store(tmp_path, monkeypatch):
    seen: dict[str, Path] = {}
    monkeypatch.setattr(release, "sync_release_bars", lambda store, *, token=None, raw_dir: seen.setdefault("bars", raw_dir) and {"bars": 0})
    monkeypatch.setattr(release, "sync_release_macro", lambda store, *, token=None, raw_dir: seen.setdefault("macro", raw_dir) and 0)
    monkeypatch.chdir(tmp_path)                       # a cwd we must not write to
    store = Store(tmp_path / "data")
    release.sync_release(store)
    assert seen["bars"].is_relative_to(store.root) and seen["macro"].is_relative_to(store.root)
    assert not (tmp_path / "raw").exists() and not (tmp_path / "raw-macro").exists()


def test_sync_release_prefers_the_local_inbox_over_the_public_release(tmp_path, monkeypatch):
    """ADR 0048: bars pushed from the owner's Mac into <data_root>/_inbox/bars are the source; data-v1 is not read."""
    store = Store(tmp_path / "data")
    inbox = store.root / release.LOCAL_BARS_INBOX
    inbox.mkdir(parents=True)
    (inbox / "xauusd_1m_dukascopy_2026.parquet").write_bytes(b"x")
    (inbox / ".xauusd_1m_dukascopy_2025.parquet.part").write_bytes(b"x")   # a push in flight is never read
    loaded: list[list[str]] = []

    def no_release(store, *, token=None, raw_dir):
        raise AssertionError("the public release must not be read while the inbox holds bars")

    monkeypatch.setattr(release, "sync_release_bars", no_release)
    def load(store, files):
        loaded.append([f.name for f in files])
        return {"1m": 1}

    monkeypatch.setattr(release, "load_into_store", load)
    monkeypatch.setattr(release, "sync_release_macro", lambda store, *, token=None, raw_dir: 0)
    assert release.sync_release(store)["1m"] == 1
    assert loaded == [["xauusd_1m_dukascopy_2026.parquet"]]


def test_sync_release_falls_back_to_the_release_without_an_inbox(tmp_path, monkeypatch):
    store = Store(tmp_path / "data")
    (store.root / release.LOCAL_BARS_INBOX).mkdir(parents=True)          # empty inbox: still the release
    monkeypatch.setattr(release, "sync_release_bars", lambda store, *, token=None, raw_dir: {"1m": 7})
    monkeypatch.setattr(release, "sync_release_macro", lambda store, *, token=None, raw_dir: 0)
    assert release.sync_release(store)["1m"] == 7
