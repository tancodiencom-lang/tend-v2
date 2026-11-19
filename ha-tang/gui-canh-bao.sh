#!/bin/bash
# Gui mot tin canh bao qua Telegram. Dung: gui-canh-bao.sh "noi dung"
# Dung chung cau hinh canh bao voi ban cu: /etc/tend/canh-bao.env (TELEGRAM_TOKEN, TELEGRAM_CHAT_ID; chi root doc).
set -euo pipefail
CONF=${TEND_CANH_BAO_CONF:-/etc/tend/canh-bao.env}
if [ ! -f "$CONF" ]; then
  echo "Chua cai canh bao Telegram (thieu $CONF). Noi dung: $1"
  exit 0
fi
# shellcheck disable=SC1090
. "$CONF"
# Dua dia chi co ma bot qua dau vao chuan de ma khong hien trong danh sach tien trinh
code=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\n' "$TELEGRAM_TOKEN" | \
  curl -sS --max-time 20 -o /dev/null -w "%{http_code}" -K - \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1")
[ "$code" = 200 ] || { echo "Gui Telegram that bai (ma $code)"; exit 1; }
