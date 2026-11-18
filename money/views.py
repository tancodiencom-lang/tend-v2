"""Báo cáo tiền trên web: sổ thu chi, công nợ, xuất MISA, đối tượng, ảnh chứng từ. Nhập phiếu làm qua bot."""
import mimetypes
from collections import defaultdict
from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import FileResponse, Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from core.text import fold
from overview.compute import customer, payables
from projects.models import projects_for

from . import misa, services
from .models import ACCRUALS, RECEIPTS, Entry, Kind, Party

ZERO = Decimal(0)
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _money_user(request):
    if not (request.user.sees_payables or request.user.sees_receivables):
        raise PermissionDenied("Báo cáo tiền dành cho Giám đốc và kế toán.")


def _date(raw, default):
    try:
        return date.fromisoformat(raw) if raw else default
    except ValueError:
        return default


def _xlsx(name, title, head, rows, money_cols=()):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    sheet = book.active
    sheet.append([title])
    sheet.append(head)
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    for row in rows:
        sheet.append(row)
    for row in sheet.iter_rows(min_row=3):
        for index in money_cols:
            row[index].number_format = "#,##0"
    for i in range(len(head)):
        sheet.column_dimensions[chr(65 + i)].width = 18
    buffer = BytesIO()
    book.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type=XLSX)
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response


@login_required
@require_GET
def hub(request):
    """Mục Báo cáo cho mọi vai: tải Excel tiến độ/chấm công; vai xem tiền có thêm sổ thu chi, công nợ, MISA, lãi lỗ."""
    today = timezone.localdate()
    return render(request, "money/hub.html", {"can_misa": bool(services.visible_kinds(request.user)),
                                              "money": request.user.sees_payables or request.user.sees_receivables,
                                              "projects": projects_for(request.user).filter(active=True).order_by("code"),
                                              "month": today.strftime("%Y-%m")})


@login_required
@require_GET
def ledger(request):
    _money_user(request)
    user = request.user
    today = timezone.localdate()
    start, end = _date(request.GET.get("tu"), today.replace(day=1)), _date(request.GET.get("den"), today)
    projects = list(projects_for(user).order_by("code"))
    project = next((p for p in projects if p.code == request.GET.get("cong_trinh")), None)
    kinds = [(k, l) for k, l in Kind.choices if k in services.visible_kinds(user)]
    kind = request.GET.get("loai") if request.GET.get("loai") in dict(kinds) else ""
    rows = Entry.objects.filter(kind__in=services.visible_kinds(user), date__range=(start, end)).filter(
        Q(project__in=projects) | Q(project__isnull=True)).select_related("project", "party_ref", "entered_by", "voided_by")
    if project:
        rows = rows.filter(project=project)
    if kind:
        rows = rows.filter(kind=kind)
    rows = list(rows.order_by("date", "pk"))
    who = request.GET.get("doi_tuong", "").strip()
    if who:
        rows = [e for e in rows if fold(who) in fold(e.party)]
    live = [e for e in rows if not e.voided_at]
    totals = {"receipts": sum((e.amount for e in live if e.kind in RECEIPTS), ZERO),
              "payments": sum((e.amount for e in live if e.kind not in RECEIPTS | ACCRUALS), ZERO),
              "accruals": sum((e.amount for e in live if e.kind in ACCRUALS), ZERO)}
    if request.GET.get("tai") == "xlsx":
        head = ["Ngày", "Số phiếu", "Loại", "Đối tượng", "Công trình", "Nội dung", "Hình thức", "Thu (₫)", "Chi (₫)",
                "Phát sinh nợ (₫)", "Người nhập", "Huỷ"]
        data = [[e.date.strftime("%d/%m/%Y"), e.number, e.get_kind_display(), e.party, e.project.code if e.project_id else "Chung",
                 e.content, e.get_method_display(), float(e.amount) if e.kind in RECEIPTS else None,
                 float(e.amount) if e.kind not in RECEIPTS | ACCRUALS else None, float(e.amount) if e.kind in ACCRUALS else None,
                 str(e.entered_by), f"Đã huỷ: {e.void_reason}" if e.voided_at else ""] for e in rows]
        return _xlsx(f"so-thu-chi-{start:%Y%m%d}-{end:%Y%m%d}.xlsx", f"Sổ thu chi {start:%d/%m/%Y} – {end:%d/%m/%Y}", head, data, (7, 8, 9))
    return render(request, "money/ledger.html", {"rows": rows, "totals": totals, "start": start, "end": end, "projects": projects,
                                                 "project": project, "kinds": kinds, "kind": kind, "who": who,
                                                 "receipts": RECEIPTS, "accruals": ACCRUALS})


@login_required
@require_GET
def debts(request):
    _money_user(request)
    user = request.user
    blocks = []
    for project in projects_for(user).filter(active=True).order_by("code"):
        block = {"project": project}
        if user.sees_payables:
            block["pay"] = payables(project)
        if user.sees_receivables:
            block["customer"] = customer(project)
        blocks.append(block)
    if request.GET.get("tai") == "xlsx":
        rows = []
        for b in blocks:
            code = b["project"].code
            for s in (b.get("pay") or {}).get("suppliers", []):
                rows.append([code, "Nhà cung cấp", s["name"], float(s["goods"]), float(s["paid"]), float(s["debt"])])
            for t in (b.get("pay") or {}).get("teams", []):
                rows.append([code, "Tổ thợ", t["name"], float(t["work"]), float(t["paid"]), float(t["debt"])])
            if "pay" in b:
                p = b["pay"]
                rows.append([code, "Lương công nhật", "Thợ công nhật", float(p["labor_accrued"]), float(p["labor_paid"]), float(p["labor_debt"])])
            if b.get("customer") and b["customer"]["contract"] is not None:
                c = b["customer"]
                rows.append([code, "Khách hàng", b["project"].name, float(c["contract"]), float(c["received"]), float(c["remaining"])])
        return _xlsx(f"cong-no-{timezone.localdate():%Y%m%d}.xlsx", f"Công nợ ngày {timezone.localdate():%d/%m/%Y}",
                     ["Công trình", "Nhóm", "Đối tượng", "Phát sinh / hợp đồng (₫)", "Đã trả / đã thu (₫)", "Còn (₫)"], rows, (3, 4, 5))
    return render(request, "money/debts.html", {"blocks": blocks})


@login_required
@require_http_methods(["GET", "POST"])
def misa_export(request):
    _money_user(request)
    user = request.user
    projects = list(projects_for(user).order_by("code"))
    today = timezone.localdate()
    if request.method == "POST":
        kind = request.POST.get("loai")
        if kind not in misa.LABELS:
            messages.error(request, "Chọn loại tệp MISA.")
            return redirect("money_misa")
        project = next((p for p in projects if p.code == request.POST.get("cong_trinh")), None)
        start, end = _date(request.POST.get("tu"), today.replace(day=1)), _date(request.POST.get("den"), today)
        result = misa.export(user, kind, start, end, project, request.POST.get("ca_da_xuat") == "1")
        if result is None:
            messages.error(request, f"Không có phiếu nào cho {misa.LABELS[kind]} từ {start:%d/%m/%Y} đến {end:%d/%m/%Y}"
                                    " (phiếu đã xuất trước đó không xuất lại trừ khi chọn).")
            return redirect("money_misa")
        name, content, _, _ = result
        response = HttpResponse(content, content_type=XLSX)
        response["Content-Disposition"] = f'attachment; filename="{name}"'
        return response
    from .models import MisaExport
    return render(request, "money/misa.html", {"labels": misa.LABELS.items(), "projects": projects,
                                               "start": today.replace(day=1), "end": today,
                                               "history": MisaExport.objects.select_related("project", "created_by").order_by("-pk")[:15]})


def _party_totals(user):
    totals = defaultdict(lambda: {"in": ZERO, "out": ZERO})
    rows = Entry.objects.filter(voided_at__isnull=True, party_ref__isnull=False, kind__in=services.visible_kinds(user)).filter(
        Q(project__in=projects_for(user)) | Q(project__isnull=True))
    for e in rows:
        if e.kind in (Kind.SUPPLIER_GOODS, Kind.TEAM_WORK):
            totals[e.party_ref_id]["in"] += e.amount
        elif e.kind in (Kind.SUPPLIER_PAYMENT, Kind.TEAM_PAYMENT):
            totals[e.party_ref_id]["out"] += e.amount
        elif e.kind in RECEIPTS:
            totals[e.party_ref_id]["out"] += e.amount
    return totals


@login_required
@require_http_methods(["GET", "POST"])
def parties(request):
    _money_user(request)
    user = request.user
    can_edit = services.can_manage_parties(user)
    if request.method == "POST":
        if not can_edit:
            raise PermissionDenied
        fields = {k: request.POST.get(k, "").strip() for k in ("name", "misa_code", "tax_code", "address", "phone")}
        type_ = request.POST.get("type")
        try:
            if request.POST.get("id"):
                party = get_object_or_404(Party, pk=request.POST["id"])
                services.set_party(user, party, type=type_ if type_ in Party.Type.values else party.type,
                                   active=request.POST.get("active") == "1", **fields)
            else:
                name = fields.pop("name")
                services.add_party(user, name, type_, channel="web", **fields)
        except ValidationError as error:
            messages.error(request, "; ".join(error.messages))
        else:
            messages.success(request, f"Đã lưu {fields.get('name') or request.POST.get('name')}.")
        return redirect("money_parties")
    totals = _party_totals(user)
    query = request.GET.get("q", "").strip()
    rows = Party.objects.order_by("-active", "type", "name")
    if query:
        rows = [p for p in rows if fold(query) in fold(p.name)]
    data = [{"party": p, "in": totals[p.pk]["in"], "out": totals[p.pk]["out"], "debt": totals[p.pk]["in"] - totals[p.pk]["out"]}
            for p in rows]
    return render(request, "money/parties.html", {"rows": data, "can_edit": can_edit, "types": Party.Type.choices, "query": query})


@login_required
@require_GET
def voucher(request, pk):
    entry = get_object_or_404(Entry, pk=pk)
    allowed = entry.kind in services.visible_kinds(request.user) and (
        entry.project_id is None or projects_for(request.user).filter(pk=entry.project_id).exists())
    if not allowed or not entry.voucher:
        raise Http404
    try:
        return FileResponse(entry.voucher.open("rb"), content_type=mimetypes.guess_type(entry.voucher.name)[0] or "image/jpeg")
    except FileNotFoundError:
        raise Http404
