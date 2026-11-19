"""Đường ghi duy nhất của trợ lý: mỗi hàm một giao dịch, nhật ký từng thay đổi, kiểm quyền ở đây (không chỉ ở bot)."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.audit import record_save
from projects.models import projects_for

from .models import Feedback, Question, Task

OPEN_STATES = (Task.State.OPEN, Task.State.REPORTED)


def _locked(task):
    return Task.objects.select_for_update().get(pk=task.pk)


def _reason(text, what):
    text = " ".join(str(text or "").split())
    if not text:
        raise ValidationError(f"{what} cần ghi lý do.")
    return text[:500]


@transaction.atomic
def create_task(user, owner, title, due, project=None, criteria="", source_text="", channel="telegram"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc giao việc qua trợ lý.")
    if owner is None or not owner.is_active:
        raise ValidationError("Không rõ người nhận việc.")
    if project is not None and not projects_for(owner).filter(pk=project.pk).exists():
        raise ValidationError(f"{owner} không thuộc công trình {project.code}.")
    title = " ".join(str(title or "").split())
    if not title:
        raise ValidationError("Ghi rõ việc cần làm.")
    if due is None:
        raise ValidationError("Việc cần có hạn.")
    task = Task(title=title[:300], owner=owner, project=project, due=due, created_by=user, criteria=str(criteria or "")[:500],
                source_text=str(source_text or "")[:2000])
    return record_save(task, user, "Giám đốc giao việc qua trợ lý", channel=channel)


@transaction.atomic
def accept(user, task, channel="telegram"):
    task = _locked(task)
    if task.owner_id != user.pk:
        raise PermissionDenied("Việc này không giao cho anh/chị.")
    if task.accepted_at or task.state not in OPEN_STATES:
        return task
    task.accepted_at = timezone.now()
    return record_save(task, user, "Nhận việc", channel=channel)


@transaction.atomic
def report_done(user, task, note="", channel="telegram"):
    task = _locked(task)
    if task.owner_id != user.pk:
        raise PermissionDenied("Chỉ người nhận việc báo xong.")
    if task.state != Task.State.OPEN:
        raise ValidationError(f"Việc #{task.pk} đang ở trạng thái: {task.get_state_display().lower()}.")
    task.state, task.reported_at, task.report_note = Task.State.REPORTED, timezone.now(), " ".join(str(note or "").split())[:1000]
    task.accepted_at = task.accepted_at or task.reported_at
    return record_save(task, user, "Báo xong việc", channel=channel)


@transaction.atomic
def approve(user, task, channel="telegram"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc nhận kết quả việc.")
    task = _locked(task)
    if task.state != Task.State.REPORTED:
        raise ValidationError(f"Việc #{task.pk} chưa báo xong.")
    task.state, task.closed_at = Task.State.DONE, timezone.now()
    return record_save(task, user, "Giám đốc nhận kết quả, việc đã xong", channel=channel)


@transaction.atomic
def send_back(user, task, reason, channel="telegram"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc trả lại việc.")
    reason = _reason(reason, "Trả lại việc")
    task = _locked(task)
    if task.state != Task.State.REPORTED:
        raise ValidationError(f"Việc #{task.pk} chưa báo xong.")
    task.state, task.close_note = Task.State.OPEN, reason
    return record_save(task, user, f"Giám đốc trả lại việc: {reason}", channel=channel)


@transaction.atomic
def cancel(user, task, reason, channel="telegram"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc huỷ việc đã giao.")
    reason = _reason(reason, "Huỷ việc")
    task = _locked(task)
    if task.state not in OPEN_STATES:
        raise ValidationError(f"Việc #{task.pk} đã {task.get_state_display().lower()}.")
    task.state, task.closed_at, task.close_note = Task.State.CANCELLED, timezone.now(), reason
    return record_save(task, user, f"Huỷ việc: {reason}", channel=channel)


@transaction.atomic
def add_feedback(user, text, kind=Feedback.Kind.USER, context="", channel="telegram"):
    text = " ".join(str(text or "").split())
    if not text:
        raise ValidationError("Ghi nội dung góp ý, ví dụ: /gopy nút xác nhận khó bấm trên điện thoại")
    return record_save(Feedback(kind=kind, author=user, text=text[:2000], context=str(context)[:200]), user,
                       "Góp ý" if kind == Feedback.Kind.USER else "Ghi chỗ vướng", channel=channel)


def visible(user):
    """Giám đốc thấy mọi việc; người khác chỉ việc của mình."""
    rows = Task.objects.select_related("owner", "project", "created_by")
    return rows if user.is_director else rows.filter(owner=user)


def open_tasks(user=None):
    rows = Task.objects.filter(state__in=OPEN_STATES).select_related("owner", "project")
    return rows if user is None else rows.filter(Q(owner=user))


def find(user, ref):
    """"#12" / "12" trong phạm vi người đó được xem."""
    ref = str(ref or "").strip().lstrip("#")
    return visible(user).filter(pk=int(ref)).first() if ref.isdecimal() else None


def task_line(task, now):
    local = timezone.localtime(task.due)
    where = f" · {task.project.code}" if task.project_id else ""
    if task.state == Task.State.REPORTED:
        mark = "↺ báo xong, chờ Giám đốc nhận"
    elif task.state == Task.State.DONE:
        mark = "✓ xong"
    elif task.state == Task.State.CANCELLED:
        mark = "× huỷ"
    elif now > task.due:
        late = now - task.due
        mark = f"▼ trễ {late.days} ngày" if late.days else f"▼ trễ {late.seconds // 3600} giờ"
    else:
        mark = "● đang làm" + ("" if task.accepted_at else ", chưa bấm nhận")
    return f"#{task.pk} {task.title}{where} · {task.owner} · hạn {local:%d/%m %H:%M} · {mark}"


# ---------- câu hỏi chờ Giám đốc ----------

@transaction.atomic
def ask_director(user, text, chat_id, where="", message_id=None, project=None, draft="", channel="telegram"):
    text = " ".join(str(text or "").split())
    if not text:
        raise ValidationError("Ghi câu hỏi, ví dụ: /hoi khách TN-22 muốn đổi gạch lát phòng khách được không?")
    return record_save(Question(asked_by=user, chat_id=str(chat_id), where=str(where)[:200], message_id=message_id, project=project,
                                text=text[:2000], draft=" ".join(str(draft or "").split())[:2000]),
                       user, "Hỏi Giám đốc qua trợ lý", channel=channel)


@transaction.atomic
def answer_question(user, question, text, channel="telegram"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc trả lời câu hỏi chờ Giám đốc.")
    question = Question.objects.select_for_update().get(pk=question.pk)
    if question.state != Question.State.OPEN:
        raise ValidationError(f"Câu hỏi #{question.pk} đã {question.get_state_display().lower()}.")
    text = str(text or "").strip()
    if not text:
        raise ValidationError("Câu trả lời trống.")
    question.state, question.answer, question.answered_by, question.answered_at = (
        Question.State.ANSWERED, text[:3000], user, timezone.now())
    return record_save(question, user, "Giám đốc trả lời câu hỏi", channel=channel)


@transaction.atomic
def drop_question(user, question, channel="telegram"):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc đóng câu hỏi.")
    question = Question.objects.select_for_update().get(pk=question.pk)
    if question.state != Question.State.OPEN:
        raise ValidationError(f"Câu hỏi #{question.pk} đã {question.get_state_display().lower()}.")
    question.state, question.answered_by, question.answered_at = Question.State.DROPPED, user, timezone.now()
    return record_save(question, user, "Giám đốc: không cần trả lời", channel=channel)

