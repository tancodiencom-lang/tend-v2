#!/bin/bash
# Kiem tra moi sang 08:07 (tend-v2-kiemtra.timer): sao luu dem qua, chep tai lieu goc, thu khoi phuc gan nhat,
# dich vu app, cho trong o dia. Co van de thi bao Telegram; sang thu Hai gui ban "moi viec on" de biet canh bao con song.
# Phong khi mot viec KHONG CHAY LUON (nen khong co loi nao de bao). Chay tay: sudo systemctl start tend-v2-kiemtra
set -uo pipefail
ROOT=${TEND_V2_ROOT:-/srv/tend-v2}
VAR=${TEND_V2_VAR:-/var/lib}
SEND=${TEND_V2_SEND:-$ROOT/app/ha-tang/gui-canh-bao.sh}
NOW=$(date +%s)
problems=()
fine=()

enabled() { [ -n "${TEND_V2_ALL_ENABLED:-}" ] || systemctl is-enabled -q "$1" 2>/dev/null; }

# check <thu muc trang thai> <so gio toi da> <ten viec> [chua-chay-lan-nao-thi-chi-nhac]
check() {
  local file=$VAR/$1/lan-cuoi-thanh-cong
  if [ ! -f "$file" ]; then
    if [ -n "${4:-}" ]; then fine+=("$3: chưa chạy lần nào."); else problems+=("$3: chưa có lần nào thành công."); fi
    return
  fi
  local hours=$(( (NOW - $(stat -c %Y "$file")) / 3600 ))
  if [ "$hours" -gt "$2" ]; then
    problems+=("$3: đã $hours giờ chưa thành công (lần cuối $(cat "$file")).")
  else
    fine+=("$3: lần cuối $(cat "$file").")
  fi
}

if enabled tend-v2-saoluu.timer; then check tend-v2-saoluu 30 "Sao lưu hằng đêm"; else problems+=("Sao lưu hằng đêm CHƯA BẬT."); fi
if enabled tend-v2-goc.timer; then check tend-v2-goc 3 "Chép tài liệu gốc lên Drive"; fi
if enabled tend-v2-khoiphuc.timer; then check tend-v2-khoiphuc $((40 * 24)) "Thử khôi phục hằng tháng" chi-nhac; fi
if [ -z "${TEND_V2_ALL_ENABLED:-}" ] && ! systemctl is-active -q tend-v2; then problems+=("App Tend mới đang KHÔNG chạy."); fi
USED=$(df -P "$ROOT" | awk 'NR==2 {gsub("%", "", $5); print $5}')
if [ "${USED:-0}" -ge 85 ]; then problems+=("Ổ đĩa máy chủ đã dùng ${USED}%: sắp đầy, sao lưu sẽ hỏng."); else fine+=("Ổ đĩa đã dùng ${USED}%."); fi

TODAY=$(date '+%d/%m/%Y')
if [ ${#problems[@]} -gt 0 ]; then
  MSG="⚠️ app.tend.vn – kiểm tra sáng $TODAY"
  for line in "${problems[@]}"; do MSG+=$'\n'"• $line"; done
  echo "$MSG"
  /bin/bash $SEND "$MSG"
elif [ "$(date +%u)" = 1 ] || [ -n "${TEND_V2_ALWAYS_REPORT:-}" ]; then
  MSG="✅ app.tend.vn – kiểm tra sáng $TODAY: mọi việc ổn"
  for line in "${fine[@]}"; do MSG+=$'\n'"• $line"; done
  echo "$MSG"
  /bin/bash $SEND "$MSG"
else
  echo "Kiem tra sang $TODAY: on"
fi
