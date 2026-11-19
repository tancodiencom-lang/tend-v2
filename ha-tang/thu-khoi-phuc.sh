#!/bin/bash
# Thu khoi phuc ban sao luu MOI NHAT cua Tend moi vao co so du lieu TAM (khong dung du lieu that), roi xoa.
# Chay tu dong ngay 3 hang thang 03:30 (tend-v2-khoiphuc.timer); chay tay: sudo bash /srv/tend-v2/app/ha-tang/thu-khoi-phuc.sh
# Dat: bao Telegram so lieu. Khong dat: thoat loi -> canh bao Telegram (OnFailure).
set -euo pipefail
cd /
ROOT=${TEND_V2_ROOT:-/srv/tend-v2}
VAR=${TEND_V2_VAR:-/var/lib}
SEND=${TEND_V2_SEND:-$ROOT/app/ha-tang/gui-canh-bao.sh}
REMOTE=${TEND_V2_BACKUP_REMOTE:-tendcrypt:tend-v2/}
REALDB=${TEND_V2_DB:-tendv2}
TESTDB=tendv2_thu_khoi_phuc
TMP=$(mktemp -d -p "${TEND_V2_TMP:-/var/tmp}")
cleanup() { sudo -u postgres dropdb --if-exists "$TESTDB" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap cleanup EXIT
fail() { echo "LOI: $*"; exit 1; }
count() { sudo -u postgres psql -tAq -d "$1" -c "SELECT count(*) FROM $2"; }

LATEST=$(rclone lsf --dirs-only "${REMOTE}hang-ngay" | sort -r | head -1)
LATEST=${LATEST%/}
[ -n "$LATEST" ] || fail "chua co ban sao luu nao tren Drive (${REMOTE}hang-ngay)"

echo "== 1. Tai ban sao luu $LATEST tu Drive va giai ma =="
NEED=$(rclone size --json "${REMOTE}hang-ngay/$LATEST" | python3 -c 'import json, sys; print(json.load(sys.stdin)["bytes"])')
FREE=$(( $(df -Pk "$TMP" | awk 'NR==2 {print $4}') * 1024 ))
[ "$FREE" -gt $(( NEED * 2 + 1073741824 )) ] || fail "o dia khong du cho de thu (can ~$(( NEED * 2 / 1048576 + 1024 )) MB, con $(( FREE / 1048576 )) MB)"
rclone copy "${REMOTE}hang-ngay/$LATEST" "$TMP" --quiet
[ -s "$TMP/tendv2-db.dump" ] || fail "ban sao luu thieu tep co so du lieu"
[ -s "$TMP/tendv2-cau-hinh.tar.gz" ] || fail "ban sao luu thieu tep cau hinh/anh"

echo "== 2. Khoi phuc vao co so du lieu tam $TESTDB =="
sudo -u postgres dropdb --if-exists "$TESTDB" 2>/dev/null || true
sudo -u postgres createdb "$TESTDB"
# Doc tep qua dau vao chuan vi user postgres khong vao duoc thu muc tam cua root
sudo -u postgres pg_restore --no-owner --no-privileges --exit-on-error -d "$TESTDB" < "$TMP/tendv2-db.dump" \
  || fail "pg_restore khong khoi phuc duoc co so du lieu"

echo "== 3. So voi du lieu that (ban sao luu cu hon vai gio nen co the it hon, khong duoc nhieu hon hay trong) =="
SUMMARY=""
for pair in core_user:"tài khoản" projects_project:"công trình" drawings_item:"chi tiết bản vẽ" field_report:"báo cáo" \
            field_attendance:"chấm công" money_entry:"phiếu thu chi" chat_chatphoto:"ảnh nhận qua chat" core_auditlog:"dòng nhật ký"; do
  table=${pair%%:*}; label=${pair#*:}
  real=$(count "$REALDB" "$table"); restored=$(count "$TESTDB" "$table")
  echo "$table: that $real, khoi phuc $restored"
  [ "$restored" -le "$real" ] || fail "$table: khoi phuc ($restored) nhieu hon du lieu that ($real)"
  [ "$real" -eq 0 ] || [ "$restored" -gt 0 ] || fail "$table: du lieu that co $real dong ma ban sao luu trong"
  SUMMARY+=$'\n'"• $label: $restored"
done

echo "== 4. Tep cau hinh va anh =="
LIST=$(tar tzf "$TMP/tendv2-cau-hinh.tar.gz") || fail "tep cau hinh/anh hong, khong mo duoc"
grep -qx "${ROOT#/}/.env" <<<"$LIST" || fail "ban sao luu thieu tep bi mat .env"
IN_BACKUP=$(grep -c "^${ROOT#/}/media/.*[^/]$" <<<"$LIST" || true)
WHEN=$(sed -E 's/^([0-9-]+)_([0-9]{2})([0-9]{2})$/\1 \2:\3/' <<<"$LATEST")
EXPECTED=0
[ -d "$ROOT/media" ] && EXPECTED=$(find "$ROOT/media" -type f ! -newermt "$WHEN" | wc -l)
echo "Anh/chung tu: ban sao luu $IN_BACKUP, tren may chu truoc luc sao luu $EXPECTED"
[ "$IN_BACKUP" -ge "$EXPECTED" ] || fail "ban sao luu chi co $IN_BACKUP anh/chung tu, may chu co $EXPECTED"

mkdir -p "$VAR/tend-v2-khoiphuc"
date '+%d/%m/%Y %H:%M' > "$VAR/tend-v2-khoiphuc/lan-cuoi-thanh-cong"
MSG="✅ app.tend.vn – thử khôi phục bản sao lưu $LATEST: ĐẠT (dữ liệu tạm đã xoá)$SUMMARY
• ảnh, chứng từ: $IN_BACKUP tệp"
echo "$MSG"
/bin/bash $SEND "$MSG" || true
echo "== XONG THU KHOI PHUC =="
