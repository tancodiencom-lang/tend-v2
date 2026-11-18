"""Gửi Giám đốc tóm tắt thu chi trong ngày qua Telegram: python manage.py tom_tat_ngay [--ngay 2026-11-18]."""
from datetime import date

from django.core.management.base import BaseCommand

from chat.money_bot import send_daily


class Command(BaseCommand):
    help = "Gửi tóm tắt thu chi, phiếu huỷ trong ngày cho Giám đốc đã kết nối Telegram."

    def add_arguments(self, parser):
        parser.add_argument("--ngay", help="YYYY-MM-DD, mặc định hôm nay")

    def handle(self, *args, ngay=None, **options):
        sent = send_daily(date.fromisoformat(ngay) if ngay else None)
        self.stdout.write(f"Đã gửi {sent} tin tóm tắt.")
