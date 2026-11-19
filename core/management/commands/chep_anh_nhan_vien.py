"""Chép ảnh nhân viên từ Tend cũ (chỉ đọc bản cũ): người cùng tên đăng nhập, bản mới chưa có ảnh.

    python manage.py chep_anh_nhan_vien --nguon postgresql:///tend_ban_sao --thu-muc /tmp/anh-cu            (xem trước)
    python manage.py chep_anh_nhan_vien --nguon postgresql:///tend_ban_sao --thu-muc /tmp/anh-cu --thuc-hien

--thu-muc: thư mục chứa bản sao tệp của bản cũ theo đúng đường dẫn lưu trong CSDL cũ (nhan-vien/anh/<mã>/...).
Ảnh được chuẩn hoá lại (≤ 512 px, bỏ EXIF), mỗi người một dòng nhật ký. Chạy lại bỏ qua người đã có ảnh.
"""
from pathlib import Path

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand
from django.db import transaction

from core.audit import record_save
from core.avatar import normalize
from core.models import User
from projects.management.commands.chep_ban_cu import Reader


class Command(BaseCommand):
    help = "Chép ảnh nhân viên từ Tend cũ cho người chưa có ảnh ở bản mới."

    def add_arguments(self, parser):
        parser.add_argument("--nguon", required=True)
        parser.add_argument("--thu-muc", required=True, dest="folder")
        parser.add_argument("--thuc-hien", action="store_true")

    def handle(self, *args, nguon, folder, thuc_hien=False, **options):
        old = Reader(nguon)
        try:
            rows = old.all("SELECT username, avatar FROM core_user WHERE avatar IS NOT NULL AND avatar <> ''")
        finally:
            old.close()
        base = Path(folder).resolve()
        done = 0
        for row in rows:
            user = User.objects.filter(username=row["username"]).first()
            if user is None or user.avatar:
                continue
            path = (base / row["avatar"]).resolve()
            if base not in path.parents or not path.is_file():
                self.stdout.write(self.style.WARNING(f"⚠ {row['username']}: không thấy tệp {row['avatar']}"))
                continue
            try:
                content = normalize(path.read_bytes())
            except ValidationError as error:
                self.stdout.write(self.style.WARNING(f"⚠ {row['username']}: {'; '.join(error.messages)}"))
                continue
            self.stdout.write(f"• {row['username']}: chép ảnh")
            if thuc_hien:
                with transaction.atomic():
                    user.avatar.save("anh.jpg", content, save=False)
                    record_save(user, user, "Chép ảnh nhân viên từ Tend cũ", channel="lenh")
            done += 1
        verb = "Đã chép" if thuc_hien else "Sẽ chép"
        self.stdout.write(self.style.SUCCESS(f"{verb} ảnh cho {done} người.") + ("" if thuc_hien else " Thêm --thuc-hien để chép."))
