#!/bin/bash
# Dua ban moi nhat cua Tend moi (app.tend.vn) tu GitHub len may chu. Dung cho lan dau va moi lan sau.
# Chay bang: sudo bash /srv/tend-v2/app/ha-tang/trien-khai.sh
# Ban cu (so.tend.vn, user tend, DB tend) KHONG bi dung toi.
# Boc trong { ... } de bash doc het tep truoc khi chay (git pull co the thay chinh tep nay).
{
set -euo pipefail
# Chay tu thu muc user tendv2 doc duoc (vd ezdxf doc ezdxf.ini o thu muc hien tai; /home/tcd thi bi tu choi)
cd /srv/tend-v2
ROOT=/srv/tend-v2
APP=$ROOT/app
PY=$APP/.venv/bin/python
as_v2() { sudo -u tendv2 -H "$@"; }

echo "== 1. Lay ma moi nhat tu GitHub =="
as_v2 git -C "$APP" pull -q --ff-only
as_v2 git -C "$APP" log --oneline -1
as_v2 ln -sfn "$ROOT/.env" "$APP/.env"
install -d -m 750 -o tendv2 -g tendv2 "$ROOT/media" "$ROOT/tai-lieu-goc"

echo "== 2. Luu moc CSDL truoc khi doi (giu 5 ban gan nhat) =="
install -d -m 700 /var/backups/tend-v2
if sudo -u postgres psql -tAc "select 1 from pg_database where datname='tendv2'" | grep -q 1; then
  sudo -u postgres pg_dump -Fc tendv2 > "/var/backups/tend-v2/truoc-trien-khai-$(date +%Y%m%d-%H%M).dump"
  ls -1t /var/backups/tend-v2/truoc-trien-khai-*.dump | tail -n +6 | xargs -r rm -f
fi

echo "== 3. Cai goi Python =="
[ -d "$APP/.venv" ] || as_v2 python3 -m venv "$APP/.venv"
as_v2 "$APP/.venv/bin/pip" install -q --disable-pip-version-check -r "$APP/requirements.txt" -r "$APP/requirements-server.txt"

echo "== 4. Cap nhat CSDL, tep giao dien, kiem tra cau hinh =="
as_v2 "$PY" "$APP/manage.py" migrate --noinput
as_v2 "$PY" "$APP/manage.py" collectstatic --noinput -v 0
as_v2 "$PY" "$APP/manage.py" check --deploy --fail-level ERROR

echo "== 5. Dich vu va hen gio =="
install -m 644 "$APP/ha-tang/tend-v2.service" /etc/systemd/system/tend-v2.service
for unit in tend-v2-canh-bao@.service tend-v2-tomtat.service tend-v2-tomtat.timer tend-v2-saoluu.service tend-v2-saoluu.timer \
            tend-v2-goc.service tend-v2-goc.timer tend-v2-kiemtra.service tend-v2-kiemtra.timer \
            tend-v2-khoiphuc.service tend-v2-khoiphuc.timer tend-v2-troly.service tend-v2-troly.timer \
            tend-v2-dangbai.service tend-v2-dangbai.timer tend-v2-keoanh.service tend-v2-keoanh.timer; do
  install -m 644 "$APP/ha-tang/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable -q tend-v2
systemctl restart tend-v2
systemctl enable -q --now tend-v2-kiemtra.timer tend-v2-troly.timer
# Tom tat thu chi 18:13 da gop vao tom tat ngay 18:30 cua tro ly
systemctl disable -q --now tend-v2-tomtat.timer 2>/dev/null || true
echo "Da bat tro ly Giam doc (moi 10 phut: ban tin 7:30, nhac bao cao 16:00/17:00, tom tat 18:30, nhac viec, canh bao)"
systemctl enable -q --now tend-v2-dangbai.timer tend-v2-keoanh.timer
if grep -q '^TEND_META_TOKEN=.' "$ROOT/.env"; then
  echo "Da bat tro ly noi dung: 08:00 soan bai nhap, dang bai da duyet moi 2 phut"
else
  echo "Tro ly noi dung: chua co khoa TEND_META_TOKEN trong $ROOT/.env nen bai duyet xong se bao loi 'chua co khoa' (soan nhap van chay)"
fi
# Doc het danh sach kho roi moi tim: 'rclone listremotes | grep -q' voi pipefail bao nham "khong thay" tren may 1 nhan
# (grep thoat som, rclone dang in dong sau bi ngat → pipefail coi la loi). Da tai hien 21/11/2026.
REMOTES=$(rclone listremotes 2>/dev/null || true)
if grep -qx 'tendcrypt:' <<<"$REMOTES"; then
  systemctl enable -q --now tend-v2-saoluu.timer tend-v2-khoiphuc.timer
  echo "Da bat sao luu hang dem 02:30 (cung kho Drive voi ban cu, thu muc tend-v2/) va thu khoi phuc ngay 3 hang thang"
else
  echo "CANH BAO: chua thay kho rclone 'tendcrypt:' nen CHUA bat sao luu Tend moi"
fi
GOC=$(grep -m1 '^TEND_GOC_DRIVE=' "$ROOT/.env" | cut -d= -f2- || true)
GOC=${GOC:-gdrive:}
if [ "$GOC" = tat ] || grep -qx -- "${GOC%%:*}:" <<<"$REMOTES"; then
  systemctl enable -q --now tend-v2-goc.timer
  echo "Da bat kho tai lieu goc: moi gio xep anh/chung tu theo ngay, chep len Google Drive (thu muc 'Tend - Tài liệu gốc')"
else
  echo "CANH BAO: chua thay kho rclone '${GOC%%:*}:' nen CHUA bat chep tai lieu goc len Drive"
fi
if grep -q '^TELEGRAM_TOKEN=.' "$ROOT/.env"; then
  as_v2 "$PY" "$APP/manage.py" telegram_webhook || echo "Chua dat duoc webhook Telegram (xem log)"
else
  echo "Chua co ma bot Telegram cho Tend moi (TELEGRAM_TOKEN trong $ROOT/.env): bot chua chay"
fi

echo "== 6. Cau hinh Caddy (chi tep rieng cua app.tend.vn) =="
install -d -m 755 /etc/caddy/sites
if ! grep -q '^import /etc/caddy/sites/\*.caddy' /etc/caddy/Caddyfile; then
  echo "LOI: /etc/caddy/Caddyfile chua co dong 'import /etc/caddy/sites/*.caddy'."
  echo "Gop PR Caddyfile cua tend-app va bam trien khai so.tend.vn truoc, roi chay lai lenh nay."
  exit 1
fi
if ! cmp -s "$APP/ha-tang/app.tend.vn.caddy" /etc/caddy/sites/app.tend.vn.caddy; then
  [ -f /etc/caddy/sites/app.tend.vn.caddy ] && cp /etc/caddy/sites/app.tend.vn.caddy "/etc/caddy/app.tend.vn.caddy.truoc-$(date +%Y%m%d-%H%M)"
  install -m 644 "$APP/ha-tang/app.tend.vn.caddy" /etc/caddy/sites/app.tend.vn.caddy
  if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    rm -f /etc/caddy/sites/app.tend.vn.caddy
    echo "LOI: cau hinh Caddy moi khong hop le, da go ra; so.tend.vn khong bi anh huong"
    exit 1
  fi
  systemctl reload caddy
  echo "Da cap nhat Caddy cho app.tend.vn"
else
  echo "Caddy khong doi"
fi

echo "== 7. Kiem tra =="
systemctl is-active tend-v2
CODE=000
for i in $(seq 1 12); do   # lan dau Caddy can vai giay de lay chung chi HTTPS
  CODE=$(curl -s -o /tmp/tend-v2-login.html -w '%{http_code}' https://app.tend.vn/dang-nhap/ || true)
  [ "$CODE" = 200 ] && break
  sleep 5
done
if [ "$CODE" != 200 ] || ! grep -q 'csrfmiddlewaretoken' /tmp/tend-v2-login.html; then
  echo "LOI: app.tend.vn chua tra trang dang nhap (HTTP $CODE). Xem: sudo journalctl -u tend-v2 -n 50"
  exit 1
fi
echo "app.tend.vn: trang dang nhap HTTP 200, co bieu mau."
OLD=$(curl -s -o /dev/null -w '%{http_code}' https://so.tend.vn/dang-nhap/ || true)
echo "so.tend.vn (ban cu): HTTP $OLD"
rm -f /tmp/tend-v2-login.html
install -d -m 700 /var/lib/tend-v2
as_v2 git -C "$APP" rev-parse HEAD > /var/lib/tend-v2/deployed-commit
printf 'Ma da trien khai: '
cat /var/lib/tend-v2/deployed-commit
free -m | awk 'NR==2 {print "RAM con trong: " $7 " MB / " $2 " MB"}'
echo "== XONG TRIEN KHAI TEND MOI =="
exit 0
}
