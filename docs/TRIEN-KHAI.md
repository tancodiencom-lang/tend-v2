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

4. Giám đốc nhập **số điện thoại** cho từng tài khoản (app.tend.vn › Tài khoản › Thiết lập › Người dùng › chọn người ›
   Điện thoại) — đúng số người đó đăng ký Telegram.
5. Mỗi người vào app.tend.vn › Tài khoản › Kết nối Telegram › Lấy mã kết nối, nhắn mã cho bot, rồi bấm nút 📱 bot gửi để
   chia sẻ số điện thoại. Số trùng mới kết nối; lộ mã cũng không sao (khác số thì bị từ chối, Giám đốc được báo).

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

Phải thấy `tend-v2-saoluu.timer` (02:30), `tend-v2-goc.timer` (mỗi giờ, phút 41), `tend-v2-kiemtra.timer` (08:07), `tend-v2-khoiphuc.timer` (ngày 3 hằng tháng) và `tend-v2-troly.timer` (mỗi 10 phút). Chạy thử sao lưu một lần:

```bash
sudo systemctl start tend-v2-saoluu && tail -3 /var/log/tend-v2-saoluu.log
```

## Kho tài liệu gốc trên Google Drive (đối chiếu)

Mọi ảnh gửi bot (ảnh công trường, phiếu viết tay, ảnh chuyển khoản…) được giữ **nguyên tệp gốc** trên máy chủ. Mỗi giờ
(phút 41) máy chủ xếp chúng theo loại và ngày rồi chép lên Google Drive công ty — **không mã hoá**, mở Drive là xem được:

| Thư mục trên Drive (`Tend - Tài liệu gốc/`) | Chứa gì | Nên chia sẻ cho |
|---|---|---|
| `Hiện trường/<mã công trình>/<tháng>/<ngày>/` | ảnh của báo cáo đã xác nhận, tên tệp có giờ, người gửi, mã chi tiết | giám sát, thiết kế của công trình đó |
| `Chứng từ thu chi/<tháng>/<ngày>/` | ảnh phiếu chi, hoá đơn, chuyển khoản của phiếu đã ghi (trừ thu khách) | kế toán nội bộ |
| `Thu tiền khách/<tháng>/<ngày>/` | chứng từ thu tiền khách | kế toán tài chính |
| `Chưa ghi phiếu/<tháng>/<ngày>/` | ảnh không gắn phiếu, phiếu bị bỏ/hết hạn/lỗi | chỉ Giám đốc |

Mỗi thư mục ngày có `danh-sach.csv` (mở bằng Excel): giờ nhận, người gửi, công trình, số tiền, đối tượng, nội dung tin
nhắn gốc, tên tệp; có cả phiếu chỉ gửi bằng chữ (tin nhắn chuyển khoản dán vào chat). Ảnh chỉ được chép khi phiếu đã
chốt (xác nhận/bỏ, hoặc hết hạn sau 24 giờ), nên tên và chỗ tệp không đổi. Drive chỉ được **thêm**, không bị xoá gì.

Dùng kho rclone `gdrive:` mà bản cũ đã cài cho sao lưu; script triển khai tự bật hẹn giờ `tend-v2-goc.timer` nếu thấy
kho này. Thư mục Drive nằm ở Drive của tài khoản đã cấp quyền cho rclone; anh chia sẻ từng thư mục con theo bảng trên
(bấm chuột phải › Chia sẻ). Muốn đổi chỗ, thêm dòng `TEND_GOC_DRIVE=<kho>:<thư mục>` vào `/srv/tend-v2/.env`
(`TEND_GOC_DRIVE=tat` để chỉ giữ trên máy chủ).

Chạy thử ngay, xem kết quả:

```bash
sudo systemctl start tend-v2-goc && tail -3 /var/log/tend-v2-goc.log
```

Giám sát, kế toán: phiếu viết tay, ảnh chuyển khoản nên gửi bot dạng **Tệp** (📎 › Tệp) để giữ đúng độ nét; gửi dạng
Ảnh thì Telegram nén bớt. Bot nhận JPG, PNG, WEBP; PDF/HEIC thì chụp màn hình rồi gửi.

## Đề phòng mất dữ liệu: kiểm tra mỗi sáng và thử khôi phục mỗi tháng

| Việc | Khi nào | Báo Telegram |
|---|---|---|
| Kiểm tra (`tend-v2-kiemtra.timer`) | 08:07 mỗi sáng | Khi sao lưu quá 30 giờ chưa thành công, chép tài liệu gốc quá 3 giờ chưa thành công, thử khôi phục quá 40 ngày, app không chạy, ổ đĩa dùng ≥ 85%. **Sáng thứ Hai** gửi bản "mọi việc ổn" để biết cảnh báo vẫn sống |
| Thử khôi phục (`tend-v2-khoiphuc.timer`) | 03:30 ngày 3 hằng tháng | Tải bản sao lưu mới nhất từ Drive, giải mã, khôi phục vào CSDL **tạm**, so số tài khoản/công trình/báo cáo/phiếu/nhật ký với dữ liệu thật, đếm ảnh trong bản sao lưu, rồi xoá CSDL tạm. Đạt: gửi số liệu. Không đạt: cảnh báo đỏ |

Tin gửi về nơi nhận cảnh báo của bản cũ (`/etc/tend/canh-bao.env`, đã cài bằng `cai-canh-bao.sh` của bản cũ), mở
đầu bằng `app.tend.vn` để phân biệt. Dữ liệu thật không bị đụng: thử khôi phục chỉ đọc CSDL `tendv2` để đếm.

Chạy thử ngay một lần (mất vài phút, cần chỗ trống gấp đôi bản sao lưu):

```bash
sudo bash /srv/tend-v2/app/ha-tang/thu-khoi-phuc.sh
```

```bash
sudo systemctl start tend-v2-kiemtra
```

## Trợ lý Giám đốc

Triển khai xong là trợ lý chạy (hẹn giờ `tend-v2-troly.timer`, mỗi 10 phút), dùng chính bot Telegram của Tend mới. Mọi người
cần **kết nối Telegram** (Tài khoản › Kết nối Telegram) thì trợ lý mới nhắn được; bản tin sáng liệt kê ai chưa kết nối.

| Giờ (thứ Hai–thứ Bảy) | Trợ lý làm |
|---|---|
| 07:30 | Bản tin sáng cho Giám đốc; việc hôm nay cho từng người |
| 16:00, 17:00 | Nhắc giám sát công trình chưa có báo cáo ngày |
| 18:00 | Nhắc phiếu chi trong ngày chưa có ảnh chứng từ |
| 18:30 | Tóm tắt ngày cho Giám đốc (thay tóm tắt thu chi 18:13 cũ) |
| 19:00 | Báo Giám đốc công trình vẫn chưa có báo cáo |
| Trong giờ | Nhắc việc sắp đến hạn / quá hạn, báo Giám đốc nếu vẫn trễ sau 4 giờ, cảnh báo Gấp |

Giám đốc giao việc: nhắn bot `/giao Nam gửi bản vẽ điện TN-22 hạn 20/11 9:00 - đủ ổ cắm, công tắc` (hoặc nói tự nhiên khi
đã bật AI), bấm ✓ Xác nhận. Đổi ngày làm việc: thêm `TEND_WORK_DAYS=1,2,3,4,5` (thứ Hai–thứ Sáu) vào `/srv/tend-v2/.env`.
Chạy thử một lượt: `sudo -u tendv2 bash -c 'cd /srv/tend-v2/app && .venv/bin/python manage.py tro_ly'`.

## Nhóm Telegram nội bộ và hỏi Giám đốc qua trợ lý

1. Cho bot đọc được mọi tin trong nhóm (làm một lần): Telegram › @BotFather › `/setprivacy` › chọn bot › **Disable**.
   (Không làm bước này thì bot chỉ thấy lệnh và tin gọi @tên bot.) Nhóm đã có bot từ trước: xoá bot khỏi nhóm rồi thêm lại.
2. Thêm bot vào nhóm (vd nhóm công trường TN-22). Bot tự chào và hướng dẫn.
3. Giám đốc nhắn trong nhóm: `/nhom TN-22` (nhóm của công trình) hoặc `/nhom chung`. Thôi dùng: `/nhom tat`.
   Chỉ muốn bot làm khi được gọi: `/nhom khongnghe` (bật lại: `/nhom nghe`).
4. Mọi người trong nhóm phải đã **kết nối Tend riêng** với bot; người chưa kết nối bot bỏ qua.

Trong nhóm: báo cáo/phiếu bot lập và **gửi riêng** người nhắn bấm Xác nhận; gọi bot (@tên bot) để hỏi tiến độ — trả lời
trong nhóm **không có số tiền**; câu khó bot hỏi Giám đốc (tin riêng, nút ✍ Tôi trả lời / ✓ Gửi bản nháp) rồi trả lời
lại trong nhóm. Nhân viên cũng hỏi riêng được: `/hoi <câu hỏi>`.

## Ảnh nhân viên

Mỗi người đổi ảnh ở app.tend.vn › mục có tên mình (cuối thanh dưới) › Ảnh nhân viên. Chép ảnh đã có ở bản cũ (chỉ cho
người chưa có ảnh; xem trước rồi mới chép):

```bash
sudo bash /srv/tend-v2/app/ha-tang/chep-anh-nhan-vien.sh
```

```bash
sudo bash /srv/tend-v2/app/ha-tang/chep-anh-nhan-vien.sh --thuc-hien
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
sudo systemctl disable --now tend-v2 tend-v2-saoluu.timer tend-v2-tomtat.timer tend-v2-goc.timer tend-v2-kiemtra.timer tend-v2-khoiphuc.timer tend-v2-troly.timer
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
