"""Trang Trợ lý: việc giao (Giám đốc xem tất cả, người khác việc của mình) và góp ý (chỉ Giám đốc). Chỉ xem: giao việc,
báo xong, nhận kết quả làm qua bot."""
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

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
        "open": [{"task": t, "line": services.task_line(t, now), "late": t.state == Task.State.OPEN and now > t.due} for t in open_],
        "closed": [{"task": t, "line": services.task_line(t, now)} for t in closed],
        "late_count": sum(1 for t in open_ if t.state == Task.State.OPEN and now > t.due),
        "feedback": list(Feedback.objects.select_related("author")[:50]) if user.is_director else [],
        "questions": list(Question.objects.select_related("asked_by", "answered_by")[:30]) if user.is_director else [],
    }
    return render(request, "assistant/index.html", context)
