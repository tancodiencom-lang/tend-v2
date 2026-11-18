"""Tìm thợ theo tên gõ trong tin nhắn: đúng tên (bỏ dấu) thì lấy luôn; gần giống thì hỏi lại để không tạo trùng người."""
import difflib
from datetime import timedelta

from django.utils import timezone

from core.text import fold

from .models import Attendance, Worker


def recent_for(project, days=60):
    """Thợ đã chấm ở công trình trong 60 ngày gần đây (ưu tiên khi trùng tên)."""
    since = timezone.localdate() - timedelta(days=days)
    return set(Attendance.objects.filter(project=project, worker__isnull=False, work_date__gte=since)
               .values_list("worker_id", flat=True))


def match(name, project=None, workers=None):
    """→ {"worker": Worker} nếu chắc chắn; {"candidates": [Worker…]} nếu cần hỏi lại; {} nếu là tên mới."""
    workers = list(workers if workers is not None else Worker.objects.filter(active=True))
    key = fold(name)
    if not key:
        return {}
    exact = [w for w in workers if fold(w.name) == key]
    if len(exact) == 1:
        return {"worker": exact[0]}
    if len(exact) > 1:
        recent = recent_for(project) if project else set()
        here = [w for w in exact if w.pk in recent]
        return {"worker": here[0]} if len(here) == 1 else {"candidates": exact[:6]}
    names = {}
    for w in workers:
        names.setdefault(fold(w.name), []).append(w)
        last = fold(w.name).split()[-1:]
        if last and last[0] != fold(w.name):
            names.setdefault(last[0], []).append(w)
    close = difflib.get_close_matches(key, list(names), n=4, cutoff=0.75)
    found = list(dict.fromkeys(w for k in close for w in names[k]))
    return {"candidates": found[:6]} if found else {}
