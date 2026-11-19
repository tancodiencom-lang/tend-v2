"""Web của hiện trường: bảng công tháng (xem, tải Excel), danh sách thợ (Giám đốc/kế toán nội bộ sửa đơn giá), ảnh."""
import calendar
import mimetypes
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from core.models import Role
from core.text import number
from projects.models import projects_for

from . import services
from .models import Attendance, Photo, Trade, Worker

ZERO = Decimal(0)


def _month(request):
    raw = request.GET.get("thang", "")
    try:
        year, month = (int(x) for x in raw.split("-"))
        return date(year, month, 1)
    except ValueError:
        return timezone.localdate().replace(day=1)


def timesheet(user, first, project=None):
    """Bảng công một tháng theo quyền: hàng người, cột ngày. Tiền chỉ khi được xem chi phí."""
    days = calendar.monthrange(first.year, first.month)[1]
    last = first.replace(day=days)
    allowed = projects_for(user)
    rows = Attendance.objects.filter(project__in=allowed, work_date__range=(first, last)).select_related("worker", "staff", "project")
    if project is not None:
        rows = rows.filter(project=project)
    people = {}
    for r in rows:
        key = ("w", r.worker_id) if r.worker_id else ("s", r.staff_id)
        person = people.setdefault(key, {"name": r.worker.name if r.worker else str(r.staff), "worker": r.worker,
                                         "team": r.worker.team if r.worker else "Giám sát", "cells": defaultdict(lambda: ZERO),
                                         "projects": set(), "dates": set(), "presence": 0})
        person["cells"][r.work_date.day] += r.days
        person["projects"].add(r.project.code)
        person["dates"].add(r.work_date)
        person["presence"] += 1
    out = []
    for key, p in people.items():
        total = sum(p["cells"].values(), ZERO)
        row = {"name": p["name"], "team": p["team"], "staff": key[0] == "s", "projects": ", ".join(sorted(p["projects"])),
               "cells": [p["cells"].get(d) for d in range(1, days + 1)], "total": total, "workdays": len(p["dates"]),
               "presence": p["presence"]}
        worker = p["worker"]
        if worker is not None:
            row["kind"] = worker.get_kind_display()
            row["pay"] = worker.get_pay_display()
            if user.sees_payables:
                row["rate"] = worker.day_rate
                if worker.pay == Worker.Pay.DAILY and worker.day_rate is not None:
                    row["cost"] = total * worker.day_rate
        out.append(row)
    out.sort(key=lambda r: (not r["staff"], r["team"], r["name"]))
    cost = sum((r.get("cost", ZERO) for r in out), ZERO)
    missing = [r["name"] for r in out if not r["staff"] and r.get("pay") == "Công nhật" and user.sees_payables and r.get("rate") is None]
    return {"days": list(range(1, days + 1)), "rows": out, "cost": cost, "missing": missing,
            "weekend": {d for d in range(1, days + 1) if first.replace(day=d).weekday() == 6}}


@login_required
@require_GET
def attendance(request):
    first = _month(request)
    projects = list(projects_for(request.user).order_by("code"))
    project = next((p for p in projects if p.code == request.GET.get("cong_trinh")), None)
    data = timesheet(request.user, first, project)
    if request.GET.get("tai") == "xlsx":
        return _xlsx(request, first, project, data)
    prev = (first.replace(day=1) - date.resolution).replace(day=1)
    nxt = (first.replace(day=28) + 4 * date.resolution).replace(day=1)
    return render(request, "field/attendance.html", {"first": first, "projects": projects, "project": project, "data": data,
                                                     "prev": prev, "next": nxt, "money": request.user.sees_payables})


def _xlsx(request, first, project, data):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    sheet = book.active
    sheet.title = f"Công {first:%m-%Y}"
    sheet.append([f"Bảng công tháng {first:%m/%Y}" + (f" · {project.code}" if project else " · các công trình được giao")])
    head = ["Tên", "Tổ", "Loại", "Hình thức", "Công trình"] + [str(d) for d in data["days"]] + ["Tổng công", "Ngày làm việc"]
    money = request.user.sees_payables
    if money:
        head += ["Đơn giá (₫)", "Thành tiền công nhật (₫)"]
    sheet.append(head)
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    for row in data["rows"]:
        values = [row["name"], row["team"], row.get("kind", "Giám sát"), row.get("pay", ""), row["projects"]]
        values += [float(c) if c else None for c in row["cells"]] + [float(row["total"]), row["workdays"]]
        if money:
            values += [float(row["rate"]) if row.get("rate") is not None else None, float(row["cost"]) if "cost" in row else None]
        sheet.append(values)
    sheet.column_dimensions["A"].width = 24
    buffer = BytesIO()
    book.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="bang-cong-{first:%Y-%m}.xlsx"'
    return response


@login_required
@require_http_methods(["GET", "POST"])
def workers(request):
    """Nhân sự được chấm công: mã NS cố định, họ tên, nghề, bậc, tổ, hình thức trả, đơn giá theo ngày hiệu lực."""
    user = request.user
    if not (user.sees_payables or user.role == Role.SUPERVISOR):
        raise PermissionDenied("Trang này dành cho Giám đốc, kế toán và giám sát.")
    can_edit = services.can_set_rates(user)
    action = request.POST.get("action", "save") if request.method == "POST" else ""
    if action == "add":
        return _add_worker(request, can_edit)
    if action == "trade":
        return _add_trade(request)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ sửa nhân sự, đơn giá.")
        worker = get_object_or_404(Worker, pk=request.POST.get("id"))
        try:
            with transaction.atomic():
                if request.POST.get("name") and request.POST["name"].strip() != worker.name:
                    services.rename_worker(user, worker, request.POST["name"])
                raw_rate = request.POST.get("day_rate", "").strip()
                rate = number(raw_rate) if raw_rate else None
                if raw_rate and rate is None:
                    raise ValidationError("Đơn giá không hợp lệ.")
                start = request.POST.get("rate_start") or None
                pay = request.POST.get("pay") if request.POST.get("pay") in Worker.Pay.values else None
                rate_changed = (rate != worker.day_rate) or (pay and pay != worker.pay)
                services.set_worker(user, worker, day_rate=rate if rate_changed and rate is not None else None,
                                    clear_rate=rate_changed and rate is None and worker.day_rate is not None,
                                    pay=pay if rate_changed else None,
                                    rate_start=date.fromisoformat(start) if start else None,
                                    kind=request.POST.get("kind") if request.POST.get("kind") in Worker.Kind.values else None,
                                    trade=Trade.objects.filter(pk=request.POST.get("trade") or 0).first(),
                                    team=request.POST.get("team", worker.team).strip()[:100], active=request.POST.get("active") == "1",
                                    channel="web")
        except (ValidationError, ValueError) as error:
            messages.error(request, f"{worker.code} {worker.name}: " + "; ".join(getattr(error, "messages", [str(error)])))
        else:
            messages.success(request, f"Đã lưu {worker.code} {worker.name}.")
        return redirect("field_workers")
    first = timezone.localdate().replace(day=1)
    month_days = defaultdict(lambda: ZERO)
    for worker_id, days in Attendance.objects.filter(worker__isnull=False, work_date__gte=first,
                                                     project__in=projects_for(user)).values_list("worker_id", "days"):
        month_days[worker_id] += days
    people = Worker.objects.select_related("trade", "user").prefetch_related("rates").order_by("-active", "code")
    rows = [{"worker": w, "month": month_days.get(w.pk, ZERO), "rates": list(w.rates.all())[::-1] if user.sees_payables else []}
            for w in people]
    return render(request, "field/workers.html", {"rows": rows, "can_edit": can_edit, "money": user.sees_payables, "first": first,
                                                  "pays": Worker.Pay.choices, "kinds": Worker.Kind.choices,
                                                  "trades": Trade.objects.filter(active=True), "today": timezone.localdate(),
                                                  "can_add": services.can_add_workers(user)})


def _add_trade(request):
    from core.audit import record_save
    if not request.user.is_director:
        raise PermissionDenied("Chỉ Giám đốc sửa danh mục nghề.")
    name = " ".join(request.POST.get("trade_name", "").split())[:60]
    if not name or Trade.objects.filter(name__iexact=name).exists():
        messages.error(request, "Nghề trống hoặc đã có.")
    else:
        record_save(Trade(name=name, order=Trade.objects.count()), request.user, "Thêm nghề", channel="web")
        messages.success(request, f"Đã thêm nghề {name}.")
    return redirect("field_workers")


def _add_worker(request, can_edit):
    """Thêm nhân sự trên web (mã NS tự cấp) để bot khớp tên/mã khi chấm công; trùng tên bỏ dấu thì báo để ghi rõ hơn."""
    from core.text import fold
    user = request.user
    name = " ".join(request.POST.get("name", "").split())
    if name and any(fold(w.name) == fold(name) for w in Worker.objects.filter(active=True)):
        messages.error(request, f"Đã có người tên \"{name}\". Nếu là người khác, ghi thêm họ hoặc tên lót (vd Nguyễn Văn {name}).")
        return redirect("field_workers")
    kind = request.POST.get("kind") if request.POST.get("kind") in Worker.Kind.values else Worker.Kind.SKILLED
    trade = Trade.objects.filter(pk=request.POST.get("trade") or 0).first()
    try:
        with transaction.atomic():
            worker = services.add_worker(user, name, request.POST.get("team", ""), kind, channel="web", trade=trade)
            raw_rate = request.POST.get("day_rate", "").strip()
            pay = request.POST.get("pay")
            if can_edit and (raw_rate or pay in Worker.Pay.values):
                rate = number(raw_rate) if raw_rate else None
                if raw_rate and rate is None:
                    raise ValidationError("Đơn giá không hợp lệ.")
                services.set_rate(user, worker, rate, pay=pay if pay in Worker.Pay.values else None, channel="web")
    except (ValidationError, PermissionDenied) as error:
        messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
    else:
        messages.success(request, f"Đã thêm {worker.code} {worker.name}. Chấm công qua bot ghi tên hoặc mã {worker.code} là khớp.")
    return redirect("field_workers")


@login_required
@require_GET
def photo(request, pk):
    item = get_object_or_404(Photo.objects.select_related("report"), pk=pk)
    if not projects_for(request.user).filter(pk=item.report.project_id).exists():
        raise Http404
    try:
        return FileResponse(item.image.open("rb"), content_type=mimetypes.guess_type(item.image.name)[0] or "image/jpeg")
    except FileNotFoundError:
        raise Http404
