"""Gom ảnh mới vào kho nội dung: ảnh báo cáo hiện trường đã ghi, ảnh thư mục Drive đã kéo về (chạy sau keo-anh-noi-dung.sh)."""
from django.core.management.base import BaseCommand

from marketing import assets


class Command(BaseCommand):
    help = "Gom ảnh mới vào kho nội dung"

    def handle(self, *args, **options):
        result = assets.collect()
        self.stdout.write(f"Ảnh mới: báo cáo {result['bao_cao']}, Drive {result['drive']}")
