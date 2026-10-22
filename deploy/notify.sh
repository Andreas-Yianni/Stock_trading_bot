#!/usr/bin/env bash
# Send a Telegram message with the bot's own token/chat id from .env (no extra secret anywhere).
#   ./notify.sh "text"
# Used by deploy.sh (deploy result) and watchdog.sh (bot restarted). Never fails the caller.
cd "$(dirname "$0")" || exit 0
token=$(sed -n 's/^TELEGRAM_BOT_TOKEN=//p' .env 2>/dev/null | tr -d '\r"' | head -1)
chat=$(sed -n 's/^TELEGRAM_CHAT_ID=//p' .env 2>/dev/null | tr -d '\r"' | head -1)
[ -n "$token" ] && [ -n "$chat" ] || exit 0
curl -s -m 20 -o /dev/null "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=$1" || true
