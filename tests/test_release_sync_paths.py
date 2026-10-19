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
