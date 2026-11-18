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
2. Bộ quy chuẩn bản vẽ + Bộ Tend cho AutoCAD + nhập DXF có lọc → bản vẽ chỉ xem; chép dữ liệu đầu từ bản cũ (chỉ đọc).
3. Bot giám sát: báo cáo, ảnh, lý do giảm, chấm công; bảng công tháng, chi phí công nhật.
4. Bot kế toán: phiếu thu/chi (chữ, ảnh phiếu), lệnh tra cứu; xuất MISA.
5. Báo cáo tải về (Excel).
6. Triển khai `app.tend.vn`, chuyển bot sang; so.tend.vn chỉ tra cứu.
