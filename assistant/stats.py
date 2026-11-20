"""Thống kê theo tuần cho Giám đốc: mức đầy đủ báo cáo của từng giám sát (field/quality.py) và chỗ vướng theo loại.

Tính lúc đọc từ báo cáo đã ghi, phiếu chat và chỗ vướng; không lưu bảng tổng hợp. Tuần là thứ Hai → Chủ nhật theo ngày
công việc của báo cáo. Giám đốc xem mọi giám sát; giám sát chỉ xem hàng của mình (chủ dự án chốt 20/11/2026).
"""
from collections import Counter
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Q
from django.utils import timezone

from chat.models import Proposal
from core.models import Role, User
from field import quality
from field.models import Report

from .models import Feedback


def week_of(day):
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def _span(start, end):
    return (timezone.make_aware(datetime.combine(start, time.min)),
            timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min)))


def people(viewer, start, end):
    """Giám sát được thống kê: Giám đốc thấy giám sát đang được giao công trình và giám sát có báo cáo trong tuần;
    giám sát chỉ thấy mình."""
    if viewer.is_director:
        return list(User.objects.filter(role=Role.SUPERVISOR).filter(
            Q(is_active=True, memberships__active=True, memberships__project__active=True)
            | Q(reports__work_date__range=(start, end))).distinct().order_by("first_name", "username"))
    return [viewer] if viewer.role == Role.SUPERVISOR else []


def supervisor_rows(viewer, start, end, now=None):
    """[{user, reports: [(report, score)], count, scored, average, label, misses: Counter, asked, extra, dropped, topics}]."""
    now = now or timezone.now()
    users = people(viewer, start, end)
    if not users:
        return []
    begin, finish = _span(start, end)
    reports = (Report.objects.filter(work_date__range=(start, end), author__in=users).select_related("project", "author")
               .prefetch_related("lines", "extra_work", "photos", "crews").order_by("work_date", "created_at"))
    by_user = {u.pk: [] for u in users}
    for report in reports:
        by_user[report.author_id].append((report, quality.of(report)))
    dropped = Counter(Proposal.objects.filter(kind=Proposal.Kind.REPORT, link__user__in=users, created_at__gte=begin,
                                              created_at__lt=finish)
                      .filter(Q(state__in=(Proposal.State.CANCELLED, Proposal.State.FAILED))
                              | Q(state=Proposal.State.PENDING, created_at__lt=now - timedelta(hours=24)))
                      .values_list("link__user_id", flat=True))
    topics = {}
    for author_id, topic in Feedback.objects.filter(kind=Feedback.Kind.FRICTION, author__in=users, created_at__gte=begin,
                                                    created_at__lt=finish).values_list("author_id", "topic"):
        topics.setdefault(author_id, Counter())[topic or "khac"] += 1
    out = []
    for user in users:
        rows = by_user[user.pk]
        scored = [s for _, s in rows if s is not None]
        average = (int((sum(Decimal(s.total) for s in scored) / len(scored)).quantize(Decimal(1), ROUND_HALF_UP))
                   if scored else None)
        misses = Counter()
        for s in scored:
            if s.coded < s.jobs:
                misses["mã chi tiết"] += 1
            if s.valued < s.jobs:
                misses["% hoặc khối lượng"] += 1
            if not s.photos:
                misses["ảnh"] += 1
            if s.staff < 20:
                misses["nhân sự"] += 1
        out.append({"user": user, "reports": rows, "count": len(rows), "scored": len(scored), "average": average,
                    "label": quality.label(average), "misses": misses, "asked": sum(r.asked for r, _ in rows),
                    "extra": sum(len(r.extra_work.all()) for r, _ in rows), "dropped": dropped.get(user.pk, 0),
                    "topics": topics.get(user.pk, Counter())})
    out.sort(key=lambda r: (r["average"] is None, r["average"] if r["average"] is not None else 0, str(r["user"])))
    return out


def advice(row):
    """"thiếu tên thợ 4/5, không ảnh 3/5" — các tiêu chí hay thiếu nhất trước."""
    words = {"mã chi tiết": "thiếu mã chi tiết", "% hoặc khối lượng": "thiếu %/khối lượng", "ảnh": "không ảnh",
             "nhân sự": "thiếu nhân sự (tên công nhật/tổ + số người)"}
    return ", ".join(f"{words[k]} {n}/{row['scored']}" for k, n in row["misses"].most_common())


def line(row):
    who = str(row["user"])
    if not row["count"]:
        return f"• {who}: ● không có báo cáo nào"
    if row["average"] is None:
        return f"• {who}: {row['count']} báo cáo, chỉ chấm công/ảnh (không có việc để chấm điểm)"
    parts = [f"{row['count']} báo cáo", f"TB {row['average']}/100 {row['label']}"]
    if row["misses"]:
        parts.append(advice(row))
    if row["asked"]:
        parts.append(f"bot hỏi lại {row['asked']} lần")
    if row["extra"]:
        parts.append(f"{row['extra']} việc ngoài bản vẽ")
    if row["dropped"]:
        parts.append(f"{row['dropped']} phiếu bỏ/hết hạn")
    return f"• {who}: " + " · ".join(parts)


def friction_topics(start, end):
    """Chỗ vướng bot tự ghi trong tuần theo loại: [(nhãn, số lần)] nhiều trước."""
    begin, finish = _span(start, end)
    counts = Counter(Feedback.objects.filter(kind=Feedback.Kind.FRICTION, created_at__gte=begin, created_at__lt=finish)
                     .values_list("topic", flat=True))
    labels = dict(Feedback.Topic.choices)
    return [(labels.get(topic, "Khác"), n) for topic, n in counts.most_common()]
