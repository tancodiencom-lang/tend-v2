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
ACCRUALS = {Kind.SUPPLIER_GOODS, Kind.TEAM_WORK}          # phát sinh nợ, không qua quỹ
NEEDS_PROJECT = {Kind.CUSTOMER_RECEIPT, Kind.SUPPLIER_GOODS, Kind.SUPPLIER_PAYMENT, Kind.TEAM_WORK, Kind.TEAM_PAYMENT}


class Party(models.Model):
    """Đối tượng công nợ: nhà cung cấp, tổ thợ, khách hàng, khác. Một nơi để công nợ không tách đôi vì gõ khác tên."""
    class Type(models.TextChoices):
        SUPPLIER = "ncc", "Nhà cung cấp"
        TEAM = "to", "Tổ thợ"
        CUSTOMER = "khach", "Khách hàng"
        OTHER = "khac", "Khác"

    name = models.CharField("Tên", max_length=200)
    type = models.CharField("Loại", max_length=6, choices=Type.choices, default=Type.SUPPLIER)
    misa_code = models.CharField("Mã đối tượng trong MISA", max_length=50, blank=True)
    tax_code = models.CharField("Mã số thuế", max_length=20, blank=True)
    address = models.CharField("Địa chỉ", max_length=300, blank=True)
    phone = models.CharField("Điện thoại", max_length=20, blank=True)
    terms_days = models.PositiveSmallIntegerField("Số ngày được nợ (hạn trả mặc định)", null=True, blank=True,
                                                  help_text="Khoản nhận hàng/khoán chưa ghi hạn thì hạn = ngày phiếu + số ngày này")
    active = models.BooleanField("Đang dùng", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Đối tượng"
        verbose_name_plural = "Đối tượng"
        ordering = ["type", "name"]

    def __str__(self):
        return self.name


# Loại đối tượng hợp với loại phiếu (để gợi ý và kiểm).
PARTY_TYPE = {Kind.CUSTOMER_RECEIPT: Party.Type.CUSTOMER, Kind.SUPPLIER_GOODS: Party.Type.SUPPLIER,
              Kind.SUPPLIER_PAYMENT: Party.Type.SUPPLIER, Kind.TEAM_WORK: Party.Type.TEAM, Kind.TEAM_PAYMENT: Party.Type.TEAM}


class AccountingSettings(models.Model):
    """Tài khoản kế toán và ngân hàng khi xuất MISA (một dòng; Giám đốc sửa ở trang quản trị)."""
    cash_account = models.CharField("TK tiền mặt", max_length=20, default="1111")
    bank_account = models.CharField("TK tiền gửi", max_length=20, default="1121")
    receivable_account = models.CharField("TK phải thu khách", max_length=20, default="131")
    payable_account = models.CharField("TK phải trả", max_length=20, default="331")
    cost_account = models.CharField("TK chi phí công trình", max_length=20, default="154")
    other_income_account = models.CharField("TK thu khác", max_length=20, default="711")
    bank_number = models.CharField("Số tài khoản ngân hàng công ty", max_length=50, blank=True)
    bank_name = models.CharField("Tên ngân hàng", max_length=200, blank=True)

    class Meta:
        verbose_name = "Cài đặt kế toán (MISA)"
        verbose_name_plural = "Cài đặt kế toán (MISA)"

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class MisaExport(models.Model):
    kind = models.CharField("Loại tệp", max_length=20)
    date_from = models.DateField()
    date_to = models.DateField()
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    row_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Lần xuất MISA"
        verbose_name_plural = "Lần xuất MISA"


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
    party_ref = models.ForeignKey(Party, verbose_name="Đối tượng trong danh sách", on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="entries")
    content = models.CharField("Nội dung", max_length=500)
    amount = models.DecimalField("Số tiền (₫)", max_digits=15, decimal_places=0)
    method = models.CharField("Hình thức", max_length=12, choices=Method.choices, default=Method.CASH)
    # Hạn trả của khoản phát sinh nợ (nhận hàng NCC, khoán tổ thợ). Trống = theo số ngày được nợ của đối tượng (nếu có).
    # Kế toán nội bộ / Giám đốc đặt khi nhập hoặc sau (services.set_due, đổi hạn đã có phải ghi lý do, có nhật ký).
    due_on = models.DateField("Hạn trả", null=True, blank=True)
    voucher = models.ImageField("Ảnh chứng từ", upload_to=voucher_path, blank=True)
    entered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    client_key = models.UUIDField("Mã chống gửi trùng", unique=True)
    misa_exported_at = models.DateTimeField("Đã xuất MISA lúc", null=True, blank=True)
    misa_export = models.ForeignKey(MisaExport, on_delete=models.PROTECT, null=True, blank=True, related_name="entries")
    voided_at = models.DateTimeField("Huỷ lúc", null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người huỷ", on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+")
    void_reason = models.CharField("Lý do huỷ", max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Phiếu thu chi / phát sinh"
        verbose_name_plural = "Phiếu thu chi / phát sinh"
        ordering = ["-date", "-created_at"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="money_amount_positive")]

    def __str__(self):
        return f"{self.number or 'phiếu #' + str(self.pk)} · {self.get_kind_display()} · {self.party}"

    @property
    def is_receipt(self):
        return self.kind in RECEIPTS


class Installment(models.Model):
    """Đợt thanh toán của khách theo hợp đồng (đợt 1, đợt 2…): số tiền + hạn. Kế toán tài chính và Giám đốc nhập (bot, web);
    kế toán nội bộ không xem. Tiền khách đã thu trả dần vào các đợt theo thứ tự hạn (money/aging.py). Không xoá: huỷ có lý do."""
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="installments")
    name = models.CharField("Tên đợt", max_length=100)
    amount = models.DecimalField("Số tiền (₫)", max_digits=15, decimal_places=0)
    due_on = models.DateField("Hạn thanh toán")
    note = models.CharField("Ghi chú (điều kiện, mốc)", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField("Huỷ lúc", null=True, blank=True)
    cancel_reason = models.CharField("Lý do huỷ", max_length=300, blank=True)

    class Meta:
        verbose_name = "Đợt thanh toán của khách"
        verbose_name_plural = "Đợt thanh toán của khách"
        ordering = ["project", "due_on", "pk"]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gt=0), name="installment_amount_positive")]

    def __str__(self):
        return f"{self.project.code} · {self.name}"

