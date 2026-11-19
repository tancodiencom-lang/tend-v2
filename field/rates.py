"""Đơn giá theo ngày hiệu lực. Đơn giá đầu tiên của một người áp dụng cho cả các công trước đó (chưa có giá nào thì
chưa tính được); các lần sau áp dụng từ ngày ghi, công cũ giữ giá cũ."""
from collections import defaultdict

from .models import Worker, WorkerRate


def table(worker_ids):
    out = defaultdict(list)
    for r in WorkerRate.objects.filter(worker_id__in=set(worker_ids)).order_by("start"):
        out[r.worker_id].append((r.start, r.pay, r.amount))
    return out


def at(rates, worker, day):
    """→ (hình thức trả, đơn giá hoặc None) áp dụng cho `worker` ngày `day`."""
    entries = rates.get(worker.pk) or []
    if not entries:
        return worker.pay, worker.day_rate
    chosen = entries[0]
    for entry in entries:
        if entry[0] <= day:
            chosen = entry
    return chosen[1], chosen[2]


def monthly_profiles():
    from django.db.models import Q
    return list(Worker.objects.filter(Q(pay=Worker.Pay.MONTHLY) | Q(rates__pay=Worker.Pay.MONTHLY)).distinct())
