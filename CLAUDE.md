# Tend mới – Quản lý công trình Tân Cổ Điển (xây lại từ đầu, 18/11/2026)

Bản cũ (repo `tend-app`, nhánh `main`, so.tend.vn) giữ làm dự phòng/tra cứu. Ứng dụng này xây lại theo cách làm **"chat nhập, web xem"**.

## Bài toán

Mọi màn hình chỉ trả lời 5 câu hỏi: **tiến độ hiện tại, tiến độ dự kiến, công nợ NCC/tổ thợ/công nhật, công nợ khách, dự báo lãi lỗ**.

- **Nhập bằng chat** (Telegram trước, Zalo sau): AI chuẩn hoá tin nhắn/ảnh thành **phiếu**, người gửi bấm **Xác nhận** mới ghi. AI không tự ghi.
- **Giám sát**: báo cáo tiến độ, ảnh, **chấm công** (giám sát tự có công khi gửi báo cáo; chấm công thợ theo tên) chỉ qua chat. Web: bản vẽ chỉ xem.
- **Kế toán nội bộ**: nhập phiếu thu/chi hằng ngày qua chat (chữ hoặc ảnh phiếu). Web: xem, tải báo cáo.
- **Kế toán tài chính**: xem, tải báo cáo (công nợ khách, lãi lỗ).
- **Thiết kế**: vẽ và bóc tách trong **AutoCAD** với Bộ Tend cho AutoCAD (bản mẫu layer, bảng nút, Kiểm tra, Xuất lọc quy chuẩn); nhập DXF trên máy tính.
- **Giám đốc**: xem tất cả, yêu cầu báo cáo theo chi tiết, duyệt sửa số đã chốt.

## Dữ liệu (tối giản)

`core.User` (một vai), `core.AuditLog` (chỉ thêm) · `projects.Project`, `Member` · `drawings.Drawing`, `Item` (mã chi tiết, khối lượng thiết kế, đơn giá bán/vốn, ngày kế hoạch) · `field.Report`, `ReportLine` (% hoặc khối lượng lũy kế, lý do giảm), `Photo`, `Worker` (đơn giá ngày công), `Attendance` · `money.Entry` (thu khách, thu khác, hàng NCC, trả NCC, khoán được tính, trả tổ, trả lương công nhật, chi khác) · `asks.Ask`, `Answer`. Số Tổng quan tính lúc đọc (`overview/compute.py`), không lưu bảng tổng hợp.

## Quy ước bắt buộc

- Giao diện tiếng Việt có dấu; ngày dd/mm/yyyy; tiền `1.250.000 ₫`; số `41,7`. Mã nguồn tiếng Anh.
- Bí mật chỉ trong `.env` (thêm tên biến vào `.env.example`), không in ra log.
- Mọi ghi dữ liệu nghiệp vụ qua `core.audit.record_save` / `record_delete` (ai, lúc nào, bảng, mã, trước/sau, IP, kênh, lý do) trong cùng giao dịch. Báo cáo/phiếu đã gửi không sửa: điều chỉnh là bản ghi mới có lý do.
- Quyền kiểm ở máy chủ theo vai (`User.sees_payables/sees_receivables/sees_profit/edits_drawings`) và thành viên công trình (`projects_for`). Không chỉ ẩn nút.
- Trạng thái luôn có ký hiệu + chữ (▲ ▼ ●), không chỉ màu. Thiếu dữ liệu thì ghi thiếu gì, không hiện 0 giả.

## Làm việc với chủ dự án

Chủ dự án là Giám đốc, không phải lập trình viên: giải thích tiếng Việt, ngắn, từng bước; không bao giờ xin mật khẩu/khoá trong chat. Chỉ làm nhánh riêng; **không tự gộp, triển khai, sửa DB/media vận hành**. Kiểm thử phải đạt trước khi báo xong.

## Lộ trình

1. ✓ Khung, 5 vai, nhật ký, Tổng quan 5 câu hỏi (đợt này).
2. ✓ Bộ quy chuẩn bản vẽ + Bộ Tend cho AutoCAD + nhập DXF có lọc → bản vẽ chỉ xem; chép dữ liệu đầu từ bản cũ (chỉ đọc). Xem mục dưới.
3. ✓ Bot giám sát: báo cáo, ảnh, lý do giảm, chấm công; bảng công tháng, chi phí công nhật. Xem mục dưới.
4. ✓ Bot kế toán: phiếu thu/chi (chữ, ảnh phiếu), lệnh tra cứu; xuất MISA. Xem mục dưới.
5. ✓ Báo cáo tải về (Excel). Xem mục dưới.
6. ✓ Triển khai `app.tend.vn` song song so.tend.vn (cùng máy chủ) — [docs/TRIEN-KHAI.md](docs/TRIEN-KHAI.md); chủ dự án đã chạy, chép dữ liệu cũ, đăng nhập được.
7. Trợ lý Giám đốc cơ bản (đợt 7): xem mục dưới. Đợt 8a (báo cáo tự do, chấm điểm, kế hoạch ngày mai, chỗ vướng): xem mục cuối. Tiếp: đợt 8 trợ lý thông minh, đợt 9 bán hàng/CSKH, đợt 10 marketing/nội dung số theo tài liệu "Mô tả công việc và quy trình vận hành" (Claude Docs https://claude.ai/artifact/5E46cxMEViuwdmFFBoMLtG).

## Bản vẽ (đợt 2, 18/11/2026)

- **Một nguồn quy chuẩn** `drawings/standard.py` (layer, block TCD-…, luật lọc `rejection`, bảng nút). Từ đó sinh: `tend.lsp` (lệnh TEND mở bảng nút DCL nhúng sẵn; TENDKIEMTRA, TENDXUAT, TENDCAIDAT, TENDKHONGDAU, TEND<nút>), bản mẫu `Tend-TCD.dxf`, tầng mẫu `Mau-Tend.dxf` (`drawings/autocad.py`, tải ở Bản vẽ › Bộ Tend cho AutoCAD). Bài kiểm bắt LISP đúng cú pháp, chỉ gọi hàm có thật, khớp layer/block/nút/lý do với quy chuẩn (`drawings/lisp_check.py`). Môi trường kiểm không có AutoCAD: phần chạy thật trong AutoCAD cần kiến trúc sư thử.
- AutoCAD 2021+ (LISPSYS=1) dùng chữ có dấu; 2018–2020 lời nhắc/bảng nút không dấu, tên phòng ghi chữ mẫu rồi bấm đúp sửa. Chữ LISP ghi vào bản vẽ dùng `\U+XXXX`; máy chủ giải mã khi đọc.
- **Nhập DXF** (`drawings/dxf_read.py` → `importer.py`, trang Bản vẽ › Nhập, chỉ Giám đốc/thiết kế): mỗi khung CH-KHUNG + tiêu đề tầng là một tầng ("TẦNG 2-5" nhân cho nhiều tầng). Tường theo tim (bù dày theo layer) hoặc đường bao; H= sát tường; cửa trừ khỏi tường; ỐP trong phòng = chu vi × cao − cửa; thiết bị đếm block; dây/ống đo dài theo nhãn. Ngoài quy chuẩn bị bỏ và liệt kê (layer, lý do, số lượng). Xem trước không ghi; Xác nhận mới ghi, nhật ký từng chi tiết.
- **Nhập lại**: chỉ đụng tầng có trong tệp; chi tiết trùng hình (IoU ≥ 50%, cùng layer) giữ mã; chi tiết cũ đã có báo cáo thì giữ và cảnh báo; chưa có báo cáo thì thôi dùng (`active=False`), không xoá. Đơn giá chi tiết mới lấy từ công trình gần nhất cùng công việc + đơn vị.
- **Xem**: bản vẽ chỉ xem (lớp bật/tắt, phóng/kéo, chạm xem % và các lần báo; ký hiệu ○ ◐ ✓ kèm chữ). Bảng khối lượng + Excel; giá bán chỉ vai xem phải thu, giá vốn chỉ vai xem phải trả.
- **Chép từ bản cũ**: `python manage.py chep_ban_cu --nguon <URL CSDL cũ>` (xem trước) rồi `--thuc-hien --nguoi <giám đốc>`. Đọc bản cũ chỉ đọc; chép tài khoản (giữ mật khẩu, không ghi băm vào nhật ký), công trình, người được giao, nền bản vẽ, chi tiết + đơn giá dự toán; không chép báo cáo/ảnh/tiền → tiến độ 0. Vai Bán hàng/Kế toán bán hàng chưa có ở bản mới: bỏ qua, có cảnh báo. Chạy lại bỏ qua phần đã có.

## Bot giám sát và chấm công (đợt 3, 18/11/2026)

- Chủ dự án chốt: **8 giờ = 1 công**; tên thợ mới giám sát thêm qua bot (bot hỏi thợ/phụ, gợi ý tên gần giống); đơn giá ngày công và hình thức trả (công nhật / tổ khoán) sửa ở **cả** trang web Thợ và lệnh bot `/dongia`, chỉ Giám đốc và kế toán nội bộ.
- Mô-đun `chat/` (viết lại gọn từ `tend-app/chatbot`): kết nối mã một lần (Tài khoản › Kết nối Telegram), chỉ tin riêng, chống lặp `update_id`, webhook `/chat/telegram/<bí mật>/` kiểm đường dẫn + header, chuỗi bí mật tự suy từ `TELEGRAM_TOKEN`. Phiếu (`Proposal`) chỉ có nút ✓ Xác nhận khi đủ: ảnh đã rõ chi tiết (không đoán theo thứ tự), báo giảm có lý do, mọi tên thợ đã rõ người hoặc chọn thêm mới, sửa số công có lý do. Hết 24 giờ hoặc × Bỏ thì không ghi. Bot nhắn Giám đốc tóm tắt mỗi báo cáo.
- Đường ghi duy nhất `field/services.py` (`submit_report`, `set_attendance`, `correct_attendance`, `add_worker`, `set_worker`): một giao dịch, nhật ký kênh `telegram`/`web`, gửi lại cùng mã phiếu không ghi trùng. Giám sát gửi báo cáo là có mặt tại công trình đó (một lượt mỗi công trình/ngày); chấm thợ trùng cùng số công thì giữ nguyên.
- Chi phí công nhật (`overview.compute.labor_cost`, Bảng công) **chỉ tính thợ công nhật**; thợ tổ khoán vẫn chấm công để biết số người. Giám sát: Bảng công tách "có mặt" (lượt theo công trình) và "ngày làm" (số ngày khác nhau).
- AI (`chat/ai.py`, Claude Haiku 5.5, trần `TEND_AI_MONTHLY_USD`): công cụ đọc chạy dưới quyền người hỏi (`chat/reads.py`: tổng quan, chi tiết, thợ, công theo người/khoảng ngày — tiền chỉ vai xem chi phí), công cụ lập phiếu báo cáo/sửa công/trả lời. Chưa bật AI: cú pháp ngắn `chat/parse.py` (`TN-22 T2.5 40% S2.1 xong. Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ`), lệnh `/tongquan /cong /yeucau /congtrinh /suacong /dongia /ngatketnoi /trogiup`.
- Web: Bảng công tháng `/cong/` (+ Excel, tiền theo vai), Thợ `/tho/`, ảnh qua `/anh/<id>/` có kiểm quyền công trình; bảng chi tiết trên bản vẽ hiện các lần báo (người, lý do giảm, ảnh) và cho Giám đốc **Yêu cầu báo cáo** (`asks`): người được hỏi nhận ngay qua Telegram; trả lời bằng báo cáo có chi tiết đó (tính lúc đọc) hoặc bằng chữ.
- Chưa thử bot/Claude API thật (bài kiểm dùng Telegram giả, AI giả): chuyển bot sang bản mới ở đợt 6.

## Bot kế toán, sổ thu chi, công nợ, MISA (đợt 4, 18/11/2026)

- Chủ dự án chốt: **danh sách đối tượng** (`money.Party`: NCC, tổ thợ, khách, khác; mã MISA, MST) — bot so tên gần đúng (`money/parties.py`), tên mới hỏi thêm; **thu tiền khách chỉ kế toán tài chính, Giám đốc nhập**, kế toán nội bộ nhập các loại còn lại (`money.services.kinds_for`) và không xem phiếu thu khách (`visible_kinds`); **phiếu chi không cần duyệt trước**; **huỷ**: người nhập tự huỷ phiếu của mình trong ngày (chưa xuất MISA) có lý do, còn lại bot gửi Giám đốc ✓ Duyệt huỷ / × Không duyệt. Không xoá phiếu: `voided_at`, `void_reason`; phiếu huỷ không tính ở đâu (`overview.compute.live`).
- Đường ghi `money/services.py` (`add_entries`, `void`, `add_party`, `set_party`): giao dịch + nhật ký; mã chống gửi trùng từng phiếu suy từ mã phiếu chat; nghi trùng (cùng số phiếu cùng nhóm thu/chi trong năm, hoặc cùng ngày + số tiền + đối tượng) phải bấm "Vẫn ghi". Loại cần công trình: thu khách, hàng NCC, trả NCC, khoán tổ, trả tổ.
- Bot (`chat/money_bot.py`): cú pháp ngắn `<loại> <số tiền> <đối tượng> [TN-22] [PC12] [ck|tm] [15/11] [- nội dung]` (`chat/parse.money`), hoặc AI (Haiku đọc **ảnh phiếu**, ảnh thu nhỏ ≤ 1568 px, `propose_entries` có `photo_index`; ảnh lưu làm chứng từ `MEDIA_ROOT/chung-tu/`). Nút hỏi: đối tượng (`mp:`), công trình (`mj:`), vẫn ghi (`md:`), Giám đốc duyệt huỷ (`dv:`/`dx:`). Lệnh `/thuchi`, `/congno`, `/baocao`, `/doituong`, `/huy`. `python manage.py tom_tat_ngay` gửi Giám đốc tóm tắt thu chi + phiếu huỷ trong ngày (hẹn giờ đặt ở đợt 6).
- Web mục **Báo cáo**: Sổ thu chi `/bao-cao/thu-chi/` (lọc, tổng thu/chi/phát sinh nợ, phiếu huỷ gạch ngang, ảnh chứng từ `/chung-tu/<id>/` có kiểm quyền, Excel), Công nợ `/bao-cao/cong-no/` (khách chỉ vai xem phải thu, Excel), Xuất MISA `/bao-cao/misa/` (`money/misa.py`, 5 mẫu ASP như bản cũ; khoán tổ không xuất; đánh dấu đã xuất, xuất lại có cảnh báo; tài khoản ở trang quản trị › Cài đặt kế toán), Đối tượng `/doi-tuong/` (sửa mã MISA, MST… có nhật ký).
- Chưa thử Claude API đọc ảnh thật, chưa đặt hẹn giờ tóm tắt ngày (đợt 6).

## Báo cáo tải về (đợt 5, 18/11/2026)

- Mục **Báo cáo** cho mọi vai (`money/views.hub`, `templates/money/hub.html`); Excel dựng ở `overview/exports.py`, tính lúc tải từ dữ liệu gốc, phần tiền theo vai (giám sát/thiết kế: tiến độ + số công; kế toán nội bộ: chi phí, nợ NCC/tổ, không giá bán/tiền khách; kế toán tài chính, Giám đốc: đủ). Phiếu huỷ không tính.
- **Tổng quan 5 câu hỏi** `/bao-cao/tai/tong-quan/`: trang tóm tắt + mỗi công trình một trang (tiến độ, quá hạn, nợ NCC/tổ/công nhật, khách, lãi lỗ).
- **Báo cáo tháng theo công trình** `/bao-cao/tai/thang/?cong_trinh=&thang=YYYY-MM`: Tóm tắt, Tiến độ từng chi tiết (% đầu/cuối tháng theo báo cáo mới nhất tới mốc, KL làm trong tháng = thiết kế × chênh %, giá trị bán/vốn theo quyền), Báo cáo ngày, Chấm công (đơn giá chỉ vai xem chi phí), Thu chi (vai kế toán). Tháng chưa tới: 404.
- **Lãi lỗ** `/bao-cao/tai/lai-lo/` (chỉ Giám đốc, kế toán tài chính): doanh thu, giá trị đã làm, chi phí theo loại (`overview.compute.profit` thêm `parts`, `earned`), lãi theo tiến độ, giá vốn còn lại, lãi dự báo, ghi chú thiếu dữ liệu, dòng TỔNG.

## Triển khai (đợt 6, 18/11/2026)

- Repo `tancodiencom-lang/tend-v2` (nhánh `main` = lịch sử đợt 1–5). Làm việc trên nhánh riêng, mở PR; chủ dự án gộp.
- Cùng droplet với bản cũ, tách hẳn: user `tendv2`, `/srv/tend-v2`, CSDL `tendv2`, dịch vụ `tend-v2` (:8002, 2 tiến trình), Caddy chỉ thêm `/etc/caddy/sites/app.tend.vn.caddy` (Caddyfile bản cũ `import /etc/caddy/sites/*.caddy`). Bot Telegram riêng. Sao lưu 02:30 cùng kho rclone `tendcrypt:tend-v2/`; tóm tắt thu chi 18:13.
- Cài lần đầu bằng `tend-app/ha-tang/cai-tend-v2.sh` (đã có trên máy chủ qua deploy bản cũ); triển khai `ha-tang/trien-khai.sh`; chép dữ liệu `ha-tang/chep-du-lieu-cu.sh` (sao CSDL cũ thành `tend_ban_sao` rồi `chep_ban_cu`, `--nguoi` nhận Giám đốc bản cũ).
- Đã diễn tập trên PostgreSQL 16: 74/74 bài kiểm, `check --deploy`, chép từ CSDL bản cũ thật (mã tend-app). Chưa chạy trên máy chủ thật.

## Kho tài liệu gốc trên Google Drive (19/11/2026)

- Chủ dự án yêu cầu lưu tài liệu gốc (ảnh giám sát, phiếu viết tay, ảnh chuyển khoản, ảnh công trường) theo ngày lên Google Drive để đối chiếu. Chọn **Drive**, không Google Photos (Photos không xếp được theo loại/công trình, không chia sẻ theo vai; Drive giữ nguyên tệp, đã có kho rclone `gdrive:`).
- Mọi ảnh nhận qua bot vẫn lưu `chat.ChatPhoto` (không xoá), nay thêm chú thích kèm ảnh; bot nhận ảnh gửi dạng **Tệp** (JPG/PNG/WEBP, giữ độ nét), từ chối PDF/HEIC có hướng dẫn.
- `chat/archive.py` + `manage.py xuat_tai_lieu_goc`: ảnh đã chốt (phiếu xác nhận/bỏ/lỗi hoặc quá 25 giờ) → liên kết cứng vào `/srv/tend-v2/tai-lieu-goc/` theo `Hiện trường/<CT>/<tháng>/<ngày>`, `Chứng từ thu chi/…`, `Thu tiền khách/…` (tách để chia sẻ theo vai), `Chưa ghi phiếu/…`; mỗi ngày `danh-sach.csv` (kể cả phiếu thu chi chỉ có chữ). Đặt `archived_as` một lần, không ghi đè/xoá.
- `ha-tang/dong-bo-goc.sh` (root, `tend-v2-goc.timer` mỗi giờ phút 41): chạy lệnh trên dưới user `tendv2`, rồi `rclone copy` (không xoá) lên `TEND_GOC_DRIVE` (mặc định `gdrive:Tend - Tài liệu gốc`, `tat` = không chép). Không mã hoá: quyền xem do chia sẻ Drive, không do Tend kiểm.
- 1 migration `chat.0003` (3 cột mới). 77/77 bài kiểm trên SQLite và PostgreSQL 16. Chưa chạy trên máy chủ thật, chưa thử rclone thật.

## Đề phòng mất dữ liệu: kiểm tra sáng, thử khôi phục tháng (19/11/2026)

- `ha-tang/kiem-tra-hang-sang.sh` (`tend-v2-kiemtra.timer` 08:07): báo Telegram khi sao lưu > 30 giờ, chép tài liệu gốc > 3 giờ, thử khôi phục > 40 ngày chưa thành công, app không chạy, đĩa ≥ 85%; thứ Hai gửi bản "mọi việc ổn". Đọc tệp `/var/lib/<việc>/lan-cuoi-thanh-cong`.
- `ha-tang/thu-khoi-phuc.sh` (`tend-v2-khoiphuc.timer` ngày 3, 03:30): tải bản `hang-ngay` mới nhất, `pg_restore --exit-on-error` vào CSDL tạm `tendv2_thu_khoi_phuc`, so số dòng 8 bảng (không nhiều hơn thật, không rỗng khi thật có), kiểm `.env` và số ảnh trong tar ≥ ảnh trên máy chủ trước giờ sao lưu; luôn xoá CSDL tạm.
- Cảnh báo riêng của bản mới: `gui-canh-bao.sh` (dùng `/etc/tend/canh-bao.env` của bản cũ), `canh-bao-dich-vu.sh`, `tend-v2-canh-bao@.service`; mọi dịch vụ v2 `OnFailure=tend-v2-canh-bao@`.
- Diễn tập trên PostgreSQL 16 với rclone giả: sao lưu thật → thử khôi phục đạt; ảnh thiếu trong bản sao lưu và tệp CSDL hỏng đều bị bắt, CSDL tạm luôn xoá; kiểm tra sáng đúng ba tình huống. Chưa chạy trên máy chủ thật.

## Trợ lý Giám đốc — đợt 7 (19/11/2026)

- Theo tài liệu mô tả công việc (trợ lý "cánh tay điều hành": nhắc, hỏi, tổng hợp, cảnh báo; Giám đốc chỉ quyết). Chủ dự án bảo "cứ làm"; các câu hỏi mở được chọn mặc định, Giám đốc chỉnh sau: **chỉ Giám đốc giao việc**; **không có tài khoản trợ lý** (việc ghi nhật ký đứng tên Giám đốc xác nhận, trợ lý không ghi gì khác); tin có tiền chỉ tới Giám đốc (chi vượt thêm kế toán nội bộ của công trình); **ngày làm việc thứ Hai–thứ Bảy** (`TEND_WORK_DAYS`), nhân viên nhận tin 07:00–19:30, Giám đốc 07:00–21:30; ngưỡng theo bảng đề xuất (dự kiến trễ ≥ 7 ngày, chi phí vượt ≥ 10% giá vốn phần đã làm, lãi dự báo âm: Gấp; leo thang sau 4 giờ).
- Mô-đun `assistant/`: `Task` (việc, người chịu trách nhiệm, công trình, hạn, tiêu chí xong; Đang làm → Báo xong → Giám đốc nhận / trả lại có lý do; huỷ có lý do; không xoá), `Feedback` (góp ý `/gopy` + chỗ vướng bot tự ghi: không hiểu tin, phiếu không ghi được), `Notice` (khoá duy nhất mỗi tin: chạy lại không gửi trùng). Đường ghi `assistant/services.py` (giao dịch + nhật ký, kiểm quyền).
- Bot (`chat/task_bot.py`): `/giao <người> <việc> [TN-22] hạn 20/11 [9:00] [- tiêu chí]` (`assistant/parse.py`) hoặc AI (`find_people`, `list_tasks`, `propose_task`) → phiếu chờ Giám đốc ✓ Xác nhận → người làm nhận tin + nút 👍 Nhận việc (`tn:`); `/viec`, `/xong 12 [ghi chú]` → Giám đốc ✓ Nhận kết quả (`ta:`) / ↩ Trả lại (`tr:`, nhắn lý do); `/huyviec 12 lý do`; `/gopy`.
- Lịch (`assistant/schedule.py`, `manage.py tro_ly`, `tend-v2-troly.timer` mỗi 10 phút): 07:30 bản tin sáng Giám đốc (việc cần quyết, cảnh báo, lịch/mốc, việc trễ và trợ lý đã làm gì, người chưa kết nối Telegram; thứ Hai thêm góp ý tuần) + việc hôm nay từng người; nhắc 2 giờ trước hạn, khi quá hạn, chưa bấm nhận sau 4 giờ, leo thang Giám đốc sau 4 giờ; 16:00/17:00 nhắc giám sát công trình chưa có báo cáo, 19:00 báo Giám đốc; 18:00 nhắc phiếu chi thiếu ảnh chứng từ; 18:30 tóm tắt ngày (tiến độ, công, thu chi, việc mở) — **thay tóm tắt thu chi 18:13** (`tend-v2-tomtat.timer` tắt khi triển khai); cảnh báo Gấp mỗi loại mỗi công trình một lần/tuần. Trang `/tro-ly/` (mục Trợ lý): Giám đốc xem mọi việc + góp ý, người khác việc của mình.
- Chưa làm (cần dữ liệu đợt sau): khách chưa trả đợt đến hạn (chưa có lịch đợt thanh toán), nợ NCC quá hạn hẹn (chưa có hạn trả), năng suất tổ thợ (báo cáo tuần đợt 8), khách mới/tin nhắn khách (đợt 9–10), xếp lịch họp/khảo sát (đợt 8).
- 2 migration (`assistant.0001`, `chat.0004` thêm loại phiếu Giao việc). 88/88 bài kiểm trên SQLite và PostgreSQL 16. Chưa chạy trên máy chủ thật, chưa thử Telegram/Claude API thật.

## Nhóm Telegram nội bộ, hỏi Giám đốc qua trợ lý, ảnh nhân viên (19/11/2026)

- Chủ dự án hỏi trợ lý dùng Zalo vào nhóm khách/NCC: đã trả lời — tài khoản Zalo cá nhân tự động trái điều khoản Zalo (không làm); khách/NCC đi Zalo OA (nhóm do OA tạo, đợt 10, cần hỏi Zalo điều kiện); làm trước **nhóm Telegram nội bộ** + **vòng hỏi Giám đốc rồi trả lời lại** (dùng lại cho Zalo OA sau).
- `chat/group_bot.py`, `chat.Group`: bot vào nhóm tự chào; Giám đốc `/nhom TN-22` | `/nhom chung` | `/nhom tat` | `/nhom nghe|khongnghe`. Chỉ người đã kết nối Tend được xử lý (người lạ: nhắc kết nối một lần/ngày khi gọi bot). Lệnh trong nhóm trả lời RIÊNG; gọi bot (@tên_bot / trả lời tin bot) thì trả lời trong nhóm bằng `GroupViewer` (proxy `User`: không phần tiền, chỉ công trình của nhóm) + `ai.group_tools()` (bỏ công cụ tiền); nghe tin thường: ảnh lưu như tin riêng, tin rõ là báo cáo/phiếu (cú pháp ngắn, hoặc AI ở chế độ `passive`) thành phiếu GỬI RIÊNG người nhắn xác nhận; chuyện phiếm im lặng. Webhook nhận thêm `my_chat_member`; nhóm nâng siêu nhóm đổi `chat_id` tự cập nhật. Muốn bot đọc mọi tin: BotFather `/setprivacy` → Disable (hoặc cho bot làm quản trị nhóm).
- `assistant.Question`: `/hoi` (riêng hoặc trong nhóm), AI `ask_director` (kèm bản nháp), hoặc gọi bot trong nhóm mà không hiểu → hỏi Giám đốc (nút ✓ Gửi bản nháp `qa:`, ✍ Tôi trả lời `qw:` rồi nhắn, × Không cần `qx:`) → trợ lý gửi "Giám đốc trả lời câu hỏi của …" đúng chỗ hỏi (trả lời vào tin gốc). Nhắc Giám đốc sau 2 giờ; bản tin sáng ① liệt kê câu hỏi chờ; trang `/tro-ly/` có bảng câu hỏi.
- Ảnh nhân viên `User.avatar` (`core/avatar.py`: JPG/PNG/WEBP ≤ 5 MB, ≤ 512 px, bỏ EXIF; ảnh cũ không xoá): đổi/bỏ ở Tài khoản (nhật ký), xem `/nhan-vien/<id>/anh/` (đã đăng nhập). Mục cuối thanh dưới là **ảnh (hoặc chữ viết tắt) + tên** người đang đăng nhập. Chép ảnh từ bản cũ: `ha-tang/chep-anh-nhan-vien.sh [--thuc-hien]` → `manage.py chep_anh_nhan_vien` (chỉ người chưa có ảnh, không đọc ra ngoài thư mục ảnh).
- 3 migration (`core.0003` ảnh nhân viên, `chat.0005` nhóm, `assistant.0002` câu hỏi). 99/99 bài kiểm trên SQLite và PostgreSQL 16. Chưa thử Telegram nhóm thật.

## Kết nối Telegram cần số điện thoại; sửa ảnh tài khoản hiện to (19/11/2026)

- Chủ dự án hỏi "lộ mã là mất chức?": kết nối nay **hai bước** — mã một lần (15 phút) rồi bấm nút 📱 của Telegram (`request_contact`) gửi số điện thoại của chính người nhắn (`contact.user_id` phải là người gửi); số phải **trùng `User.phone`** (chuẩn hoá `core.text.phone`: +84/84 → 0). Sai số: mã hỏng, Giám đốc nhận cảnh báo (số che bớt). Tài khoản chưa có số: không lấy được mã (Giám đốc nhập ở Thiết lập › Người dùng). `LinkCode` thêm `claimed_by/claimed_chat/claimed_name/linked_at` (migration `chat.0006`). Kết nối đã có từ trước giữ nguyên.
- Ảnh tài khoản hiện cỡ gốc + "QUquan": trình duyệt giữ `tend.css` cũ (tên không đổi). Sửa: `core.storage.StaticFiles` (Manifest, tên có dấu vân tay, chưa collectstatic thì dùng tên gốc) + `width/height` trên ảnh. Thanh dưới: ảnh/chữ viết tắt + **tên + chức danh**.
- 101/101 bài kiểm trên SQLite và PostgreSQL 16.

## Trang Nhân viên (số điện thoại), thao tác việc trên web (19/11/2026)

- **Quản lý tài khoản không cần trang quản trị** (tài khoản chép từ bản cũ không có `is_staff`; sửa trong admin không ghi nhật ký): `core/accounts.py` (chỉ Giám đốc; giao dịch + nhật ký, không ghi mật khẩu) + `core/staff_views.py`: `/nhan-vien/` danh sách (vai, công trình, số điện thoại, Telegram, đã khoá), `/nhan-vien/them/` thêm tài khoản (tên đăng nhập chữ thường không dấu, vai, số điện thoại chuẩn hoá 10 số không trùng, công trình, mật khẩu tạm), `/nhan-vien/<id>/` sửa tên/vai/số/công trình (thôi giao = `Member.active=False`), đặt mật khẩu tạm, khoá (bắt buộc lý do)/mở khoá. Giám đốc không tự khoá/tự hạ vai. Không xoá tài khoản.
- `User.must_change_password` (migration `core.0004`): mật khẩu tạm → middleware `ForcePasswordChange` chỉ cho mở `/doi-mat-khau/` (và đăng xuất) tới khi đổi. Mọi người đổi mật khẩu ở Tài khoản › Đổi mật khẩu. `core.validators.MinimumLength` báo lỗi tiếng Việt (bản dịch Django thiếu).
- Trang Trợ lý: thêm cột **Người giao** (và lúc giao); thao tác trên web như bot (`assistant.views.act`, `/tro-ly/viec/<id>/<nhan|xong|nhan-ket-qua|tra-lai|huy>/`, kênh `web`): người làm Nhận việc / Báo xong (ghi chú); Giám đốc Nhận kết quả / Trả lại (lý do) / Huỷ (lý do); báo Telegram như khi bấm trên bot (`task_bot.notify_done`, `task_bot.tell`). Giao việc mới vẫn qua bot (phiếu xác nhận).
- 107/107 bài kiểm trên SQLite và PostgreSQL 16.

## Chấm công đọc đúng tin thật; danh sách thợ nhập trước (20/11/2026)

- Giám đốc nhắn "Báo cáo + chấm công hôm nay tn-22 t2.1 58% Công Thắng thợ phụ, Thuận thợ chính" → phiếu chỉ có tiến độ, mất 2 thợ mà không báo (cú pháp ngắn bắt buộc "Công:"). Sửa `chat/parse.py`: phần chấm công bắt đầu ở "công/chấm công/công nhân" (có hoặc không dấu hai chấm; không nhầm "công trình/việc/tác/ty/nhật") đứng SAU dòng báo cáo cuối; bỏ "hôm nay" và mã công trình khỏi tên; chữ "thợ phụ/phụ/thợ chính/thợ" cuối tên → `kind` (tên một chữ như "Phú" giữ nguyên). Tên mới có `kind` + người gửi được thêm thợ → phiếu lập sẵn "thêm thợ mới" (không hỏi); tên gần giống người có sẵn vẫn hỏi. Còn chữ sau dòng cuối mà không đọc ra người → phiếu hiện "⚠ Bot CHƯA ĐỌC được phần …". AI `propose_report.people[].kind`. Nhãn người gửi theo vai ("giám đốc, có mặt").
- Trang Thợ `/tho/`: **+ Thêm thợ** (họ tên, tổ, thợ/phụ; hình thức trả + đơn giá ngày chỉ Giám đốc, kế toán nội bộ; trùng tên bỏ dấu bị chặn; nhật ký kênh web). Lối vào: Bảng công và Tài khoản (trước đây trang Thợ không có link).
- 111/111 bài kiểm trên SQLite và PostgreSQL 16.

## Nhân sự: mã NS cố định, nghề, đơn giá theo ngày hiệu lực, lương tháng chia theo ngày có mặt (20/11/2026)

- Chủ dự án chốt: "thợ" → **nhân sự được chấm công** (thợ, phụ, giám sát…); mã `NS-0001` cấp một lần, không đổi, không dùng lại; lương tháng giám sát **chia vào chi phí công trình theo tỷ lệ ngày có mặt trong tháng**; danh mục nghề ban đầu Giám sát, Thợ xây, Thợ ốp lát, Thợ sơn bả, Thợ điện nước, Thợ mộc, Thợ thạch cao, Lái xe.
- `field.Trade` (danh mục nghề, Giám đốc thêm ở trang Nhân sự); `Worker` thêm `code` (tự cấp `services.next_code`, khoá dòng), `trade`, `user` (liên kết tài khoản), bậc Thợ chính/Thợ phụ/Học việc, hình thức trả thêm Lương tháng; `WorkerRate` (đơn giá + hình thức trả theo ngày hiệu lực; giá đầu tiên áp cho cả công trước; `Worker.day_rate/pay` = giá hiện hành). Migration `field.0003` (cấu trúc) + `field.0004` (nghề, mã cho thợ cũ theo thứ tự thêm, giá cũ thành lịch sử từ đầu, hồ sơ cho tài khoản giám sát).
- Tính tiền: `overview.compute.labor_cost` dùng giá đang áp dụng ngày công (`field/rates.py`); `salary_cost` = lương tháng × công ở công trình / tổng công tháng đó (có mặt theo hồ sơ hoặc theo tài khoản giám sát gửi báo cáo) → `profit.parts["salary"]`, cột "Lương tháng phân bổ" ở Excel lãi lỗ. Bảng công tháng và bot `/cong` cũng tính theo giá đúng ngày.
- Đường ghi `field/services.py`: `add_worker` (cấp mã), `rename_worker`, `set_rate`, `set_worker` (giá qua `set_rate`), `ensure_profile` (tài khoản giám sát thêm/đổi vai ở trang Tài khoản đăng nhập tự có hồ sơ). Bot khớp theo tên hoặc mã ("NS-0012", "ns12"); nhãn bot "NS-0001 Thắng (thợ phụ, xây)".
- Trang `/tho/` thành **Nhân sự**: thêm (mã tự cấp), sửa họ tên/nghề/bậc/tổ/hình thức trả/đơn giá + **áp dụng từ ngày**, lịch sử đơn giá, đã nghỉ. Trang Tài khoản › Nhân viên đổi tên **Tài khoản đăng nhập**.
- 115/115 bài kiểm trên SQLite và PostgreSQL 16; migration chạy trên CSDL diễn tập chép từ bản cũ.

## Nhập bù phiếu thu chi cũ (20/11/2026)

- Chủ dự án nhập chứng từ nộp tiền tháng 8 bị chặn ("chỉ nhập phiếu trong 62 ngày"). Sổ tiền bản cũ không chép sang nên cần nhập bù: `money.services.check_date` nay cho tới **2 năm** (`MAX_DAYS_BACK = 730`, quá thì báo kiểm lại năm); phiếu cũ hơn `RECENT_DAYS = 62` vẫn ghi nhưng phiếu chờ xác nhận hiện "⚠ Nhập bù: phiếu cũ N ngày. Kiểm tra kỳ này trên MISA đã khoá sổ chưa trước khi xuất MISA" (`backfill_days`).

## Báo cáo tự do → mẫu chuẩn + link tra mã; chọn tên thợ từ danh sách (20/11/2026)

- Chủ dự án: giám sát nhắn không chuẩn thì đưa báo cáo chuẩn kèm link bản vẽ/bảng khối lượng; thiếu tên thợ hoặc sai chức danh thì đưa danh sách người có.
- Phiếu báo cáo lập qua AI (`via: ai`) hoặc có phần chưa đọc → `payload.free`; `bot.summary` thêm "📝 Lần sau nhắn theo mẫu này…" (`standard_text`, từ đợt 8a nhiều dòng: "TN-22 19/11 D179 20%; D207 30%" / "+ Cắt đục sàn mái" / "Công: Hậu thợ; Thắng phụ, Thuận phụ" / "Dự kiến 20/11: …") + link `/ban-ve/<CT>/` và `/ban-ve/<CT>/khoi-luong/`. Trả lời "Chưa hiểu tin nhắn" cũng kèm link bảng khối lượng.
- Phiếu báo cáo không có tên thợ nào → `offer_staff` gửi nút "＋ NS-0003 Thắng (thợ phụ)" (người làm ở công trình 60 ngày gần đây trước, không gồm lương tháng; tối đa 16) — bấm (`pa:<phiếu>:<id>`) là thêm 1 công, không trùng, gửi lại phiếu. Tên khớp người có sẵn nhưng tin ghi thợ/phụ khác danh sách → dòng chấm công có cảnh báo.
- 118/118 bài kiểm trên SQLite và PostgreSQL 16.

## Đợt 8a: báo cáo tự do, mức đầy đủ, kế hoạch ngày mai, chỗ vướng (20/11/2026)

- Bối cảnh: giám sát nhắn "Báo cáo cv 19/11 - Nhân sự: thợ xây: 3 thợ 2 phụ - Công việc + Trát wc tầng 5 + Cắt đục sàn mái - cv dự kiến 20/11 + Trát tường tầng 4" → trước đây mất việc không mã, số người không tên, kế hoạch. Chủ dự án chốt: **thang 100** (mã 25, %/KL 25 theo tỷ lệ số việc có; ảnh 20; tên thợ 20; không hỏi lại 10, mỗi lần hỏi trừ 5; TB tuần ≥ 80 ▲ tốt, 60–79 ● được, < 60 ▼ cần hướng dẫn); **giám sát thấy điểm của mình** (bot báo sau Xác nhận, trang Trợ lý chỉ hàng mình), Giám đốc thấy tất cả; **việc dự kiến chưa làm chỉ báo, không bắt lý do**; **che số điện thoại cả góp ý/chỗ vướng cũ** (migration).
- `chat/parse.report` đọc báo cáo tự do khi chưa bật AI: tách phần "dự kiến/kế hoạch/ngày mai" (không phải việc đã làm), phần chấm công có tên (trước hoặc sau kế hoạch), "Nhân sự: 3 thợ 2 phụ" → `crew` (không tên, không tính công); gạch đầu dòng (+, -, •, xuống dòng) không mã → việc ngoài bản vẽ (`floor_of`: "tầng 5", "t5", "sàn mái", "tum"…); ngày trong tiêu đề → ngày báo cáo. Mã không có dấu chấm (D179, DT12 chép từ bản cũ) đọc theo **mã thật của công trình** (`codes=bot.item_codes`) — trước đây cú pháp ngắn không đọc được. Thiếu mã công trình mà người gửi có nhiều công trình → nút chọn (`rp:`, giữ tin ở `ChatLink.state.report_text`); trong nhóm của một công trình thì lấy công trình của nhóm. Nghe tin trong nhóm (`strict`): tin gạch đầu dòng chỉ là báo cáo khi mở đầu "Báo cáo/Công việc/cv…" hoặc có mã. AI: `propose_report` thêm `extra`, `plan`, `crew`.
- Dữ liệu (migration `field.0005`): `field.ExtraWork` (báo cáo, tầng, mô tả, % tự báo — **không tính % tiến độ**), `field.PlanLine` (báo cáo, ngày dự kiến, chi tiết nếu có mã, tầng, mô tả), `Report.via/asked/named/crew` (dữ kiện chấm điểm). Ghi qua `field.services.submit_report` (nhật ký từng dòng); ngày dự kiến phải sau ngày báo cáo, ≤ 31 ngày; không ghi ngày → ngày làm việc kế tiếp (`field.plans.next_workday`, `TEND_WORK_DAYS`).
- `field/plans.py` so kế hoạch với thực làm **lúc đọc**: mỗi người lập lấy kế hoạch trong báo cáo mới nhất cho ngày đó; việc có mã: có báo cáo chi tiết đó; không mã: cùng tầng và ≥ 75% chữ của việc dự kiến có trong việc đã làm. Hiện ở phiếu báo cáo hôm sau ("So với kế hoạch…: ✓ / ● chưa thấy trong báo cáo"), tin báo Giám đốc, tóm tắt 18:30 ("● Dự kiến hôm nay mà chưa thấy làm"), Excel tháng.
- `field/quality.py` tính điểm lúc đọc (`quality.of(report)`; báo cáo không có việc nào không chấm). `assistant/stats.py` thống kê tuần (thứ Hai–Chủ nhật) theo giám sát: số báo cáo, điểm TB, hay thiếu gì, số lần bot hỏi lại, việc ngoài bản vẽ, phiếu bỏ/hết hạn; chỗ vướng theo loại. Bản tin sáng thứ Hai mục ⑤ + "Cần hướng dẫn cách báo cáo: …"; trang `/tro-ly/` bảng tuần (`?tuan=YYYY-MM-DD`) + từng báo cáo. Excel tháng: "Báo cáo ngày" thêm việc ngoài bản vẽ, kế hoạch, nhân sự theo tin, mức đầy đủ (chỉ Giám đốc); trang mới "Việc ngoài bản vẽ", "Kế hoạch".
- Chỗ vướng: mỗi lần bot hỏi lại ghi `assistant.Feedback` (kind friction) có `topic` (ảnh, tên thợ, thiếu tên thợ, lý do, phần chưa đọc, mã không có, thiếu %, công trình, đối tượng/nghi trùng thu chi, không hiểu, không ghi được) và `project` (migration `assistant.0003`); đếm vào `payload.asks` của phiếu (mỗi chỗ một lần) → `Report.asked`. `core.text.mask_phones` che số điện thoại ("[số ĐT đã che]") trong `assistant.services.add_feedback` trước khi lưu (nên nhật ký mới không có số); migration che dòng cũ, ghi nhật ký mới không chép nội dung cũ, nhật ký cũ giữ nguyên (chỉ thêm).
- 132/132 bài kiểm trên SQLite và PostgreSQL 16; migration diễn tập trên PostgreSQL có dữ liệu cũ. Chưa thử Telegram/Claude API thật.

