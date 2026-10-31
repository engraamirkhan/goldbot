#!/usr/bin/env bash
# mt5-second-terminal.sh <account> <install dir> <bridge port> <sibling account> <windows python>
#
# ADR 0062: a second terminal on a box mt5_bootstrap.sh already built (vantage-247 beside vantage-demo). Run as root
# by `mt5_bootstrap.sh --instance`, from root's own clone (never the mt5-writable tree). No apt, no Wine or Python
# install (an upgrade under a running terminal is never done here) and nothing of the running sibling is restarted:
#   * the terminal goes to C:\MT5\<install dir> in the same Wine prefix (MetaQuotes' generic installer, ADR 0020); an
#     installer that starts its terminal has that one stopped (the unit is its only launcher), never the sibling's;
#   * the sibling's broker server list (Config/servers.dat) is copied when the new install has none, so the keyring
#     login (`goldbot-mt5 login <account>`, goldbot/ops/login.py) finds the broker's servers without the wizard;
#   * the AutoTrading script that takes an install dir is installed root-owned, the per-instance drop-ins are written
#     (mt5-instance.sh; the shared unit templates stay as they are), and the account's units are enabled, not started.
# The sibling's narrowed guards apply at its next restart (daemon-reload changes no running unit).
set -euo pipefail
export LC_ALL=C
HERE=$(cd "$(dirname "$0")" && pwd)
USAGE="usage: mt5-second-terminal.sh <account> <install dir> <bridge port> <sibling account> <windows python>"
[ $# -eq 5 ] || { echo "$USAGE"; exit 2; }
acct=$1 dir=$2 port=$3 sib=$4 winpy=$5
[[ "$dir" =~ ^[A-Za-z0-9]{1,32}$ ]] && [ "$dir" != ICMarkets ] || { echo "$USAGE"; exit 2; }
[ "$(id -u)" = 0 ] || { echo "run as root (mt5_bootstrap.sh --instance)"; exit 1; }
[ "$(cat /etc/goldbot/role 2>/dev/null)" = mt5 ] && [ -f "$winpy" ] \
  || { echo "refused: build the box first (sudo bash mt5_bootstrap.sh <sha> --account $sib)"; exit 1; }
avail=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
[ "${avail:-0}" -ge 500000 ] || { echo "refused: under 500 MB available for a second terminal"; exit 1; }
# validated and written first: a bad argument changes nothing on the box
bash "$HERE/mt5-instance.sh" "$acct" "$dir" "$port" --beside "$sib" ICMarkets
install -m 755 -o root -g root "$HERE/mt5/mt5-experts-enable.sh" /usr/local/libexec/goldbot/mt5-experts-enable.sh
install -m 755 -o root -g root "$HERE/mt5/mt5-ini.sh" /usr/local/libexec/goldbot/mt5-ini.sh
sudo -u mt5 -H bash -lc "
  set -euo pipefail
  export DISPLAY=:99 WINEDEBUG=-all WINEARCH=win64
  cd /tmp
  curl -fsSLo mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
  wine mt5setup.exe /auto /path:'C:\\MT5\\$dir' || true
  rm -f mt5setup.exe
  test -f ~/.wine/drive_c/MT5/$dir/terminal64.exe || { echo 'MT5 terminal not installed at C:\\MT5\\$dir'; exit 1; }
  pkill -u mt5 -f 'MT5.$dir.[t]erminal64[.]exe' || true
  if [ -f ~/.wine/drive_c/MT5/ICMarkets/Config/servers.dat ] && [ ! -f ~/.wine/drive_c/MT5/$dir/Config/servers.dat ]; then
    mkdir -p ~/.wine/drive_c/MT5/$dir/Config
    cp ~/.wine/drive_c/MT5/ICMarkets/Config/servers.dat ~/.wine/drive_c/MT5/$dir/Config/servers.dat
  fi
"
systemctl daemon-reload
systemctl enable "goldbot-mt5-terminal@$acct"
systemctl enable "goldbot-bridge@$acct"
systemctl enable "goldbot-bridge-watchdog@$acct.timer"
echo "Second terminal installed at C:\\MT5\\$dir for $acct (bridge 127.0.0.1:$port). Next: RUNBOOK \"XAUUSD247 weekend"
echo "lane\" (bridge listen address and token, the tunnel key's ports, start, login from the keyring, symbol check)."
