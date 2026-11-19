"""Tổng quan năm câu hỏi (toàn công ty và từng công trình). Chỉ đọc."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from projects.models import projects_for

from . import exports
from .compute import answers

ZERO = Decimal(0)


@login_required
@require_GET
def overview(request):
    today = timezone.localdate()
    rows = [{"project": p, "data": answers(request.user, p, today)} for p in projects_for(request.user).filter(active=True)]

    def total(section, key):
        values = [r["data"][section][key] for r in rows if section in r["data"] and r["data"][section][key] is not None]
        return sum(values, ZERO) if values else None

    totals = {"supplier": total("payables", "supplier_debt"), "team": total("payables", "team_debt"),
              "labor": total("payables", "labor_debt"), "customer": total("customer", "remaining"),
              "profit": total("profit", "forecast")}
    return render(request, "overview/index.html", {"today": today, "rows": rows, "totals": totals})


@login_required
@require_GET
def overview_project(request, code):
    project = get_object_or_404(projects_for(request.user), code=code)
    today = timezone.localdate()
    return render(request, "overview/project.html", {"project": project, "today": today,
                                                      "data": answers(request.user, project, today)})


@login_required
@require_http_methods(["GET", "POST"])
def account(request):
    """Tài khoản: tên, vai, ảnh nhân viên (đổi/bỏ ảnh có nhật ký), kết nối Telegram, đăng xuất."""
    error = ""
    if request.method == "POST":
        from core.models import User
        user = User.objects.get(pk=request.user.pk)
        from django.core.exceptions import ValidationError

        from core.audit import record_save
        from core.avatar import normalize
        try:
            if request.POST.get("bo_anh"):
                user.avatar = ""
                record_save(user, user, "Bỏ ảnh nhân viên")
            else:
                upload = request.FILES.get("anh")
                if upload is None:
                    raise ValidationError("Chọn ảnh trước khi bấm Lưu ảnh.")
                user.avatar.save("anh.jpg", normalize(upload.read()), save=False)
                record_save(user, user, "Đổi ảnh nhân viên")
            return redirect("account")
        except ValidationError as exc:
            error = "; ".join(exc.messages)
    return render(request, "core/account.html", {"error": error})


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _download(content, name):
    response = HttpResponse(content, content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@login_required
@require_GET
def export_overview(request):
    today = timezone.localdate()
    projects = list(projects_for(request.user).filter(active=True).order_by("code"))
    return _download(exports.overview_book(request.user, projects, today), f"tong-quan-{today:%Y%m%d}.xlsx")


@login_required
@require_GET
def export_month(request):
    project = get_object_or_404(projects_for(request.user), code=request.GET.get("cong_trinh", ""))
    today = timezone.localdate()
    try:
        year, month = (int(x) for x in request.GET.get("thang", "").split("-"))
        first = date(year, month, 1)
    except ValueError:
        first = today.replace(day=1)
    if first > today:
        raise Http404("Tháng chưa tới.")
    return _download(exports.month_book(request.user, project, first, today), f"bao-cao-thang-{project.code}-{first:%Y-%m}.xlsx")


@login_required
@require_GET
def export_profit(request):
    if not request.user.sees_profit:
        raise PermissionDenied("Báo cáo lãi lỗ dành cho Giám đốc và kế toán tài chính.")
    today = timezone.localdate()
    projects = list(projects_for(request.user).filter(active=True).order_by("code"))
    return _download(exports.profit_book(request.user, projects, today), f"lai-lo-{today:%Y%m%d}.xlsx")
