#!/bin/bash
# Chep du lieu dau tu Tend cu sang Tend moi, CHI DOC ban cu.
# B1: sao CSDL cu 'tend' thanh ban sao 'tend_ban_sao' (CSDL goc chi duoc doc de sao)
# B2: chay lenh chep tren ban sao. Mac dinh chi XEM TRUOC, khong ghi.
#   Xem truoc:  sudo bash /srv/tend-v2/app/ha-tang/chep-du-lieu-cu.sh
#   Chep that:  sudo bash /srv/tend-v2/app/ha-tang/chep-du-lieu-cu.sh --thuc-hien --nguoi quan
#   (quan = ten dang nhap Giam doc o ban cu; nhat ky dung ten nguoi nay)
set -euo pipefail
APP=/srv/tend-v2/app

echo "== 1. Sao CSDL cu thanh ban sao tend_ban_sao =="
sudo -u postgres dropdb --if-exists tend_ban_sao
sudo -u postgres createdb -O tendv2 tend_ban_sao
sudo -u postgres pg_dump -Fc tend | sudo -u tendv2 pg_restore --no-owner --no-acl -d tend_ban_sao
echo "Da sao xong (CSDL goc 'tend' khong bi sua)."

echo "== 2. Chep sang Tend moi =="
sudo -u tendv2 -H "$APP/.venv/bin/python" "$APP/manage.py" chep_ban_cu --nguon postgresql:///tend_ban_sao "$@"
echo "Ban sao tend_ban_sao giu lai de doi chieu; xoa khi khong can: sudo -u postgres dropdb tend_ban_sao"
