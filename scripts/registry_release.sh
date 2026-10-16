#!/usr/bin/env bash
# The shared trial registry on release research-v1 (registry.jsonl), for the workflows that write it (research.yml,
# preregister-quarter.yml). Never overwrites the published registry after a failed read (security review of ab24d87),
# nor over a concurrent writer (BACKLOG 51):
#   registry_release.sh fetch OUT    the published registry into OUT; empty only when the release or the asset does
#                                    not exist yet. Any other failure (view or download), or a pending upload with no
#                                    published copy (another writer mid-swap), fails the job.
#   registry_release.sh save LOCAL   note the published asset's stamp (id, updated_at, size, digest), fetch, union
#                                    with LOCAL (goldbot.research.registry_sync, idempotent), refuse if the union has
#                                    fewer lines than the published copy, upload it as registry.jsonl.pending-*,
#                                    re-check the stamp, then delete the pulled asset by id and rename the pending one.
#                                    A changed stamp, a 404 on the delete or a taken name on the rename means another
#                                    writer landed (the VPS monthly loop is outside `concurrency: research-registry`):
#                                    drop the pending upload and start again, at most 3 times, then fail the job.
# The VPS side runs the same protocol (goldbot.research.registry_sync.push_release).
# Needs gh with GH_TOKEN, and python with goldbot installed.
set -euo pipefail

TAG=research-v1
ASSET=registry.jsonl
ATTEMPTS=3
API="repos/{owner}/{repo}"

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
  elif grep -q "^$ASSET\.pending-" <<< "$assets"; then
    echo "::error::release $TAG has a pending $ASSET upload but no published one: a write is in flight" >&2
    return 1
  else
    echo "release $TAG has no $ASSET yet: starting from an empty registry"
    : > "$out"
  fi
}

release_json() {   # the release as the API serves it; {"assets": []} only when the release does not exist
  local out err
  err=$(mktemp)
  if out=$(gh api "$API/releases/tags/$TAG" 2> "$err"); then
    printf '%s\n' "$out"
    return 0
  fi
  cat "$err" >&2
  if grep -q "HTTP 404" "$err"; then
    echo '{"assets": []}'
    return 0
  fi
  echo "::error::cannot read release $TAG" >&2
  return 1
}

stamp() {   # stamp [--own NAME | --id-of NAME]: see `python -m goldbot.research.registry_sync stamp --help`
  local json
  json=$(release_json) || return 1
  python -m goldbot.research.registry_sync stamp "$@" <<< "$json"
}

swap() {   # swap BEFORE PENDING_ID: delete the pulled asset by id, rename the pending one; non-zero = lost the race
  local before=$1 pending_id=$2
  if [ "$before" != none ]; then
    gh api -X DELETE "$API/releases/assets/${before%%$'\t'*}" > /dev/null || return 1
  fi
  gh api -X PATCH "$API/releases/assets/$pending_id" -f name="$ASSET" > /dev/null || return 1
}

save() {
  local local_file=$1 merged remote before after attempt dir pending pending_id
  if [ "$(basename "$local_file")" != "$ASSET" ]; then
    echo "::error::the upload's asset name is the file name: pass a file named $ASSET" >&2
    return 1
  fi
  for (( attempt = 1; attempt <= ATTEMPTS; attempt++ )); do
    before=$(stamp) || return 1      # before the download: any change after this shows at the re-check
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
    dir=$(mktemp -d)
    pending="$ASSET.pending-$(date +%s)-$$-$RANDOM"
    cp -f "$local_file" "$dir/$pending"
    gh release upload "$TAG" "$dir/$pending"             # never --clobber: the published copy is swapped by id
    pending_id=$(stamp --id-of "$pending") || return 1
    after=$(stamp --own "$pending") || return 1
    if [ "$after" = "$before" ] && swap "$before" "$pending_id"; then
      echo "uploaded $local_file ($merged lines; published copy had $remote)"
      return 0
    fi
    gh api -X DELETE "$API/releases/assets/$pending_id" > /dev/null 2>&1 \
      || echo "::warning::could not delete the pending upload $pending" >&2
    echo "::warning::release $TAG/$ASSET changed after the pull (attempt $attempt/$ATTEMPTS): retrying" >&2
  done
  echo "::error::release $TAG/$ASSET changed during each of $ATTEMPTS saves; the published copy was left as the other writer wrote it, re-run the job" >&2
  return 1
}

case "${1:-}" in
  fetch) fetch "${2:?usage: registry_release.sh fetch OUT}" ;;
  save) save "${2:?usage: registry_release.sh save LOCAL}" ;;
  *) echo "usage: registry_release.sh fetch OUT | save LOCAL" >&2; exit 2 ;;
esac
