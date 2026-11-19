"""Tài khoản theo 5 vai và nhật ký thay đổi chỉ thêm (không sửa, không xoá)."""
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models

from .avatar import avatar_path


class Role(models.TextChoices):
    DIRECTOR = "director", "Giám đốc"
    SUPERVISOR = "supervisor", "Giám sát"
    DESIGNER = "designer", "Thiết kế"
    INTERNAL_ACCOUNTANT = "internal", "Kế toán nội bộ"
    FINANCE_ACCOUNTANT = "finance", "Kế toán tài chính"


class Manager(UserManager):
    def create_superuser(self, username, email=None, password=None, **extra):
        extra.setdefault("role", Role.DIRECTOR)
        return super().create_superuser(username, email, password, **extra)


class User(AbstractUser):
    objects = Manager()
    role = models.CharField("Vai", max_length=12, choices=Role.choices, default=Role.SUPERVISOR)
    phone = models.CharField("Điện thoại", max_length=20, blank=True)
    avatar = models.FileField("Ảnh nhân viên", upload_to=avatar_path, blank=True,
                              help_text="Hiện cạnh tên ở thanh mục; ảnh cũ giữ trong thư mục, không xoá.")

    class Meta:
        verbose_name = "Người dùng"
        verbose_name_plural = "Người dùng"

    def __str__(self):
        return self.get_full_name() or self.username

    # Một vai mỗi người: quyền đọc ngay từ vai, không ghép nhóm để vượt quyền.
    @property
    def is_director(self):
        return self.is_superuser or self.role == Role.DIRECTOR

    @property
    def sees_payables(self):
        """Công nợ NCC, tổ thợ, chi phí, đơn giá công."""
        return self.is_director or self.role in (Role.INTERNAL_ACCOUNTANT, Role.FINANCE_ACCOUNTANT)

    @property
    def sees_receivables(self):
        """Hợp đồng, thu khách."""
        return self.is_director or self.role == Role.FINANCE_ACCOUNTANT

    @property
    def sees_profit(self):
        return self.is_director or self.role == Role.FINANCE_ACCOUNTANT

    @property
    def edits_drawings(self):
        return self.is_director or self.role == Role.DESIGNER


class AuditLog(models.Model):
    """Ai, lúc nào, bảng/mã bản ghi, hành động, giá trị trước và sau, IP, lý do. Ghi trong cùng giao dịch."""
    actor = models.ForeignKey(User, verbose_name="Người làm", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    at = models.DateTimeField("Lúc", auto_now_add=True)
    table = models.CharField("Bảng", max_length=80)
    object_id = models.CharField("Mã bản ghi", max_length=64)
    action = models.CharField("Hành động", max_length=10)
    before = models.JSONField("Trước", null=True, blank=True)
    after = models.JSONField("Sau", null=True, blank=True)
    ip = models.GenericIPAddressField("IP", null=True, blank=True)
    channel = models.CharField("Kênh", max_length=20, default="web")
    reason = models.CharField("Lý do", max_length=300, blank=True)

    class Meta:
        verbose_name = "Nhật ký"
        verbose_name_plural = "Nhật ký"
        ordering = ["-at", "-id"]
        indexes = [models.Index(fields=["table", "object_id"])]

    def save(self, *args, **kwargs):
        if self.pk:
            raise RuntimeError("Nhật ký chỉ được thêm, không được sửa.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise RuntimeError("Nhật ký không được xoá.")
