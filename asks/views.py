"""Giám đốc tạo yêu cầu báo cáo từ bản vẽ; người được hỏi nhận ngay qua Telegram."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from core.models import User
from drawings.models import Item
from projects.models import projects_for

from . import services


@login_required
@require_POST
def create(request, code):
    project = get_object_or_404(projects_for(request.user), code=code)
    if not request.user.is_director:
        raise PermissionDenied("Chỉ Giám đốc tạo yêu cầu báo cáo.")
    item = get_object_or_404(Item, pk=request.POST.get("item_id"), project=project)
    assignee = get_object_or_404(User, pk=request.POST.get("assignee"), is_active=True)
    due = None
    if request.POST.get("due"):
        try:
            due = date.fromisoformat(request.POST["due"])
        except ValueError:
            messages.error(request, "Hạn không hợp lệ.")
            return redirect(f"{reverse('drawings_view', args=[project.code])}?tang={item.floor}")
    try:
        with transaction.atomic():
            ask = services.create(request.user, project, assignee, [item], request.POST.get("note", ""), due)
            transaction.on_commit(lambda: _notify(ask))
    except ValidationError as error:
        messages.error(request, "; ".join(error.messages))
    else:
        messages.success(request, f"Đã gửi yêu cầu báo cáo {item.code} cho {assignee}.")
    return redirect(f"{reverse('drawings_view', args=[project.code])}?tang={item.floor}")


def _notify(ask):
    from chat.bot import notify_ask
    notify_ask(ask)
