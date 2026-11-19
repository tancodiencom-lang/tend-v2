"""Tệp giao diện có dấu vân tay trong tên (tend.3f2a….css): mỗi lần triển khai đổi nội dung là đổi tên, trình duyệt
không dùng nhầm bản cũ đã lưu. Thiếu bảng tên (chưa collectstatic, khi chạy thử) thì dùng tên gốc, không báo lỗi."""
from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class StaticFiles(ManifestStaticFilesStorage):
    manifest_strict = False

    def stored_name(self, name):
        try:
            return super().stored_name(name)
        except ValueError:        # chưa collectstatic (chạy thử, bài kiểm): dùng tên gốc
            return name
