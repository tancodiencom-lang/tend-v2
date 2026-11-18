#!/bin/bash
# Sao luu Tend moi ra Google Drive (da ma hoa), cung kho rclone voi ban cu, thu muc rieng tend-v2/.
# Chay tu dong 02:30 (tend-v2-saoluu.timer). Chay tay: sudo systemctl start tend-v2-saoluu
set -euo pipefail
# Chay tu thu muc user tendv2 doc duoc (vd ezdxf doc ezdxf.ini o thu muc hien tai; /home/tcd thi bi tu choi)
cd /

REMOTE=tendcrypt:tend-v2/
KEEP_DAILY=14
KEEP_WEEKLY=8
KEEP_MONTHLY=12
LOG=/var/log/tend-v2-saoluu.log
STATE=/var/lib/tend-v2-saoluu
STAMP=$(date +%Y-%m-%d_%H%M)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

log() { echo "$(date '+%d/%m/%Y %H:%M:%S') $*" | tee -a "$LOG"; }
mkdir -p "$STATE"
log "Bat dau sao luu Tend moi $STAMP"

# 1. Co so du lieu (khoi phuc bang pg_restore)
sudo -u postgres pg_dump -Fc tendv2 > "$TMP/tendv2-db.dump"

# 2. Bi mat, cau hinh Caddy va anh / chung tu (MEDIA_ROOT)
FILES=(srv/tend-v2/.env etc/caddy/sites/app.tend.vn.caddy)
[ -d /srv/tend-v2/media ] && FILES+=(srv/tend-v2/media)
tar czf "$TMP/tendv2-cau-hinh.tar.gz" -C / "${FILES[@]}"
SIZE=$(du -sh "$TMP" | cut -f1)

# 3. Day len Drive va kiem tra lai
rclone copy "$TMP" "${REMOTE}hang-ngay/$STAMP" --quiet
rclone cryptcheck "$TMP" "${REMOTE}hang-ngay/$STAMP" --one-way --quiet
if [ "$(date +%u)" = 7 ]; then rclone copy "$TMP" "${REMOTE}hang-tuan/$STAMP" --quiet; fi
if [ "$(date +%d)" = 01 ]; then rclone copy "$TMP" "${REMOTE}hang-thang/$STAMP" --quiet; fi

# 4. Giu so ban theo quy dinh (ban cu vao thung rac Drive, con 30 ngay)
prune() {
  local folder=$1 keep=$2
  rclone lsf --dirs-only "${REMOTE}$folder" 2>/dev/null | sort -r | tail -n +$((keep + 1)) | while read -r old; do
    rclone purge "${REMOTE}$folder/${old%/}" --quiet && log "Da xoa ban cu $folder/${old%/}"
  done || true
}
prune hang-ngay "$KEEP_DAILY"
prune hang-tuan "$KEEP_WEEKLY"
prune hang-thang "$KEEP_MONTHLY"

date '+%d/%m/%Y %H:%M' > "$STATE/lan-cuoi-thanh-cong"
log "Xong sao luu Tend moi $STAMP ($SIZE)"
