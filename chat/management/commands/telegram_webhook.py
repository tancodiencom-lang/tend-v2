"""Đặt địa chỉ nhận tin cho bot Telegram: python manage.py telegram_webhook (chạy trên máy chủ, sau triển khai)."""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from chat import telegram


class Command(BaseCommand):
    help = 'Đặt webhook Telegram về APP_URL/chat/telegram/<bí mật>/ (không in mã bot hay chuỗi bí mật).'

    def handle(self, *args, **options):
        secret = telegram.webhook_secret()
        if not telegram.token() or not secret:
            raise CommandError('Thiếu TELEGRAM_TOKEN trong .env.')
        result = telegram.set_webhook(f'{settings.APP_URL}/chat/telegram/{secret}/', secret)
        if result is None:
            raise CommandError('Telegram không nhận lệnh (xem log, kiểm tra mã bot).')
        self.stdout.write(self.style.SUCCESS('Đã đặt địa chỉ nhận tin cho bot Telegram.'))
