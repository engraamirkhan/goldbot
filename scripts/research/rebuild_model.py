"""Rebuild a model from its run manifest and compare the artifact's sha256 (ADR 0106 decision 7: "any past model can
be rebuilt exactly from its logged data version, settings, seed and code sha"; goldbot/research/run_manifest.py).

    uv run python scripts/research/rebuild_model.py <manifest_id | registry version> \\
        [--models models] [--manifests DIR] [--repo .] [--data-root DIR]

Run it in a clean checkout at the manifest's commit (`git worktree add ../rebuild-<sha> <code_sha>`, then
`scripts/worktree_env.sh` there so uv.lock is the run's) and pass that checkout as --repo: its directory goes first on
sys.path, so the builder and goldbot are imported from the commit that built the model, never from another tree.

Exit 0: rebuilt byte-identical (prints the sha256). Exit 1: rebuilt, different bytes. Exit 2: refused before any fit
(changed uv.lock, dirty tree, another commit, a changed data file, no builder, or an unknown manifest).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ref", help="manifest id, or a model registry version")
    ap.add_argument("--models", default="models", help="model registry folder (manifests in <models>/manifests)")
    ap.add_argument("--manifests", default=None, help="manifest folder, when not <models>/manifests")
    ap.add_argument("--repo", default=".", help="the clean checkout at the manifest's commit")
    ap.add_argument("--data-root", default=None, help="root of the manifest's data files (default: --repo)")
    a = ap.parse_args(argv)
    repo = Path(a.repo).resolve()
    sys.path.insert(0, str(repo))
    try:        # torch's OpenMP runtime before sklearn/lightgbm's: a multi-threaded SEQ-1 rebuild (seq1.omp_before_torch)
        import torch  # noqa: F401
    except ImportError:
        pass
    from goldbot.research import run_manifest as rm
    if (repo / "goldbot").is_dir():
        import goldbot
        here = Path(goldbot.__file__).resolve()
        if not here.is_relative_to(repo):
            print(f"refused: goldbot imports from {here.parent}, not from --repo {repo}", file=sys.stderr)
            return 2
    try:
        m = rm.load(a.ref, a.manifests) if a.manifests else rm.resolve(a.ref, models_dir=a.models)
        sha = rm.rebuild(m, repo=repo, data_root=Path(a.data_root) if a.data_root else repo)
    except (rm.ManifestError, rm.RebuildRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except rm.RebuildMismatch as exc:
        print(f"mismatch: {exc}", file=sys.stderr)
        return 1
    print(f"identical: {m.manifest_id} artifact sha256 {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
