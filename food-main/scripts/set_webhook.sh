#!/usr/bin/env sh
# Usage:
#   BOT_TOKEN=123:ABC #   BOT_DOMAIN=bot-production-0297.up.railway.app #   WEBHOOK_SECRET=foodySecret123 #   ./scripts/set_webhook.sh

set -e
if [ -z "$BOT_TOKEN" ] || [ -z "$BOT_DOMAIN" ] || [ -z "$WEBHOOK_SECRET" ]; then
  echo "Please set BOT_TOKEN, BOT_DOMAIN and WEBHOOK_SECRET env vars"
  exit 1
fi

WEBHOOK_URL="https://${BOT_DOMAIN}/tg/webhook"
curl -s "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook"   -d url="${WEBHOOK_URL}"   -d secret_token="${WEBHOOK_SECRET}" | jq .
