#!/usr/bin/env bash
# mt5-gold-profile.sh apply | rollback <backup-dir>   (MT5 box, run AS USER mt5, never root)
# Makes the MT5 terminal open on one maximised XAUUSD M15 chart without the one-click panel (docs/deploy/
# mt5-default-gold-chart.md). The terminal must be STOPPED: it rewrites common.ini (ProfileLast) and the open profile on
# exit, so edits made while it runs are overwritten.
#   apply     backs up Config/{common,terminal}.ini and Profiles/ to ~/mt5-profile-backup-<UTC>, installs profile
#             `goldbot` and template goldbot.tpl, sets [Charts] ProfileLast=goldbot and [Window] Fullscreen=1
#             (chart only: no toolbars, Market Watch, Navigator, Toolbox; F11 over VNC toggles it). Prints the backup dir.
#   rollback  puts the backed-up ini files and Profiles/ back.
# MT5 files are UTF-16LE with BOM and CRLF; the repo keeps UTF-8/LF and this converts. Prints states only, never ini
# contents (common.ini holds the login).
set -euo pipefail
export LC_ALL=C
HERE=$(cd "$(dirname "$0")" && pwd)
MT5_DIR=${MT5_DIR:-$HOME/.wine/drive_c/MT5/ICMarkets}
BACKUP_ROOT=${BACKUP_ROOT:-$HOME}
CFG=$MT5_DIR/Config
# newer builds read profiles from MQL5/Profiles (the top-level Profiles/ is a leftover and is ignored)
PROF=$MT5_DIR/MQL5/Profiles; [ -d "$PROF/Charts" ] || PROF=$MT5_DIR/Profiles

die() { echo "refused: $*" >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run as user mt5, not root"
[ -d "$CFG" ] && [ -d "$PROF" ] || die "no MT5 data dir at $MT5_DIR"
if [ -z "${SKIP_RUNNING_CHECK:-}" ] && pgrep -u "$(id -u)" -f '[t]erminal64[.]exe' >/dev/null; then
  die "terminal64.exe is running: stop it first (it rewrites these files on exit)"
fi

# shellcheck source-path=SCRIPTDIR source=mt5-ini.sh
. "$HERE/mt5-ini.sh"   # to_mt5, set_ini

case "${1:-}" in
  apply)
    b=$BACKUP_ROOT/mt5-profile-backup-$(date -u +%Y%m%dT%H%M%SZ)
    mkdir -m 700 "$b"
    cp -a "$CFG/common.ini" "$CFG/terminal.ini" "$b/"
    cp -a "$PROF" "$b/Profiles"
    rm -rf "$PROF/Charts/goldbot"
    mkdir -p "$PROF/Charts/goldbot"
    for f in "$HERE"/profile/goldbot/*; do to_mt5 "$f" "$PROF/Charts/goldbot/$(basename "$f")"; done
    to_mt5 "$HERE/goldbot.tpl" "$PROF/Templates/goldbot.tpl"
    set_ini "$CFG/common.ini" Charts ProfileLast goldbot
    [ -n "${NO_FULLSCREEN:-}" ] || set_ini "$CFG/terminal.ini" Window Fullscreen 1
    echo "applied; backup: $b"
    ;;
  rollback)
    b=${2:-}
    [ -n "$b" ] && [ -f "$b/common.ini" ] && [ -d "$b/Profiles" ] || die "usage: rollback <backup-dir>"
    cp -a "$b/common.ini" "$b/terminal.ini" "$CFG/"
    rm -rf "$PROF"
    cp -a "$b/Profiles" "$PROF"
    echo "rolled back from $b"
    ;;
  *) die "usage: $0 apply | rollback <backup-dir>" ;;
esac
