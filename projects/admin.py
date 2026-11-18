from django.contrib import admin

from .models import Member, Project


class MemberInline(admin.TabularInline):
    model = Member
    extra = 1


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "start_date", "handover_date", "active")
    inlines = [MemberInline]
