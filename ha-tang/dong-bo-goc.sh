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
# doc tep co the hut kho 'gdrive:' hoac chep loi tam thoi. Chi bao loi khi ca 3 lan deu hong, KEM LY DO (loi rclone
# that, da che chuoi dai giong khoa) de tin canh bao Telegram cho biet vi sao, khong phai doan.
NAME="${REMOTE%%:*}:"
hide() { tr '\n' ' ' | sed -E 's/[A-Za-z0-9_.+=-]{40,}/[da che]/g' | cut -c1-"$1"; }
ok=""
why=""
for attempt in 1 2 3; do
  remotes=$(rclone listremotes 2>&1) || true          # doc het roi moi tim (khong noi ong vao grep -q)
  if ! grep -qx -- "$NAME" <<<"$remotes"; then
    why="rclone khong thay kho '$NAME' trong cau hinh: $(rclone config file 2>&1 | hide 160) Cac kho dang co: $(printf '%s' "${remotes:-khong co}" | hide 160)"
  elif err=$(rclone copy "$DIR" "$REMOTE" --exclude '*.tam' --quiet 2>&1); then
    ok=1
    break
  else
    why="rclone copy bao loi: $(printf '%s' "$err" | tail -n 4 | hide 400)"
  fi
  [ "$attempt" -lt 3 ] && sleep 30
done
if [ -z "$ok" ]; then
  log "LOI: 3 lan chua chep duoc len $REMOTE. Anh/chung tu van tren may chu, gio sau thu lai."
  log "Ly do: $(printf '%s' "$why" | cut -c1-270)"
  exit 1
fi
date '+%d/%m/%Y %H:%M' > "$STATE/lan-cuoi-thanh-cong"
log "Da chep kho tai lieu goc len $REMOTE"
