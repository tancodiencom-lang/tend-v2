"""Năm câu hỏi của Giám đốc, tính lúc đọc từ dữ liệu gốc; chỉ trả phần vai được xem.

1. Tiến độ hiện tại   — % theo giá trị (khối lượng thiết kế × đơn giá bán) từ báo cáo mới nhất mỗi chi tiết.
2. Tiến độ dự kiến    — kế hoạch đến hôm nay (ngày bắt đầu/xong từng chi tiết, thiếu thì rải khởi công → bàn giao),
                        ngày xong dự kiến theo tốc độ 14 ngày gần nhất, chi tiết quá hạn.
3. Công nợ NCC/tổ thợ — hàng nhận − đã trả theo NCC; khoán được tính − đã trả; công nhật ước tính − đã trả lương.
4. Công nợ khách      — giá trị hợp đồng − tiền khách đã trả.
5. Dự báo lãi lỗ      — doanh thu − chi phí đã phát sinh − giá vốn phần việc còn lại.
Thiếu dữ liệu thì ghi thiếu gì (missing), không trả 0 giả.
"""
from collections import defaultdict
from dataclasses import dataclass, field as dc_field
from datetime import date, timedelta
from decimal import ROUND_CEILING, Decimal

from django.db.models import Q, Sum

from drawings.models import Item
from field import rates
from field.models import Attendance, ReportLine, Worker
from money.models import Entry, Kind

ZERO, HUNDRED = Decimal(0), Decimal(100)
WINDOW = 14


@dataclass
class Row:
    item: Item
    value: Decimal
    history: list = dc_field(default_factory=list)   # [(ngày, %)] theo thứ tự gửi

    def percent_at(self, day):
        value = ZERO
        for when, pct in self.history:
            if when <= day:
                value = pct
        return value

    def plan(self, project):
        if self.item.plan_start and self.item.plan_end:
            return self.item.plan_start, self.item.plan_end
        if project.start_date and project.handover_date and project.handover_date >= project.start_date:
            return project.start_date, project.handover_date
        return None


def _line_percent(line):
    if line.percent is not None:
        return min(HUNDRED, max(ZERO, line.percent))
    if line.quantity is not None and line.item.design_quantity:
        return min(HUNDRED, max(ZERO, line.quantity / line.item.design_quantity * HUNDRED))
    return None


def rows(project):
    items = list(project.items.filter(active=True))
    by_id = {item.pk: Row(item=item, value=(item.design_quantity or ZERO) * (item.sale_price or ZERO)) for item in items}
    lines = (ReportLine.objects.filter(item__project=project, item__active=True)
             .select_related("item", "report").order_by("report__work_date", "report__created_at", "pk"))
    for line in lines:
        pct = _line_percent(line)
        if pct is not None:
            by_id[line.item_id].history.append((line.report.work_date, pct))
    return list(by_id.values())


def _plan_percent(plan, day):
    if not plan:
        return None
    start, end = plan
    if day < start:
        return ZERO
    if day >= end:
        return HUNDRED
    return Decimal((day - start).days + 1) / Decimal((end - start).days + 1) * HUNDRED


def progress(project, today):
    data = rows(project)
    out = {"item_count": len(data), "reported": sum(1 for r in data if r.history), "missing": "", "actual": None,
           "planned": None, "gap": None, "forecast": None, "target": project.handover_date, "late": None, "overdue": []}
    if not data:
        out["missing"] = "Chưa có chi tiết khối lượng (chưa nhập bản vẽ)"
        return out
    total = sum((r.value for r in data), ZERO)
    if not total:
        out["missing"] = "Các chi tiết chưa có đơn giá bán nên chưa quy đổi được % chung"
        return out

    def actual_at(day):
        return sum((r.value * r.percent_at(day) for r in data), ZERO) / total

    actual = actual_at(today)
    out["actual"] = actual.quantize(Decimal("0.1"))
    plans = [(r, r.plan(project)) for r in data]
    if any(plan for _, plan in plans):
        planned = sum((r.value * (_plan_percent(plan, today) or ZERO) for r, plan in plans), ZERO) / total
        out["planned"] = planned.quantize(Decimal("0.1"))
        out["gap"] = out["actual"] - out["planned"]
    first = project.start_date or min((when for r in data for when, _ in r.history), default=today)
    span = max(1, min(WINDOW, (today - first).days))
    velocity = (actual - actual_at(today - timedelta(days=span))) / span
    remain = HUNDRED - actual
    if remain <= Decimal("0.001"):
        out["forecast"] = today
    elif velocity > Decimal("0.005"):
        out["forecast"] = today + timedelta(days=int((remain / velocity).to_integral_value(rounding=ROUND_CEILING)))
    if out["forecast"] and project.handover_date:
        out["late"] = (out["forecast"] - project.handover_date).days
    overdue = sorted(((r, plan) for r, plan in plans if plan and r.item.plan_end and today > plan[1] and r.percent_at(today) < HUNDRED),
                     key=lambda pair: (pair[1][1], pair[0].item.code))
    out["overdue"] = [{"code": r.item.code, "work": r.item.work, "percent": r.percent_at(today).quantize(Decimal("0.1")),
                       "days": (today - plan[1]).days} for r, plan in overdue[:10]]
    out["rows"] = data
    return out


def labor(project, today, user):
    """Công ngày gần nhất, người-ngày 7 ngày; chi phí công nhật ước tính chỉ cho người xem được chi phí."""
    week = Attendance.objects.filter(project=project, work_date__range=(today - timedelta(days=6), today)).select_related("worker", "staff")
    days = defaultdict(list)
    for row in week:
        days[row.work_date].append(row)
    out = {"latest_date": None, "people": [], "total": ZERO, "week_total": sum((r.days for r in week), ZERO)}
    if days:
        latest = max(days)
        out["latest_date"] = latest
        out["people"] = [{"name": str(r.worker or r.staff), "team": (r.worker.team if r.worker else "Giám sát"), "days": r.days}
                         for r in sorted(days[latest], key=lambda r: (r.worker.team if r.worker else "", str(r.worker or r.staff)))]
        out["total"] = sum((r.days for r in days[latest]), ZERO)
    if user.sees_payables:
        cost, missing = labor_cost(project)
        out.update(cost=cost, missing_rates=missing)
    return out


def labor_cost(project, start=None, end=None):
    """Chi phí công nhật = số công × đơn giá ĐANG ÁP DỤNG ngày đó, chỉ người trả công nhật ngày đó (tổ khoán nằm trong
    tiền khoán; lương tháng tính ở salary_cost)."""
    rows_ = Attendance.objects.filter(project=project, worker__isnull=False).select_related("worker")
    if start:
        rows_ = rows_.filter(work_date__gte=start)
    if end:
        rows_ = rows_.filter(work_date__lte=end)
    rows_ = list(rows_)
    table = rates.table(r.worker_id for r in rows_)
    cost, missing = ZERO, set()
    for row in rows_:
        pay, amount = rates.at(table, row.worker, row.work_date)
        if pay != Worker.Pay.DAILY:
            continue
        if amount is None:
            missing.add(row.worker.name)
        else:
            cost += row.days * amount
    return cost, sorted(missing)


def salary_cost(project, start=None, end=None):
    """Lương tháng (giám sát…) chia vào công trình theo tỷ lệ ngày có mặt trong tháng: lương × công ở đây / tổng công
    tháng đó ở mọi công trình. Có mặt ghi theo hồ sơ nhân sự hoặc theo tài khoản giám sát gửi báo cáo."""
    cost, missing = ZERO, set()
    for person in rates.monthly_profiles():
        who = Q(worker=person) | (Q(staff_id=person.user_id) if person.user_id else Q(pk__in=[]))
        rows_ = list(Attendance.objects.filter(who).values_list("project_id", "work_date", "days"))
        total, here, last = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO), {}
        for project_id, day, days in rows_:
            month = (day.year, day.month)
            total[month] += days
            if project_id == project.pk and (not start or day >= start) and (not end or day <= end):
                here[month] += days
                last[month] = max(last.get(month, day), day)
        table = rates.table([person.pk])
        for month, days in here.items():
            pay, amount = rates.at(table, person, last[month])
            if pay != Worker.Pay.MONTHLY:
                continue
            if amount is None:
                missing.add(person.name)
            elif total[month]:
                cost += (amount * days / total[month]).quantize(Decimal(1))
    return cost, sorted(missing)


def _sum(project, kind):
    return live(project).filter(kind=kind).aggregate(s=Sum("amount"))["s"] or ZERO


def live(project):
    """Phiếu còn hiệu lực (phiếu đã huỷ không tính ở đâu cả)."""
    return Entry.objects.filter(project=project, voided_at__isnull=True)


def party_name(entry):
    return entry.party_ref.name if entry.party_ref_id else entry.party


def payables(project):
    suppliers = defaultdict(lambda: {"goods": ZERO, "paid": ZERO})
    for row in live(project).filter(kind__in=(Kind.SUPPLIER_GOODS, Kind.SUPPLIER_PAYMENT)).select_related("party_ref"):
        suppliers[party_name(row)]["goods" if row.kind == Kind.SUPPLIER_GOODS else "paid"] += row.amount
    teams = defaultdict(lambda: {"work": ZERO, "paid": ZERO})
    for row in live(project).filter(kind__in=(Kind.TEAM_WORK, Kind.TEAM_PAYMENT)).select_related("party_ref"):
        teams[party_name(row)]["work" if row.kind == Kind.TEAM_WORK else "paid"] += row.amount
    labor_accrued, missing = labor_cost(project)
    labor_paid = _sum(project, Kind.LABOR_PAYMENT)
    supplier_rows = [{"name": k, **v, "debt": v["goods"] - v["paid"]} for k, v in sorted(suppliers.items())]
    team_rows = [{"name": k, **v, "debt": v["work"] - v["paid"]} for k, v in sorted(teams.items())]
    return {"suppliers": supplier_rows, "supplier_debt": sum((r["debt"] for r in supplier_rows), ZERO),
            "teams": team_rows, "team_debt": sum((r["debt"] for r in team_rows), ZERO),
            "labor_accrued": labor_accrued, "labor_paid": labor_paid, "labor_debt": labor_accrued - labor_paid,
            "missing_rates": missing}


def contract_value(project, data=None):
    if project.contract_value is not None:
        return project.contract_value
    data = data if data is not None else rows(project)
    return sum((r.value for r in data), ZERO) or None


def customer(project, data=None):
    contract = contract_value(project, data)
    received = _sum(project, Kind.CUSTOMER_RECEIPT)
    return {"contract": contract, "received": received, "remaining": (contract - received) if contract is not None else None}


def profit(project, today, data=None):
    data = data if data is not None else rows(project)
    revenue = contract_value(project, data)
    labor_accrued, missing_rates = labor_cost(project)
    salary, missing_salary = salary_cost(project)
    missing_rates = sorted(set(missing_rates) | set(missing_salary))
    parts = {"goods": _sum(project, Kind.SUPPLIER_GOODS), "team": _sum(project, Kind.TEAM_WORK), "labor": labor_accrued,
             "salary": salary,
             "other": _sum(project, Kind.OTHER_EXPENSE)}
    incurred = sum(parts.values(), ZERO)
    earned = sum((r.value * r.percent_at(today) / HUNDRED for r in data), ZERO)
    missing_cost = [r.item.code for r in data if r.item.cost_price is None]
    remaining = sum(((r.item.cost_price or ZERO) * (r.item.design_quantity or ZERO) * (HUNDRED - r.percent_at(today)) / HUNDRED
                     for r in data), ZERO)
    out = {"revenue": revenue, "incurred": incurred, "parts": parts, "earned": earned, "remaining_cost": remaining,
           "missing_cost": len(missing_cost),
           "missing_rates": missing_rates, "forecast": None, "ratio": None}
    if revenue and not missing_cost and data:
        out["forecast"] = revenue - incurred - remaining
        out["ratio"] = (out["forecast"] / revenue * HUNDRED).quantize(Decimal("0.1"))
    return out


def answers(user, project, today=None):
    """Năm câu trả lời của một công trình theo quyền người xem."""
    today = today or date.today()
    p = progress(project, today)
    data = p.pop("rows", None)
    out = {"progress": p, "labor": labor(project, today, user)}
    if user.sees_payables:
        out["payables"] = payables(project)
    if user.sees_receivables:
        out["customer"] = customer(project, data)
    if user.sees_profit:
        out["profit"] = profit(project, today, data)
    return out
