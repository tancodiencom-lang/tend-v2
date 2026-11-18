# Triển khai Tend mới lên app.tend.vn (cùng máy chủ với so.tend.vn)

Tend mới chạy **song song** với bản cũ trên cùng droplet, không tốn thêm tiền:

| | Bản cũ (giữ nguyên) | Tend mới |
|---|---|---|
| Tên miền | so.tend.vn | app.tend.vn (DNS `app` → 168.144.139.252, đã thêm 18/11/2026) |
| User hệ thống | `tend` | `tendv2` |
| Mã, bí mật, ảnh | /srv/tend/{app,.env,media} | /srv/tend-v2/{app,.env,media} |
| CSDL | `tend` | `tendv2` (đăng nhập qua socket, không mật khẩu) |
| Dịch vụ | `tend` (cổng nội bộ 8001) | `tend-v2` (cổng nội bộ 8002, 2 tiến trình) |
| Sao lưu | 02:00 → Drive | 02:30 → cùng kho Drive mã hoá, thư mục `tend-v2/` |
| Bot Telegram | bot hiện tại | **bot riêng** (tạo mới, miễn phí) |

Caddy dùng chung: Caddyfile của bản cũ có dòng `import /etc/caddy/sites/*.caddy`; Tend mới chỉ đặt tệp riêng
`/etc/caddy/sites/app.tend.vn.caddy` (kiểm `caddy validate` trước khi nạp; sai thì tự gỡ, so.tend.vn không ảnh hưởng).

Mọi lệnh chạy trong **web console của DigitalOcean**, đăng nhập user `tcd` (lệnh `sudo` hỏi mật khẩu tcd: anh tự gõ).
Không dán mật khẩu, mã bot hay khoá API vào chat.

## Bước 1 — Cập nhật bản cũ để có dòng import Caddy và script cài đặt

Gộp PR "Caddy import + script cài Tend mới" của repo **tend-app**, rồi GitHub › tend-app › Actions › Triển khai lên
so.tend.vn › Run workflow (nhánh main). Bản cũ chỉ đổi Caddyfile (thêm một dòng) và có thêm tệp script.

## Bước 2 — Cài lần đầu (tạo user, CSDL, .env, khoá đọc mã)

```bash
sudo bash /srv/tend/app/ha-tang/cai-tend-v2.sh
```

Lần chạy đầu in RAM/đĩa còn trống và **một dòng khoá công khai** `ssh-ed25519 …`. Chép dòng đó vào
GitHub › **tend-v2** › Settings › Deploy keys › Add deploy key (Title: `May chu app.tend.vn`, **không** tích Allow write
access) › Add key. Khoá công khai không phải bí mật.

Script tự chép khoá trợ lý AI (`ANTHROPIC_API_KEY`) từ bản cũ nếu bản cũ đã có, không in ra màn hình.

## Bước 3 — Chạy lại lệnh cài: lấy mã và triển khai

```bash
sudo bash /srv/tend/app/ha-tang/cai-tend-v2.sh
```

Lần này script lấy mã từ GitHub rồi chạy `trien-khai.sh` của Tend mới: cài gói, tạo bảng, dịch vụ, hẹn giờ, Caddy,
kiểm tra. Cuối cùng in `app.tend.vn: trang dang nhap HTTP 200` và `XONG TRIEN KHAI TEND MOI`.

## Bước 4 — Chép dữ liệu đầu từ bản cũ (chỉ đọc bản cũ)

Xem trước (không ghi gì):

```bash
sudo bash /srv/tend-v2/app/ha-tang/chep-du-lieu-cu.sh
```

Script sao CSDL cũ thành bản sao `tend_ban_sao` (CSDL gốc chỉ được đọc để sao), rồi in: tài khoản sẽ chép và vai,
công trình, số chi tiết, số chi tiết thiếu giá, tài khoản bị bỏ (vai Bán hàng / Kế toán bán hàng chưa có ở bản mới).

Đồng ý thì chép thật, nhật ký đứng tên Giám đốc của bản cũ (thay `quan` nếu tên đăng nhập khác):

```bash
sudo bash /srv/tend-v2/app/ha-tang/chep-du-lieu-cu.sh --thuc-hien --nguoi quan
```

Mọi người đăng nhập app.tend.vn bằng **tên và mật khẩu cũ**. Tiến độ mọi chi tiết bắt đầu 0%; báo cáo, ảnh, chứng từ
cũ vẫn ở so.tend.vn. Chạy lại lệnh chép không chép trùng (bỏ qua công trình/tài khoản đã có).

## Bước 5 — Bot Telegram riêng cho Tend mới

1. Trong Telegram, nhắn **@BotFather** › `/newbot` › đặt tên (vd "Tend Tân Cổ Điển") và tên bot kết thúc bằng `bot`.
   BotFather trả một mã dạng `123456:ABC…`.
2. Mở tệp bí mật và tự dán mã vào dòng `TELEGRAM_TOKEN=` (Ctrl+O, Enter để lưu; Ctrl+X để thoát):

   ```bash
   sudo nano /srv/tend-v2/.env
   ```

3. Triển khai lại để đặt địa chỉ nhận tin cho bot:

   ```bash
   sudo bash /srv/tend-v2/app/ha-tang/trien-khai.sh
   ```

4. Mỗi người vào app.tend.vn › Tài khoản › Kết nối Telegram › Lấy mã kết nối.

Muốn dùng chung bot cũ thay vì bot mới: chép dòng `TELEGRAM_TOKEN=` của `/srv/tend/.env` sang `/srv/tend-v2/.env` rồi
triển khai lại — từ đó bot trả lời ở bản mới, bản cũ thôi nhận báo cáo qua chat.

## Bật trợ lý AI sau (nếu bước 2 chưa chép được khoá)

Tạo khoá ở Claude Console của công ty (đặt hạn mức chi tiêu tháng), rồi tự thêm dòng `ANTHROPIC_API_KEY=<khoá>` vào
`/srv/tend-v2/.env` bằng `sudo nano` như trên, và:

```bash
sudo systemctl restart tend-v2
```

Trần chi phí trong Tend: `TEND_AI_MONTHLY_USD=4` (≈ 100.000 ₫/tháng); vượt trần bot tự dùng cú pháp ngắn.

## Kiểm tra sau triển khai

```bash
systemctl is-active tend-v2 tend
```

```bash
systemctl list-timers 'tend*'
```

Phải thấy `tend-v2-saoluu.timer` (02:30) và `tend-v2-tomtat.timer` (18:13). Chạy thử sao lưu một lần:

```bash
sudo systemctl start tend-v2-saoluu && tail -3 /var/log/tend-v2-saoluu.log
```

## Các lần sau: nút Triển khai trên GitHub (tuỳ chọn, như bản cũ)

Trên máy tính của anh tạo một cặp khoá mới (không đặt passphrase), khoá **công khai** cài lên máy chủ:

```bash
sudo bash /srv/tend-v2/app/ha-tang/cai-trien-khai-tu-dong.sh "ssh-ed25519 AAAA… trien-khai-tend-v2"
```

Khoá **riêng** dán vào GitHub › tend-v2 › Settings › Secrets › `DEPLOY_SSH_KEY`; `DEPLOY_KNOWN_HOSTS` chép giống repo
tend-app. Từ đó: GitHub › tend-v2 › Actions › Triển khai lên app.tend.vn › Run workflow (nhánh main). Khoá này chỉ chạy
được đúng lệnh triển khai Tend mới.

Không dùng nút thì triển khai tay: `sudo bash /srv/tend-v2/app/ha-tang/trien-khai.sh` (lấy mã mới nhất nhánh main).

## Gỡ Tend mới (nếu cần quay lại chỉ dùng bản cũ)

```bash
sudo systemctl disable --now tend-v2 tend-v2-saoluu.timer tend-v2-tomtat.timer
```

```bash
sudo rm /etc/caddy/sites/app.tend.vn.caddy && sudo systemctl reload caddy
```

CSDL `tendv2`, `/srv/tend-v2` và bản sao lưu trên Drive vẫn giữ (xoá chỉ khi Giám đốc đồng ý rõ ràng). Bản cũ không
bị đụng ở bất kỳ bước nào.

## Đã kiểm trước khi giao (18/11/2026)

- 74/74 bài kiểm đạt trên **PostgreSQL 16** và SQLite; `check --deploy` với cấu hình thật (DEBUG=False) không lỗi.
- Diễn tập đúng các lệnh của bước 4 trên PostgreSQL: CSDL "bản cũ" dựng bằng mã tend-app (main) → `pg_dump | pg_restore`
  → chép: đúng công trình/chi tiết/tài khoản, mật khẩu cũ đăng nhập được, nhật ký đứng tên Giám đốc bản cũ, CSDL cũ
  không đổi. Diễn tập phát hiện và đã sửa: giá trị hợp đồng lẻ đồng (PostgreSQL), khoá dòng kèm cột có thể trống.
- `bash -n` mọi script. **Chưa** kiểm `caddy validate` ở máy thử (không tải được Caddy); trên máy chủ script tự kiểm
  trước khi nạp và gỡ nếu sai. Chưa chạy trên máy chủ thật, chưa thử bot/Claude API thật.
