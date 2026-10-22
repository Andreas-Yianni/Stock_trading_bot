#!/usr/bin/env bash
# Pull the newest image from ghcr.io and restart the containers, then check that the bot runs.
# Called by GitHub Actions through the forced-command deploy key (whatever command the client
# sends is ignored), or by hand as user tradebot: /opt/tradebot/deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

exec 9>/tmp/tradebot-deploy.lock
flock 9                                   # one deploy at a time (watchdog.sh waits too)

# Telegram message with the result (notify.sh uses the bot's own token from .env).
on_exit() {
    local code=$?
    [ -x ./notify.sh ] || return
    if [ "$code" -ne 0 ]; then
        ./notify.sh "❌ Το deploy της νέας έκδοσης απέτυχε. Το bot μπορεί να τρέχει την προηγούμενη έκδοση ή να είναι σταματημένο. Πες το στο Claude."
    else
        ./notify.sh "✅ Νέα έκδοση του bot στον server (${REV:-?}). Όλα κανονικά."
    fi
}
trap on_exit EXIT

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
REV=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$(docker compose ps -q bot)" \
      2>/dev/null | cut -c1-7)
echo "Deploy OK: ${REV:-unknown revision}"
