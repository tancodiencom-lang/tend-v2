"""Lệnh chép từ Tend cũ: đọc đúng bảng của bản cũ (chỉ đọc), giữ mật khẩu, tiến độ về 0, chạy lại không trùng."""
import hashlib
import io
import json
import sqlite3
import tempfile
from decimal import Decimal
from pathlib import Path

from django.contrib.auth import authenticate
from django.contrib.auth.hashers import make_password
from django.core.management import CommandError, call_command
from django.test import TestCase

from core.models import AuditLog, Role, User
from drawings.models import Drawing, Item
from field.models import ReportLine

from .models import Member, Project

# Cột của bản cũ mà lệnh đọc (đúng tên bảng/cột của Tend cũ sau PR #42).
SCHEMA = """
CREATE TABLE core_user (id INTEGER PRIMARY KEY, password TEXT, username TEXT, first_name TEXT, last_name TEXT, email TEXT,
  is_active BOOL, is_superuser BOOL);
CREATE TABLE auth_group (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE core_user_groups (id INTEGER PRIMARY KEY, user_id INT, group_id INT);
CREATE TABLE projects_project (id INTEGER PRIMARY KEY, code TEXT, name TEXT, address TEXT, start_date DATE, handover_date DATE,
  contract_value DECIMAL, discount_amount DECIMAL, stage TEXT, site_manager_id INT, is_deleted BOOL);
CREATE TABLE projects_projectmembership (id INTEGER PRIMARY KEY, project_id INT, user_id INT, is_active BOOL);
CREATE TABLE progress_floorplan (id INTEGER PRIMARY KEY, project_id INT, floor INT, view_box TEXT, paths TEXT, source TEXT);
CREATE TABLE projects_estimateitem (id INTEGER PRIMARY KEY, project_id INT, name TEXT, unit TEXT, quantity DECIMAL,
  contract_unit_price DECIMAL, cost_unit_price DECIMAL, area TEXT, position INT, is_deleted BOOL);
CREATE TABLE progress_progressitem (id INTEGER PRIMARY KEY, project_id INT, estimate_item_id INT, floor_label TEXT, work_name TEXT,
  location TEXT, mark TEXT, unit TEXT, quantity DECIMAL, percent DECIMAL, plan_start DATE, plan_end DATE, geo TEXT, position INT, is_deleted BOOL);
CREATE TABLE progress_mapelement (id INTEGER PRIMARY KEY, project_id INT, code TEXT, floor INT, pts TEXT, is_deleted BOOL);
CREATE TABLE progress_mapelement_links (id INTEGER PRIMARY KEY, mapelement_id INT, progressitem_id INT);
CREATE TABLE projects_variationorder (id INTEGER PRIMARY KEY, project_id INT, contract_amount DECIMAL, status TEXT, is_deleted BOOL);
"""


def old_db():
    path = Path(tempfile.mkdtemp()) / "tend-cu.sqlite3"
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    pw = make_password("mat-khau-cu-123")
    users = [(1, "quan", "Giám đốc", 0), (2, "cht-thu", "Chỉ huy trưởng", 0), (3, "ketoantend", "Kế toán nội bộ", 0),
             (4, "ktttend", "Kế toán trưởng", 0), (5, "bhtend", "Bán hàng", 0), (6, "admin", None, 1)]
    for uid, name, group, sup in users:
        db.execute("INSERT INTO core_user VALUES (?,?,?,?,?,?,?,?)", (uid, pw, name, "", "", "", 1, sup))
        if group:
            db.execute("INSERT OR IGNORE INTO auth_group (name) VALUES (?)", (group,))
            gid = db.execute("SELECT id FROM auth_group WHERE name=?", (group,)).fetchone()[0]
            db.execute("INSERT INTO core_user_groups (user_id, group_id) VALUES (?,?)", (uid, gid))
    db.execute("INSERT INTO projects_project VALUES (1,'TN-22','Thiên Nga 22','Hà Nội','2026-10-01','2027-02-01',NULL,1000000,'construction',2,0)")
    db.execute("INSERT INTO projects_project VALUES (2,'XOA','Đã xoá','',NULL,NULL,NULL,0,'intake',NULL,1)")
    db.execute("INSERT INTO projects_projectmembership VALUES (1,1,3,1),(2,1,4,0)")
    db.execute("INSERT INTO progress_floorplan VALUES (1,1,2,?,?, 'tn22.dxf')", (json.dumps([0, 0, 9000, 6000]), json.dumps({"KT-COT": "M0 0 L1 1"})))
    db.execute("INSERT INTO projects_estimateitem VALUES (1,1,'Xây tường 110','m²',20.125,300000,200000,'Tầng 2',1,0),"
               "(2,1,'Vận chuyển','chuyến',3,1500000,NULL,'',2,0)")
    db.execute("INSERT INTO projects_variationorder VALUES (1,1,2000000,'signed',0),(2,1,9000000,'pending',0)")
    geo = json.dumps({"f": 2, "pts": [[0, 0], [3000, 0], [3000, 110], [0, 110]]})
    db.execute("INSERT INTO progress_progressitem VALUES (1,1,1,'Tầng 2','Xây tường','giữa P1 và P2','T2.1','m2',9.9,60,NULL,NULL,?,1,0),"
               "(2,1,NULL,'Tầng 2','Lát nền','P1','S2.1','m2',12,100,NULL,NULL,NULL,2,0),"
               "(3,1,NULL,'Tầng 2','Đã xoá','','X','m2',1,0,NULL,NULL,NULL,3,1)", (geo,))
    db.execute("INSERT INTO progress_mapelement VALUES (1,1,'S2.1',2,?,0)", (json.dumps([[0, 200], [3000, 200], [3000, 4200], [0, 4200]]),))
    db.execute("INSERT INTO progress_mapelement_links VALUES (1,1,2)")
    db.commit()
    db.close()
    return path


class CopyOldTests(TestCase):
    def setUp(self):
        self.path = old_db()
        self.url = f"sqlite:///{self.path}"
        self.boss = User.objects.create_superuser("giamdoc", "", "mat-khau-moi-123")

    def run_command(self, *args):
        out = io.StringIO()
        call_command("chep_ban_cu", "--nguon", self.url, *args, stdout=out)
        return out.getvalue()

    def test_preview_writes_nothing_and_reports(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        out = self.run_command()
        self.assertIn("Công trình TN-22 · Thiên Nga 22: 1 tầng bản vẽ, 3 chi tiết (2 có hình)", out)
        self.assertIn("Bỏ qua tài khoản bhtend", out)
        self.assertIn("Chưa ghi gì", out)
        self.assertNotIn("XOA", out)
        self.assertEqual((Project.objects.count(), User.objects.count()), (0, 1))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_copy_then_rerun_is_idempotent(self):
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.run_command("--thuc-hien", "--nguoi", "giamdoc")
        project = Project.objects.get(code="TN-22")
        self.assertEqual(project.contract_value, Decimal("11537500"))   # 20,125×300k + 4,5tr − 1tr chiết khấu + 2tr phát sinh đã ký
        roles = dict(User.objects.values_list("username", "role"))
        self.assertEqual(roles, {"giamdoc": Role.DIRECTOR, "quan": Role.DIRECTOR, "cht-thu": Role.SUPERVISOR,
                                 "ketoantend": Role.INTERNAL_ACCOUNTANT, "ktttend": Role.FINANCE_ACCOUNTANT, "admin": Role.DIRECTOR})
        self.assertIsNotNone(authenticate(username="cht-thu", password="mat-khau-cu-123"))
        self.assertEqual(sorted(Member.objects.values_list("user__username", flat=True)), ["cht-thu", "ketoantend"])
        items = {i.code: i for i in Item.objects.filter(project=project)}
        self.assertEqual(sorted(items), ["DT1", "S2.1", "T2.1"])
        wall = items["T2.1"]
        self.assertEqual((wall.floor, wall.unit, wall.design_quantity, wall.sale_price, wall.cost_price),
                         ("Tầng 2", "m2", Decimal("9.900"), 300000, 200000))
        self.assertEqual(items["S2.1"].geometry["rings"][0][2], [3000.0, 4200.0])       # hình phòng từ bản đồ cũ
        self.assertEqual((items["DT1"].work, items["DT1"].cost_price), ("Vận chuyển", None))
        self.assertFalse(ReportLine.objects.exists())                                     # tiến độ bắt đầu 0
        drawing = Drawing.objects.get(project=project)
        self.assertEqual((drawing.floor, drawing.background), ("Tầng 2", {"KT-COT": ["M0 0 L1 1"]}))
        logs = AuditLog.objects.filter(channel="lenh")
        self.assertTrue(logs.exists())
        self.assertFalse(any("password" in (log.after or {}) for log in logs))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        count = AuditLog.objects.count()
        out = self.run_command("--thuc-hien", "--nguoi", "giamdoc")
        self.assertIn("Công trình TN-22: đã có ở bản mới, bỏ qua", out)
        self.assertEqual(AuditLog.objects.count(), count)

    def test_old_director_signs_log(self):
        User.objects.filter(username="giamdoc").delete()
        out = self.run_command("--thuc-hien", "--nguoi", "quan")
        self.assertIn("Đã chép", out)
        quan = User.objects.get(username="quan")
        self.assertEqual(quan.role, Role.DIRECTOR)
        self.assertTrue(AuditLog.objects.filter(table="projects.Project", actor=quan).exists())
        self.assertIsNone(AuditLog.objects.get(table="core.User", object_id=str(quan.pk)).actor)
        with self.assertRaises(CommandError):
            self.run_command("--thuc-hien", "--nguoi", "cht-thu")    # không phải Giám đốc

    def test_write_needs_director(self):
        User.objects.create_user("gs", password="x-123-abcd", role=Role.SUPERVISOR)
        for args in (("--thuc-hien",), ("--thuc-hien", "--nguoi", "gs")):
            with self.assertRaises(CommandError):
                self.run_command(*args)
        self.assertFalse(Project.objects.exists())
