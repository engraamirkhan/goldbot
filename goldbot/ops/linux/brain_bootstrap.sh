#!/usr/bin/env bash
# goldbot "brain" on an Oracle Cloud Always Free Ampere A1 VM (Ubuntu 22.04/24.04, ARM): engine, supervisor,
# scheduler (retrain, research jobs), dashboard API, Telegram and news services under systemd. The MT5 terminal runs
# on the second VM (mt5_bootstrap.sh) and is reached through the bridge (goldbot/execution/bridge.py).
#
#   sudo bash brain_bootstrap.sh <40-hex commit id>   # the commit the owner checked is on GitHub's main (recommended;
#                                                     # docs/deploy/first-deploy.md: download it and check its sha256)
#   sudo bash brain_bootstrap.sh                      # whatever main is at this moment
#
# Credentials are NEVER in this script. Afterwards the owner stores them with `goldbot accounts ...` (docs/RUNBOOK.md,
# section "Free hosting on Oracle Cloud"). Services are installed but not started.
set -euo pipefail
REPO=https://github.com/engraamirkhan/goldbot.git
ROOT=/opt/goldbot
NODE_MAJOR=22                       # match CI (.github/workflows/ci.yml NODE_VERSION)
REF=${1:-}
LOG=/root/goldbot-bootstrap.log
if [ -n "$REF" ] && ! [[ "$REF" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: sudo bash brain_bootstrap.sh [<40-hex commit id on main>]"; exit 2
fi
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
# A dropped SSH session must not stop the install half-way (docs/AGENT_STANDARDS.md "Root-run tooling"): SIGHUP and
# SIGPIPE are ignored from here on (children inherit it), and everything is also written to a root-only log. When the
# terminal is gone, tee keeps writing the log and `cat` drains the pipe, so no write fails.
trap '' PIPE HUP
exec > >(tee -a "$LOG" --output-error=warn 2>/dev/null; cat > /dev/null) 2>&1
echo "== goldbot brain bootstrap $(date -u +%FT%TZ) ref=${REF:-main}"

# Fallback host (docs/deploy/first-deploy.md "If Oracle has no A1 capacity"): on a box with under 4 GB of RAM (a 1 GB
# x86 micro) a 4 GB swap file keeps the dashboard build and the Saturday retrain from being killed. The A1 shape
# (12 GB) gets none.
if [ "$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)" -lt 4000000 ] && ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
  grep -q /swapfile /etc/fstab || echo "/swapfile none swap sw 0 0" >> /etc/fstab
fi

apt-get update -y
# restic: encrypted off-host backups of the state (goldbot/ops/backup.py, run by the scheduler as user goldbot)
DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates chrony build-essential xz-utils restic
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

id goldbot >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/goldbot --shell /usr/sbin/nologin goldbot
# The service user's tree is created by root once and then written only as goldbot: on a re-run, root must not
# follow a symlink goldbot planted there (chmod/chown as root would change the target).
if [ ! -d "$ROOT/.git" ]; then
  install -d -m 755 -o goldbot -g goldbot "$ROOT"   # root-ok: creates /opt/goldbot in root-owned /opt, empty so far
  sudo -u goldbot -H git clone -q "$REPO" "$ROOT"
  [ -z "$REF" ] || sudo -u goldbot -H git -C "$ROOT" checkout -q --detach "$REF"   # same code as root's copy above
elif [ -n "$REF" ]; then          # a re-run never moves an installed tree; say so when it is on another commit
  head=$(sudo -u goldbot -H git -C "$ROOT" rev-parse HEAD 2>/dev/null | tr -cd '0-9a-f' | head -c 40)
  [ "$head" = "$REF" ] || echo "WARNING: $ROOT is at $head, not $REF: left as it is (update it with sudo goldbot-deploy $REF)"
fi
sudo -u goldbot mkdir -p "$ROOT/state" "$ROOT/logs" "$ROOT/models" "$ROOT/data"
sudo -u goldbot chmod 700 "$ROOT/state"   # the secrets file (no desktop keyring on a server) lives here, owner-only

# Python 3.11 via uv (same minor version as CI)
sudo -u goldbot -H bash -c "curl -LsSf https://astral.sh/uv/install.sh | sh"
UV=/var/lib/goldbot/.local/bin/uv
sudo -u goldbot -H bash -c "cd $ROOT && $UV sync --frozen --extra live --python 3.11"   # exact versions from uv.lock

# Node 22 for the dashboard build (official tarball, checked against the published SHA-256)
if ! node --version 2>/dev/null | grep -q "^v$NODE_MAJOR\."; then
  ARCH=$(uname -m); case "$ARCH" in aarch64) NARCH=arm64;; x86_64) NARCH=x64;; *) echo "unsupported $ARCH"; exit 1;; esac
  VER=$(curl -fsSL https://nodejs.org/dist/index.json | python3 -c "import json,sys; print(next(r['version'] for r in json.load(sys.stdin) if r['version'].startswith('v$NODE_MAJOR.')))")
  TGZ="node-$VER-linux-$NARCH.tar.xz"
  curl -fsSLo "$OPS_SRC/$TGZ" "https://nodejs.org/dist/$VER/$TGZ"           # root-only directory, not /tmp
  curl -fsSL "https://nodejs.org/dist/$VER/SHASUMS256.txt" | grep " $TGZ\$" | (cd "$OPS_SRC" && sha256sum -c -)
  tar -xJf "$OPS_SRC/$TGZ" -C /usr/local --strip-components=1
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

for u in supervisor engine@ api scheduler telegram news bridge-tunnel; do install -m 644 "$OPS/systemd/goldbot-$u.service" /etc/systemd/system/; done
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
systemctl enable goldbot-bridge-tunnel && systemctl restart goldbot-bridge-tunnel   # also moves a running tunnel
WRAP
chmod 755 /usr/local/bin/goldbot-tunnel
echo "bridge tunnel key (authorise it on the MT5 box with: sudo goldbot-mt5-authorize '<this line>'):"
sudo -u goldbot cat /var/lib/goldbot/.ssh/id_ed25519.pub

# Deploys: only versions the owner approves (Telegram [Deploy] or `sudo goldbot-deploy latest`), never by itself.
# The script runs as root, so a root-owned copy is installed from root's own clone ($OPS); the repo copy (writable by
# the service user) is never run or installed as root. To update it later: RUNBOOK 0.5 "Installing a new
# goldbot-deploy" (root downloads it from GitHub at the commit; the owner checks its sha256 off-box first).
mkdir -p /etc/goldbot
echo brain > /etc/goldbot/role
install -m 755 -o root -g root "$OPS/goldbot-deploy.sh" /usr/local/sbin/goldbot-deploy
install -m 644 "$OPS/systemd/goldbot-deploy.service" "$OPS/systemd/goldbot-deploy.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable goldbot-deploy.timer

# Cloudflare Tunnel client for the dashboard (the tunnel token is entered by the owner, see the runbook). Pinned
# version and SHA-256 (GitHub release asset digests); downloaded into the root-only directory and checked before dpkg.
CLOUDFLARED_VERSION=2026.10.0
if ! command -v cloudflared >/dev/null; then
  ARCH=$(dpkg --print-architecture)
  case "$ARCH" in
    arm64) CF_SHA=7e0d76de3858b6396bd234ae26182bee222133398667d2647b90381d00c00e98 ;;
    amd64) CF_SHA=9721287c28f78cb623e77c7b95a63feea87e909054d75666a0ec9eff20e5d548 ;;
    *) echo "unsupported $ARCH for cloudflared"; exit 1 ;;
  esac
  curl -fsSLo "$OPS_SRC/cloudflared.deb" \
    "https://github.com/cloudflare/cloudflared/releases/download/$CLOUDFLARED_VERSION/cloudflared-linux-$ARCH.deb"
  echo "$CF_SHA  $OPS_SRC/cloudflared.deb" | sha256sum -c -
  dpkg -i "$OPS_SRC/cloudflared.deb"
fi
echo "brain installed. Next: docs/RUNBOOK.md 'Free hosting on Oracle Cloud' (store secrets, then start services)."
