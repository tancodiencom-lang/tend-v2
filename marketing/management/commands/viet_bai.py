from django.core.management.base import BaseCommand

from marketing import jobs


class Command(BaseCommand):
    help = "Trợ lý nội dung: làm các việc viết bài đang chờ (chạy nền khi giao bài/nhờ sửa; hẹn giờ đăng bài chạy bù)."

    def handle(self, *args, **options):
        done = jobs.run_pending()
        self.stdout.write(f"Đã làm {len(done)} việc viết bài.")
