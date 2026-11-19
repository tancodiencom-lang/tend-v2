"""Trang Trợ lý: việc giao (Giám đốc xem tất cả, người khác việc của mình), câu hỏi chờ Giám đốc, góp ý.

Thao tác trên việc làm được cả ở đây lẫn trên bot (cùng đường ghi `services`, nhật ký kênh web/telegram):
người làm Nhận việc / Báo xong; Giám đốc Nhận kết quả / Trả lại / Huỷ (trả lại, huỷ phải có lý do).
Giao việc mới vẫn qua bot (/giao hoặc nói với trợ lý) để có phiếu xác nhận.
"""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from . import services
from .models import Feedback, Question, Task


@login_required
@require_GET
def index(request):
    user, now = request.user, timezone.now()
    rows = services.visible(user)
    open_ = list(rows.filter(state__in=services.OPEN_STATES))
    closed = list(rows.exclude(state__in=services.OPEN_STATES).filter(created_at__gte=now - timedelta(days=30)).order_by("-closed_at"))
    context = {
        "open": [{"task": t, "line": services.task_line(t, now), "late": t.state == Task.State.OPEN and now > t.due,
                  "mine": t.owner_id == user.pk} for t in open_],
        "closed": [{"task": t, "line": services.task_line(t, now)} for t in closed],
        "late_count": sum(1 for t in open_ if t.state == Task.State.OPEN and now > t.due),
        "feedback": list(Feedback.objects.select_related("author")[:50]) if user.is_director else [],
        "questions": list(Question.objects.select_related("asked_by", "answered_by")[:30]) if user.is_director else [],
    }
    return render(request, "assistant/index.html", context)


@login_required
@require_POST
def act(request, pk, action):
    from chat import task_bot          # báo qua Telegram như khi thao tác trên bot
    user = request.user
    task = services.find(user, str(pk))
    if task is None:
        raise Http404
    note = request.POST.get("ghi_chu", "")
    try:
        with transaction.atomic():
            if action == "nhan":
                services.accept(user, task, channel="web")
                done = f"Đã nhận việc #{task.pk}."
            elif action == "xong":
                task = services.report_done(user, task, note, channel="web")
                transaction.on_commit(lambda: task_bot.notify_done(task, user))
                done = f"Đã báo Giám đốc việc #{task.pk} xong."
            elif action == "nhan-ket-qua":
                task = services.approve(user, task, channel="web")
                transaction.on_commit(lambda: task_bot.tell(task.owner, f"✓ Giám đốc đã nhận kết quả việc #{task.pk} \"{task.title}\". Việc đã xong."))
                done = f"Việc #{task.pk} đã xong."
            elif action == "tra-lai":
                task = services.send_back(user, task, note, channel="web")
                transaction.on_commit(lambda: task_bot.tell(task.owner, f"Giám đốc TRẢ LẠI việc #{task.pk} \"{task.title}\": "
                                                                         f"{task.close_note}. Làm tiếp rồi /xong {task.pk} lần nữa."))
                done = f"Đã trả lại việc #{task.pk}."
            elif action == "huy":
                task = services.cancel(user, task, note, channel="web")
                transaction.on_commit(lambda: task_bot.tell(task.owner, f"Giám đốc huỷ việc #{task.pk} \"{task.title}\": {task.close_note}."))
                done = f"Đã huỷ việc #{task.pk}."
            else:
                raise Http404
    except (PermissionDenied, ValidationError) as error:
        messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, done)
    return redirect("assistant")
