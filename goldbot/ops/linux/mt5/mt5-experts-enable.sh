#!/usr/bin/env bash
# mt5-experts-enable.sh   (MT5 box, run AS USER mt5 with the terminal STOPPED; ExecStartPre of
# goldbot-mt5-terminal@.service, installed root-owned at /usr/local/libexec/goldbot with mt5-ini.sh)
# Starts every terminal with AutoTrading (Algo Trading) on. Incident 2026-10-20: the probe terminal's common.ini had no
# [Experts] section, so AutoTrading was off and the terminal refused every order (10027), while the demo terminal's
# had it. This sets exactly the demo terminal's seven [Experts] keys:
#   AllowDllImport=0 Enabled=1 Account=1 Profile=1 Chart=0 Api=0 WebRequest=0
# (Enabled=1 is the AutoTrading button; DLL imports and WebRequest stay off). The terminal rewrites common.ini on exit,
# so it must not run meanwhile: the unit's one-terminal guard runs first, and this refuses on its own too. Idempotent:
# when the keys are already set the file is not touched; otherwise it is backed up first to
# ~/mt5-common-backup-<UTC>.ini (0600: it holds the login). Prints states only, never ini contents. Exit 2 on a
# refusal (the unit runs it with '-': the terminal still starts, and the bridge's /health says autotrading_off).
set -euo pipefail
export LC_ALL=C
HERE=$(cd "$(dirname "$0")" && pwd)
# MT5_DIR, BACKUP_ROOT and SKIP_RUNNING_CHECK are honoured only under GOLDBOT_TEST=1 (the tests): in the unit the edit
# always targets mt5's own tree and the running check always runs (security review LOW)
if [ "${GOLDBOT_TEST:-}" != 1 ]; then unset MT5_DIR BACKUP_ROOT SKIP_RUNNING_CHECK; fi
MT5_DIR=${MT5_DIR:-${HOME:-/home/mt5}/.wine/drive_c/MT5/ICMarkets}
BACKUP_ROOT=${BACKUP_ROOT:-${HOME:-/home/mt5}}
INI=$MT5_DIR/Config/common.ini
KEYS=(AllowDllImport=0 Enabled=1 Account=1 Profile=1 Chart=0 Api=0 WebRequest=0)

die() { echo "refused: $*" >&2; exit 2; }
[ "$(id -u)" -ne 0 ] || die "run as user mt5, not root"
[ -f "$INI" ] || die "no Config/common.ini under $MT5_DIR (the terminal writes it on its first exit)"
if [ -z "${SKIP_RUNNING_CHECK:-}" ] && pgrep -u "$(id -u)" -f '[t]erminal64[.]exe' >/dev/null; then
  die "terminal64.exe is running: stop it first (it rewrites common.ini on exit)"
fi

# shellcheck source-path=SCRIPTDIR source=mt5-ini.sh
. "$HERE/mt5-ini.sh"   # mt5_read, ini_set, to_mt5

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mt5_read "$INI" "$work/now"
cp "$work/now" "$work/want"
for kv in "${KEYS[@]}"; do ini_set "$work/want" Experts "${kv%%=*}" "${kv#*=}"; done
if cmp -s "$work/now" "$work/want"; then
  echo "AutoTrading: [Experts] already set; common.ini unchanged"
  exit 0
fi
b=$BACKUP_ROOT/mt5-common-backup-$(date -u +%Y%m%dT%H%M%SZ).ini
(umask 077 && cp "$INI" "$b")
to_mt5 "$work/want" "$INI"
echo "AutoTrading: [Experts] set in common.ini; backup: $b"
