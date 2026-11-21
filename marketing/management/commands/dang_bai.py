"""Đăng các bài đã duyệt lên Trang Facebook (hẹn giờ tend-v2-dangbai.timer, mỗi 2 phút) và báo người duyệt kết quả.
Trước đó chạy bù các việc viết bài còn chờ (tiến trình nền chưa làm hoặc bị dừng)."""
from django.core.management.base import BaseCommand

from marketing import bot, jobs, publish


class Command(BaseCommand):
    help = "Đăng bài đã duyệt, đối soát bài chưa rõ kết quả"

    def handle(self, *args, **options):
        written = jobs.run_pending()
        if written:
            self.stdout.write(f"Đã làm {len(written)} việc viết bài.")
        results = publish.run()
        bot.report_results(results)
        for post, channel, state in results:
            self.stdout.write(f"Bài #{post.pk} · {channel.name}: {state}")
