"""Bản vẽ (nền từng tầng từ DXF) và chi tiết khối lượng. Vẽ, bóc tách làm trong AutoCAD; ở đây chỉ nhập và xem."""
from django.db import models

from projects.models import Project


class Drawing(models.Model):
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="drawings")
    floor = models.CharField("Tầng", max_length=30)
    order = models.PositiveIntegerField("Thứ tự", default=0)
    view_box = models.JSONField("Khung nhìn", default=list)
    background = models.JSONField("Nét nền", default=dict, help_text="{layer: [đường SVG]}")
    source_name = models.CharField("Tệp gốc", max_length=255, blank=True)
    version = models.PositiveIntegerField("Lần nhập", default=1)
    wall_height = models.PositiveIntegerField("Cao tường mặc định (mm)", null=True, blank=True)
    imported_at = models.DateTimeField("Nhập lúc", auto_now_add=True)

    class Meta:
        verbose_name = "Bản vẽ tầng"
        verbose_name_plural = "Bản vẽ tầng"
        ordering = ["project", "order", "floor"]
        constraints = [models.UniqueConstraint(fields=["project", "floor"], name="drawing_floor_once")]


class Item(models.Model):
    """Một chi tiết theo dõi: một bức tường, một phòng lát, một cấu kiện… có mã trên bản vẽ (vd T2.5)."""
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="items")
    code = models.CharField("Mã chi tiết", max_length=30)
    floor = models.CharField("Tầng", max_length=30, blank=True)
    work = models.CharField("Công việc", max_length=200)
    location = models.CharField("Vị trí", max_length=200, blank=True)
    unit = models.CharField("Đơn vị", max_length=20)
    design_quantity = models.DecimalField("Khối lượng thiết kế", max_digits=14, decimal_places=3)
    sale_price = models.DecimalField("Đơn giá bán (₫)", max_digits=15, decimal_places=0, null=True, blank=True)
    cost_price = models.DecimalField("Đơn giá vốn (₫)", max_digits=15, decimal_places=0, null=True, blank=True)
    plan_start = models.DateField("Kế hoạch bắt đầu", null=True, blank=True)
    plan_end = models.DateField("Kế hoạch xong", null=True, blank=True)
    note = models.CharField("Cách tính", max_length=300, blank=True)
    geometry = models.JSONField("Hình trên bản vẽ", null=True, blank=True)
    source_key = models.CharField("Mã nguồn bản vẽ", max_length=100, blank=True)
    active = models.BooleanField("Còn dùng", default=True)

    class Meta:
        verbose_name = "Chi tiết khối lượng"
        verbose_name_plural = "Chi tiết khối lượng"
        ordering = ["project", "floor", "code"]
        constraints = [models.UniqueConstraint(fields=["project", "code"], name="item_code_once")]

    def __str__(self):
        return f"{self.code} · {self.work}"
