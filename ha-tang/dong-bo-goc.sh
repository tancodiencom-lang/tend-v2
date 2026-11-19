#!/bin/bash
# Kho tai lieu goc: xep anh/chung tu goc nhan qua chat theo loai va ngay, roi chep len Google Drive (KHONG ma hoa,
# xem truc tiep tren Drive de doi chieu). Chay moi gio (tend-v2-goc.timer). Chay tay: sudo systemctl start tend-v2-goc
# Chi THEM/cap nhat tep tren Drive, khong bao gio xoa. Ban cu (so.tend.vn) khong bi dung toi.
set -euo pipefail
cd /

DIR=/srv/tend-v2/tai-lieu-goc
LOG=/var/log/tend-v2-goc.log
STATE=/var/lib/tend-v2-goc
log() { echo "$(date '+%d/%m/%Y %H:%M:%S') $*" | tee -a "$LOG"; }
mkdir -p "$STATE"
install -d -m 750 -o tendv2 -g tendv2 "$DIR"

# Thu muc tren Drive: dong TEND_GOC_DRIVE trong /srv/tend-v2/.env (mac dinh "gdrive:Tend - Tài liệu gốc"; "tat" = khong chep)
REMOTE=$(grep -m1 '^TEND_GOC_DRIVE=' /srv/tend-v2/.env 2>/dev/null | cut -d= -f2- || true)
REMOTE=${REMOTE:-"gdrive:Tend - Tài liệu gốc"}

# 1. Xep tep (user tendv2, chi doc CSDL va tao lien ket cung, khong ghi du lieu nghiep vu)
OUT=$(runuser -u tendv2 -- bash -c 'cd /srv/tend-v2/app && .venv/bin/python manage.py xuat_tai_lieu_goc' 2>&1)
log "$OUT"

# 2. Chep len Drive (chi tep moi/doi; khong xoa gi tren Drive)
if [ "$REMOTE" = "tat" ]; then
  log "TEND_GOC_DRIVE=tat: chi xep tep tren may chu, khong chep len Drive"
  exit 0
fi
# Thu toi 3 lan, cach 30 giay: luc rclone (hoac sao luu ban cu) dang ghi lai tep cau hinh de lam moi quyen Google,
# doc tep co the hut kho 'gdrive:' hoac chep loi tam thoi. Chi bao loi khi ca 3 lan deu hong.
ok=""
for attempt in 1 2 3; do
  if rclone listremotes 2>/dev/null | grep -qx "${REMOTE%%:*}:" \
     && rclone copy "$DIR" "$REMOTE" --exclude '*.tam' --quiet; then
    ok=1
    break
  fi
  [ "$attempt" -lt 3 ] && sleep 30
done
if [ -z "$ok" ]; then
  log "LOI: 3 lan chua chep duoc len $REMOTE (kho rclone '${REMOTE%%:*}:' khong doc duoc hoac mang loi). Anh/chung tu van tren may chu, gio sau thu lai."
  exit 1
fi
date '+%d/%m/%Y %H:%M' > "$STATE/lan-cuoi-thanh-cong"
log "Da chep kho tai lieu goc len $REMOTE"
