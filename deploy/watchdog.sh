#!/usr/bin/env bash
# Run by cron every 5 minutes (installed by setup.sh). If the bot container is not running or Docker marks
# it "unhealthy" (heartbeat older than 5 min), restart it and tell the user on Telegram (at most once an
# hour, so a crash loop doesn't spam). Skips while a deploy holds the lock.
set -uo pipefail
cd "$(dirname "$0")" || exit 1

exec 9>/tmp/tradebot-deploy.lock
flock -n 9 || exit 0

id=$(docker compose ps -q bot 2>/dev/null)
state="missing"
[ -n "$id" ] && state=$(docker inspect -f '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{end}}' "$id")
case "$state" in
    "running healthy"|"running starting"|"running ") exit 0 ;;
esac

echo "$(date -u +%FT%TZ) bot: $state -> restart" >> data/watchdog.log
if [ "$state" = "missing" ]; then
    docker compose up -d bot >> data/watchdog.log 2>&1
else
    docker compose restart bot >> data/watchdog.log 2>&1
fi

last=$(cat data/watchdog_alert 2>/dev/null || echo 0)
now=$(date +%s)
if [ $((now - last)) -ge 3600 ]; then
    echo "$now" > data/watchdog_alert
    ./notify.sh "⚠️ Το bot στον server είχε κολλήσει ή σταματήσει ($state) και ξαναξεκίνησε αυτόματα. Αν το ξαναδείς σύντομα, πες το στο Claude."
fi
