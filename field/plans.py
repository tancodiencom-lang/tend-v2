"""Kế hoạch ngày mai ghi trong báo cáo ("cv dự kiến 20/11 …") so với việc thực làm hôm đó — tính lúc đọc, không lưu kết quả.

Mỗi người lập kế hoạch: lấy kế hoạch trong báo cáo MỚI NHẤT của người đó cho ngày đó (gửi lại kế hoạch thì thay, không
cộng trùng). Việc dự kiến có mã: đã làm khi hôm đó có báo cáo chi tiết đó. Việc không mã: đã làm khi có việc (có mã hoặc
ngoài bản vẽ) cùng tầng và chứa gần đủ các chữ của việc dự kiến ("Đánh lạnh phòng ngoài tầng 5" ~ "Đánh lạnh tường phòng
ngoài tầng 5"). Đây là so gần đúng: kết quả ghi "chưa thấy trong báo cáo", không kết luận thay giám sát.
"""
import re
from datetime import timedelta

from django.conf import settings

from core.text import fold

from .models import ExtraWork, PlanLine, ReportLine

COVER = 0.75
NOISE = re.compile(r"^(?:tang|lau|t\d+|\d+|mai|tum|ham|tret|lung|o|tai|cac|va|cua|lam|tiep|xong)$")


def work_days():
    """Ngày làm việc (thứ Hai = 1 … Chủ nhật = 7), mặc định thứ Hai–thứ Bảy (TEND_WORK_DAYS)."""
    raw = str(getattr(settings, "TEND_WORK_DAYS", "1,2,3,4,5,6"))
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}


def next_workday(day):
    """Ngày làm việc kế tiếp (mặc định thứ Hai–thứ Bảy): báo cáo thứ Bảy ghi "dự kiến" không ngày → thứ Hai."""
    days = work_days() or {1, 2, 3, 4, 5, 6, 7}
    nxt = day + timedelta(days=1)
    while nxt.isoweekday() not in days:
        nxt += timedelta(days=1)
    return nxt


def words(text):
    return {w for w in fold(text).replace(",", " ").replace(".", " ").split() if not NOISE.match(w)}


def planned(project, day):
    """Các dòng kế hoạch cho ngày `day`: mỗi người lập lấy báo cáo mới nhất có kế hoạch cho ngày đó."""
    rows = list(PlanLine.objects.filter(report__project=project, for_date=day)
                .select_related("report__author", "item").order_by("report__created_at", "pk"))
    latest = {}
    for row in rows:
        latest[row.report.author_id] = row.report_id
    return [row for row in rows if latest[row.report.author_id] == row.report_id]


def done_work(project, day, pending=None):
    """Việc thực làm ngày `day`: (mã chi tiết đã báo, [(tầng, chữ)]). `pending`: dòng của phiếu đang chờ xác nhận
    ({lines: [{item_id, code, work…}], extra: [{floor, description}]}) để so ngay khi lập phiếu."""
    items, texts = set(), []
    for line in ReportLine.objects.filter(report__project=project, report__work_date=day).select_related("item"):
        items.add(line.item_id)
        texts.append((line.item.floor, f"{line.item.work} {line.item.location} {line.note}"))
    for extra in ExtraWork.objects.filter(report__project=project, report__work_date=day):
        texts.append((extra.floor, extra.description))
    for line in (pending or {}).get("lines") or []:
        items.add(line.get("item_id"))
        texts.append((line.get("floor", ""), f"{line.get('work', '')} {line.get('location', '')} {line.get('note', '')}"))
    for extra in (pending or {}).get("extra") or []:
        texts.append((extra.get("floor", ""), extra.get("description", "")))
    return items, texts


def _same_floor(a, b):
    return not a or not b or fold(a) == fold(b)


def matches(floor, description, texts):
    want = words(description)
    if not want:
        return False
    return any(_same_floor(floor, f) and len(want & words(t)) / len(want) >= COVER for f, t in texts)


def compare(project, day, pending=None):
    """→ [{"text", "floor", "code", "done"}] cho kế hoạch ngày `day` (rỗng nếu không có kế hoạch)."""
    plan = planned(project, day)
    if not plan:
        return []
    items, texts = done_work(project, day, pending)
    out = []
    for row in plan:
        done = (row.item_id in items) if row.item_id else False
        done = done or matches(row.floor, row.description, texts)
        out.append({"text": row.description, "floor": row.floor, "code": row.item.code if row.item_id else "", "done": done,
                    "author": str(row.report.author)})
    return out


def label(row):
    where = row["floor"] if row["floor"] and fold(row["floor"]) not in fold(row["text"]) else ""
    return (f"{where} · " if where else "") + row["text"]
