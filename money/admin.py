from django.contrib import admin

from .models import AccountingSettings


@admin.register(AccountingSettings)
class AccountingSettingsAdmin(admin.ModelAdmin):
    """Tài khoản xuất MISA (Giám đốc sửa)."""
    list_display = ("cash_account", "bank_account", "receivable_account", "payable_account", "cost_account", "bank_name")

    def has_add_permission(self, request):
        return not AccountingSettings.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False
