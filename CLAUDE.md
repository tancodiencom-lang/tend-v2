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
5. Báo cáo tải về (Excel).
6. Triển khai `app.tend.vn`, chuyển bot sang; so.tend.vn chỉ tra cứu.

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
