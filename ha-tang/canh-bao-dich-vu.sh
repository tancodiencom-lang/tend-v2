#!/bin/bash
# Goi khi mot dich vu cua Tend moi bi loi (OnFailure). Dung: canh-bao-dich-vu.sh ten-dich-vu
set -uo pipefail
UNIT=${1:-khong-ro}
case "$UNIT" in
  tend-v2-saoluu*) WHAT="SAO LƯU HẰNG ĐÊM của Tend mới bị lỗi. Dữ liệu đêm nay CHƯA được sao lưu." ;;
  tend-v2-goc*) WHAT="CHÉP TÀI LIỆU GỐC lên Google Drive bị lỗi. Ảnh/chứng từ vẫn còn trên máy chủ, giờ sau sẽ thử lại." ;;
  tend-v2-khoiphuc*) WHAT="THỬ KHÔI PHỤC hằng tháng KHÔNG ĐẠT: bản sao lưu có thể không dùng được. Cần kiểm tra ngay." ;;
  tend-v2-tomtat*) WHAT="Gửi tóm tắt thu chi cuối ngày bị lỗi." ;;
  tend-v2-troly*) WHAT="TRỢ LÝ GIÁM ĐỐC chạy lỗi: bản tin, nhắc việc, cảnh báo lượt này chưa gửi (10 phút sau tự chạy lại)." ;;
  tend-v2.service) WHAT="APP TEND MỚI bị dừng do lỗi (hệ thống tự khởi động lại; báo lại nhiều lần thì cần kiểm tra)." ;;
  *) WHAT="Dịch vụ $UNIT bị lỗi." ;;
esac
LOG=$(journalctl -u "$UNIT" -n 8 --no-pager -o cat 2>/dev/null | cut -c1-200)
MSG="⚠️ app.tend.vn – $(date '+%d/%m/%Y %H:%M')
$WHAT

Nhật ký gần nhất:
$LOG"
/bin/bash /srv/tend-v2/app/ha-tang/gui-canh-bao.sh "$MSG"
