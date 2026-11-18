from django.contrib import admin

from .models import Worker


@admin.register(Worker)
class WorkerAdmin(admin.ModelAdmin):
    """Danh sách thợ và đơn giá ngày công (Giám đốc/kế toán). Chấm công nhập qua chat."""
    list_display = ("name", "team", "kind", "day_rate", "active")
    list_filter = ("team", "kind", "active")
    search_fields = ("name", "team")
