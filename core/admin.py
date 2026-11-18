"""Trang quản trị chỉ để Giám đốc thiết lập ban đầu (tài khoản, vai). Nhật ký chỉ xem."""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import AuditLog, User


@admin.register(User)
class TendUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("Tend", {"fields": ("role", "phone")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("Tend", {"fields": ("role", "first_name", "last_name")}),)
    list_display = ("username", "first_name", "last_name", "role", "is_active")
    list_filter = ("role", "is_active")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("at", "actor", "table", "object_id", "action", "channel", "reason")
    list_filter = ("table", "action", "channel")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
