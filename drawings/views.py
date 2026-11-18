"""Bản vẽ chỉ xem, bảng khối lượng, nhập DXF (máy tính, Giám đốc/thiết kế) và tải Bộ Tend cho AutoCAD."""
import json
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods

from asks.models import Ask
from asks.services import status as ask_status
from core.text import vn_number
from field.models import ReportLine
from overview.compute import rows as progress_rows
from projects.models import projects_for

from . import autocad, dxf_read, importer
from . import standard as S
from .models import Drawing

MAX_UPLOAD = 80 * 1024 * 1024
HUNDRED = Decimal(100)


def _project(request, code):
    return get_object_or_404(projects_for(request.user), code=code)


def _editor(request):
    if not request.user.edits_drawings:
        raise PermissionDenied("Chỉ Giám đốc và thiết kế nhập bản vẽ.")


def _svg(geometry, dot):
    """Hình chi tiết → (đường SVG, điểm đặt mã, kiểu). Toạ độ đã là toạ độ khung (mm)."""
    kind = geometry.get("type")
    if kind in ("polygon", "outline"):
        rings = geometry.get("rings") or []
        d = " ".join("M" + " L".join(f"{x:g} {y:g}" for x, y in ring) + " Z" for ring in rings if ring)
        ring = rings[0] if rings else [[0, 0]]
        return d, (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring)), kind
    if kind == "lines":
        lines = geometry.get("lines") or []
        d = " ".join("M" + " L".join(f"{x:g} {y:g}" for x, y in line) for line in lines if line)
        first = lines[0][0] if lines and lines[0] else [0, 0]
        return d, tuple(first), kind
    points = geometry.get("points") or []
    d = " ".join(f"M{x - dot:g} {y:g} a{dot:g} {dot:g} 0 1 0 {2 * dot:g} 0 a{dot:g} {dot:g} 0 1 0 {-2 * dot:g} 0" for x, y in points)
    first = points[0] if points else [0, 0]
    return d, tuple(first), "points"


# Lớp xem trên bản vẽ: (khoá, tên, các layer). Thứ tự vẽ: lớp sau nằm trên, chạm được trước.
GROUPS = [("tran", "Trần", ("KL-TRAN",)), ("bt", "Bê tông", ("KL-BT",)), ("op", "Ốp", ("KL-OP", "KL-GHI")),
          ("san", "Sàn", ("KL-SAN",)), ("tuong", "Tường", ("KL-XAY-110", "KL-XAY-220", "KL-XAY-50")), ("cua", "Cửa", ("KL-CUA",)),
          ("dn", "Điện nước", tuple(l.name for l in S.LAYERS if l.kind in (S.DEVICE, S.RUN))), ("khac", "Khác", ())]
GROUP_OF = {layer: key for key, _, layers in GROUPS for layer in layers}
LABELLED = {"san", "tuong", "bt"}   # lớp ghi mã trên bản vẽ (trần trùng phòng, cửa nhỏ: xem trong danh sách)
GROUP_ORDER = {key: i for i, (key, _, _) in enumerate(GROUPS)}


def _group(item):
    parts = (item.source_key or "").split("|")
    return GROUP_OF.get(parts[1] if len(parts) > 1 else "", "khac")


def _status(percent):
    if percent >= HUNDRED:
        return "done", "✓ Xong"
    if percent > 0:
        return "doing", "◐ Đang làm"
    return "todo", "○ Chưa làm"


@login_required
@require_GET
def index(request):
    projects = projects_for(request.user).filter(active=True)
    floors = {}
    for drawing in Drawing.objects.filter(project__in=projects).order_by("order"):
        floors.setdefault(drawing.project_id, []).append(drawing.floor)
    return render(request, "drawings/index.html", {"rows": [{"project": p, "floors": floors.get(p.pk, [])} for p in projects]})


@login_required
@require_GET
def view(request, code):
    project = _project(request, code)
    drawings = list(project.drawings.order_by("order", "floor"))
    if not drawings:
        return render(request, "drawings/view.html", {"project": project, "drawings": [], "drawing": None})
    drawing = next((d for d in drawings if d.floor == request.GET.get("tang")), drawings[0])
    today = timezone.localdate()
    data = sorted((r for r in progress_rows(project) if r.item.floor == drawing.floor and r.item.geometry),
                  key=lambda r: (GROUP_ORDER[_group(r.item)], r.item.code))
    size = max(drawing.view_box[2], drawing.view_box[3]) if len(drawing.view_box) == 4 else 10000
    shapes, info = [], []
    history = _history([r.item for r in data])
    open_asks = _open_asks(project)
    for index, row in enumerate(data):
        item = row.item
        percent = row.percent_at(today)
        css, label = _status(percent)
        info.append({"id": item.pk, "code": item.code, "work": item.work, "location": item.location, "unit": item.unit,
                     "quantity": vn_number(item.design_quantity), "note": item.note,
                     "percent": f"{percent:.0f}", "status": label, "history": history.get(item.pk, [])[-6:],
                     "asks": open_asks.get(item.pk, [])})
        d, (lx, ly), kind = _svg(item.geometry, size / 120)
        shapes.append({"i": index, "css": css, "kind": kind, "d": d, "code": item.code, "lx": lx, "ly": ly,
                       "mark": label[0], "label": kind == "polygon" and _group(item) in LABELLED, "group": _group(item)})
    return render(request, "drawings/view.html", {
        "project": project, "drawings": drawings, "drawing": drawing, "shapes": shapes, "info": info,
        "box": " ".join(f"{v:g}" for v in drawing.view_box), "label_size": f"{size / 32:g}",
        "ratio": f"{drawing.view_box[2]:g} / {drawing.view_box[3]:g}" if len(drawing.view_box) == 4 else "4 / 3",
        "groups": [(key, title) for key, title, _ in GROUPS if any(s["group"] == key for s in shapes)],
        "can_import": request.user.edits_drawings, "asks": _asks_for(request.user, project),
        "can_ask": request.user.is_director,
        "assignees": [m.user for m in project.members.filter(active=True, user__is_active=True).select_related("user")]})


def _history(items):
    """Các lần báo của từng chi tiết: ngày, người, giá trị, lý do giảm, ảnh (đường xem có kiểm quyền)."""
    out = {}
    lines = (ReportLine.objects.filter(item__in=items).select_related("report__author", "item")
             .prefetch_related("photos").order_by("report__work_date", "report__created_at", "pk"))
    for line in lines:
        value = (f"{vn_number(line.percent)}%" if line.percent is not None else f"{vn_number(line.quantity)} {line.item.unit}")
        out.setdefault(line.item_id, []).append({
            "date": line.report.work_date.strftime("%d/%m/%Y"), "percent": value, "author": str(line.report.author),
            "reason": line.decrease_reason, "note": line.note,
            "photos": [reverse("field_photo", args=[p.pk]) for p in line.photos.all()]})
    return out


def _open_asks(project):
    out = {}
    for ask in Ask.objects.filter(project=project, cancelled_at__isnull=True).prefetch_related("items").select_related("assignee"):
        state, label = ask_status(ask)
        if state in ("open", "late"):
            for item in ask.items.all():
                out.setdefault(item.pk, []).append(f"{label} · {ask.assignee}: {ask.note}")
    return out


def _asks_for(user, project):
    rows = Ask.objects.filter(project=project).select_related("assignee", "requester").prefetch_related("items")
    if not user.is_director:
        rows = rows.filter(assignee=user)
    return [{"ask": a, "status": ask_status(a)} for a in rows[:20]]


def _quantity_rows(request, project):
    today = timezone.localdate()
    out = []
    for row in sorted(progress_rows(project), key=lambda r: (r.item.floor, r.item.code)):
        item = row.item
        percent = row.percent_at(today)
        line = {"item": item, "percent": percent, "done": (item.design_quantity * percent / HUNDRED).quantize(Decimal("0.01"))}
        if request.user.sees_receivables and item.sale_price is not None:
            line["sale_total"] = item.sale_price * item.design_quantity
        if request.user.sees_payables and item.cost_price is not None:
            line["cost_total"] = item.cost_price * item.design_quantity
        out.append(line)
    return out


@login_required
@require_GET
def quantities(request, code):
    project = _project(request, code)
    lines = _quantity_rows(request, project)
    if request.GET.get("tai") == "xlsx":
        return _xlsx(request, project, lines)
    cols = 7 + 2 * request.user.sees_receivables + 2 * request.user.sees_payables
    return render(request, "drawings/quantities.html", {"project": project, "lines": lines, "cols": cols,
                                                        "show_sale": request.user.sees_receivables,
                                                        "show_cost": request.user.sees_payables})


def _xlsx(request, project, lines):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    book = Workbook()
    sheet = book.active
    sheet.title = "Khối lượng"
    head = ["Tầng", "Mã", "Công việc", "Vị trí", "Đơn vị", "KL thiết kế", "% đã báo", "KL đã làm", "Cách tính"]
    if request.user.sees_receivables:
        head += ["Đơn giá bán (₫)", "Thành tiền bán (₫)"]
    if request.user.sees_payables:
        head += ["Đơn giá vốn (₫)", "Thành tiền vốn (₫)"]
    sheet.append([f"{project.code} · {project.name} — bảng khối lượng ngày {timezone.localdate():%d/%m/%Y}"])
    sheet.append(head)
    for cell in sheet[2]:
        cell.font = Font(bold=True)
    for line in lines:
        item = line["item"]
        values = [item.floor, item.code, item.work, item.location, item.unit, float(item.design_quantity),
                  float(line["percent"]) / 100, float(line["done"]), item.note]
        if request.user.sees_receivables:
            values += [float(item.sale_price) if item.sale_price is not None else None,
                       float(line["sale_total"]) if "sale_total" in line else None]
        if request.user.sees_payables:
            values += [float(item.cost_price) if item.cost_price is not None else None,
                       float(line["cost_total"]) if "cost_total" in line else None]
        sheet.append(values)
    for row in sheet.iter_rows(min_row=3):
        row[6].number_format = "0%"
        for cell in row[9:]:
            cell.number_format = "#,##0"
    widths = [10, 9, 34, 28, 8, 12, 10, 12, 36, 16, 18, 16, 18]
    for i, width in enumerate(widths[:len(head)]):
        sheet.column_dimensions[chr(65 + i)].width = width
    buffer = BytesIO()
    book.save(buffer)
    response = HttpResponse(buffer.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = f'attachment; filename="khoi-luong-{project.code}.xlsx"'
    return response


# --- Nhập DXF ----------------------------------------------------------------------------------------------------

def _temp_dir():
    path = Path(settings.MEDIA_ROOT) / "tam"
    path.mkdir(parents=True, exist_ok=True)
    return path


@login_required
@require_http_methods(["GET", "POST"])
def upload(request, code):
    project = _project(request, code)
    _editor(request)
    if request.method == "POST":
        file = request.FILES.get("tep")
        if not file or Path(file.name).suffix.lower() not in (".dxf", ".zip"):
            messages.error(request, "Chọn tệp .dxf (hoặc .zip chứa một tệp .dxf) xuất từ AutoCAD.")
        elif file.size > MAX_UPLOAD:
            messages.error(request, "Tệp lớn quá 80 MB. Nén .zip rồi tải lên lại.")
        else:
            token = uuid.uuid4().hex
            source = _temp_dir() / f"ban-ve-{token}{Path(file.name).suffix.lower()}"
            with open(source, "wb") as out:
                for chunk in file.chunks():
                    out.write(chunk)
            try:
                parsed = dxf_read.read(source)
            except dxf_read.DxfError as error:
                messages.error(request, str(error))
                parsed = None
            finally:
                source.unlink(missing_ok=True)
                source.with_suffix(".dxf").unlink(missing_ok=True)
            if parsed is not None:
                parsed.update(source_name=file.name, project=project.pk, user=request.user.pk)
                (_temp_dir() / f"ban-ve-{token}.json").write_text(json.dumps(parsed, ensure_ascii=False))
                return redirect("drawings_preview", code=project.code, token=token)
    return render(request, "drawings/upload.html", {"project": project})


def _load(request, project, token):
    if not token.isalnum():
        raise Http404
    path = _temp_dir() / f"ban-ve-{token}.json"
    if not path.exists():
        raise Http404("Bản xem trước đã hết hạn, tải tệp lên lại.")
    parsed = json.loads(path.read_text())
    if parsed.get("project") != project.pk or parsed.get("user") != request.user.pk:
        raise Http404
    return parsed, path


def _heights(request, project, parsed):
    previous = dict(project.drawings.values_list("floor", "wall_height"))
    out = {}
    for floor in parsed["floors"]:
        raw = (request.POST if request.method == "POST" else request.GET).get(f"cao_{floor['token']}", "")
        raw = raw.replace(".", "").replace(",", "").strip()
        if raw.isdigit() and 1000 <= int(raw) <= 9000:
            out[floor["token"]] = int(raw)
        else:
            out[floor["token"]] = previous.get(floor["name"]) or S.DEFAULT_WALL_HEIGHT
    return out


@login_required
@require_http_methods(["GET", "POST"])
def preview(request, code, token):
    project = _project(request, code)
    _editor(request)
    parsed, path = _load(request, project, token)
    heights = _heights(request, project, parsed)
    if request.method == "POST":
        try:
            importer.apply(project, parsed, heights, request.user)
        except ValueError as error:
            messages.error(request, str(error))
        else:
            path.unlink(missing_ok=True)
            messages.success(request, f"Đã nhập bản vẽ {parsed['source_name']}.")
            return redirect(reverse("drawings_view", args=[project.code]))
    result = importer.plan(project, parsed, heights)
    floors = []
    for floor in parsed["floors"]:
        rows = [r for r in result["rows"] if r.floor == floor["name"]]
        floors.append({"floor": floor, "height": heights[floor["token"]], "rows": rows,
                       "has_walls": any(e["kind"] == "wall" and not e["height"] for e in floor["elements"]),
                       "counts": {s: sum(1 for r in rows if r.status == s) for s in ("new", "update", "same")}})
    return render(request, "drawings/preview.html", {"project": project, "parsed": parsed, "floors": floors, "result": result})


# --- Bộ Tend cho AutoCAD -----------------------------------------------------------------------------------------

@login_required
@require_GET
def kit(request):
    _editor(request)
    groups = [(group, [label for _, label, _ in buttons]) for group, buttons in S.BUTTONS]
    return render(request, "drawings/kit.html", {"layers": S.LAYERS, "blocks": S.BLOCKS, "groups": groups,
                                                 "version": autocad.VERSION, "reasons": S.REASONS.values()})


@login_required
@require_GET
def kit_download(request):
    _editor(request)
    response = HttpResponse(autocad.kit_zip(), content_type="application/zip")
    response["Content-Disposition"] = f'attachment; filename="Bo-Tend-AutoCAD-{autocad.VERSION}.zip"'
    return response
