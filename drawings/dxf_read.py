"""Đọc tệp DXF theo bộ quy chuẩn (drawings/standard.py): lọc, tách tầng theo khung, tính hình học.

Chỉ đọc model space. Mọi thứ ngoài quy chuẩn bị bỏ và được liệt kê (layer, lý do, số lượng) để người nhập biết,
nên dù quên bấm "Xuất cho Tend" trong AutoCAD thì dữ liệu vào vẫn sạch. Kết quả là dữ liệu JSON thuần
(lưu tạm giữa bước xem trước và xác nhận); khối lượng tường nhân chiều cao ở drawings/importer.py.
"""
import re
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import ezdxf
import ezdxf.recover
from ezdxf import bbox as dxf_bbox
from ezdxf import path as dxf_path
from shapely.geometry import LineString, Point, Polygon, box

from . import standard as S

UNICODE_ESCAPE = re.compile(r"\\U\+([0-9A-Fa-f]{4})")
SIZE = re.compile(r"(\d{3,4})\s*[X×*]\s*(\d{3,4})")
DOOR_CODE = re.compile(r"\b([A-Z]{1,3}\d{1,3}[A-Z]?)\b")
HEIGHT_NOTE = re.compile(r"^H\s*=\s*(\d{3,5})")
CLAD_NOTE = re.compile(r"^OP\s*(\d{3,5})\b")
FLOOR_LIST = re.compile(r"TANG\s*((?:\d+\s*(?:,|\.|;|&|-|VA|DEN|\s)\s*)*\d+)")


class DxfError(ValueError):
    pass


def fold(text):
    """Bỏ dấu, viết hoa: "Mặt bằng tầng 2" → "MAT BANG TANG 2"."""
    text = unicodedata.normalize("NFD", text or "").replace("đ", "d").replace("Đ", "D")
    return "".join(c for c in text if unicodedata.category(c) != "Mn").upper().replace("–", "-").replace("—", "-")


def clean(text):
    text = UNICODE_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), text or "")
    return " ".join(text.replace("\\P", " ").split())


def parse_floors(title):
    """"MẶT BẰNG TẦNG 2" → [2]; "tầng 2, 3 và 4", "TẦNG 2-5" → nhiều tầng; "MÁI" → ["M"]."""
    t = fold(title)
    m = FLOOR_LIST.search(t)
    if m:
        numbers, body = [], m.group(1)
        for part in re.split(r"\s*(?:,|\.|;|&|VA|\s)\s*", re.sub(r"\s*(?:-|DEN)\s*", "-", body)):
            if re.fullmatch(r"\d+-\d+", part):
                a, b = map(int, part.split("-"))
                numbers.extend(range(min(a, b), max(a, b) + 1))
            elif part.isdigit():
                numbers.append(int(part))
        return sorted(set(n for n in numbers if 0 < n < 100))
    if re.search(r"\bMAI\b", t):
        return ["M"]
    return []


def vn(value, places=2):
    """Số kiểu Việt Nam cho phần ghi cách tính: 6,00."""
    return f"{value:,.{places}f}".replace(",", " ").replace(".", ",").replace(" ", ".")


def _material(work, text):
    """Bỏ phần nhắc lại tên việc ở đầu chữ ("Trần Thạch cao" trong vùng trần → "Thạch cao")."""
    words, head = text.split(), fold(work).split()
    i = 0
    while i < len(words) and i < len(head) and fold(words[i]) == head[i]:
        i += 1
    return " ".join(words[i:])


def floor_name(token):
    return "Mái" if token == "M" else f"Tầng {token}"


def floor_order(token):
    return 99 if token == "M" else int(token)


def open_dxf(path):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.lower().endswith(".dxf") and not n.startswith("__MACOSX")]
            if len(names) != 1:
                raise DxfError("Tệp .zip phải chứa đúng một tệp .dxf.")
            target = path.with_suffix(".dxf")
            target.write_bytes(archive.read(names[0]))
            path = target
    try:
        doc, _ = ezdxf.recover.readfile(str(path))
    except (IOError, ezdxf.DXFStructureError) as error:
        raise DxfError("Không đọc được tệp DXF. Lưu lại bằng AutoCAD (Xuất cho Tend) rồi tải lên lại.") from error
    return doc


# --- Đọc từng đối tượng ra dạng đơn giản ------------------------------------------------------------------------

def _text_of(entity):
    try:
        raw = entity.plain_text()
    except Exception:   # chữ hỏng: coi như rỗng
        raw = entity.dxf.get("text", "")
    return clean(raw)


def _text_point(entity):
    if entity.dxftype() == "TEXT" and (entity.dxf.get("halign", 0) or entity.dxf.get("valign", 0)):
        p = entity.dxf.get("align_point", entity.dxf.insert)
    else:
        p = entity.dxf.insert
    return (float(p[0]), float(p[1]))


def _points(entity):
    if entity.dxftype() == "LINE":
        return [(entity.dxf.start[0], entity.dxf.start[1]), (entity.dxf.end[0], entity.dxf.end[1])], False
    pts = [(float(x), float(y)) for x, y in entity.get_points("xy")]
    closed = bool(entity.closed) or (len(pts) > 2 and Point(pts[0]).distance(Point(pts[-1])) < 1.0)
    if closed and len(pts) > 2 and Point(pts[0]).distance(Point(pts[-1])) < 1.0:
        pts = pts[:-1]
    return pts, closed


def _bounds(entity):
    try:
        ext = dxf_bbox.extents([entity], fast=True)
    except Exception:
        return None
    if not ext.has_data:
        return None
    return (ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)


def _svg_paths(entity, flatten=20):
    out = []
    try:
        sources = [entity] if entity.dxftype() != "INSERT" else list(entity.virtual_entities())
        for source in sources:
            try:
                p = dxf_path.make_path(source)
            except (TypeError, ValueError):
                continue
            for sub in p.sub_paths():
                pts = list(sub.flattening(flatten))
                if len(pts) > 1:
                    out.append([(float(v.x), float(v.y)) for v in pts])
    except Exception:   # hình lạ ở nền: bỏ, không làm hỏng lần nhập
        return []
    return out


def _ref_point(entity):
    kind = entity.dxftype()
    if kind in ("TEXT", "MTEXT"):
        return _text_point(entity)
    if kind == "INSERT":
        return (float(entity.dxf.insert[0]), float(entity.dxf.insert[1]))
    if kind in ("LINE", "LWPOLYLINE"):
        pts, _ = _points(entity)
        if len(pts) > 2:
            g = Polygon(pts)
            if g.is_valid and g.area > 0:
                c = g.representative_point()
                return (c.x, c.y)
        line = LineString(pts) if len(pts) > 1 else Point(pts[0])
        c = line.interpolate(0.5, normalized=True) if len(pts) > 1 else line
        return (c.x, c.y)
    b = _bounds(entity)
    return ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b else None


# --- Đọc cả tệp ------------------------------------------------------------------------------------------------

def read(path):
    doc = open_dxf(path)
    return read_doc(doc, Path(path).name)


def read_doc(doc, source_name=""):
    dropped = Counter()
    warnings = []
    frames, legends, items = [], [], []
    for entity in doc.modelspace():
        layer = (entity.dxf.get("layer", "") or "").upper()
        kind = entity.dxftype()
        closed = True
        if kind in ("LWPOLYLINE",):
            closed = _points(entity)[1]
        reason = S.rejection(layer, kind, closed, entity.dxf.get("name", "") if kind == "INSERT" else "")
        if reason:
            dropped[(layer or "0", reason)] += 1
            continue
        lkind = S.kind_of(layer)
        if lkind == S.FRAME:
            b = _bounds(entity) if kind == "INSERT" else Polygon(_points(entity)[0]).bounds
            if b:
                frames.append({"box": box(*b), "titles": []})
            continue
        if lkind == S.LEGEND:
            if kind == "LWPOLYLINE" and closed:
                legends.append(Polygon(_points(entity)[0]))
            continue
        point = _ref_point(entity)
        if point is None:
            continue
        items.append((layer, lkind, entity, point))

    if not frames:
        raise DxfError("Bản vẽ chưa có khung tầng (layer CH-KHUNG). Trong AutoCAD bấm nút Khung tầng cho từng mặt bằng.")
    by_frame = defaultdict(list)
    for layer, lkind, entity, point in items:
        p = Point(point)
        if any(zone.contains(p) for zone in legends):
            continue
        frame = next((i for i, f in enumerate(frames) if f["box"].covers(p)), None)
        if frame is None:
            if lkind not in (S.BACKGROUND, S.TITLE, S.ROOM_TEXT):
                dropped[(layer, "Nằm ngoài khung tầng")] += 1
            continue
        if lkind == S.TITLE:
            text = _text_of(entity)
            height = float(entity.dxf.get("height", 0) or entity.dxf.get("char_height", 0) or 0)
            frames[frame]["titles"].append((height, text))
            continue
        by_frame[frame].append((layer, lkind, entity, point))

    floors, seen = [], {}
    for index, frame in enumerate(frames):
        titles = sorted(frame["titles"], reverse=True)
        tokens, title = [], ""
        for _, text in titles:
            tokens = parse_floors(text)
            if tokens:
                title = text
                break
        count = len(by_frame[index])
        if not tokens:
            if count:
                warnings.append(f"Khung có {count} đối tượng nhưng chưa có tiêu đề tầng (vd \"MẶT BẰNG TẦNG 2\" ở layer CH-TEXT): bỏ cả khung.")
                dropped[("CH-KHUNG", "Khung chưa có tiêu đề tầng")] += count
            continue
        sheet = _sheet(frame["box"], by_frame[index], dropped, warnings)
        for token in tokens:
            token = str(token)
            if token in seen:
                warnings.append(f"{floor_name(token)} có hơn một khung (\"{seen[token]}\" và \"{title}\"): chỉ lấy khung đầu.")
                dropped[("CH-KHUNG", "Khung trùng tầng")] += count
                continue
            seen[token] = title
            floors.append({"token": token, "name": floor_name(token), "order": floor_order(token), "title": title, **sheet})
    floors.sort(key=lambda f: f["order"])
    if not floors:
        raise DxfError("Không khung nào có tiêu đề tầng (vd \"MẶT BẰNG TẦNG 2\" ở layer CH-TEXT).")
    return {"source_name": source_name, "floors": floors, "warnings": warnings,
            "dropped": [{"layer": layer, "reason": reason, "count": n} for (layer, reason), n in sorted(dropped.items())]}


def _sheet(frame_box, entries, dropped, warnings):
    """Một khung tầng → nền SVG + các phần tử khối lượng (toạ độ khung: gốc trên-trái, y hướng xuống, mm)."""
    minx, miny, maxx, maxy = frame_box.bounds

    def local(pt):
        return [round(pt[0] - minx, 1), round(maxy - pt[1], 1)]

    def ring(geom):
        return [local(p) for p in list(geom.exterior.coords)[:-1]]

    texts = defaultdict(list)       # layer → [(Point, text, height)]
    shapes = defaultdict(list)      # layer → [(entity, pts, closed)]
    inserts = defaultdict(list)     # block → [Point]
    background = defaultdict(list)
    for layer, lkind, entity, point in entries:
        kind = entity.dxftype()
        if lkind == S.BACKGROUND:
            for pts in _svg_paths(entity):
                background[layer].append("M" + " L".join(f"{x:g} {y:g}" for x, y in (local(p) for p in pts)))
            continue
        if kind in ("TEXT", "MTEXT"):
            height = float(entity.dxf.get("height", 0) or entity.dxf.get("char_height", 0) or 0)
            texts[layer].append((Point(point), _text_of(entity), height))
        elif kind == "INSERT":
            inserts[entity.dxf.name.upper()].append(Point(point))
        else:
            pts, closed = _points(entity)
            if len(pts) >= 2:
                shapes[layer].append((pts, closed))

    elements = []
    # Phòng
    rooms = []
    for pts, closed in shapes.get("KL-SAN", []):
        poly = Polygon(pts)
        if not poly.is_valid or poly.area <= 0:
            dropped[("KL-SAN", "Đường bao tự cắt hoặc diện tích 0")] += 1
            continue
        inside = sorted((t for t in texts.get("CH-PHONG", []) if poly.contains(t[0])), key=lambda t: (-t[2], -t[0].y))
        rooms.append({"poly": poly, "name": inside[0][1] if inside else "", "material": " ".join(t[1] for t in inside[1:])})
    orphan_names = [t for t in texts.get("CH-PHONG", []) if not any(r["poly"].contains(t[0]) for r in rooms)]
    if orphan_names:
        warnings.append(f"{len(orphan_names)} chữ tên phòng nằm ngoài vùng sàn KL-SAN: " + ", ".join(t[1] for t in orphan_names[:5]))

    def room_at(geom):
        names = [r["name"] for r in rooms if r["name"] and r["poly"].intersects(geom)]
        return names

    def location(geom):
        names = list(dict.fromkeys(room_at(geom)))
        if len(names) >= 2:
            return f"giữa {names[0]} và {names[1]}"
        return names[0] if names else ""

    # Cửa
    doors = []
    for pts, closed in shapes.get("KL-CUA", []):
        poly = Polygon(pts)
        if not poly.is_valid or poly.area <= 0:
            dropped[("KL-CUA", "Đường bao tự cắt hoặc diện tích 0")] += 1
            continue
        inside = [t for layer in ("KL-CUA", "KL-GHI") for t in texts.get(layer, []) if poly.buffer(50).contains(t[0])]
        joined = fold(" ".join(t[1] for t in inside))
        size = SIZE.search(joined)
        code = DOOR_CODE.search(SIZE.sub(" ", joined))
        rect = poly.minimum_rotated_rectangle
        sides = sorted(LineString(list(rect.exterior.coords)[i:i + 2]).length for i in range(2))
        width = int(size.group(1)) if size else round(sides[-1])
        height = int(size.group(2)) if size else S.DEFAULT_DOOR_HEIGHT
        doors.append({"poly": poly, "code": code.group(1) if code else "", "width": width, "height": height})
        if not code:
            warnings.append("Có khung cửa chưa ghi mã cửa (vd D1) bên trong.")

    # Tường
    walls = []
    for layer in ("KL-XAY-110", "KL-XAY-220", "KL-XAY-50"):
        thick = S.BY_NAME[layer].thickness
        for pts, closed in shapes.get(layer, []):
            if closed and len(pts) > 2:
                poly = Polygon(pts)
                if not poly.is_valid or poly.area <= 0:
                    dropped[(layer, "Đường bao tự cắt hoặc diện tích 0")] += 1
                    continue
                length = poly.area / thick
            else:
                line = LineString(pts)
                if line.length < 1:
                    continue
                poly = line.buffer(thick / 2, cap_style="flat", join_style="mitre")
                length = line.length
            walls.append({"layer": layer, "poly": poly, "length": length, "height": None, "door_area": 0.0, "doors": []})
    for layer in ("KL-GHI", "KL-XAY-110", "KL-XAY-220", "KL-XAY-50"):
        for point, text, _ in texts.get(layer, []):
            m = HEIGHT_NOTE.match(fold(text))
            if not m or not walls:
                continue
            nearest = min(walls, key=lambda w: w["poly"].distance(point))
            if nearest["poly"].distance(point) <= S.NOTE_REACH:
                nearest["height"] = int(m.group(1))
            else:
                warnings.append(f"Chữ \"{text}\" không nằm sát bức tường nào (cách quá {S.NOTE_REACH} mm).")
    for door in doors:
        hits = [(w["poly"].intersection(door["poly"]).area, i) for i, w in enumerate(walls)]
        hits = [h for h in hits if h[0] > 0]
        if hits:
            wall = walls[max(hits)[1]]
            wall["door_area"] += door["width"] * door["height"] / 1e6
            wall["doors"].append(f"{door['code'] or 'cửa'} {door['width']}×{door['height']}")
    for wall in walls:
        meta = S.BY_NAME[wall["layer"]]
        elements.append({"kind": "wall", "layer": wall["layer"], "prefix": meta.prefix, "work": meta.work, "unit": meta.unit,
                         "location": location(wall["poly"].buffer(150)), "length": round(wall["length"], 1),
                         "height": wall["height"], "door_area": round(wall["door_area"], 4), "doors": wall["doors"],
                         "geometry": {"type": "polygon", "rings": [ring(wall["poly"])]}, "_c": wall["poly"].centroid})

    # Lát nền, ốp trong phòng
    for room in rooms:
        work = "Lát nền" + (f" {room['material']}" if room["material"] else "")
        elements.append({"kind": "area", "layer": "KL-SAN", "prefix": "S", "work": work, "unit": "m2", "location": room["name"],
                         "quantity": round(room["poly"].area / 1e6, 3),
                         "geometry": {"type": "polygon", "rings": [ring(room["poly"])]}, "_c": room["poly"].centroid})
    for point, text, _ in texts.get("KL-GHI", []):
        m = CLAD_NOTE.match(fold(text))
        if not m:
            continue
        room = next((r for r in rooms if r["poly"].contains(point)), None)
        if room is None:
            warnings.append(f"Chữ \"{text}\" không nằm trong phòng (vùng KL-SAN) nào.")
            continue
        clad_h = int(m.group(1))
        material = " ".join(text.split()[2:])
        edge = room["poly"].exterior.buffer(200)
        deduct = sum(d["width"] * min(d["height"], clad_h) / 1e6 for d in doors if d["poly"].intersects(edge))
        quantity = room["poly"].exterior.length * clad_h / 1e6 - deduct
        elements.append({"kind": "cladding", "layer": "KL-GHI", "prefix": "OP", "work": "Ốp tường" + (f" {material}" if material else ""),
                         "unit": "m2", "location": room["name"], "quantity": round(max(quantity, 0), 3),
                         "note": f"chu vi {vn(room['poly'].exterior.length / 1000)} m × cao {clad_h} mm, trừ cửa {vn(deduct)} m2",
                         "geometry": {"type": "outline", "rings": [ring(room["poly"])]}, "_c": room["poly"].centroid})

    # Trần, ốp mặt đứng, bê tông
    for layer in ("KL-TRAN", "KL-OP", "KL-BT"):
        meta = S.BY_NAME[layer]
        for pts, closed in shapes.get(layer, []):
            poly = Polygon(pts)
            if not poly.is_valid or poly.area <= 0:
                dropped[(layer, "Đường bao tự cắt hoặc diện tích 0")] += 1
                continue
            words = [t[1] for lay in (layer, "KL-GHI") for t in texts.get(lay, [])
                     if poly.contains(t[0]) and not HEIGHT_NOTE.match(fold(t[1])) and not CLAD_NOTE.match(fold(t[1]))]
            material = _material(meta.work, " ".join(words))
            elements.append({"kind": "area", "layer": layer, "prefix": meta.prefix, "unit": meta.unit,
                             "work": meta.work + (f" {material}" if material else ""), "location": location(poly.representative_point()),
                             "quantity": round(poly.area / 1e6, 3), "geometry": {"type": "polygon", "rings": [ring(poly)]},
                             "_c": poly.centroid})

    for door in doors:
        elements.append({"kind": "door", "layer": "KL-CUA", "prefix": "C", "unit": "bộ", "quantity": 1,
                         "work": f"Lắp cửa {door['code']}".strip(), "location": location(door["poly"]),
                         "note": f"{door['width']}×{door['height']}", "geometry": {"type": "polygon", "rings": [ring(door["poly"])]},
                         "_c": door["poly"].centroid})

    # Thiết bị: mỗi loại block một chi tiết theo tầng
    for name in sorted(inserts):
        block = S.BLOCK_BY_NAME[name]
        pts = inserts[name]
        elements.append({"kind": "device", "layer": block.layer, "block": name, "prefix": "TB", "unit": "cái", "quantity": len(pts),
                         "work": block.work, "location": "", "geometry": {"type": "points", "points": [local((p.x, p.y)) for p in pts]},
                         "_c": pts[0]})

    # Dây, ống: gom theo layer + loại ghi sát tuyến
    for layer in ("D-DAY", "N-CAP-NUOC", "N-ONG-THOAT"):
        lines = [LineString(pts) for pts, _ in shapes.get(layer, []) if len(pts) >= 2]
        if not lines:
            continue
        labels = [""] * len(lines)
        for point, text, _ in texts.get(layer, []):
            distances = [line.distance(point) for line in lines]
            best = min(range(len(lines)), key=distances.__getitem__)
            if distances[best] <= S.NOTE_REACH:
                labels[best] = text
        meta = S.BY_NAME[layer]
        groups = defaultdict(list)
        for line, label in zip(lines, labels):
            groups[label].append(line)
        for label, group in sorted(groups.items()):
            elements.append({"kind": "run", "layer": layer, "label": label, "prefix": meta.prefix, "unit": "m",
                             "work": meta.work + (f" {label}" if label else ""), "location": "",
                             "quantity": round(sum(g.length for g in group) / 1000, 2),
                             "geometry": {"type": "lines", "lines": [[local(p) for p in g.coords] for g in group]},
                             "_c": group[0].interpolate(0.5, normalized=True)})

    # Thứ tự đánh mã: từ trên xuống (dải 1 m), trái sang phải
    elements.sort(key=lambda e: (e["prefix"], -round(e["_c"].y / 1000), e["_c"].x))
    for element in elements:
        c = element.pop("_c")
        element["center"] = local((c.x, c.y))
    return {"view_box": [0, 0, round(maxx - minx, 1), round(maxy - miny, 1)], "background": dict(background),
            "elements": elements}
