"""Trợ lý IT: sổ tài khoản của công ty — KHÔNG giữ mật khẩu hay khoá (những thứ đó ở Bitwarden). Sổ ghi tài khoản nào, ai
giữ, dùng ở đâu, đã bật xác thực 2 lớp chưa, ngày hết hạn, lần đổi khoá gần nhất; trợ lý nhắc khi sắp hết hạn/quá hạn đổi."""
from django.conf import settings
from django.db import models


class ServiceAccount(models.Model):
    class Kind(models.TextChoices):
        WEB = "web", "Dịch vụ / ứng dụng"
        SERVER = "may_chu", "Máy chủ"
        DOMAIN = "ten_mien", "Tên miền / SSL"
        API = "khoa_api", "Khoá API / mã bot"
        EMAIL = "email", "Email"
        SOCIAL = "mxh", "Mạng xã hội / Trang"
        OTHER = "khac", "Khác"

    class TwoFactor(models.TextChoices):
        YES = "co", "Đã bật"
        NO = "chua", "Chưa bật"
        UNKNOWN = "?", "Chưa rõ"

    name = models.CharField("Tài khoản / dịch vụ", max_length=150)
    kind = models.CharField("Loại", max_length=10, choices=Kind.choices, default=Kind.WEB)
    login = models.CharField("Tên đăng nhập / email (không ghi mật khẩu)", max_length=150, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người giữ", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="+")
    vault_ref = models.CharField("Mục trong Bitwarden", max_length=200, blank=True,
                                 help_text="vd Tend / Facebook tendct — mật khẩu, khoá chỉ nằm ở đó")
    used_by = models.CharField("Dùng ở đâu", max_length=300, blank=True,
                               help_text="vd app.tend.vn (.env TEND_META_TOKEN), máy kế toán, GitHub Actions")
    two_factor = models.CharField("Xác thực 2 lớp", max_length=4, choices=TwoFactor.choices, default=TwoFactor.UNKNOWN)
    expires_on = models.DateField("Hết hạn ngày", null=True, blank=True, help_text="Tên miền, SSL, khoá có hạn, gói dịch vụ")
    rotated_on = models.DateField("Đổi mật khẩu/khoá gần nhất", null=True, blank=True)
    rotate_days = models.PositiveSmallIntegerField("Đổi lại sau (ngày)", null=True, blank=True)
    note = models.CharField("Ghi chú", max_length=500, blank=True)
    active = models.BooleanField("Đang dùng", default=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Tài khoản (sổ IT)"
        verbose_name_plural = "Sổ tài khoản"
        ordering = ["-active", "name"]

    def __str__(self):
        return self.name
