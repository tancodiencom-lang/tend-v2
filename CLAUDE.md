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
3. Bot giám sát: báo cáo, ảnh, lý do giảm, chấm công; bảng công tháng, chi phí công nhật.
4. Bot kế toán: phiếu thu/chi (chữ, ảnh phiếu), lệnh tra cứu; xuất MISA.
5. Báo cáo tải về (Excel).
6. Triển khai `app.tend.vn`, chuyển bot sang; so.tend.vn chỉ tra cứu.

## Bản vẽ (đợt 2, 18/11/2026)

- **Một nguồn quy chuẩn** `drawings/standard.py` (layer, block TCD-…, luật lọc `rejection`, bảng nút). Từ đó sinh: `tend.lsp` (lệnh TEND mở bảng nút DCL nhúng sẵn; TENDKIEMTRA, TENDXUAT, TENDCAIDAT, TENDKHONGDAU, TEND<nút>), bản mẫu `Tend-TCD.dxf`, tầng mẫu `Mau-Tend.dxf` (`drawings/autocad.py`, tải ở Bản vẽ › Bộ Tend cho AutoCAD). Bài kiểm bắt LISP đúng cú pháp, chỉ gọi hàm có thật, khớp layer/block/nút/lý do với quy chuẩn (`drawings/lisp_check.py`). Môi trường kiểm không có AutoCAD: phần chạy thật trong AutoCAD cần kiến trúc sư thử.
- AutoCAD 2021+ (LISPSYS=1) dùng chữ có dấu; 2018–2020 lời nhắc/bảng nút không dấu, tên phòng ghi chữ mẫu rồi bấm đúp sửa. Chữ LISP ghi vào bản vẽ dùng `\U+XXXX`; máy chủ giải mã khi đọc.
- **Nhập DXF** (`drawings/dxf_read.py` → `importer.py`, trang Bản vẽ › Nhập, chỉ Giám đốc/thiết kế): mỗi khung CH-KHUNG + tiêu đề tầng là một tầng ("TẦNG 2-5" nhân cho nhiều tầng). Tường theo tim (bù dày theo layer) hoặc đường bao; H= sát tường; cửa trừ khỏi tường; ỐP trong phòng = chu vi × cao − cửa; thiết bị đếm block; dây/ống đo dài theo nhãn. Ngoài quy chuẩn bị bỏ và liệt kê (layer, lý do, số lượng). Xem trước không ghi; Xác nhận mới ghi, nhật ký từng chi tiết.
- **Nhập lại**: chỉ đụng tầng có trong tệp; chi tiết trùng hình (IoU ≥ 50%, cùng layer) giữ mã; chi tiết cũ đã có báo cáo thì giữ và cảnh báo; chưa có báo cáo thì thôi dùng (`active=False`), không xoá. Đơn giá chi tiết mới lấy từ công trình gần nhất cùng công việc + đơn vị.
- **Xem**: bản vẽ chỉ xem (lớp bật/tắt, phóng/kéo, chạm xem % và các lần báo; ký hiệu ○ ◐ ✓ kèm chữ). Bảng khối lượng + Excel; giá bán chỉ vai xem phải thu, giá vốn chỉ vai xem phải trả.
- **Chép từ bản cũ**: `python manage.py chep_ban_cu --nguon <URL CSDL cũ>` (xem trước) rồi `--thuc-hien --nguoi <giám đốc>`. Đọc bản cũ chỉ đọc; chép tài khoản (giữ mật khẩu, không ghi băm vào nhật ký), công trình, người được giao, nền bản vẽ, chi tiết + đơn giá dự toán; không chép báo cáo/ảnh/tiền → tiến độ 0. Vai Bán hàng/Kế toán bán hàng chưa có ở bản mới: bỏ qua, có cảnh báo. Chạy lại bỏ qua phần đã có.
