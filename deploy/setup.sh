#!/usr/bin/env bash
# One-time setup of a fresh Hetzner Ubuntu 24.04 server for the trading bot (see deploy/README.md).
# Run as root from a folder that also holds docker-compose.yml, deploy.sh and (optional) deploy_key.pub:
#   bash /root/tradebot-setup/setup.sh
# Safe to run again: every step checks what is already there.
set -euo pipefail

APP_USER=tradebot          # non-root user that owns the app and runs docker compose
APP_UID=1000               # the container user "bot" is uid 1000 too -> data/ stays writable
APP_DIR=/opt/tradebot
SRC="$(cd "$(dirname "$0")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "Run as root."; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
[ "$ID" = ubuntu ] || { echo "Expected Ubuntu, got $ID."; exit 1; }

export DEBIAN_FRONTEND=noninteractive

echo "== packages"
apt-get update -q
apt-get upgrade -yq
apt-get install -yq ca-certificates curl ufw unattended-upgrades
dpkg-reconfigure -f noninteractive unattended-upgrades   # automatic security updates

echo "== docker (official apt repository)"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc]" \
         "https://download.docker.com/linux/ubuntu $VERSION_CODENAME stable" \
         > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
# Container logs: rotated, so they can't fill the small disk.
if [ ! -f /etc/docker/daemon.json ]; then
    echo '{"log-driver": "local", "log-opts": {"max-size": "10m", "max-file": "3"}}' > /etc/docker/daemon.json
    systemctl restart docker
fi
systemctl enable --now docker

echo "== swap (2 GB safety margin for backtests)"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== user $APP_USER"
if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash --uid "$APP_UID" "$APP_USER"
fi
usermod -aG docker "$APP_USER"          # note: the docker group is root-equivalent
install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/$APP_USER/.ssh"
AUTH="/home/$APP_USER/.ssh/authorized_keys"
touch "$AUTH"
# Same personal key as root (the one added in the Hetzner console).
if [ -f /root/.ssh/authorized_keys ]; then
    while read -r line; do
        [ -n "$line" ] && ! grep -qxF "$line" "$AUTH" && echo "$line" >> "$AUTH"
    done < /root/.ssh/authorized_keys
fi
# GitHub Actions deploy key: may ONLY run deploy.sh (forced command, no shell/forwarding/pty).
if [ -f "$SRC/deploy_key.pub" ]; then
    line="command=\"$APP_DIR/deploy.sh\",restrict $(cat "$SRC/deploy_key.pub")"
    grep -qxF "$line" "$AUTH" || echo "$line" >> "$AUTH"
fi
chown "$APP_USER:$APP_USER" "$AUTH"
chmod 600 "$AUTH"

echo "== app directory $APP_DIR"
install -d -m 755 -o "$APP_USER" -g "$APP_USER" "$APP_DIR"
install -d -m 755 -o "$APP_UID" -g "$APP_UID" "$APP_DIR/data"
for f in docker-compose.yml deploy.sh; do
    [ -f "$SRC/$f" ] && install -m 644 -o "$APP_USER" -g "$APP_USER" "$SRC/$f" "$APP_DIR/$f"
done
[ -f "$APP_DIR/deploy.sh" ] && chmod 755 "$APP_DIR/deploy.sh"

echo "== ssh: keys only"
cat > /etc/ssh/sshd_config.d/10-tradebot.conf <<'EOF'
# Written by deploy/setup.sh. Files here are read in name order and the first value wins,
# so this overrides cloud-init's 50-cloud-init.conf.
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
EOF
# Ubuntu 24.04 starts sshd on demand (ssh.socket), so /run/sshd may not exist yet; sshd -t needs it.
install -d -m 755 /run/sshd
sshd -t
# With socket activation every new connection reads the new config; reload only if sshd runs as a service.
systemctl try-reload-or-restart ssh.service

echo "== firewall: only SSH open"
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable
# Docker-published ports bypass ufw; the only one (dashboard) is bound to 127.0.0.1 in compose.

echo
echo "Done. Next steps (deploy/README.md): copy .env, config.yaml and data/ to $APP_DIR,"
echo "log in to ghcr.io as $APP_USER, then run $APP_DIR/deploy.sh once by hand."
