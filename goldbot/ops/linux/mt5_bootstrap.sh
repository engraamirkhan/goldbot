#!/usr/bin/env bash
# goldbot MT5 box on an Oracle Cloud Always Free VM.Standard.E2.1.Micro (x86, 1 GB, Ubuntu 22.04/24.04): the MT5
# terminal under Wine on a virtual display, and the goldbot bridge (goldbot/execution/bridge.py) under a Windows
# Python in the same Wine prefix. The bridge listens on 127.0.0.1 only; the brain reaches it through an SSH tunnel
# whose key may only forward to that port (`goldbot-mt5-authorize`). No port is opened.
#
#   sudo bash mt5_bootstrap.sh <40-hex commit id>     # the commit the owner checked is on GitHub's main (recommended)
#   sudo bash mt5_bootstrap.sh                        # whatever main is at this moment
#
# No credentials here: the owner logs in to MT5 once through VNC (tunnelled over SSH) with "save password", and stores
# the bridge listen address + token with `goldbot-mt5 accounts bridge-serve icm-demo` (docs/RUNBOOK.md).
set -euo pipefail
REPO=https://github.com/engraamirkhan/goldbot.git
ROOT=/opt/goldbot
PYVER=3.11.9
REF=${1:-}
LOG=/root/goldbot-bootstrap.log
if [ -n "$REF" ] && ! [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: sudo bash mt5_bootstrap.sh [<40-hex commit id on main>]"; exit 2
fi
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "MT5 needs an x86_64 VM (E2.1.Micro), not ARM"; exit 1; }
# A dropped SSH session must not stop the install half-way (docs/AGENT_STANDARDS.md "Root-run tooling"): SIGHUP and
# SIGPIPE are ignored from here on (children inherit it), and everything is also written to a root-only log. When the
# terminal is gone, tee keeps writing the log and `cat` drains the pipe, so no write fails.
trap '' PIPE HUP
exec > >(tee -a "$LOG" --output-error=warn 2>/dev/null; cat > /dev/null) 2>&1
echo "== goldbot mt5 bootstrap $(date -u +%FT%TZ) ref=${REF:-main}"

# 1 GB of RAM: a 4 GB swap file keeps Wine + terminal + bridge from being killed (docs/deploy/free-hosting-options.md)
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

dpkg --add-architecture i386
install -d -m 755 /etc/apt/keyrings
curl -fsSLo /etc/apt/keyrings/winehq-archive.key https://dl.winehq.org/wine-builds/winehq.key
. /etc/os-release
curl -fsSLo "/etc/apt/sources.list.d/winehq-$VERSION_CODENAME.sources" \
  "https://dl.winehq.org/wine-builds/ubuntu/dists/$VERSION_CODENAME/winehq-$VERSION_CODENAME.sources"
apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y --install-recommends winehq-stable
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
  "$OPS"/systemd/goldbot-vnc.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now goldbot-xvfb

# Wine prefix (Windows 10), Windows Python, MT5 terminal at C:\MT5\ICMarkets (the path config/accounts.yaml expects)
sudo -u mt5 -H bash -lc "
  set -euo pipefail
  export DISPLAY=:99 WINEDEBUG=-all WINEARCH=win64
  wineboot -i && wine reg add 'HKCU\\Software\\Wine' /v Version /d win10 /f
  cd /tmp
  curl -fsSLo python.exe https://www.python.org/ftp/python/$PYVER/python-$PYVER-amd64.exe
  wine python.exe /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
  curl -fsSLo mt5setup.exe https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe
  wine mt5setup.exe /auto /path:'C:\\MT5\\ICMarkets'
  # exact versions and sha256 from uv.lock (scripts/bridge_requirements.py), wheels only, nothing unpinned: no pip
  # self-upgrade, and goldbot is put on the path with a .pth file instead of an editable build (that would fetch an
  # unpinned setuptools into the build environment)
  wine python -m pip install --require-hashes --no-deps --only-binary :all: -r 'Z:$ROOT/goldbot/ops/linux/mt5-bridge-requirements.txt'
  wine python -c 'import pathlib, sysconfig; pathlib.Path(sysconfig.get_paths()[\"purelib\"], \"goldbot.pth\").write_text(\"Z:$ROOT\\n\")'
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
cd /opt/goldbot && exec sudo -u mt5 -H env DISPLAY=:99 WINEDEBUG=-all PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring \
  wine python -m "goldbot.ops.$mod" "$@"
WRAP
chmod 755 /usr/local/bin/goldbot-mt5
systemctl enable goldbot-bridge@icm-demo

# Deploys: only versions the owner approves (Telegram [Deploy] or `sudo goldbot-deploy latest`), never by itself.
# The script runs as root, so a root-owned copy is installed from root's own clone ($OPS); the repo copy (writable by
# the service user) is never run or installed as root. To update it later: RUNBOOK 0.5 "Installing a new
# goldbot-deploy" (root downloads it from GitHub at the commit; the owner checks its sha256 off-box first).
mkdir -p /etc/goldbot
echo mt5 > /etc/goldbot/role
install -m 755 -o root -g root "$OPS/goldbot-deploy.sh" /usr/local/sbin/goldbot-deploy
# the MT5 box is updated by hand only (sudo goldbot-deploy latest): no deploy timer here

echo "MT5 box installed. Next: log in to MT5 once over VNC, then bridge-serve (docs/RUNBOOK.md)."
