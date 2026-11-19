"""Một lượt việc của trợ lý (bản tin, nhắc, cảnh báo, tóm tắt). Chạy mỗi 10 phút bởi tend-v2-troly.timer:
python manage.py tro_ly"""
from django.core.management.base import BaseCommand

from assistant import schedule


class Command(BaseCommand):
    help = "Chạy một lượt lịch của trợ lý Giám đốc; tin đã gửi không gửi lại."

    def handle(self, *args, **options):
        done = schedule.run()
        self.stdout.write(f"Trợ lý: đã gửi {len(done)} tin" + (": " + ", ".join(done) if done else ""))
