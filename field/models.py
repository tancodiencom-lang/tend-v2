"""Báo cáo hiện trường và chấm công: chỉ nhập qua chat (phiếu đã xác nhận); web chỉ xem.

Báo cáo đã gửi không sửa: điều chỉnh là một báo cáo mới (có lý do khi giảm). Chấm công sửa có lý do, giữ lịch sử.
"""
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models

from drawings.models import Item
from projects.models import Project


class Report(models.Model):
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="reports")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người báo", on_delete=models.PROTECT, related_name="reports")
    work_date = models.DateField("Ngày công việc")
    note = models.TextField("Ghi chú", blank=True, max_length=5000)
    channel = models.CharField("Kênh", max_length=20, default="telegram")
    client_key = models.UUIDField("Mã chống gửi trùng", unique=True)
    created_at = models.DateTimeField("Gửi lúc", auto_now_add=True)

    class Meta:
        verbose_name = "Báo cáo ngày"
        verbose_name_plural = "Báo cáo ngày"
        ordering = ["-work_date", "-created_at"]


class ReportLine(models.Model):
    report = models.ForeignKey(Report, on_delete=models.PROTECT, related_name="lines")
    item = models.ForeignKey(Item, verbose_name="Chi tiết", on_delete=models.PROTECT, related_name="report_lines")
    percent = models.DecimalField("% hoàn thành (ước lượng)", max_digits=5, decimal_places=1, null=True, blank=True)
    quantity = models.DecimalField("Khối lượng lũy kế", max_digits=14, decimal_places=3, null=True, blank=True)
    note = models.CharField("Ghi chú", max_length=2000, blank=True)
    decrease_reason = models.CharField("Lý do điều chỉnh giảm", max_length=1000, blank=True)

    class Meta:
        verbose_name = "Dòng báo cáo"
        verbose_name_plural = "Dòng báo cáo"
        constraints = [models.CheckConstraint(condition=models.Q(percent__isnull=False) | models.Q(quantity__isnull=False),
                                              name="report_line_has_value")]


def photo_path(instance, filename):
    return f"hien-truong/{instance.report.project_id}/{uuid4().hex}{Path(filename).suffix.lower() or '.jpg'}"


class Photo(models.Model):
    report = models.ForeignKey(Report, on_delete=models.PROTECT, related_name="photos")
    line = models.ForeignKey(ReportLine, on_delete=models.PROTECT, null=True, blank=True, related_name="photos")
    image = models.ImageField("Ảnh", upload_to=photo_path)

    class Meta:
        verbose_name = "Ảnh hiện trường"
        verbose_name_plural = "Ảnh hiện trường"


class Worker(models.Model):
    class Kind(models.TextChoices):
        SKILLED = "tho", "Thợ"
        HELPER = "phu", "Phụ"

    name = models.CharField("Tên", max_length=100)
    team = models.CharField("Tổ", max_length=100, blank=True)
    kind = models.CharField("Loại", max_length=4, choices=Kind.choices, default=Kind.SKILLED)
    phone = models.CharField("Điện thoại", max_length=20, blank=True)
    day_rate = models.DecimalField("Đơn giá ngày công (₫)", max_digits=12, decimal_places=0, null=True, blank=True,
                                   help_text="Chỉ Giám đốc, kế toán xem và sửa.")
    active = models.BooleanField("Đang làm", default=True)

    class Meta:
        verbose_name = "Thợ"
        verbose_name_plural = "Thợ"
        ordering = ["team", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_kind_display().lower()}{', ' + self.team if self.team else ''})"


class Attendance(models.Model):
    """Một công: một người (thợ hoặc giám sát) ở một công trình trong một ngày. Giám sát tự có công khi gửi báo cáo."""
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="attendance")
    work_date = models.DateField("Ngày")
    worker = models.ForeignKey(Worker, verbose_name="Thợ", on_delete=models.PROTECT, null=True, blank=True, related_name="attendance")
    staff = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Nhân viên", on_delete=models.PROTECT, null=True, blank=True,
                              related_name="attendance")
    days = models.DecimalField("Số công", max_digits=4, decimal_places=2, default=1, help_text="1 cả ngày, 0,5 nửa ngày")
    report = models.ForeignKey(Report, verbose_name="Báo cáo nguồn", on_delete=models.PROTECT, null=True, blank=True,
                               related_name="attendance")
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    correction_reason = models.CharField("Lý do sửa", max_length=500, blank=True)

    class Meta:
        verbose_name = "Chấm công"
        verbose_name_plural = "Chấm công"
        ordering = ["-work_date"]
        constraints = [
            models.CheckConstraint(condition=models.Q(worker__isnull=False) ^ models.Q(staff__isnull=False), name="attendance_one_person"),
            models.UniqueConstraint(fields=["project", "work_date", "worker"], condition=models.Q(worker__isnull=False),
                                    name="attendance_worker_once"),
            models.UniqueConstraint(fields=["project", "work_date", "staff"], condition=models.Q(staff__isnull=False),
                                    name="attendance_staff_once"),
            models.CheckConstraint(condition=models.Q(days__gt=0) & models.Q(days__lte=3), name="attendance_days_range"),
        ]
