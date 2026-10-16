#!/usr/bin/env bash
# The shared trial registry on release research-v1 (registry.jsonl), for the workflows that write it (research.yml,
# preregister-quarter.yml). The protocol lives in one place, goldbot.research.registry_sync (the VPS monthly loop runs
# the same code over HTTP); this wrapper runs it through gh:
#   registry_release.sh fetch OUT [--bootstrap]    the published registry into OUT. Empty only when the release does
#                                                  not exist yet (or, with --bootstrap, has no registry.jsonl). A failed
#                                                  read, a release without registry.jsonl, or a pending upload with no
#                                                  published copy fails the job (security review of ab24d87, BACKLOG 51).
#   registry_release.sh save LOCAL [--bootstrap]   union LOCAL with the published copy and publish it by
#                                                  compare-and-swap: stamp at the pull, upload as
#                                                  registry.jsonl.pending-*, re-check, delete the pulled id, rename,
#                                                  verify. Refuses a union shorter than the published copy; another
#                                                  writer landing (the VPS loop is outside `concurrency:
#                                                  research-registry`) means merge and retry, at most 3 times.
#   registry_release.sh recover                    union every registry.jsonl and registry.jsonl.pending-* on the
#                                                  release and publish it the same way (after a stranded rename).
# Needs gh with GH_TOKEN, and python with goldbot installed. The release notes name every writer: "the research and
# preregister-quarter workflows and the VPS monthly loop" (registry_sync.RELEASE_NOTES).
set -euo pipefail

case "${1:-}" in
  fetch)
    exec python -m goldbot.research.registry_sync fetch "${2:?usage: registry_release.sh fetch OUT [--bootstrap]}" \
      "${@:3}" ;;
  save)
    exec python -m goldbot.research.registry_sync save "${2:?usage: registry_release.sh save LOCAL [--bootstrap]}" \
      "${@:3}" ;;
  recover) exec python -m goldbot.research.registry_sync recover ;;
  *) echo "usage: registry_release.sh fetch OUT [--bootstrap] | save LOCAL [--bootstrap] | recover" >&2; exit 2 ;;
esac
