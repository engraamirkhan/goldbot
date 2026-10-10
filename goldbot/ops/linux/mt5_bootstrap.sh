#!/usr/bin/env bash
# goldbot MT5 box on an Oracle Cloud Always Free VM.Standard.E2.1.Micro (x86, 1 GB, Ubuntu 22.04/24.04): the MT5
# terminal under Wine on a virtual display, and the goldbot bridge (goldbot/execution/bridge.py) under a Windows
# Python in the same Wine prefix. The bridge listens on 127.0.0.1 only; the brain reaches it through an SSH tunnel
# whose key may only forward to that port (`goldbot-mt5-authorize`). No port is opened.
#
#   sudo bash mt5_bootstrap.sh
#
# No credentials here: the owner logs in to MT5 once through VNC (tunnelled over SSH) with "save password", and stores
# the bridge listen address + token with `goldbot-mt5 accounts bridge-serve icm-demo` (docs/RUNBOOK.md).
set -euo pipefail
REPO=https://github.com/engraamirkhan/goldbot.git
ROOT=/opt/goldbot
PYVER=3.11.9
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
[ "$(uname -m)" = x86_64 ] || { echo "MT5 needs an x86_64 VM (E2.1.Micro), not ARM"; exit 1; }

# 1 GB of RAM: a 2 GB swap file keeps Wine + terminal + bridge from being killed
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
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

id mt5 >/dev/null 2>&1 || useradd --create-home --shell /bin/bash mt5
[ -d "$ROOT/.git" ] || git clone "$REPO" "$ROOT"
mkdir -p "$ROOT/state" "$ROOT/logs" && chown -R mt5:mt5 "$ROOT" && chmod 700 "$ROOT/state"

install -m 644 "$ROOT"/goldbot/ops/linux/systemd/goldbot-xvfb.service "$ROOT"/goldbot/ops/linux/systemd/goldbot-bridge@.service \
  "$ROOT"/goldbot/ops/linux/systemd/goldbot-vnc.service /etc/systemd/system/
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
  wine python -m pip install --upgrade pip
  wine python -m pip install MetaTrader5 pandas numpy pyarrow 'pydantic>=2.6' pyyaml requests keyring
  wine python -m pip install --no-deps -e 'Z:$ROOT'
"

# the brain's tunnel key: port forwarding to the bridge only (no shell, no other ports)
cat > /usr/local/bin/goldbot-mt5-authorize <<'WRAP'
#!/usr/bin/env bash
# sudo goldbot-mt5-authorize '<ssh-ed25519 ... goldbot@brain>'
set -euo pipefail
key=${1:?public key line from the brain}
case "$key" in ssh-ed25519\ *) ;; *) echo "expected an ssh-ed25519 public key"; exit 1;; esac
install -d -m 700 -o mt5 -g mt5 /home/mt5/.ssh
echo "restrict,port-forwarding,permitopen=\"127.0.0.1:8765\" $key" >> /home/mt5/.ssh/authorized_keys
chown mt5:mt5 /home/mt5/.ssh/authorized_keys && chmod 600 /home/mt5/.ssh/authorized_keys
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
# The script runs as root, so a root-owned copy is installed; the repo copy (writable by the service user) is never run
# as root. After reviewing a change to it: sudo install -m 755 -o root -g root <repo>/goldbot/ops/linux/goldbot-deploy.sh /usr/local/sbin/goldbot-deploy
mkdir -p /etc/goldbot
echo mt5 > /etc/goldbot/role
install -m 755 -o root -g root "$ROOT/goldbot/ops/linux/goldbot-deploy.sh" /usr/local/sbin/goldbot-deploy
# the MT5 box is updated by hand only (sudo goldbot-deploy latest): no deploy timer here

echo "MT5 box installed. Next: log in to MT5 once over VNC, then bridge-serve (docs/RUNBOOK.md)."
