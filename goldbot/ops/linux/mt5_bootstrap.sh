#!/usr/bin/env bash
# goldbot MT5 box on an Oracle Cloud Always Free VM.Standard.E2.1.Micro (x86, 1 GB, Ubuntu 22.04/24.04): the MT5
# terminal under Wine on a virtual display, and the goldbot bridge (goldbot/execution/bridge.py) under a Windows
# Python in the same Wine prefix. The bridge listens on 127.0.0.1 only; the brain reaches it through an SSH tunnel
# whose key may only forward to that port (`goldbot-mt5-authorize`). No port is opened.
#
#   sudo bash mt5_bootstrap.sh <40-hex commit id>     # the commit the owner checked is on GitHub's main (recommended)
#   sudo bash mt5_bootstrap.sh                        # whatever main is at this moment
#   sudo bash mt5_bootstrap.sh <sha> --account icm-probe   # a box for another account (default icm-demo)
#
# One box per account (docs/ops/icm-probe-account-plan.md, Option B): every box has its own Wine prefix, terminal at
# C:\MT5\ICMarkets and bridge on its own 127.0.0.1:8765; only the account's unit instances differ. The account comes
# from --account or the ACCOUNT environment variable (sudo drops it unless given as `sudo ACCOUNT=... bash ...`).
#
# No credentials here: the owner logs in to MT5 once through VNC (tunnelled over SSH) with "save password", and stores
# the bridge listen address + token with `goldbot-mt5 accounts bridge-serve <account>` (docs/RUNBOOK.md).
set -euo pipefail
REPO=https://github.com/engraamirkhan/goldbot.git
ROOT=/opt/goldbot
PYVER=3.11.9
# sha256 of python-$PYVER-amd64.exe. python.org publishes no SHA-256 for 3.11.9 (its release API has md5 only), so
# it was derived from a download whose md5 matched python.org's (e8dcd502e34932eebcaf1be056d5cbcd) and whose
# Sigstore bundle verified for pablogsal@python.org (https://accounts.google.com). Bump with PYVER, the same way.
PY_SHA256=5ee42c4eee1e6b4464bb23722f90b45303f79442df63083f05322f1785f5fdde
# The Windows Python, always called by this absolute path (bootstrap, goldbot-bridge@.service, goldbot-mt5,
# goldbot-deploy): Windows looks in the current directory before PATH, so a bare `wine python` runs any python.exe
# in the working directory (/tmp next to the installer, or the service user's /opt/goldbot) instead. A Unix path
# (Wine accepts one) keeps backslashes out of systemd and shell quoting. Bump the PythonXY part with PYVER; the check
# below and tests/test_bootstrap_sh.py refuse a mismatch.
WINPY=/home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/Python311/python.exe
REF=
ACCOUNT=${ACCOUNT:-icm-demo}
LOG=/root/goldbot-bootstrap.log
USAGE="usage: sudo bash mt5_bootstrap.sh [<40-hex commit id on main>] [--account <account id, default icm-demo>]"
while [ $# -gt 0 ]; do
  case "$1" in
    --account) [ $# -ge 2 ] || { echo "$USAGE"; exit 2; }; ACCOUNT=$2; shift 2 ;;
    --account=*) ACCOUNT=${1#--account=}; shift ;;
    *) [ -z "$REF" ] || { echo "$USAGE"; exit 2; }; REF=$1; [ -n "$REF" ] || { echo "$USAGE"; exit 2; }; shift ;;
  esac
done
if [ -n "$REF" ] && ! [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "$USAGE"; exit 2
fi
# the account id goes into unit instance names run by root: lowercase id only, as in config/accounts.yaml
if ! [[ "$ACCOUNT" =~ ^[a-z][a-z0-9-]{0,31}$ ]]; then
  echo "$USAGE"; echo "refused: the account id must be lowercase letters, digits and '-'"; exit 2
fi
[[ "$WINPY" == */Python"$(echo "$PYVER" | cut -d. -f1,2 | tr -d .)"/python.exe ]] || { echo "WINPY does not match PYVER $PYVER"; exit 1; }
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "MT5 needs an x86_64 VM (E2.1.Micro), not ARM"; exit 1; }
# A dropped SSH session must not stop the install half-way (docs/AGENT_STANDARDS.md "Root-run tooling"): SIGHUP and
# SIGPIPE are ignored from here on (children inherit it), and everything is also written to a root-only log. When the
# terminal is gone, tee keeps writing the log and `cat` drains the pipe, so no write fails.
trap '' PIPE HUP
exec > >(tee -a "$LOG" --output-error=warn 2>/dev/null; cat > /dev/null) 2>&1
echo "== goldbot mt5 bootstrap $(date -u +%FT%TZ) ref=${REF:-main} account=$ACCOUNT"

# 1 GB of RAM: a 4 GB swap file keeps Wine + terminal + bridge from being killed (docs/deploy/free-hosting-options.md)
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

# A watchdog SIGABRT (goldbot-engine@ WatchdogSec) must not leave process memory, which holds tokens and the
# file-store secrets, in a core file or a crash report. A pipe core_pattern (apport, systemd-coredump) ignores
# LimitCORE=0, so apport is turned off and no process dumps core (persisted in sysctl.d; 60- sorts after
# systemd-coredump's 50-coredump.conf). docs/deploy/first-deploy.md checks /proc/sys/kernel/core_pattern.
echo "core_pattern was: $(head -c 200 /proc/sys/kernel/core_pattern 2>/dev/null || echo unreadable)"
systemctl disable --now apport.service 2>/dev/null || true
if [ -f /etc/default/apport ]; then sed -i 's/^enabled=1/enabled=0/' /etc/default/apport; fi
printf '%s\n' '# goldbot: no core files or crash reports (they would hold secrets)' 'kernel.core_pattern=|/bin/false' \
  > /etc/sysctl.d/60-goldbot-no-core.conf
sysctl -q -p /etc/sysctl.d/60-goldbot-no-core.conf || echo "WARNING: kernel.core_pattern not applied now (applies at boot)"

dpkg --add-architecture i386
install -d -m 755 /etc/apt/keyrings
curl -fsSLo /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
. /etc/os-release
curl -fsSLo "/etc/apt/sources.list.d/winehq-$VERSION_CODENAME.sources" \
  "https://dl.winehq.org/wine-builds/ubuntu/dists/$VERSION_CODENAME/winehq-$VERSION_CODENAME.sources"
apt-get update -y
# winehq-devel, not -stable: Wine 10.3 to 11.5 (so wine-stable 11.0) make the current MT5 build's protection report
# "A debugger has been found running in your system" (WineHQ bug 58043, fixed in 11.6). MetaQuotes' mt5linux.sh
# uses winehq-staging. Installing it replaces winehq-stable (both provide /usr/bin/wine).
# NOT PINNED, deliberately: whether to pin a Wine version (and which) is an OWNER DECISION, open in
# docs/decisions/0019-wine-version-for-the-mt5-box.md (proposed). Until the owner decides, every run (and every
# `apt-get upgrade`) takes the newest winehq-devel; docs/incidents/2026-10-16-mt5-debugger-found-under-wine.md.
DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends winehq-devel
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl xvfb x11vnc chrony
systemctl enable --now chrony

# What root installs (systemd units, the root-run deploy script) comes from root's OWN shallow clone in a root-only
# directory, never from $ROOT: the service user can write that tree and its .git, and it runs code (pip/npm/Wine
# installs) before the installs below. docs/AGENT_STANDARDS.md "Root-run tooling".
OPS_SRC=$(mktemp -d /root/goldbot-ops.XXXXXX)
trap 'rm -rf -- "$OPS_SRC"' EXIT
if [ -n "$REF" ]; then             # exactly the commit the owner checked off-box (git verifies the content by its id)
  git init -q "$OPS_SRC/src" && git -C "$OPS_SRC/src" fetch -q --depth 1 "$REPO" "$REF" \
    && git -C "$OPS_SRC/src" checkout -q --detach FETCH_HEAD
  [ "$(git -C "$OPS_SRC/src" rev-parse HEAD)" = "$REF" ] || { echo "fetched commit is not $REF"; exit 1; }
else
  git clone -q --depth 1 "$REPO" "$OPS_SRC/src"
fi
OPS=$OPS_SRC/src/goldbot/ops/linux

id mt5 >/dev/null 2>&1 || useradd --create-home --shell /bin/bash mt5
# The service user's tree is created by root once and then written only as mt5: on a re-run, root must not follow
# a symlink mt5 planted there (chmod/chown as root would change the target).
if [ ! -d "$ROOT/.git" ]; then
  install -d -m 755 -o mt5 -g mt5 "$ROOT"   # root-ok: creates /opt/goldbot in root-owned /opt, empty so far
  sudo -u mt5 -H git clone -q "$REPO" "$ROOT"
  [ -z "$REF" ] || sudo -u mt5 -H git -C "$ROOT" checkout -q --detach "$REF"   # same code as root's copy above
elif [ -n "$REF" ]; then          # a re-run never moves an installed tree; say so when it is on another commit
  head=$(sudo -u mt5 -H git -C "$ROOT" rev-parse HEAD 2>/dev/null | tr -cd '0-9a-f' | head -c 40)
  [ "$head" = "$REF" ] || echo "WARNING: $ROOT is at $head, not $REF: left as it is (update it with sudo goldbot-deploy $REF)"
fi
sudo -u mt5 mkdir -p "$ROOT/state" "$ROOT/logs"
sudo -u mt5 chmod 700 "$ROOT/state"

install -m 644 "$OPS"/systemd/goldbot-xvfb.service "$OPS"/systemd/goldbot-bridge@.service \
  "$OPS"/systemd/goldbot-mt5-terminal@.service \
  "$OPS"/systemd/goldbot-vnc.service "$OPS"/systemd/goldbot-bridge-watchdog@.service \
  "$OPS"/systemd/goldbot-bridge-watchdog@.timer /etc/systemd/system/
# root-run, so root's copy from its own clone (never the mt5-writable tree): restarts a bridge stuck in a terminal call
# and a terminal that stays down. The probe it runs as nobody (it reads the mt5-written /health) is root-owned too.
install -m 755 -o root -g root "$OPS/goldbot-bridge-watchdog.sh" /usr/local/sbin/goldbot-bridge-watchdog
install -m 755 -o root -g root "$OPS/goldbot-bridge-probe.sh" /usr/local/sbin/goldbot-bridge-probe
# run as mt5 by the terminal unit before each start (AutoTrading on); root-owned so mt5 cannot change what its unit runs
install -d -m 755 -o root -g root /usr/local/libexec/goldbot
install -m 755 -o root -g root "$OPS/mt5/mt5-experts-enable.sh" /usr/local/libexec/goldbot/mt5-experts-enable.sh
install -m 755 -o root -g root "$OPS/mt5/mt5-ini.sh" /usr/local/libexec/goldbot/mt5-ini.sh
# needrestart (run by unattended-upgrades) never restarts a goldbot unit in market hours (incident 2026-10-22)
install -d -m 755 -o root -g root /etc/needrestart/conf.d
install -m 644 -o root -g root "$OPS/needrestart/goldbot.conf" /etc/needrestart/conf.d/goldbot.conf
systemctl daemon-reload
systemctl enable --now goldbot-xvfb

# Wine prefix (Windows 10), Windows Python, MT5 terminal at C:\MT5\ICMarkets (the path config/accounts.yaml expects)
# The installed interpreter is called by $WINPY (top of file): a bare `python` in /tmp would run the installer saved
# there (its Modify Setup dialog blocks the script), which is why the installer is also not named python.exe.
sudo -u mt5 -H bash -lc "
  set -euo pipefail
  export DISPLAY=:99 WINEDEBUG=-all WINEARCH=win64
  # mscoree off for this one call: no Wine Mono download dialog to block an unattended run (MT5 and Python need no .NET)
  WINEDLLOVERRIDES=mscoree= wineboot -i && wine reg add 'HKCU\\Software\\Wine' /v Version /d win10 /f
  cd /tmp
  curl -fsSLo python-installer.exe https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe
  echo '$PY_SHA256  python-installer.exe' | sha256sum -c -     # refuse a changed installer before Wine runs it
  wine python-installer.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
  rm -f python-installer.exe
  # The MetaQuotes generic installer, kept on purpose (docs/decisions/0020-mt5-installer-generic-plus-company-search.md):
  # it ships NO IC Markets servers, so File > Login to ICMarketsSC-Demo silently never connects until the owner adds
  # the company once with File > Open an Account > ICMarkets > Raw Trading Ltd (RUNBOOK 0.3)
  curl -fsSLo mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
  # mt5setup exits 1 even after a complete /auto install, so success is the installed terminal, not the exit code
  wine mt5setup.exe /auto /path:'C:\\MT5\\ICMarkets' || true
  rm -f mt5setup.exe
  test -f ~/.wine/drive_c/MT5/ICMarkets/terminal64.exe || { echo 'MT5 terminal not installed at C:\\MT5\\ICMarkets'; exit 1; }
  cd ~
  # exact versions and sha256 from uv.lock (scripts/bridge_requirements.py), wheels only, nothing unpinned: no pip
  # self-upgrade, and goldbot is put on the path with a .pth file instead of an editable build (that would fetch an
  # unpinned setuptools into the build environment)
  wine '$WINPY' -m pip install --require-hashes --no-deps --only-binary :all: -r 'Z:$ROOT/goldbot/ops/linux/mt5-bridge-requirements.txt'
  wine '$WINPY' -c 'import pathlib, sysconfig; pathlib.Path(sysconfig.get_paths()[\"purelib\"], \"goldbot.pth\").write_text(\"Z:$ROOT\\n\")'
"

# the brain's tunnel key: forwarding to the bridge port only; any command runs /bin/false (`ssh -N` opens none)
cat > /usr/local/bin/goldbot-mt5-authorize <<'WRAP'
#!/usr/bin/env bash
# sudo goldbot-mt5-authorize '<ssh-ed25519 ... goldbot@brain>'
# Root only validates: exactly one ssh-ed25519 key line (no options, no second line, printable ASCII). Every write
# into mt5's home runs AS mt5, so a symlink mt5 plants in ~/.ssh can only reach mt5's own files.
set -euo pipefail
export LC_ALL=C
key=${1-}
re='^ssh-ed25519 [A-Za-z0-9+/=]+( [[:print:]]*)?$'
if [[ "$key" == *$'\n'* || "$key" == *$'\r'* ]] || ! [[ "$key" =~ $re ]]; then
  echo "refused: expected one ssh-ed25519 public key line (the brain's ~/.ssh/id_ed25519.pub)"
  exit 1
fi
sudo -u mt5 -H sh -c 'umask 077; mkdir -p ~/.ssh && chmod 700 ~/.ssh && printf "%s\n" "$1" >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys' \
  _ "restrict,port-forwarding,permitopen=\"127.0.0.1:8765\",command=\"/bin/false\" $key"
echo "authorised (forwarding to 127.0.0.1:8765 only)"
WRAP
chmod 755 /usr/local/bin/goldbot-mt5-authorize

cat > /usr/local/bin/goldbot-mt5 <<'WRAP'
#!/usr/bin/env bash
# e.g. goldbot-mt5 accounts bridge-serve icm-demo
set -euo pipefail
mod=$1; shift
# the interpreter by absolute path ($WINPY in mt5_bootstrap.sh): a bare `python` would run a python.exe in /opt/goldbot
cd /opt/goldbot && exec sudo -u mt5 -H env DISPLAY=:99 WINEDEBUG=-all PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring GOLDBOT_ALLOW_PLAINTEXT=1 \
  wine /home/mt5/.wine/drive_c/users/mt5/AppData/Local/Programs/Python/Python311/python.exe -m "goldbot.ops.$mod" "$@"
WRAP
chmod 755 /usr/local/bin/goldbot-mt5
# the terminal unit is the only launcher of the terminal (the bridge Requires= it, Restart=always); enabled, not
# started: the owner starts it for the first login over VNC (RUNBOOK 0.3)
systemctl enable "goldbot-mt5-terminal@$ACCOUNT"
systemctl enable "goldbot-bridge@$ACCOUNT"
systemctl enable --now "goldbot-bridge-watchdog@$ACCOUNT.timer"   # idle until the bridge has run 5 min

# Deploys: only versions the owner approves (Telegram [Deploy] or `sudo goldbot-deploy latest`), never by itself.
# The script runs as root, so a root-owned copy is installed from root's own clone ($OPS); the repo copy (writable by
# the service user) is never run or installed as root. To update it later: RUNBOOK 0.5 "Installing a new
# goldbot-deploy" (root downloads it from GitHub at the commit; the owner checks its sha256 off-box first).
mkdir -p /etc/goldbot
echo mt5 > /etc/goldbot/role
install -m 755 -o root -g root "$OPS/goldbot-deploy.sh" /usr/local/sbin/goldbot-deploy
# the MT5 box is updated by hand only (sudo goldbot-deploy latest): no deploy timer here

echo "MT5 box installed. Next: sudo systemctl start goldbot-mt5-terminal@$ACCOUNT, log in to MT5 once over VNC, then"
echo "bridge-serve (docs/RUNBOOK.md 0.3)."
echo "The terminal has no IC Markets servers yet: log in through File > Open an Account > ICMarkets > Raw Trading Ltd,"
echo "not File > Login (that never connects until the company is added)."
