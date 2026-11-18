"""Chép dữ liệu đầu từ Tend cũ (so.tend.vn) sang Tend mới — chỉ đọc bản cũ.

Chép: tài khoản (giữ mật khẩu cũ), công trình, người được giao, bản vẽ tầng (nền), chi tiết khối lượng (mã, tầng,
công việc, vị trí, đơn vị, khối lượng, hình, kế hoạch) kèm đơn giá bán/vốn từ dự toán.
Không chép: báo cáo, ảnh, nghiệm thu, chứng từ, thu chi → mọi chi tiết bắt đầu 0%.

    python manage.py chep_ban_cu --nguon postgres://…/tend            # chỉ xem sẽ chép gì
    python manage.py chep_ban_cu --nguon … --thuc-hien --nguoi giamdoc  # ghi, nhật ký đứng tên người này

Kết nối bản cũ ở chế độ chỉ đọc (PostgreSQL: default_transaction_read_only; SQLite: mode=ro).
Công trình/tài khoản đã có trong bản mới (trùng mã/tên đăng nhập) thì bỏ qua, chạy lại không chép trùng.
"""
import json
import sqlite3
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from urllib.parse import unquote, urlparse

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.audit import record_save
from core.models import Role, User
from drawings.models import Drawing, Item
from projects.models import Member, Project

# Nhóm quyền bản cũ → vai bản mới (nhiều nhóm thì lấy vai đứng trước).
ROLE_ORDER = [("Giám đốc", Role.DIRECTOR), ("Kế toán trưởng", Role.FINANCE_ACCOUNTANT), ("Kế toán tài chính", Role.FINANCE_ACCOUNTANT),
              ("Kế toán nội bộ", Role.INTERNAL_ACCOUNTANT), ("Kế toán", Role.INTERNAL_ACCOUNTANT), ("Kiến trúc sư", Role.DESIGNER),
              ("Chỉ huy trưởng", Role.SUPERVISOR)]
NO_ROLE = {"Bán hàng", "Kế toán bán hàng"}   # bản mới chưa có vai này


def connect(url):
    """Kết nối DB-API chỉ đọc tới CSDL bản cũ."""
    parsed = urlparse(url)
    if parsed.scheme in ("sqlite", "sqlite3"):
        path = Path(unquote(parsed.path)[1:])     # sqlite:////tuyet/doi.db hoặc sqlite:///tuong-doi.db
        db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        return db, "?"
    if parsed.scheme in ("postgres", "postgresql", "pgsql"):
        import psycopg
        from psycopg.rows import dict_row
        db = psycopg.connect(url, row_factory=dict_row, options="-c default_transaction_read_only=on")
        return db, "%s"
    raise CommandError("Chỉ đọc được bản cũ PostgreSQL hoặc SQLite.")


def floor_name(number):
    if number in (None, ""):
        return ""
    number = int(number)
    return "Mái" if number == 6 else f"Tầng {number}"


def unit_key(unit):
    return (unit or "").lower().replace("²", "2").replace("³", "3").replace(" ", "")


def ring(points):
    try:
        out = [[round(float(x), 1), round(float(y), 1)] for x, y in points]
    except (TypeError, ValueError):
        return None
    return out if len(out) >= 3 else None


def as_json(value):
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


class Reader:
    def __init__(self, url):
        self.db, self.mark = connect(url)

    def all(self, sql, *params):
        cursor = self.db.cursor()
        cursor.execute(sql.replace("?", self.mark), params)
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self.db.close()


def read_old(url, codes=None):
    """Đọc toàn bộ phần cần chép thành dữ liệu thuần (không ghi gì vào bản cũ)."""
    old = Reader(url)
    try:
        groups = defaultdict(set)
        for row in old.all("SELECT ug.user_id, g.name FROM core_user_groups ug JOIN auth_group g ON g.id = ug.group_id"):
            groups[row["user_id"]].add(row["name"])
        users = old.all("SELECT id, username, password, first_name, last_name, email, is_active, is_superuser FROM core_user")
        for user in users:
            user["groups"] = sorted(groups[user["id"]])
        projects = old.all("SELECT id, code, name, address, start_date, handover_date, contract_value, discount_amount, stage, "
                           "site_manager_id FROM projects_project WHERE is_deleted = ? ORDER BY code", False)
        if codes:
            projects = [p for p in projects if p["code"] in codes]
        for p in projects:
            pid = p["id"]
            p["members"] = sorted({r["user_id"] for r in old.all(
                "SELECT user_id FROM projects_projectmembership WHERE project_id = ? AND is_active = ?", pid, True)}
                | ({p["site_manager_id"]} if p["site_manager_id"] else set()))
            p["plans"] = old.all("SELECT floor, view_box, paths, source FROM progress_floorplan WHERE project_id = ? ORDER BY floor", pid)
            p["estimate"] = old.all("SELECT id, name, unit, quantity, contract_unit_price, cost_unit_price, area, position "
                                    "FROM projects_estimateitem WHERE project_id = ? AND is_deleted = ? ORDER BY position, id", pid, False)
            p["items"] = old.all("SELECT id, estimate_item_id, floor_label, work_name, location, mark, unit, quantity, plan_start, "
                                 "plan_end, geo, position FROM progress_progressitem WHERE project_id = ? AND is_deleted = ? "
                                 "ORDER BY position, id", pid, False)
            elements = {r["id"]: r for r in old.all(
                "SELECT id, code, floor, pts FROM progress_mapelement WHERE project_id = ? AND is_deleted = ?", pid, False)}
            links = defaultdict(list)
            for r in old.all("SELECT l.mapelement_id, l.progressitem_id FROM progress_mapelement_links l "
                             "JOIN progress_mapelement e ON e.id = l.mapelement_id WHERE e.project_id = ?", pid):
                if r["mapelement_id"] in elements:
                    links[r["progressitem_id"]].append(elements[r["mapelement_id"]])
            for item in p["items"]:
                item["elements"] = links.get(item["id"], [])
            p["signed_variations"] = sum((Decimal(str(r["contract_amount"] or 0)) for r in old.all(
                "SELECT contract_amount FROM projects_variationorder WHERE project_id = ? AND status = ? AND is_deleted = ?",
                pid, "signed", False)), Decimal(0))
        return users, projects
    finally:
        old.close()


def plan_items(project):
    """Chi tiết bản mới của một công trình cũ, cùng các cảnh báo."""
    warnings, out, codes = [], [], set()
    estimate = {e["id"]: e for e in project["estimate"]}
    linked = set()
    for item in project["items"]:
        geo = as_json(item["geo"]) or {}
        floor = floor_name(geo.get("f")) if geo.get("f") not in (None, "") else ""
        geometry = None
        if geo.get("pts") and ring(geo["pts"]):
            geometry = {"type": "polygon", "rings": [ring(geo["pts"])]}
        for element in item["elements"]:
            pts = ring(as_json(element["pts"]) or [])
            if pts and geometry is None:
                geometry = {"type": "polygon", "rings": [pts]}
                floor = floor or floor_name(element["floor"])
        floor = floor or (item["floor_label"] or "")[:30]
        code = (item["mark"] or "").strip() or f"D{item['id']}"
        if code in codes:
            warnings.append(f"{project['code']}: mã {code} lặp, chi tiết #{item['id']} đổi thành {code}-{item['id']}")
            code = f"{code}-{item['id']}"
        codes.add(code)
        sale = cost = None
        source = estimate.get(item["estimate_item_id"])
        if source:
            linked.add(source["id"])
            if unit_key(source["unit"]) == unit_key(item["unit"]):
                sale, cost = source["contract_unit_price"], source["cost_unit_price"]
            else:
                warnings.append(f"{project['code']} {code}: đơn vị {item['unit']} khác dự toán ({source['unit']}), chưa lấy đơn giá")
        out.append({"code": code[:30], "floor": floor, "work": (item["work_name"] or "")[:200], "location": (item["location"] or "")[:200],
                    "unit": (item["unit"] or "")[:20], "design_quantity": Decimal(str(item["quantity"] or 0)),
                    "sale_price": sale, "cost_price": cost, "plan_start": item["plan_start"], "plan_end": item["plan_end"],
                    "geometry": geometry, "source_key": f"old:pi:{item['id']}"})
    number = 0
    for row in project["estimate"]:
        if row["id"] in linked:
            continue
        number += 1
        code = f"DT{number}"
        while code in codes:
            number += 1
            code = f"DT{number}"
        codes.add(code)
        out.append({"code": code, "floor": "", "work": (row["name"] or "")[:200], "location": (row["area"] or "")[:200],
                    "unit": (row["unit"] or "")[:20], "design_quantity": Decimal(str(row["quantity"] or 0)),
                    "sale_price": row["contract_unit_price"], "cost_price": row["cost_unit_price"], "plan_start": None,
                    "plan_end": None, "geometry": None, "source_key": f"old:ei:{row['id']}"})
    return out, warnings


def contract_value(project):
    if project["contract_value"] is not None:
        return Decimal(str(project["contract_value"]))
    items = sum((Decimal(str(r["quantity"] or 0)) * Decimal(str(r["contract_unit_price"] or 0)) for r in project["estimate"]), Decimal(0))
    if not items:
        return None
    total = items - Decimal(str(project["discount_amount"] or 0)) + project["signed_variations"]
    return total.quantize(Decimal(1))   # tiền đồng chẵn (khối lượng × đơn giá có thể lẻ)


def role_of(user):
    if user["is_superuser"]:
        return Role.DIRECTOR
    for group, role in ROLE_ORDER:
        if group in user["groups"]:
            return role
    return None


class Command(BaseCommand):
    help = "Chép công trình, tài khoản, bản vẽ, khối lượng từ Tend cũ (chỉ đọc bản cũ). Mặc định chỉ xem trước."

    def add_arguments(self, parser):
        parser.add_argument("--nguon", required=True, help="Địa chỉ CSDL bản cũ, vd postgres://user@/tend")
        parser.add_argument("--cong-trinh", action="append", dest="codes", help="Chỉ chép mã công trình này (lặp được)")
        parser.add_argument("--thuc-hien", action="store_true", help="Ghi vào bản mới (không có thì chỉ xem trước)")
        parser.add_argument("--nguoi", help="Tên đăng nhập (bản mới) đứng tên nhật ký khi ghi")

    def handle(self, *args, nguon, codes, thuc_hien, nguoi, **options):
        users, projects = read_old(nguon, set(codes or ()))
        actor, actor_old = None, None
        if thuc_hien:
            # Người đứng tên nhật ký: Giám đốc đã có ở bản mới, hoặc Giám đốc của bản cũ (chép trước, rồi đứng tên).
            actor = User.objects.filter(username=nguoi).first() if nguoi else None
            if actor is None and nguoi:
                actor_old = next((u for u in users if u["username"] == nguoi and role_of(u) == Role.DIRECTOR and u["is_active"]), None)
            if (actor is None or not actor.is_director) and actor_old is None:
                raise CommandError("Ghi cần --nguoi là tên đăng nhập của Giám đốc (ở bản cũ hoặc bản mới), vd --nguoi quan.")
        lines, warnings = [], []
        by_old = {}
        for user in users:
            role = role_of(user)
            existing = User.objects.filter(username=user["username"]).first()
            if existing:
                by_old[user["id"]] = existing
                lines.append(f"Tài khoản {user['username']}: đã có, giữ nguyên")
                continue
            if role is None:
                why = "vai Bán hàng / Kế toán bán hàng chưa có ở bản mới" if set(user["groups"]) & NO_ROLE else "chưa có vai"
                warnings.append(f"Bỏ qua tài khoản {user['username']}: {why}")
                continue
            if len([g for g, _ in ROLE_ORDER if g in user["groups"]]) > 1:
                warnings.append(f"Tài khoản {user['username']} có nhiều vai {', '.join(user['groups'])} → lấy {Role(role).label}")
            lines.append(f"Tài khoản {user['username']}: {Role(role).label}{'' if user['is_active'] else ' (đang khoá)'}")
            by_old[user["id"]] = (user, role)
        planned = []
        for project in projects:
            if Project.objects.filter(code=project["code"]).exists():
                lines.append(f"Công trình {project['code']}: đã có ở bản mới, bỏ qua")
                continue
            items, item_warnings = plan_items(project)
            warnings += item_warnings
            with_geo = sum(1 for i in items if i["geometry"])
            lines.append(f"Công trình {project['code']} · {project['name']}: {len(project['plans'])} tầng bản vẽ, {len(items)} chi tiết "
                         f"({with_geo} có hình), {sum(1 for i in items if i['sale_price'] is None)} chưa có giá bán, "
                         f"{sum(1 for i in items if i['cost_price'] is None)} chưa có giá vốn, {len(project['members'])} người được giao")
            planned.append((project, items))
        for line in lines:
            self.stdout.write(line)
        for line in warnings:
            self.stdout.write(self.style.WARNING("⚠ " + line))
        if not thuc_hien:
            self.stdout.write(self.style.NOTICE("Chưa ghi gì. Thêm --thuc-hien --nguoi <giám đốc> để chép."))
            return
        with transaction.atomic():
            reason = "Chép từ Tend cũ"
            if actor_old is not None:   # Giám đốc bản cũ: tạo trước để đứng tên các bản ghi sau
                order = sorted(by_old.items(), key=lambda pair: pair[0] != actor_old["id"])
            else:
                order = list(by_old.items())
            for old_id, value in order:
                if isinstance(value, tuple):
                    user, role = value
                    new = User(username=user["username"], first_name=user["first_name"] or "", last_name=user["last_name"] or "",
                               email=user["email"] or "", is_active=bool(user["is_active"]), role=role, password=user["password"])
                    record_save(new, actor, reason, channel="lenh")
                    by_old[old_id] = new
                    if actor is None:
                        actor = new
            for project, items in planned:
                new = Project(code=project["code"], name=project["name"], address=project["address"] or "",
                              start_date=project["start_date"], handover_date=project["handover_date"],
                              contract_value=contract_value(project), active=project["stage"] != "warranty")
                record_save(new, actor, reason, channel="lenh")
                for old_user in project["members"]:
                    user = by_old.get(old_user)
                    if isinstance(user, User) and not user.is_director:
                        record_save(Member(project=new, user=user), actor, reason, channel="lenh")
                for order, plan in enumerate(project["plans"]):
                    paths = as_json(plan["paths"]) or {}
                    record_save(Drawing(project=new, floor=floor_name(plan["floor"]), order=int(plan["floor"]),
                                        view_box=as_json(plan["view_box"]) or [], source_name=(plan["source"] or "")[:255],
                                        background={k: [v] if isinstance(v, str) else list(v) for k, v in paths.items()}),
                                actor, reason, channel="lenh")
                for row in items:
                    record_save(Item(project=new, **row), actor, reason, channel="lenh")
        self.stdout.write(self.style.SUCCESS(f"Đã chép {len(planned)} công trình. Tiến độ mọi chi tiết bắt đầu từ 0%."))
