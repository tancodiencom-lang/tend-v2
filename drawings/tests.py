"""Bộ quy chuẩn → bản mẫu, LISP, bộ lọc nhập DXF khớp nhau; khối lượng tính tay; nhập lại giữ mã và số liệu thi công."""
import io
import re
import tempfile
import zipfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import ezdxf
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Role, User
from field.models import Report, ReportLine
from projects.models import Member, Project

from . import autocad, dxf_read, importer, lisp_check
from . import standard as S
from .models import Drawing, Item

TMP = tempfile.mkdtemp(prefix="tend-v2-media-")
BUILTINS = set("""* + - / /= 1+ 1- = > >= action_tile and angle append assoc caddr cadr car cdr close command cond cons defun
distance entdel entget entlast entmake equal foreach getcorner getint getpoint getstring getvar if initget itoa last length list
load_dialog logand mapcar max member new_dialog not null open or polar princ progn repeat reverse rtos setq setvar ssadd ssget
sslength ssname sssetfirst start_dialog strcase strcat subst tblsearch trans unload_dialog vl-filename-base vl-load-com
vl-remove-if-not vla-activate vla-get-activedocument vla-get-documents vla-open vla-save vla-saveas
vlax-curve-getpointatparam vlax-get-acad-object while write-line lambda""".split())


def by_work(floor):
    return {(e["work"], e.get("location", "")): e for e in floor["elements"]}


class SampleDrawingTests(TestCase):
    def test_sample_floor_quantities_by_hand_and_nothing_dropped(self):
        parsed = dxf_read.read_doc(autocad.sample_doc(), "mau.dxf")
        self.assertEqual(parsed["dropped"], [])
        self.assertEqual(parsed["warnings"], [])
        [floor] = parsed["floors"]
        self.assertEqual((floor["name"], floor["view_box"]), ("Tầng 1", [0, 0, 12000.0, 9000.0]))
        self.assertEqual(set(floor["background"]), {"KT-TUONG-HT", "KT-COT"})
        rows, missing = importer.rows(parsed, {"1": 3300})
        self.assertEqual(missing, [])
        got = {(r.work, r.location): (r.quantity, r.unit) for r in rows}
        expected = {
            ("Xây tường 110", "giữa PHÒNG KHÁCH và WC"): (Decimal("7.920"), "m2"),   # 3 m × 3,3 m − cửa 0,9 × 2,2
            ("Xây tường 220", "PHÒNG KHÁCH"): (Decimal("19.020"), "m2"),             # 6 m × H=3170
            ("Lát nền Gạch 600×600", "PHÒNG KHÁCH"): (Decimal("30.000"), "m2"),
            ("Lát nền Gạch 300×300", "WC"): (Decimal("6.000"), "m2"),
            ("Ốp tường Gạch 300×600", "WC"): (Decimal("22.020"), "m2"),             # chu vi 10 m × 2,4 − 0,9 × 2,2
            ("Trần Thạch cao khung chìm", "PHÒNG KHÁCH"): (Decimal("20.000"), "m2"),
            ("Lắp cửa D1", "giữa PHÒNG KHÁCH và WC"): (Decimal("1.000"), "bộ"),
            ("Lắp ổ cắm đôi", ""): (Decimal("2.000"), "cái"),
            ("Lắp đèn âm trần", ""): (Decimal("1.000"), "cái"),
            ("Đi dây điện Cu 2x1,5", ""): (Decimal("5.000"), "m"),
        }
        self.assertEqual(got, expected)

    def test_everything_outside_standard_is_dropped_with_reason(self):
        doc = autocad.sample_doc()
        msp = doc.modelspace()
        doc.layers.add("XREF-RAC")
        msp.add_line((2000, 2000), (3000, 3000), dxfattribs={"layer": "XREF-RAC"})
        msp.add_line((2000, 2000), (3000, 2000), dxfattribs={"layer": "0"})
        msp.add_circle((3000, 3000), 100, dxfattribs={"layer": "D-O-CAM"})                       # ổ cắm đã phá khối
        doc.blocks.new("O-CAM-LA").add_circle((0, 0), 50)
        msp.add_blockref("O-CAM-LA", (3500, 3000), dxfattribs={"layer": "D-O-CAM"})               # block lạ
        msp.add_lwpolyline([(1000, 7000), (2000, 7000), (2000, 8000)], dxfattribs={"layer": "KL-SAN"})  # chưa kín
        msp.add_lwpolyline([(20000, 0), (21000, 0), (21000, 1000), (20000, 1000)], close=True, dxfattribs={"layer": "KL-SAN"})
        msp.add_hatch(dxfattribs={"layer": "KL-SAN"}).paths.add_polyline_path([(0, 0), (1, 0), (1, 1)])
        parsed = dxf_read.read_doc(doc)
        dropped = {(d["layer"], d["reason"]): d["count"] for d in parsed["dropped"]}
        self.assertEqual(dropped, {
            ("XREF-RAC", S.REASONS["layer"]): 1, ("0", S.REASONS["layer"]): 1,
            ("D-O-CAM", S.REASONS["explode"]): 1, ("D-O-CAM", S.REASONS["block"]): 1,
            ("KL-SAN", S.REASONS["open"]): 1, ("KL-SAN", S.REASONS["type"]): 1, ("KL-SAN", "Nằm ngoài khung tầng"): 1})
        self.assertEqual(len(parsed["floors"][0]["elements"]), 10)   # phần đúng chuẩn không đổi

    def test_floor_titles(self):
        cases = {"MẶT BẰNG TẦNG 2": [2], "Mặt bằng lát sàn tầng 2, 3 và 4": [2, 3, 4], "TẦNG 2-5": [2, 3, 4, 5],
                 "MẶT BẰNG MÁI": ["M"], dxf_read.clean("M\\U+1EB6T B\\U+1EB0NG T\\U+1EA6NG 3"): [3], "MẶT ĐỨNG TRỤC 1-5": []}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(dxf_read.parse_floors(text), expected)

    def test_frame_for_several_floors_and_frame_without_title(self):
        doc = autocad.sample_doc()
        title = next(e for e in doc.modelspace() if e.dxf.layer == "CH-TEXT")
        title.dxf.text = "MẶT BẰNG TẦNG 2-3"
        msp = doc.modelspace()
        msp.add_lwpolyline([(20000, 0), (30000, 0), (30000, 9000), (20000, 9000)], close=True, dxfattribs={"layer": "CH-KHUNG"})
        msp.add_line((21000, 1000), (25000, 1000), dxfattribs={"layer": "KL-XAY-110"})
        parsed = dxf_read.read_doc(doc)
        self.assertEqual([f["name"] for f in parsed["floors"]], ["Tầng 2", "Tầng 3"])
        self.assertIn(("CH-KHUNG", "Khung chưa có tiêu đề tầng"), {(d["layer"], d["reason"]) for d in parsed["dropped"]})
        codes = [r.code for r in importer.plan(Project.objects.create(code="P", name="P"), parsed, {"2": 3000, "3": 3000})["rows"]]
        self.assertIn("T2.1", codes)
        self.assertIn("T3.1", codes)
        self.assertIn("S3.2", codes)

    def test_no_frame_is_a_clear_error(self):
        doc = ezdxf.new()
        doc.modelspace().add_line((0, 0), (1, 1), dxfattribs={"layer": "KL-XAY-110"})
        with self.assertRaisesMessage(dxf_read.DxfError, "chưa có khung tầng"):
            dxf_read.read_doc(doc)


class AutocadKitTests(TestCase):
    def test_lisp_parses_and_only_calls_known_functions(self):
        source = autocad.lisp()
        forms = lisp_check.parse(source)
        unknown = lisp_check.heads(forms) - lisp_check.defined(forms) - BUILTINS
        self.assertEqual(unknown, set())
        self.assertNotIn("%%", source)

    def test_lisp_dcl_and_standard_agree(self):
        source = autocad.lisp()
        keys = [key for _, buttons in S.BUTTONS for key, _, _ in buttons]
        self.assertEqual(len(keys), len(set(keys)))
        for key in keys:
            self.assertIn(f'((= k "{key}")', source)
            self.assertIn(f'key = \\"{key}\\"', source)          # nút có trong cả hai bản DCL
        self.assertEqual(source.count('key = \\"KHUNG\\"'), 2)
        for layer in S.LAYERS:
            self.assertIn(f'("{layer.name}" {layer.color} "{layer.kind}")', source)
        for block in S.BLOCKS:
            self.assertIn(f'(tblsearch "BLOCK" "{block.name}")', source)
        for reason in S.REASONS.values():
            self.assertIn(autocad.lisp_str(reason), source)
        for command in ("c:TEND ", "c:TENDKIEMTRA", "c:TENDXUAT", "c:TENDCAIDAT", "c:TENDKHONGDAU", "c:TENDXAY110", "c:TENDPHONG"):
            self.assertIn(f"(defun {command}", source)
        # chữ ghi vào bản vẽ không có ký tự ngoài ASCII trong mã LISP chạy (dùng \U+XXXX)
        dispatch = source[source.find("(defun tend:run-key"):source.find("(defun c:TEND (")]
        self.assertTrue(all(ord(c) < 128 for c in dispatch), dispatch)
        self.assertIn('"Tr\\\\U+1EA7n"', dispatch)

    def test_ascii_dialog_for_old_autocad(self):
        self.assertTrue(all(ord(c) < 128 for line in autocad.dcl(False) for c in line))
        self.assertIn('label = "Khung tầng";', "\n".join(autocad.dcl(True)))

    def test_template_has_every_layer_and_block_and_kit_zip(self):
        doc = autocad.template_doc()
        for layer in S.LAYERS:
            self.assertEqual(doc.layers.get(layer.name).color, layer.color)
        for block in S.BLOCKS:
            self.assertIn(block.name, doc.blocks)
        with zipfile.ZipFile(io.BytesIO(autocad.kit_zip())) as archive:
            names = sorted(archive.namelist())
            self.assertEqual(names, sorted(f"Bo-Tend-AutoCAD/{n}" for n in ("tend.lsp", "Tend-TCD.dxf", "Mau-Tend.dxf", "HUONG-DAN.txt")))
            sample = Path(TMP) / "mau-tu-bo.dxf"
            sample.write_bytes(archive.read("Bo-Tend-AutoCAD/Mau-Tend.dxf"))
        self.assertEqual(dxf_read.read(sample)["dropped"], [])   # tệp mẫu trong bộ nhập sạch


@override_settings(MEDIA_ROOT=TMP)
class ImportFlowTests(TestCase):
    def setUp(self):
        self.users = {role: User.objects.create_user(f"u-{role}", password="mat-khau-thu-123", role=role) for role in Role.values}
        self.project = Project.objects.create(code="TN-22", name="Thiên Nga 22")
        for role, user in self.users.items():
            if role != Role.DIRECTOR:
                Member.objects.create(project=self.project, user=user)
        old = Project.objects.create(code="CU", name="Công trình cũ")
        Item.objects.create(project=old, code="S1.1", work="Lát nền Gạch 600×600", unit="m2", design_quantity=1,
                            sale_price=350_000, cost_price=250_000)

    def dxf(self, doc, name="mat-bang_tend.dxf"):
        path = Path(TMP) / f"{uuid4().hex}-{name}"
        doc.saveas(path)
        return path

    def upload(self, user, doc):
        self.client.force_login(user)
        with open(self.dxf(doc), "rb") as handle:
            return self.client.post(reverse("drawings_upload", args=["TN-22"]), {"tep": handle})

    def test_upload_preview_confirm_creates_items_drawing_and_log(self):
        designer = self.users[Role.DESIGNER]
        response = self.upload(designer, autocad.sample_doc())
        self.assertEqual(response.status_code, 302)
        preview = response["Location"]
        page = self.client.get(preview)
        self.assertContains(page, "Mới 10")
        self.assertContains(page, "giữa PHÒNG KHÁCH và WC")
        self.assertFalse(Item.objects.filter(project=self.project).exists())        # xem trước không ghi
        self.assertContains(self.client.get(preview, {"cao_1": "3.000"}), "7,02 m2")  # 3 × 3 − 1,98
        response = self.client.post(preview, {"cao_1": "3300"})
        self.assertRedirects(response, reverse("drawings_view", args=["TN-22"]))
        items = {i.code: i for i in Item.objects.filter(project=self.project)}
        self.assertEqual(sorted(items), ["C1.1", "DD1.1", "OP1.1", "S1.1", "S1.2", "T1.1", "T1.2", "TB1.1", "TB1.2", "TR1.1"])
        self.assertEqual(items["S1.1"].location, "PHÒNG KHÁCH")
        self.assertEqual((items["S1.1"].sale_price, items["S1.1"].cost_price), (350_000, 250_000))   # đơn giá công trình gần nhất
        self.assertIsNone(items["S1.2"].sale_price)
        self.assertEqual(items["T1.2"].design_quantity, Decimal("7.920"))
        drawing = Drawing.objects.get(project=self.project)
        self.assertEqual((drawing.floor, drawing.version, drawing.wall_height), ("Tầng 1", 1, 3300))
        self.assertEqual(AuditLog.objects.filter(table="drawings.Item", action="create").count(), 10)
        self.assertEqual(AuditLog.objects.filter(table="drawings.Drawing").count(), 1)
        self.assertEqual(self.client.get(preview).status_code, 404)                 # bản tạm đã dọn

    def test_reimport_keeps_codes_and_reported_items(self):
        importer.apply(self.project, dxf_read.read_doc(autocad.sample_doc(), "v1"), {"1": 3300}, self.users[Role.DIRECTOR])
        wc = Item.objects.get(project=self.project, code="S1.2")
        report = Report.objects.create(project=self.project, author=self.users[Role.SUPERVISOR], work_date=timezone.localdate(),
                                       client_key=uuid4())
        ReportLine.objects.create(report=report, item=wc, percent=50)
        doc = autocad.sample_doc()
        msp = doc.modelspace()
        for e in list(msp):
            if e.dxf.layer == "KL-SAN" and e.dxftype() == "LWPOLYLINE" and e.get_points("xy")[0] == (7000, 1000):
                msp.delete_entity(e)                                    # bỏ phòng WC đã có báo cáo
            elif e.dxf.layer == "KL-XAY-110":
                e.dxf.end = (7000, 4500)                                # tường dài thêm 0,5 m
        msp.add_lwpolyline([(1000, 6500), (4000, 6500), (4000, 8000), (1000, 8000)], close=True, dxfattribs={"layer": "KL-SAN"})
        msp.add_text("KHO", height=250, dxfattribs={"layer": "CH-PHONG"}).set_placement((2500, 7200))
        result = importer.apply(self.project, dxf_read.read_doc(doc, "v2"), {"1": 3300}, self.users[Role.DESIGNER])
        items = {i.code: i for i in Item.objects.filter(project=self.project)}
        self.assertEqual(items["T1.2"].design_quantity, Decimal("9.570"))      # 3,5 × 3,3 − 1,98, vẫn mã T1.2
        self.assertTrue(items["S1.2"].active)                                    # có báo cáo: giữ
        self.assertEqual([i.code for i in result["kept"]], ["S1.2"])
        self.assertEqual(items["S1.3"].location, "KHO")                          # phòng mới: mã tiếp theo
        self.assertEqual(Drawing.objects.get(project=self.project).version, 2)
        self.assertTrue(AuditLog.objects.filter(table="drawings.Item", object_id=str(items["T1.2"].pk), action="update").exists())
        # tệp không có phòng cũ chưa báo cáo → thôi dùng, không xoá
        doc3 = autocad.sample_doc()
        for e in list(doc3.modelspace()):
            if e.dxf.layer == "KL-TRAN":
                doc3.modelspace().delete_entity(e)
        importer.apply(self.project, dxf_read.read_doc(doc3, "v3"), {"1": 3300}, self.users[Role.DESIGNER])
        self.assertFalse(Item.objects.get(code="TR1.1").active)

    def test_only_director_and_designer_import(self):
        for role in (Role.SUPERVISOR, Role.INTERNAL_ACCOUNTANT, Role.FINANCE_ACCOUNTANT):
            with self.subTest(role=role):
                self.client.force_login(self.users[role])
                self.assertEqual(self.client.get(reverse("drawings_upload", args=["TN-22"])).status_code, 403)
                self.assertEqual(self.client.get(reverse("drawings_kit")).status_code, 403)
        self.assertEqual(self.upload(self.users[Role.DIRECTOR], autocad.sample_doc()).status_code, 302)

    def test_bad_file_message(self):
        self.client.force_login(self.users[Role.DESIGNER])
        bad = Path(TMP) / "hong.dxf"
        bad.write_text("không phải dxf")
        with open(bad, "rb") as handle:
            response = self.client.post(reverse("drawings_upload", args=["TN-22"]), {"tep": handle})
        self.assertContains(response, "Không đọc được tệp DXF")


@override_settings(MEDIA_ROOT=TMP)
class ViewAndQuantityTests(TestCase):
    def setUp(self):
        self.users = {role: User.objects.create_user(f"v-{role}", password="mat-khau-thu-123", role=role) for role in Role.values}
        self.project = Project.objects.create(code="TN-22", name="Thiên Nga 22")
        for role, user in self.users.items():
            if role != Role.DIRECTOR:
                Member.objects.create(project=self.project, user=user)
        importer.apply(self.project, dxf_read.read_doc(autocad.sample_doc(), "v1"), {"1": 3300}, self.users[Role.DIRECTOR])
        Item.objects.filter(project=self.project).update(sale_price=100_000, cost_price=70_000)
        report = Report.objects.create(project=self.project, author=self.users[Role.SUPERVISOR], work_date=timezone.localdate(),
                                       client_key=uuid4())
        ReportLine.objects.create(report=report, item=Item.objects.get(code="S1.1"), percent=100)
        ReportLine.objects.create(report=report, item=Item.objects.get(code="T1.2"), percent=40)

    def test_view_shows_status_by_symbol_and_class(self):
        self.client.force_login(self.users[Role.SUPERVISOR])
        html = self.client.get(reverse("drawings_view", args=["TN-22"])).content.decode()
        self.assertIn('class="it done polygon g-san"', html)
        self.assertIn("✓ S1.1", html)
        self.assertIn("◐ T1.2", html)
        self.assertIn("○ S1.2", html)
        self.assertIn('"percent": "40"', html)
        self.assertNotIn("₫", html)
        self.assertNotIn("Nhập bản vẽ DXF", html)

    def test_prices_follow_role(self):
        expected = {Role.SUPERVISOR: (False, False), Role.DESIGNER: (False, False), Role.INTERNAL_ACCOUNTANT: (False, True),
                    Role.FINANCE_ACCOUNTANT: (True, True), Role.DIRECTOR: (True, True)}
        for role, (sale, cost) in expected.items():
            with self.subTest(role=role):
                self.client.force_login(self.users[role])
                html = self.client.get(reverse("drawings_quantities", args=["TN-22"])).content.decode()
                self.assertEqual("Đơn giá bán" in html, sale)
                self.assertEqual("Đơn giá vốn" in html, cost)
                self.assertIn("100,0%", html)
                book = self.client.get(reverse("drawings_quantities", args=["TN-22"]), {"tai": "xlsx"})
                self.assertEqual(book["Content-Type"].split(";")[0], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                from openpyxl import load_workbook
                head = [c.value for c in load_workbook(io.BytesIO(book.content)).active[2]]
                self.assertEqual("Đơn giá bán (₫)" in head, sale)
                self.assertEqual("Đơn giá vốn (₫)" in head, cost)

    def test_other_project_hidden(self):
        Project.objects.create(code="KHAC", name="Khác")
        self.client.force_login(self.users[Role.SUPERVISOR])
        self.assertEqual(self.client.get(reverse("drawings_view", args=["KHAC"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("drawings_quantities", args=["KHAC"])).status_code, 404)
