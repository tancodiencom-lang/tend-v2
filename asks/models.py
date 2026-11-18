"""Giám đốc yêu cầu người liên quan báo cáo về chi tiết trên bản vẽ; trả lời qua chat."""
from uuid import uuid4

from django.conf import settings
from django.db import models

from drawings.models import Item
from projects.models import Project


class Ask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, related_name="asks")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="asks_made")
    assignee = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người trả lời", on_delete=models.PROTECT,
                                 related_name="asks_assigned")
    items = models.ManyToManyField(Item, verbose_name="Chi tiết", blank=True)
    note = models.TextField("Nội dung hỏi", max_length=2000)
    due = models.DateField("Hạn", null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Yêu cầu báo cáo"
        verbose_name_plural = "Yêu cầu báo cáo"
        ordering = ["-created_at"]


class Answer(models.Model):
    ask = models.ForeignKey(Ask, on_delete=models.PROTECT, related_name="answers")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    note = models.TextField("Trả lời", max_length=5000)
    report = models.ForeignKey("field.Report", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
