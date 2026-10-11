#!/usr/bin/env bash
# The shared trial registry on release research-v1 (registry.jsonl), for the workflows that write it (research.yml,
# preregister-quarter.yml). Never overwrites the published registry after a failed read (security review of ab24d87):
#   registry_release.sh fetch OUT    the published registry into OUT; empty only when the release or the asset does
#                                    not exist yet. Any other failure (view or download) fails the job.
#   registry_release.sh save LOCAL   fetch again, union with LOCAL (goldbot.research.registry_sync, idempotent), refuse
#                                    if the union has fewer lines than the published copy, then upload LOCAL.
# Needs gh with GH_TOKEN, and python with goldbot installed.
set -euo pipefail

TAG=research-v1
ASSET=registry.jsonl

fetch() {
  local out=$1 assets err
  err=$(mktemp)
  if ! assets=$(gh release view "$TAG" --json assets --jq '.assets[].name' 2> "$err"); then
    cat "$err" >&2
    if grep -qi "release not found" "$err"; then
      echo "release $TAG does not exist yet: starting from an empty registry"
      : > "$out"
      return 0
    fi
    echo "::error::cannot read release $TAG; refusing to start from an empty registry" >&2
    return 1
  fi
  if grep -qx "$ASSET" <<< "$assets"; then
    gh release download "$TAG" -p "$ASSET" -O "$out"      # must succeed: no fallback to an empty file
  else
    echo "release $TAG has no $ASSET yet: starting from an empty registry"
    : > "$out"
  fi
}

save() {
  local local_file=$1 merged remote
  if [ "$(basename "$local_file")" != "$ASSET" ]; then
    echo "::error::the upload's asset name is the file name: pass a file named $ASSET" >&2
    return 1
  fi
  fetch registry.remote.jsonl
  # union with whatever another writer pushed meanwhile, so no row is dropped
  python -m goldbot.research.registry_sync merge "$local_file" registry.remote.jsonl -o "$local_file"
  merged=$(wc -l < "$local_file")
  remote=$(wc -l < registry.remote.jsonl)
  if (( merged < remote )); then
    echo "::error::the merged registry has $merged lines, the published one $remote: refusing to upload" >&2
    return 1
  fi
  if ! gh release view "$TAG" > /dev/null 2>&1; then
    gh release create "$TAG" --title "Research trial registry" --latest=false \
      --notes "registry.jsonl: every trial and pre-registration (feeds the deflated Sharpe). Written by the research and preregister-quarter workflows and the VPS monthly loop (goldbot.research.registry_sync)."
  fi
  gh release upload "$TAG" "$local_file" --clobber
  echo "uploaded $local_file ($merged lines; published copy had $remote)"
}

case "${1:-}" in
  fetch) fetch "${2:?usage: registry_release.sh fetch OUT}" ;;
  save) save "${2:?usage: registry_release.sh save LOCAL}" ;;
  *) echo "usage: registry_release.sh fetch OUT | save LOCAL" >&2; exit 2 ;;
esac
