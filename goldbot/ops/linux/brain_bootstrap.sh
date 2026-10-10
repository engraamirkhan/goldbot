#!/usr/bin/env bash
# goldbot "brain" on an Oracle Cloud Always Free Ampere A1 VM (Ubuntu 22.04/24.04, ARM): engine, supervisor,
# scheduler (retrain, research jobs), dashboard API, Telegram and news services under systemd. The MT5 terminal runs
# on the second VM (mt5_bootstrap.sh) and is reached through the bridge (goldbot/execution/bridge.py).
#
#   curl -fsSL https://raw.githubusercontent.com/engraamirkhan/goldbot/main/goldbot/ops/linux/brain_bootstrap.sh | sudo bash
#
# Credentials are NEVER in this script. Afterwards the owner stores them with `goldbot accounts ...` (docs/RUNBOOK.md,
# section "Free hosting on Oracle Cloud"). Services are installed but not started.
set -euo pipefail
REPO=https://github.com/engraamirkhan/goldbot.git
ROOT=/opt/goldbot
NODE_MAJOR=22                       # match CI (.github/workflows/ci.yml NODE_VERSION)
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }

apt-get update -y
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates chrony build-essential xz-utils
systemctl enable --now chrony

id goldbot >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/goldbot --shell /usr/sbin/nologin goldbot
[ -d "$ROOT/.git" ] || git clone "$REPO" "$ROOT"
mkdir -p "$ROOT/state" "$ROOT/logs" "$ROOT/models" "$ROOT/data"
chown -R goldbot:goldbot "$ROOT"
chmod 700 "$ROOT/state"            # the secrets file (no desktop keyring on a server) lives here, owner-only

# Python 3.11 via uv (same minor version as CI)
sudo -u goldbot -H bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
UV=/var/lib/goldbot/.local/bin/uv
sudo -u goldbot -H bash -c "cd $ROOT && $UV venv --python 3.11 .venv && $UV pip install --python .venv/bin/python -e '.[live]'"

# Node 22 for the dashboard build (official tarball, checked against the published SHA-256)
if ! node --version 2>/dev/null | grep -q "^v$NODE_MAJOR\."; then
  ARCH=$(uname -m); case "$ARCH" in aarch64) NARCH=arm64;; x86_64) NARCH=x64;; *) echo "unsupported $ARCH"; exit 1;; esac
  VER=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c "import json,sys; print(next(r['version'] for r in json.load(sys.stdin) if r['version'].startswith('v$NODE_MAJOR.')))")
  TGZ="node-$VER-linux-$NARCH.tar.xz"
  curl -fsSLo "/tmp/$TGZ" "https://nodejs.org/dist/$VER/$TGZ"
  curl -fsSL "https://nodejs.org/dist/$VER/SHASUMS256.txt" | grep " $TGZ\$" | (cd /tmp && sha256sum -c -)
  tar -xJf "/tmp/$TGZ" -C /usr/local --strip-components=1
fi
sudo -u goldbot -H bash -c "cd $ROOT/web && npm ci && npm run build"

# `goldbot <module> <args>` runs a goldbot CLI as the service user with the file secret store
cat > /usr/local/bin/goldbot <<'WRAP'
#!/usr/bin/env bash
# e.g. goldbot accounts bridge-use icm-demo   |   goldbot run health
set -euo pipefail
mod=$1; shift
cd /opt/goldbot && exec sudo -u goldbot -H env PYTHON_KEYRING_BACKEND=keyring.backends.fail.Keyring \
  /opt/goldbot/.venv/bin/python -m "goldbot.ops.$mod" "$@"
WRAP
chmod 755 /usr/local/bin/goldbot

for u in supervisor engine@ api scheduler telegram news bridge-tunnel; do install -m 644 "$ROOT/goldbot/ops/linux/systemd/goldbot-$u.service" /etc/systemd/system/; done
systemctl daemon-reload
for s in goldbot-supervisor goldbot-engine@icm-demo goldbot-api goldbot-scheduler goldbot-telegram goldbot-news; do
  systemctl enable "$s"
done

# SSH key the bridge tunnel uses (its public half is authorised on the MT5 box for port forwarding only)
sudo -u goldbot -H bash -c "mkdir -p ~/.ssh && chmod 700 ~/.ssh && [ -f ~/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N '' -f ~/.ssh/id_ed25519"
mkdir -p /etc/goldbot
cat > /usr/local/bin/goldbot-tunnel <<'WRAP'
#!/usr/bin/env bash
# sudo goldbot-tunnel <mt5 private ip>   -> starts the SSH tunnel to the MT5 bridge
set -euo pipefail
echo "MT5_HOST=${1:?mt5 private ip}" > /etc/goldbot/bridge.env
systemctl enable --now goldbot-bridge-tunnel
WRAP
chmod 755 /usr/local/bin/goldbot-tunnel
echo "bridge tunnel key (authorise it on the MT5 box with: sudo goldbot-mt5-authorize '<this line>'):"
cat /var/lib/goldbot/.ssh/id_ed25519.pub

# Deploys: only versions the owner approves (Telegram [Deploy] or `sudo goldbot-deploy latest`), never by itself.
# The script runs as root, so a root-owned copy is installed; the repo copy (writable by the service user) is never run
# as root. After reviewing a change to it: sudo install -m 755 -o root -g root <repo>/goldbot/ops/linux/goldbot-deploy.sh /usr/local/sbin/goldbot-deploy
mkdir -p /etc/goldbot
echo brain > /etc/goldbot/role
install -m 755 -o root -g root "$ROOT/goldbot/ops/linux/goldbot-deploy.sh" /usr/local/sbin/goldbot-deploy
install -m 644 "$ROOT/goldbot/ops/linux/systemd/goldbot-deploy.service" "$ROOT/goldbot/ops/linux/systemd/goldbot-deploy.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable goldbot-deploy.timer

# Cloudflare Tunnel client for the dashboard (the tunnel token is entered by the owner, see the runbook)
if ! command -v cloudflared >/dev/null; then
  ARCH=$(dpkg --print-architecture)
  curl -fsSLo /tmp/cloudflared.deb "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH.deb"
  dpkg -i /tmp/cloudflared.deb
fi
echo "brain installed. Next: docs/RUNBOOK.md 'Free hosting on Oracle Cloud' (store secrets, then start services)."
