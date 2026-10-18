#!/usr/bin/env bash
# MT5 box. The ONLY command the brain's chart key may run (authorized_keys command=, restrict): emits the latest
# screenshot as a tar stream (tar keeps the capture time as the file's mtime). Ignores SSH_ORIGINAL_COMMAND.
set -euo pipefail
exec /usr/bin/tar -C "${CHART_DIR:-/home/mt5/chart}" -cf - mt5.png
