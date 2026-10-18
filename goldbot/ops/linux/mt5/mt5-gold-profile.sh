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
PROF=$MT5_DIR/Profiles

die() { echo "refused: $*" >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run as user mt5, not root"
[ -d "$CFG" ] && [ -d "$PROF" ] || die "no MT5 data dir at $MT5_DIR"
if [ -z "${SKIP_RUNNING_CHECK:-}" ] && pgrep -u "$(id -u)" -f '[t]erminal64[.]exe' >/dev/null; then
  die "terminal64.exe is running: stop it first (it rewrites these files on exit)"
fi

to_mt5() {  # to_mt5 <utf8-src> <dst>: UTF-16LE + BOM + CRLF, written atomically
  local tmp
  tmp=$(mktemp "$2.XXXXXX")
  { printf '\xff\xfe'; sed 's/$/\r/' "$1" | iconv -f UTF-8 -t UTF-16LE; } >"$tmp"
  mv -f "$tmp" "$2"
}

set_ini() {  # set_ini <file> <section> <key> <value>: set key in section (added at the section end if missing)
  local f=$1 sec=$2 key=$3 val=$4 u tmp
  u=$(mktemp)
  iconv -f UTF-16 -t UTF-8 "$f" | tr -d '\r' >"$u"
  awk -v sec="[$sec]" -v key="$key" -v val="$val" '
    function flush() { if (insec && !done) { print key "=" val; done = 1 } }
    /^\[/ { flush(); insec = ($0 == sec) }
    insec && index($0, key "=") == 1 { if (!done) print key "=" val; done = 1; next }
    { print }
    END { flush(); if (!done) { print sec; print key "=" val } }' "$u" >"$u.new"
  tmp=$(mktemp "$f.XXXXXX")
  { printf '\xff\xfe'; sed 's/$/\r/' "$u.new" | iconv -f UTF-8 -t UTF-16LE; } >"$tmp"
  rm -f "$u" "$u.new"
  mv -f "$tmp" "$f"
}

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
