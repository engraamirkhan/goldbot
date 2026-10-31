#!/usr/bin/env bash
# mt5-instance.sh <account> <install dir> <bridge port> --beside <sibling account> <sibling install dir>
#
# ADR 0062: a second MT5 terminal + bridge on one box (vantage-247 beside vantage-demo on the Vantage box). Run as root
# by mt5_bootstrap.sh --instance, from root's own clone. It never edits the shared unit templates (every other box keeps
# them byte for byte): it writes systemd drop-ins, root-owned 0644, under /etc/systemd/system/<unit>.d/:
#   * <account>: the terminal at C:\MT5\<dir> (ExecStart), its one-terminal guard and its AutoTrading edit scoped to
#     that install, the bridge's terminal wait scoped to it, and the watchdog probing the bridge on <port>;
#   * <sibling>: its terminal guard, AutoTrading edit and bridge wait narrowed to its own install <sibling dir>, so a
#     restart of either terminal is never refused because the other one runs (nothing else of the sibling changes).
# Patterns are literals (no shell variable) in the bracket form, so a guard's own command line never matches it
# (incident 2026-10-19). The caller runs `systemctl daemon-reload`; nothing is started or restarted here.
set -euo pipefail
export LC_ALL=C
USAGE="usage: mt5-instance.sh <account> <install dir> <bridge port> --beside <sibling account> <sibling install dir>"
# GOLDBOT_DROPIN_ROOT only under GOLDBOT_TEST=1 (the tests): on a box the drop-ins always go to systemd's own tree
if [ "${GOLDBOT_TEST:-}" = 1 ]; then DEST=${GOLDBOT_DROPIN_ROOT:-/etc/systemd/system}; else DEST=/etc/systemd/system; fi
LIBEXEC=/usr/local/libexec/goldbot
WINE_MT5=/home/mt5/.wine/drive_c/MT5

[ $# -eq 6 ] && [ "$4" = --beside ] || { echo "$USAGE"; exit 2; }
acct=$1 dir=$2 port=$3 sib=$5 sibdir=$6
for a in "$acct" "$sib"; do
  [[ "$a" =~ ^[a-z][a-z0-9-]{0,31}$ ]] || { echo "$USAGE"; echo "refused: account ids are lowercase ids"; exit 2; }
done
for d in "$dir" "$sibdir"; do        # letters and digits only: the dir is part of a pgrep pattern, never a regex
  [[ "$d" =~ ^[A-Za-z0-9]{1,32}$ ]] || { echo "$USAGE"; echo "refused: install dirs are letters and digits"; exit 2; }
done
[[ "$port" =~ ^[0-9]{4,5}$ ]] && [ "$port" -ge 1024 ] && [ "$port" -le 65535 ] && [ "$port" != 8765 ] \
  || { echo "$USAGE"; echo "refused: the bridge port is 1024..65535 and not the sibling's 8765"; exit 2; }
[ "$acct" != "$sib" ] && [ "$dir" != "$sibdir" ] || { echo "$USAGE"; echo "refused: the sibling is another install"; exit 2; }

write() {  # write <unit> <body>: the drop-in, replaced atomically
  local d="$DEST/$1.d" tmp
  install -d -m 755 "$d"
  tmp=$(mktemp "$d/.goldbot-instance.XXXXXX")
  printf '%s\n' "# written by mt5-instance.sh (ADR 0062); remove the file and daemon-reload to undo" "[Service]" "$2" > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$d/goldbot-instance.conf"
}

guard() {  # the terminal unit's one-terminal guard, scoped to install $1
  printf '%s' "ExecStartPre=/bin/sh -c 'if /usr/bin/pgrep -u mt5 -f \"MT5.$1.[t]erminal64[.]exe\" >/dev/null; then echo \"this install already runs an MT5 terminal: refusing a second one\"; exit 1; fi'"
}

terminal() {  # terminal <account> <dir>
  write "goldbot-mt5-terminal@$1.service" "ExecStartPre=
$(guard "$2")
ExecStartPre=-$LIBEXEC/mt5-experts-enable.sh $2
ExecStart=
ExecStart=/usr/bin/wine $WINE_MT5/$2/terminal64.exe /profile:goldbot"
}

bridge() {  # bridge <account> <dir>: attach only to this install's terminal (never let initialize launch one)
  write "goldbot-bridge@$1.service" "ExecStartPre=
ExecStartPre=/usr/bin/timeout 60 /bin/sh -c 'until /usr/bin/pgrep -u mt5 -f \"MT5.$2.[t]erminal64[.]exe\" >/dev/null; do sleep 2; done'"
}

terminal "$acct" "$dir"
bridge "$acct" "$dir"
write "goldbot-bridge-watchdog@$acct.service" "ExecStart=
ExecStart=/usr/local/sbin/goldbot-bridge-watchdog $acct $port"
terminal "$sib" "$sibdir"
bridge "$sib" "$sibdir"
echo "drop-ins written for $acct (C:\\MT5\\$dir, bridge port $port) and $sib (C:\\MT5\\$sibdir); run systemctl daemon-reload"
