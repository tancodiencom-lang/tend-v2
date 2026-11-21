"""Kênh chat: kết nối Telegram, phiếu chờ xác nhận, ảnh nhận qua chat, chi phí AI.

Phiếu chưa phải dữ liệu nghiệp vụ: chỉ khi người gửi bấm Xác nhận, `field.services` / `asks.services` mới ghi
(có nhật ký). Kết nối/ngắt và quyết định phiếu cũng ghi nhật ký.
"""
from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.db import models

from core.models import User
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
    # Nhập mã xong còn bước 2: chia sẻ số điện thoại Telegram, phải trùng số công ty ghi ở tài khoản
    claimed_by = models.CharField(max_length=64, blank=True)
    claimed_chat = models.CharField(max_length=64, blank=True)
    claimed_name = models.CharField(max_length=200, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)


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
        TASK = "task", "Giao việc"
        DEBT = "debt", "Hạn trả, đợt thu"

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
    caption = models.CharField("Chú thích kèm ảnh", max_length=1000, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    # Kho tài liệu gốc (chat/archive.py): đường dẫn tương đối trong thư mục xuất, đặt một lần, không đổi
    archived_as = models.CharField(max_length=400, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["pk"]


class AiUsage(models.Model):
    """Dùng AI mỗi ngày theo việc: "chat" (bot báo cáo, thu chi…) và "noi_dung" (trợ lý bài đăng) — trần chi phí riêng."""
    day = models.DateField()
    purpose = models.CharField(max_length=12, default="chat")
    input_tokens = models.BigIntegerField(default=0)
    output_tokens = models.BigIntegerField(default=0)
    calls = models.IntegerField(default=0)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["day", "purpose"], name="ai_usage_day_purpose_once")]


class Group(models.Model):
    """Nhóm Telegram nội bộ có bot. Giám đốc gắn nhóm với công trình bằng /nhom TN-22 (hoặc /nhom chung)."""
    chat_id = models.CharField(max_length=64, unique=True)
    title = models.CharField("Tên nhóm", max_length=200, blank=True)
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="+")
    active = models.BooleanField("Đang dùng", default=False)
    listening = models.BooleanField("Nghe tin trong nhóm", default=True,
                                    help_text="Tự nhận báo cáo/phiếu theo cú pháp (gửi riêng người nhắn xác nhận).")
    linked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    linked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Nhóm Telegram"
        verbose_name_plural = "Nhóm Telegram"

    def __str__(self):
        return self.title or self.chat_id


class GroupViewer(User):
    """Quyền ĐỌC thu hẹp (chỉ dùng để đọc số, không dùng để ghi):
    - trả lời TRONG NHÓM: mọi người trong nhóm cùng đọc nên không có phần tiền, chỉ công trình của nhóm;
    - trả lời ở một BÀN trợ lý (chat/desks.py): chỉ phần tiền của đúng vị trí đó (vd bàn Hiện trường không có tiền, kể cả
      khi Giám đốc hỏi). `money` = các phần được xem trong ("payables", "receivables", "profit"), vẫn cần quyền gốc của người."""
    only_project_id = None
    money = ()

    class Meta:
        proxy = True

    @property
    def sees_payables(self):
        return "payables" in self.money and User.sees_payables.fget(self)

    @property
    def sees_receivables(self):
        return "receivables" in self.money and User.sees_receivables.fget(self)

    @property
    def sees_profit(self):
        return "profit" in self.money and User.sees_profit.fget(self)

    @classmethod
    def of(cls, user, project=None, money=()):
        viewer = cls()
        viewer.__dict__.update({k: v for k, v in user.__dict__.items() if not k.startswith("_")})
        viewer._state = user._state
        viewer.only_project_id = project.pk if project else None
        viewer.money = tuple(money)
        return viewer

