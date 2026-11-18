"""Đọc số liệu cho bot và AI — luôn chạy dưới quyền người hỏi (công trình được giao, quyền xem tiền theo vai)."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from asks import services as asks
from core.text import fold, vn_number, vnd
from drawings.models import Item
from field.models import Attendance, Worker
from field.workers import match
from overview.compute import answers, rows as progress_rows
from projects.models import projects_for


def projects(user):
    return list(projects_for(user).filter(active=True).order_by("code"))


def project(user, code):
    key = str(code or "").strip().casefold()
    found = next((p for p in projects(user) if p.code.casefold() == key), None)
    if found is None:
        raise PermissionDenied(f'Không có công trình "{code}" trong các công trình anh/chị được giao.')
    return found


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def overview(user, proj):
    data = answers(user, proj, timezone.localdate())
    return {"project": f"{proj.code} · {proj.name}", **_plain(data)}


def items_with_percent(proj):
    today = timezone.localdate()
    return [(r.item, r.percent_at(today)) for r in progress_rows(proj)]


def find_details(user, code, query):
    proj = project(user, code)
    words = fold(query).split()
    out = []
    for item, percent in items_with_percent(proj):
        hay = fold(f"{item.code} {item.work} {item.location} {item.floor}")
        if all(w in hay for w in words):
            out.append({"code": item.code, "work": item.work, "location": item.location, "floor": item.floor,
                        "unit": item.unit, "percent": float(percent)})
    return out[:40] or [{"code": i.code, "work": i.work, "floor": i.floor} for i, _ in items_with_percent(proj)[:40]]


def find_workers(user, code=None, name=None):
    proj = project(user, code) if code else None
    if name:
        result = match(name, proj)
        found = [result["worker"]] if result.get("worker") else result.get("candidates", [])
    else:
        ids = Attendance.objects.filter(project=proj, worker__isnull=False).values_list("worker_id", flat=True) if proj else []
        found = list(Worker.objects.filter(pk__in=ids, active=True)) if proj else list(Worker.objects.filter(active=True)[:60])
    return [{"name": w.name, "team": w.team, "kind": w.get_kind_display()} for w in found]


def attendance_summary(user, code=None, name=None, start=None, end=None):
    today = timezone.localdate()
    start = date.fromisoformat(start) if start else today.replace(day=1)
    end = date.fromisoformat(end) if end else today
    allowed = projects(user)
    rows = Attendance.objects.filter(project__in=allowed, work_date__range=(start, end)).select_related("worker", "staff", "project")
    if code:
        rows = rows.filter(project=project(user, code))
    if name:
        key = fold(name)
        rows = [r for r in rows if key in fold(str(r.worker or r.staff))]
    people = defaultdict(lambda: {"days": Decimal(0), "projects": set(), "cost": Decimal(0), "missing_rate": False})
    for r in rows:
        person = people[(r.worker.name if r.worker else str(r.staff), r.worker.team if r.worker else "Giám sát")]
        person["days"] += r.days
        person["projects"].add(r.project.code)
        if r.worker and r.worker.pay == Worker.Pay.DAILY:
            if r.worker.day_rate is None:
                person["missing_rate"] = True
            else:
                person["cost"] += r.days * r.worker.day_rate
    out = {"from": start.strftime("%d/%m/%Y"), "to": end.strftime("%d/%m/%Y"), "people": []}
    for (who, team), v in sorted(people.items()):
        row = {"name": who, "team": team, "days": float(v["days"]), "projects": sorted(v["projects"])}
        if user.sees_payables:
            row["labor_cost"] = float(v["cost"])
            row["missing_rate"] = v["missing_rate"]
        out["people"].append(row)
    return out


def execute(user, name, data):
    """Công cụ cho AI."""
    if name == "list_projects":
        return {"projects": [{"code": p.code, "name": p.name} for p in projects(user)],
                "sees_money": {"payables": user.sees_payables, "receivables": user.sees_receivables, "profit": user.sees_profit}}
    if name == "project_overview":
        return overview(user, project(user, data.get("project_code")))
    if name == "find_details":
        return find_details(user, data.get("project_code"), data.get("query", ""))
    if name == "find_workers":
        return find_workers(user, data.get("project_code"), data.get("name"))
    if name == "attendance_summary":
        return attendance_summary(user, data.get("project_code"), data.get("name"), data.get("start"), data.get("end"))
    if name == "pending_asks":
        return [{"ask_id": str(a.pk), "project": a.project.code, "note": a.note, "items": [i.code for i in a.items.all()],
                 "due": a.due.strftime("%d/%m/%Y") if a.due else ""} for a in asks.pending_for(user)]
    raise PermissionDenied(f"Công cụ {name} không có.")


def overview_text(user):
    lines = [f"TỔNG QUAN {timezone.localdate():%d/%m/%Y}"]
    for proj in projects(user)[:15]:
        data = answers(user, proj, timezone.localdate())
        p, lab = data["progress"], data["labor"]
        row = f"\n{proj.code} · {proj.name}\n- Tiến độ: "
        if p["actual"] is not None:
            row += f"{vn_number(p['actual'])}%"
            if p["planned"] is not None:
                row += f" / kế hoạch {vn_number(p['planned'])}%"
            if p["forecast"]:
                row += f", dự kiến xong {p['forecast']:%d/%m/%Y}"
        else:
            row += p["missing"] or "chưa có dữ liệu"
        if lab["latest_date"]:
            row += f"\n- Nhân công {lab['latest_date']:%d/%m}: {vn_number(lab['total'])} công"
        if "payables" in data:
            pay = data["payables"]
            row += f"\n- Nợ NCC {vnd(pay['supplier_debt'])}; tổ thợ {vnd(pay['team_debt'])}; công nhật {vnd(pay['labor_debt'])}"
        if "customer" in data and data["customer"]["remaining"] is not None:
            row += f"\n- Khách còn phải trả {vnd(data['customer']['remaining'])}"
        if "profit" in data:
            forecast = data["profit"]["forecast"]
            row += "\n- Lãi dự báo: " + (vnd(forecast) if forecast is not None else "chưa tính được (thiếu giá vốn)")
        lines.append(row)
    lines.append(f"\nXem chi tiết: {settings.APP_URL}/tong-quan/")
    return "\n".join(lines)


def month_text(user, month=None):
    today = timezone.localdate()
    first = (month or today).replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    data = attendance_summary(user, start=first.isoformat(), end=min(last, today).isoformat())
    if not data["people"]:
        return f"Chưa có chấm công tháng {first:%m/%Y} ở các công trình anh/chị được giao."
    out = [f"CÔNG THÁNG {first:%m/%Y} (đến {data['to']})"]
    for row in data["people"]:
        line = f"• {row['name']} ({row['team'] or 'chưa có tổ'}): {vn_number(row['days'])} công · {', '.join(row['projects'])}"
        if "labor_cost" in row:
            line += f" · {vnd(row['labor_cost'])}" + (" (thiếu đơn giá)" if row["missing_rate"] else "")
        out.append(line)
    out.append(f"\nBảng công: {settings.APP_URL}/cong/?thang={first:%Y-%m}")
    return "\n".join(out)
