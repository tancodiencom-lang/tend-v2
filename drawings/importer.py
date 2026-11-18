"""Từ kết quả đọc DXF đến chi tiết khối lượng: tính tường theo chiều cao, so với bản đang có, ghi khi xác nhận.

Nhập lại bản vẽ mới không làm mất số liệu thi công:
- chi tiết mới trùng hình chi tiết cũ (cùng tầng, cùng loại, chồng hình ≥ 50%; thiết bị/dây theo loại) giữ **mã cũ**,
  chỉ cập nhật hình và khối lượng;
- chi tiết cũ không còn trên bản vẽ: chưa có báo cáo thì thôi dùng (không xoá), đã có báo cáo thì giữ và báo để quyết định;
- chỉ đụng các tầng có trong tệp; tầng khác giữ nguyên.
Mọi thay đổi ghi nhật ký trong cùng giao dịch.
"""
import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from shapely.geometry import Polygon

from core.audit import record_save
from field.models import ReportLine
from projects.models import Project

from .dxf_read import vn
from .models import Drawing, Item

MATCH = 0.5


@dataclass
class Row:
    floor: str
    token: str
    kind: str
    key: str
    prefix: str
    work: str
    location: str
    unit: str
    quantity: Decimal
    note: str
    geometry: dict
    code: str = ""
    status: str = "new"        # new | update | same
    item: Item | None = None
    changes: list = field(default_factory=list)


def _dec(value, places=3):
    return Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)


def rows(parsed, heights):
    """Các dòng chi tiết của tệp; `heights` {token tầng: chiều cao tường mm}. Thiếu chiều cao thì báo trong `missing`."""
    out, missing = [], []
    for floor in parsed["floors"]:
        default = heights.get(floor["token"])
        for e in floor["elements"]:
            note = e.get("note", "")
            if e["kind"] == "wall":
                height = e["height"] or default
                if not height:
                    missing.append(floor["name"])
                    continue
                quantity = max(0.0, e["length"] * height / 1e6 - e["door_area"])
                note = f"dài {vn(e['length'] / 1000)} m × cao {height} mm" + (f", trừ cửa {', '.join(e['doors'])}" if e["doors"] else "")
            else:
                quantity = e["quantity"]
            key = "|".join(["dxf", e["layer"], e.get("block", ""), e.get("label", "")])
            out.append(Row(floor=floor["name"], token=floor["token"], kind=e["kind"], key=key, prefix=e["prefix"], work=e["work"][:200],
                           location=e.get("location", "")[:200], unit=e["unit"], quantity=_dec(quantity), note=note,
                           geometry=e["geometry"]))
    return out, sorted(set(missing))


def _poly(geometry):
    if not geometry or geometry.get("type") not in ("polygon", "outline"):
        return None
    try:
        poly = Polygon(geometry["rings"][0])
        return poly if poly.is_valid and poly.area > 0 else poly.buffer(0)
    except (KeyError, IndexError, ValueError, TypeError):
        return None


def _score(row, item):
    if row.key != item.source_key:
        return 0
    if row.kind in ("device", "run"):
        return 1
    a, b = _poly(row.geometry), _poly(item.geometry)
    if a is None or b is None or a.area <= 0 or b.area <= 0:
        return 0
    union = a.union(b).area
    return a.intersection(b).area / union if union else 0


def plan(project, parsed, heights):
    """So tệp với chi tiết đang có. Không ghi gì."""
    new_rows, missing = rows(parsed, heights)
    floors = {f["name"] for f in parsed["floors"]}
    existing = list(Item.objects.filter(project=project, active=True, floor__in=floors))
    reported = set(ReportLine.objects.filter(item__in=existing).values_list("item_id", flat=True))
    pairs = sorted(((_score(r, it), i, j) for i, r in enumerate(new_rows) for j, it in enumerate(existing)
                    if r.floor == it.floor), reverse=True)
    used_rows, used_items = set(), set()
    for score, i, j in pairs:
        if score < MATCH or i in used_rows or j in used_items:
            continue
        used_rows.add(i)
        used_items.add(j)
        row, item = new_rows[i], existing[j]
        row.item, row.code = item, item.code
        row.changes = [label for label, old, new in (("khối lượng", item.design_quantity, row.quantity), ("công việc", item.work, row.work),
                                                     ("vị trí", item.location, row.location), ("hình", item.geometry, row.geometry))
                       if old != new]
        row.status = "update" if row.changes else "same"
    taken = set(Item.objects.filter(project=project).values_list("code", flat=True))
    counters = {}
    for row in new_rows:
        if row.item:
            continue
        base = f"{row.prefix}{row.token}."
        if base not in counters:
            numbers = [int(m.group(1)) for code in taken if (m := re.fullmatch(re.escape(base) + r"(\d+)", code))]
            counters[base] = max(numbers, default=0)
        counters[base] += 1
        row.code = f"{base}{counters[base]}"
        taken.add(row.code)
    gone = [it for j, it in enumerate(existing) if j not in used_items]
    return {"rows": new_rows, "missing_heights": missing, "kept": [it for it in gone if it.pk in reported],
            "retired": [it for it in gone if it.pk not in reported]}


def _last_price(work, unit, project):
    item = (Item.objects.filter(work=work, unit=unit).exclude(project=project)
            .exclude(sale_price=None, cost_price=None).order_by("-pk").first())
    return (item.sale_price, item.cost_price) if item else (None, None)


@transaction.atomic
def apply(project, parsed, heights, actor):
    project = Project.objects.select_for_update().get(pk=project.pk)
    result = plan(project, parsed, heights)
    if result["missing_heights"]:
        raise ValueError("Chưa nhập chiều cao tường cho: " + ", ".join(result["missing_heights"]))
    reason = f"Nhập bản vẽ {parsed.get('source_name', '')}".strip()
    for floor in parsed["floors"]:
        drawing = Drawing.objects.filter(project=project, floor=floor["name"]).first()
        if drawing is None:
            drawing = Drawing(project=project, floor=floor["name"], version=0)
        drawing.order = floor["order"]
        drawing.view_box = floor["view_box"]
        drawing.background = floor["background"]
        drawing.source_name = parsed.get("source_name", "")[:255]
        drawing.wall_height = heights.get(floor["token"])
        drawing.version += 1
        record_save(drawing, actor, reason)
    for row in result["rows"]:
        item = row.item
        if item is None:
            sale, cost = _last_price(row.work, row.unit, project)
            item = Item(project=project, code=row.code, sale_price=sale, cost_price=cost)
        elif row.status == "same":
            continue
        item.floor, item.work, item.location, item.unit = row.floor, row.work, row.location, row.unit
        item.design_quantity, item.geometry, item.source_key, item.note = row.quantity, row.geometry, row.key, row.note[:300]
        record_save(item, actor, reason)
    for item in result["retired"]:
        item.active = False
        record_save(item, actor, f"{reason}: không còn trên bản vẽ")
    return result
