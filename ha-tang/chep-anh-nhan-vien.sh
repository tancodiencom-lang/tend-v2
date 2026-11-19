#!/bin/bash
# Chep anh nhan vien tu Tend cu sang Tend moi (CHI DOC ban cu). Chi cho nguoi chua co anh o ban moi.
#   Xem truoc:  sudo bash /srv/tend-v2/app/ha-tang/chep-anh-nhan-vien.sh
#   Chep that:  sudo bash /srv/tend-v2/app/ha-tang/chep-anh-nhan-vien.sh --thuc-hien
set -euo pipefail
cd /srv/tend-v2
APP=/srv/tend-v2/app
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

echo "== 1. Sao CSDL cu thanh ban sao tend_ban_sao (CSDL goc chi duoc doc) =="
sudo -u postgres dropdb --if-exists tend_ban_sao
sudo -u postgres createdb -O tendv2 tend_ban_sao
sudo -u postgres pg_dump -Fc tend | sudo -u tendv2 pg_restore --no-owner --no-acl -d tend_ban_sao

echo "== 2. Sao thu muc anh nhan vien cua ban cu ra thu muc tam =="
mkdir -p "$TMP/nhan-vien"
[ -d /srv/tend/media/nhan-vien/anh ] && cp -r /srv/tend/media/nhan-vien/anh "$TMP/nhan-vien/"
chown -R tendv2:tendv2 "$TMP"

echo "== 3. Chep anh =="
sudo -u tendv2 -H "$APP/.venv/bin/python" "$APP/manage.py" chep_anh_nhan_vien --nguon postgresql:///tend_ban_sao --thu-muc "$TMP" "$@"
