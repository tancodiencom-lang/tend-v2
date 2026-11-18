"""Công trình và người được giao. Giám đốc thấy mọi công trình; vai khác chỉ công trình mình là thành viên."""
from django.conf import settings
from django.db import models


class Project(models.Model):
    code = models.CharField("Mã", max_length=30, unique=True)
    name = models.CharField("Tên công trình", max_length=200)
    address = models.CharField("Địa chỉ", max_length=300, blank=True)
    start_date = models.DateField("Khởi công", null=True, blank=True)
    handover_date = models.DateField("Hẹn bàn giao", null=True, blank=True)
    contract_value = models.DecimalField("Giá trị hợp đồng (₫)", max_digits=15, decimal_places=0, null=True, blank=True,
                                         help_text="Để trống thì lấy tổng khối lượng thiết kế × đơn giá bán.")
    active = models.BooleanField("Đang thi công", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Công trình"
        verbose_name_plural = "Công trình"
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} · {self.name}"


class Member(models.Model):
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người", on_delete=models.PROTECT, related_name="memberships")
    active = models.BooleanField("Đang giao", default=True)

    class Meta:
        verbose_name = "Người được giao"
        verbose_name_plural = "Người được giao"
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="member_once")]


def projects_for(user):
    if not user.is_authenticated or not user.is_active:
        return Project.objects.none()
    if user.is_director:
        return Project.objects.all()
    return Project.objects.filter(members__user=user, members__active=True).distinct()
