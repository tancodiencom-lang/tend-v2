#!/bin/bash
# Cai MOT LAN de bam nut "Trien khai" tren GitHub (repo tend-v2) ma khong can mat khau sudo.
# Chay bang: sudo bash /srv/tend-v2/app/ha-tang/cai-trien-khai-tu-dong.sh "<noi dung tep khoa CONG KHAI .pub>"
# Khoa rieng chi nam o GitHub Secrets cua repo tend-v2; may chu chi nhan khoa cong khai.
set -euo pipefail
PUB="${1:-}"
case "$PUB" in ssh-ed25519\ *) ;; *) echo "Thieu khoa cong khai (phai bat dau bang ssh-ed25519)"; exit 1;; esac
USER_DEPLOY=tcd

install -m 755 -o root -g root /dev/stdin /usr/local/sbin/tend-v2-trien-khai <<'W'
#!/bin/bash
set -euo pipefail
APP=/srv/tend-v2/app
sudo -u tendv2 -H git -C "$APP" fetch -q origin main
sudo -u tendv2 -H git -C "$APP" reset -q --hard origin/main
exec bash "$APP/ha-tang/trien-khai.sh"
W

TMP=$(mktemp)
echo "$USER_DEPLOY ALL=(root) NOPASSWD: /usr/local/sbin/tend-v2-trien-khai" > "$TMP"
visudo -cf "$TMP" >/dev/null
install -m 440 -o root -g root "$TMP" /etc/sudoers.d/tend-v2-trien-khai
rm -f "$TMP"

AK="/home/$USER_DEPLOY/.ssh/authorized_keys"
LINE="command=\"sudo /usr/local/sbin/tend-v2-trien-khai\",restrict $PUB"
grep -qF "$PUB" "$AK" || echo "$LINE" >> "$AK"
echo "Xong. Khoa nay chi chay duoc lenh: sudo /usr/local/sbin/tend-v2-trien-khai"
