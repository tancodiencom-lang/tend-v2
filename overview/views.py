"""Tổng quan năm câu hỏi (toàn công ty và từng công trình). Chỉ đọc."""
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_GET

from projects.models import projects_for

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
@require_GET
def reports(request):
    return render(request, "overview/reports.html", {})


@login_required
@require_GET
def account(request):
    return render(request, "core/account.html", {})
