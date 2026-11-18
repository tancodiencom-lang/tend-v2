"""Phiếu thu, phiếu chi và các khoản phát sinh công nợ. Kế toán nhập qua chat (phiếu đã xác nhận); web chỉ xem, tải.

Công nợ tính lúc đọc: NCC = hàng nhận − đã trả; tổ thợ = giá trị khoán được tính − đã trả; khách = hợp đồng − đã thu.
"""
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models

from projects.models import Project


class Kind(models.TextChoices):
    CUSTOMER_RECEIPT = "thu_khach", "Thu tiền khách"
    OTHER_RECEIPT = "thu_khac", "Thu khác"
    SUPPLIER_GOODS = "hang_ncc", "Hàng NCC đã nhận (phát sinh nợ)"
    SUPPLIER_PAYMENT = "tra_ncc", "Trả nhà cung cấp"
    TEAM_WORK = "khoan_tinh", "Giá trị khoán tổ thợ được tính (phát sinh nợ)"
    TEAM_PAYMENT = "tra_to", "Trả tổ thợ"
    LABOR_PAYMENT = "tra_cong", "Trả lương công nhật"
    OTHER_EXPENSE = "chi_khac", "Chi khác"


RECEIPTS = {Kind.CUSTOMER_RECEIPT, Kind.OTHER_RECEIPT}
CASH_OUT = {Kind.SUPPLIER_PAYMENT, Kind.TEAM_PAYMENT, Kind.LABOR_PAYMENT, Kind.OTHER_EXPENSE}


def voucher_path(instance, filename):
    return f"chung-tu/{instance.project_id or 'chung'}/{uuid4().hex}{Path(filename).suffix.lower() or '.jpg'}"


class Entry(models.Model):
    class Method(models.TextChoices):
        CASH = "tien_mat", "Tiền mặt"
        BANK = "chuyen_khoan", "Chuyển khoản"
        NONE = "khong", "Không qua quỹ"

    kind = models.CharField("Loại", max_length=12, choices=Kind.choices)
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="money")
    date = models.DateField("Ngày")
    number = models.CharField("Số phiếu", max_length=40, blank=True)
    party = models.CharField("Đối tượng (khách / NCC / tổ thợ / người)", max_length=200)
    content = models.CharField("Nội dung", max_length=500)
    amount = models.DecimalField("Số tiền (₫)", max_digits=15, decimal_places=0)
    method = models.CharField("Hình thức", max_length=12, choices=Method.choices, default=Method.CASH)
    voucher = models.ImageField("Ảnh chứng từ", upload_to=voucher_path, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    client_key = models.UUIDField("Mã chống gửi trùng", unique=True)
    misa_exported_at = models.DateTimeField("Đã xuất MISA lúc", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Phiếu thu chi / phát sinh"
        verbose_name_plural = "Phiếu thu chi / phát sinh"
        ordering = ["-date", "-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="money_amount_positive")]
