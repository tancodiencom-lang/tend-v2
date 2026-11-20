"""Trợ lý Giám đốc (đợt 7): việc giao có người chịu trách nhiệm/hạn/tiêu chí xong, góp ý, và sổ tin đã gửi.

Trợ lý chỉ nhắc, hỏi, tổng hợp, cảnh báo: không ghi báo cáo, chấm công, phiếu thu chi; không duyệt hay huỷ gì.
Việc giao do Giám đốc xác nhận mới tạo; người làm báo xong, Giám đốc nhận hoặc trả lại. Không xoá: huỷ có lý do.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone

from projects.models import Project


class Task(models.Model):
    class State(models.TextChoices):
        OPEN = "open", "Đang làm"
        REPORTED = "reported", "Báo xong, chờ Giám đốc nhận"
        DONE = "done", "Đã xong"
        CANCELLED = "cancelled", "Đã huỷ"

    title = models.CharField("Việc", max_length=300)
    criteria = models.CharField("Tiêu chí xong", max_length=500, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người chịu trách nhiệm", on_delete=models.PROTECT,
                              related_name="tasks")
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="tasks")
    due = models.DateTimeField("Hạn")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người giao", on_delete=models.PROTECT, related_name="+")
    state = models.CharField("Trạng thái", max_length=10, choices=State.choices, default=State.OPEN)
    accepted_at = models.DateTimeField("Người làm nhận việc lúc", null=True, blank=True)
    reported_at = models.DateTimeField("Báo xong lúc", null=True, blank=True)
    report_note = models.CharField("Ghi chú khi báo xong", max_length=1000, blank=True)
    closed_at = models.DateTimeField("Đóng lúc", null=True, blank=True)
    close_note = models.CharField("Lý do huỷ / trả lại gần nhất", max_length=500, blank=True)
    source_text = models.TextField("Chỉ đạo gốc", max_length=2000, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Việc giao"
        verbose_name_plural = "Việc giao"
        ordering = ["due", "pk"]

    def __str__(self):
        return f"#{self.pk} {self.title}"


class Feedback(models.Model):
    """Góp ý của người dùng (/gopy) và chỗ vướng bot tự ghi (bot không hiểu, phiếu bị bỏ…)."""

    class Kind(models.TextChoices):
        USER = "user", "Góp ý"
        FRICTION = "friction", "Chỗ vướng (tự ghi)"

    class Topic(models.TextChoices):
        PHOTO = "anh", "Hỏi ảnh của chi tiết nào"
        PERSON = "ten_tho", "Hỏi tên thợ là ai"
        NO_STAFF = "thieu_tho", "Báo cáo chưa có tên thợ"
        REASON = "ly_do", "Hỏi lý do (báo giảm, sửa công)"
        UNREAD = "chua_doc", "Phần tin bot chưa đọc được"
        CODE = "ma", "Mã chi tiết không có trên bản vẽ"
        VALUE = "thieu_so", "Việc có mã nhưng thiếu % hoặc khối lượng"
        PROJECT = "cong_trinh", "Hỏi công trình nào"
        PARTY = "doi_tuong", "Hỏi đối tượng thu chi"
        DUPLICATE = "nghi_trung", "Hỏi phiếu nghi trùng"
        NOT_UNDERSTOOD = "khong_hieu", "Bot không hiểu tin"
        FAILED = "khong_ghi", "Phiếu không ghi được"

    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.USER)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    text = models.TextField("Nội dung (đã che số điện thoại)", max_length=2000)
    context = models.CharField("Ở đâu", max_length=200, blank=True)
    topic = models.CharField("Loại chỗ vướng", max_length=12, choices=Topic.choices, blank=True)
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Góp ý"
        verbose_name_plural = "Góp ý"
        ordering = ["-created_at"]


class Notice(models.Model):
    """Một tin trợ lý đã gửi (khoá duy nhất): chạy lịch nhiều lần không gửi trùng."""
    key = models.CharField(max_length=120, unique=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    text = models.TextField(blank=True)
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-sent_at"]


class Question(models.Model):
    """Câu hỏi trợ lý không tự trả lời được: hỏi Giám đốc, Giám đốc trả lời (hoặc duyệt bản nháp), trợ lý trả lời lại đúng chỗ."""

    class State(models.TextChoices):
        OPEN = "open", "Chờ Giám đốc"
        ANSWERED = "answered", "Đã trả lời"
        DROPPED = "dropped", "Không cần trả lời"

    asked_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Người hỏi", on_delete=models.PROTECT, related_name="+")
    chat_id = models.CharField("Hỏi ở (mã chat)", max_length=64)
    where = models.CharField("Hỏi ở", max_length=200, blank=True)
    message_id = models.BigIntegerField(null=True, blank=True)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    text = models.TextField("Câu hỏi", max_length=2000)
    draft = models.TextField("Bản nháp trả lời", max_length=2000, blank=True)
    state = models.CharField(max_length=10, choices=State.choices, default=State.OPEN)
    answer = models.TextField("Trả lời", max_length=3000, blank=True)
    answered_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    answered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Câu hỏi chờ Giám đốc"
        verbose_name_plural = "Câu hỏi chờ Giám đốc"
        ordering = ["-created_at"]

