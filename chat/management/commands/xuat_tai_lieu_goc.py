"""Xếp ảnh gốc nhận qua chat theo loại và ngày vào thư mục tài liệu gốc: python manage.py xuat_tai_lieu_goc [--ngay YYYY-MM-DD].

Chạy mỗi giờ bởi ha-tang/dong-bo-goc.sh (rồi rclone chép lên Google Drive). Chạy lại không chép trùng.
"""
from datetime import date

from django.core.management.base import BaseCommand

from chat import archive


class Command(BaseCommand):
    help = "Xuất ảnh/chứng từ gốc đã chốt ra thư mục tài liệu gốc, viết danh sách từng ngày."

    def add_arguments(self, parser):
        parser.add_argument("--ngay", action="append", default=[], help="Viết lại danh sách ngày này (YYYY-MM-DD), lặp được")

    def handle(self, *args, ngay=(), **options):
        done = archive.export(log=self.stderr.write)
        for day in ngay:
            archive.write_indexes(date.fromisoformat(day))
        self.stdout.write(f"Đã xuất {done} ảnh vào {archive.root()}")
