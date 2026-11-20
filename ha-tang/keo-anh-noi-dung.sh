#!/bin/bash
# Tro ly noi dung: keo anh tu cac thu muc Google Drive (anh thiet ke, anh cong trinh) ve may chu de tro ly xem va chon cho
# bai dang. CHI DOC tren Drive (rclone copy tu Drive ve, khong ghi/xoa gi tren Drive). Chay moi ngay 06:23
# (tend-v2-keoanh.timer). Chay tay: sudo systemctl start tend-v2-keoanh
# Thu muc: dong TEND_MKT_DRIVE trong /srv/tend-v2/.env, vd "gdrive:Thiết kế 3D;gdrive:Ảnh công trình" (cach nhau bang ;)
# Trong de trong hoac "tat": khong keo.
set -euo pipefail
cd /

DEST=/srv/tend-v2/media/noi-dung-drive
LOG=/var/log/tend-v2-keoanh.log
log() { echo "$(date '+%d/%m/%Y %H:%M:%S') $*" | tee -a "$LOG"; }
SRC=$(grep -m1 '^TEND_MKT_DRIVE=' /srv/tend-v2/.env 2>/dev/null | cut -d= -f2- || true)
if [ -z "$SRC" ] || [ "$SRC" = "tat" ]; then
  log "TEND_MKT_DRIVE trong hoac 'tat': khong keo anh tu Drive"
else
  install -d -m 750 -o tendv2 -g tendv2 "$DEST"
  IFS=';' read -ra FOLDERS <<< "$SRC"
  for folder in "${FOLDERS[@]}"; do
    folder="$(echo "$folder" | sed 's/^ *//;s/ *$//')"
    [ -z "$folder" ] && continue
    name="${folder#*:}"; name="${name//\//-}"; name="${name:-goc}"
    # Chi anh, toi da 30 MB/anh; giu cau truc thu muc (thu muc con co ma cong trinh thi tro ly gan dung cong trinh)
    if rclone copy "$folder" "$DEST/$name" --include '*.{jpg,jpeg,png,webp,JPG,JPEG,PNG,WEBP}' --max-size 30M --quiet; then
      log "Da keo anh tu $folder"
    else
      log "LOI: chua keo duoc anh tu $folder (kho rclone hoac duong dan sai)"
    fi
  done
  chown -R tendv2:tendv2 "$DEST"
fi
# Ghi anh moi vao kho noi dung (user tendv2; chi them, khong xoa)
OUT=$(runuser -u tendv2 -- bash -c 'cd /srv/tend-v2/app && .venv/bin/python manage.py thu_anh_noi_dung' 2>&1)
log "$OUT"
