"""Yêu cầu báo cáo: Giám đốc hỏi người liên quan về chi tiết trên bản vẽ; trả lời bằng báo cáo hoặc bằng chữ.

Trạng thái tính lúc đọc: có câu trả lời chữ, hoặc người được hỏi đã gửi báo cáo có chi tiết được hỏi sau lúc hỏi.
"""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import record_save
from field.models import ReportLine
from projects.models import projects_for

from .models import Answer, Ask


@transaction.atomic
def create(user, project, assignee, items, note, due=None, channel="web"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc tạo yêu cầu báo cáo.")
    if not projects_for(assignee).filter(pk=project.pk).exists() or not assignee.is_active:
        raise ValidationError("Người được hỏi không thuộc công trình này.")
    note = str(note or "").strip()
    if not note:
        raise ValidationError("Ghi nội dung cần báo cáo.")
    if any(item.project_id != project.pk for item in items):
        raise ValidationError("Chi tiết không thuộc công trình.")
    ask = record_save(Ask(project=project, requester=user, assignee=assignee, note=note[:2000], due=due), user,
                      "Giám đốc yêu cầu báo cáo", channel=channel)
    ask.items.set(items)
    return ask


def status(ask):
    """("done", "✓ Đã có") / ("late", "▼ Trễ hạn") / ("open", "? Chưa có") / ("cancelled", "× Đã huỷ")."""
    if ask.cancelled_at:
        return "cancelled", "× Đã huỷ"
    if ask.answers.exists() or ReportLine.objects.filter(
            report__author=ask.assignee, report__created_at__gte=ask.created_at, item__in=ask.items.all()).exists():
        return "done", "✓ Đã có"
    if ask.due and ask.due < timezone.localdate():
        return "late", "▼ Trễ hạn"
    return "open", "? Chưa có"


def pending_for(user):
    rows = Ask.objects.filter(assignee=user, cancelled_at__isnull=True).select_related("project", "requester").prefetch_related("items")
    return [ask for ask in rows if status(ask)[0] in ("open", "late")]


def text(ask):
    codes = ", ".join(item.code for item in ask.items.all())
    due = f" · hạn {ask.due:%d/%m/%Y}" if ask.due else ""
    how = ("Trả lời bằng báo cáo ngày có các chi tiết này (kèm ảnh)." if codes
           else "Bấm Trả lời yêu cầu này rồi nhắn câu trả lời.")
    return (f"YÊU CẦU BÁO CÁO · {ask.project.code}{due}\n{ask.requester} hỏi: {ask.note}"
            + (f"\nChi tiết: {codes}" if codes else "") + f"\n{how}")


@transaction.atomic
def answer(user, ask, note, channel="telegram"):
    ask = Ask.objects.select_for_update().get(pk=ask.pk)
    if ask.assignee_id != user.pk or ask.cancelled_at:
        raise PermissionDenied("Yêu cầu này không chờ anh/chị trả lời.")
    note = str(note or "").strip()
    if not note:
        raise ValidationError("Câu trả lời trống.")
    return record_save(Answer(ask=ask, author=user, note=note[:5000]), user, "Trả lời yêu cầu báo cáo", channel=channel)
