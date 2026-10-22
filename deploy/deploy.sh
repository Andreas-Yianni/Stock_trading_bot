#!/usr/bin/env bash
# Pull the newest image from ghcr.io and restart the containers, then check that the bot runs.
# Called by GitHub Actions through the forced-command deploy key (whatever command the client
# sends is ignored), or by hand as user tradebot: /opt/tradebot/deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

exec 9>/tmp/tradebot-deploy.lock
flock 9                                   # one deploy at a time

for f in .env config.yaml docker-compose.yml; do
    # A missing bind-mounted file would be created by docker as a DIRECTORY.
    [ -f "$f" ] || { echo "Missing $PWD/$f - see deploy/README.md"; exit 1; }
done

echo "== pull"
docker compose pull
echo "== up"
docker compose up -d --remove-orphans

sleep 20
docker compose ps
if ! docker compose ps --status running --services | grep -qx bot; then
    echo "The bot container is not running:"
    docker compose logs --tail 50 bot
    exit 1
fi

docker image prune -f >/dev/null           # drop old image versions (disk is small)
echo "Deploy OK: $(docker compose images bot --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null || true)"
