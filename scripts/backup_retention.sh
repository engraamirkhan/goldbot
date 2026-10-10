#!/usr/bin/env bash
# Monthly retention for goldbot's restic repository, run from the OWNER'S MAC, never from a goldbot server.
#
# The brain's key only appends (`restic backup`); thinning the history needs delete rights, which only the
# retention key held here has. A compromised brain therefore cannot forget or prune its own backups.
#
#   scripts/backup_retention.sh            # unlock stale locks, forget + prune, upload the retention marker
#   scripts/backup_retention.sh --dry-run  # show what would be forgotten; changes nothing, no marker
#
# Credentials come from the environment or, when unset, from the macOS Keychain (account "goldbot"):
#   RESTIC_REPOSITORY      <- restic-repository              (same repository as the brain)
#   RESTIC_PASSWORD        <- restic-password                (same restic password as the brain)
#   AWS_ACCESS_KEY_ID      <- restic-retention-s3-access-key (the SECOND customer secret key, with delete rights)
#   AWS_SECRET_ACCESS_KEY  <- restic-retention-s3-secret-key
# Store them once with `security add-generic-password -a goldbot -s <name> -w` (it prompts; nothing is echoed).
# Retention defaults equal goldbot/config.py BackupSettings (14 daily, 8 weekly, 12 monthly); override with
# KEEP_DAILY / KEEP_WEEKLY / KEEP_MONTHLY. The brain records the marker's time and health `backup_prune` warns
# when it is older than 45 days.
set -euo pipefail

ROLE_FILE=${GOLDBOT_ROLE_FILE:-/etc/goldbot/role}
if [[ -e "$ROLE_FILE" ]]; then
  echo "refusing: this is a goldbot server (role '$(cat "$ROLE_FILE" 2>/dev/null)'); retention runs only from the owner's Mac" >&2
  exit 2
fi

DRY_RUN=()
case "${1:-}" in
  "") ;;
  --dry-run) DRY_RUN=(--dry-run) ;;
  *) echo "usage: $0 [--dry-run]" >&2; exit 64 ;;
esac

RESTIC=${RESTIC:-restic}
command -v "$RESTIC" >/dev/null 2>&1 || { echo "restic is not installed (brew install restic)" >&2; exit 1; }

load_secret() {   # load_secret ENV_NAME keychain-service
  local name=$1 service=$2 value=""
  if [[ -n "${!name:-}" ]]; then
    export "${name?}"
    return 0
  fi
  if command -v security >/dev/null 2>&1; then
    value=$(security find-generic-password -a goldbot -s "$service" -w 2>/dev/null || true)
  fi
  if [[ -z "$value" ]]; then
    echo "missing $name: set it or store Keychain item '$service' (RUNBOOK 0.6)" >&2
    exit 1
  fi
  export "$name=$value"
}
load_secret RESTIC_REPOSITORY restic-repository
load_secret RESTIC_PASSWORD restic-password
load_secret AWS_ACCESS_KEY_ID restic-retention-s3-access-key
load_secret AWS_SECRET_ACCESS_KEY restic-retention-s3-secret-key
if [[ "$RESTIC_REPOSITORY" =~ compat\.objectstorage\.([a-z0-9-]+)\.oraclecloud\.com ]]; then
  export AWS_DEFAULT_REGION="${BASH_REMATCH[1]}"   # OCI's S3 endpoint signs with its region
fi

HOST=${GOLDBOT_BACKUP_HOST:-goldbot-brain}
MARKER=goldbot-retention

"$RESTIC" unlock    # stale locks, including ones the brain's key could not remove
"$RESTIC" forget --host "$HOST" --tag goldbot --keep-daily "${KEEP_DAILY:-14}" --keep-weekly "${KEEP_WEEKLY:-8}" \
  --keep-monthly "${KEEP_MONTHLY:-12}" --prune ${DRY_RUN[@]+"${DRY_RUN[@]}"}

if [[ ${#DRY_RUN[@]} -eq 0 ]]; then
  printf '{"pruned_utc": "%s", "by": "scripts/backup_retention.sh"}\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" |
    "$RESTIC" backup --stdin --stdin-filename retention.json --host "$MARKER" --tag "$MARKER"
  "$RESTIC" forget --host "$MARKER" --tag "$MARKER" --keep-last 6 --prune
  echo "retention done; the brain's next backup records it (health backup_prune)"
fi
