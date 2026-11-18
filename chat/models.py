"""Kênh chat: kết nối Telegram, phiếu chờ xác nhận, ảnh nhận qua chat, chi phí AI.

Phiếu chưa phải dữ liệu nghiệp vụ: chỉ khi người gửi bấm Xác nhận, `field.services` / `asks.services` mới ghi
(có nhật ký). Kết nối/ngắt và quyết định phiếu cũng ghi nhật ký.
"""
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models

from projects.models import Project


class ChatLink(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="chat_links")
    external_id = models.CharField("Mã người dùng Telegram", max_length=64)
    chat_id = models.CharField(max_length=64)
    display_name = models.CharField(max_length=200, blank=True)
    active = models.BooleanField(default=True)
    state = models.JSONField("Bước đang chờ", default=dict, blank=True)
    linked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Kết nối Telegram"
        verbose_name_plural = "Kết nối Telegram"
        constraints = [
            models.UniqueConstraint(fields=["external_id"], condition=models.Q(active=True), name="chat_link_external_once"),
            models.UniqueConstraint(fields=["user"], condition=models.Q(active=True), name="chat_link_user_once"),
        ]


class LinkCode(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    digest = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)


class ChatUpdate(models.Model):
    update_id = models.BigIntegerField(unique=True)
    received_at = models.DateTimeField(auto_now_add=True)


class Proposal(models.Model):
    class Kind(models.TextChoices):
        REPORT = "report", "Báo cáo ngày"
        FIX = "fix", "Sửa chấm công"
        RATE = "rate", "Sửa thợ"
        ANSWER = "answer", "Trả lời yêu cầu"
        MONEY = "money", "Phiếu thu chi"
        VOID = "void", "Huỷ phiếu"

    class State(models.TextChoices):
        PENDING = "pending", "Chờ xác nhận"
        CONFIRMED = "confirmed", "Đã ghi"
        CANCELLED = "cancelled", "Đã bỏ"
        FAILED = "failed", "Không ghi được"

    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    link = models.ForeignKey(ChatLink, on_delete=models.PROTECT, related_name="proposals")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    payload = models.JSONField(default=dict)
    source_text = models.TextField(blank=True, max_length=5000)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    error = models.CharField(max_length=500, blank=True)
    result = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Phiếu chat"
        verbose_name_plural = "Phiếu chat"
        ordering = ["-created_at"]


def chat_photo_path(instance, filename):
    return f"chat/{instance.link.user_id}/{uuid4().hex}{Path(filename).suffix.lower() or '.jpg'}"


class ChatPhoto(models.Model):
    """Ảnh nhận qua chat. target: "code:T2.5" (chú thích, chưa gắn phiếu), "item:<id>", "" (ảnh chung), "?" (chờ chọn)."""
    link = models.ForeignKey(ChatLink, on_delete=models.PROTECT, related_name="photos")
    proposal = models.ForeignKey(Proposal, on_delete=models.PROTECT, null=True, blank=True, related_name="photos")
    file_unique_id = models.CharField(max_length=128, blank=True)
    file_id = models.CharField(max_length=256, blank=True)
    image = models.FileField(upload_to=chat_photo_path)
    target = models.CharField(max_length=40, blank=True)
    prompt_message_id = models.BigIntegerField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class AiUsage(models.Model):
    day = models.DateField(unique=True)
    input_tokens = models.BigIntegerField(default=0)
    output_tokens = models.BigIntegerField(default=0)
    calls = models.IntegerField(default=0)
