"""Bảng công tháng, trang Thợ, ảnh có kiểm quyền, chi phí công nhật chỉ tính thợ công nhật."""
import io
import tempfile
from datetime import date
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook
from PIL import Image

from core.models import AuditLog, Role, User
from drawings.models import Item
from overview.compute import labor_cost
from projects.models import Member, Project

from . import services
from .models import Attendance, Photo, Report, Worker
from .views import timesheet

TMP = tempfile.mkdtemp(prefix="tend-field-")
DAY = date(2026, 11, 3)


@override_settings(MEDIA_ROOT=TMP)
class FieldWebTests(TestCase):
    def setUp(self):
        self.users = {r: User.objects.create_user(f"f-{r}", password="x-123-abcd", role=r) for r in Role.values}
        self.site = self.users[Role.SUPERVISOR]
        self.a = Project.objects.create(code="A", name="Công trình A")
        self.b = Project.objects.create(code="B", name="Công trình B")
        for user in self.users.values():
            if user.role != Role.DIRECTOR:
                Member.objects.create(project=self.a, user=user)
        Member.objects.create(project=self.b, user=self.site)
        self.daily = Worker.objects.create(name="Hùng", team="xây", day_rate=500_000)
        self.team = Worker.objects.create(name="Bình", team="điện", pay=Worker.Pay.TEAM, day_rate=400_000)
        self.norate = Worker.objects.create(name="Lan", team="xây", kind="phu")
        rows = [(self.a, DAY, self.daily, 1), (self.a, date(2026, 11, 4), self.daily, Decimal("0.5")), (self.a, DAY, self.team, 1),
                (self.a, DAY, self.norate, 1)]
        for project, day, worker, days in rows:
            Attendance.objects.create(project=project, work_date=day, worker=worker, days=days, recorded_by=self.site)
        for project in (self.a, self.b):   # giám sát đi hai công trình cùng ngày
            Attendance.objects.create(project=project, work_date=DAY, staff=self.site, days=1, recorded_by=self.site)

    def test_timesheet_numbers(self):
        data = timesheet(self.users[Role.DIRECTOR], date(2026, 11, 1))
        rows = {r["name"]: r for r in data["rows"]}
        self.assertEqual(rows["Hùng"]["total"], Decimal("1.5"))
        self.assertEqual(rows["Hùng"]["cost"], Decimal("750000"))
        self.assertNotIn("cost", rows["Bình"])                    # tổ khoán: không tính tiền công nhật
        self.assertEqual(data["cost"], Decimal("750000"))
        self.assertEqual(data["missing"], ["Lan"])
        site = rows[str(self.site)]
        self.assertEqual((site["presence"], site["workdays"], site["projects"]), (2, 1, "A, B"))
        self.assertEqual(labor_cost(self.a), (Decimal("750000.00"), ["Lan"]))

    def test_supervisor_sees_days_not_money(self):
        self.client.force_login(self.site)
        html = self.client.get(reverse("field_attendance"), {"thang": "2026-11"}).content.decode()
        self.assertIn("Hùng", html)
        self.assertNotIn("₫", html)
        book = load_workbook(io.BytesIO(self.client.get(reverse("field_attendance"), {"thang": "2026-11", "tai": "xlsx"}).content))
        head = [c.value for c in book.active[2]]
        self.assertNotIn("Đơn giá (₫)", head)
        self.client.force_login(self.users[Role.INTERNAL_ACCOUNTANT])
        html = self.client.get(reverse("field_attendance"), {"thang": "2026-11", "cong_trinh": "A"}).content.decode()
        self.assertIn("750.000 ₫", html)
        self.assertIn("khoán", html)

    def test_designer_sees_only_assigned_projects(self):
        Member.objects.filter(user=self.users[Role.DESIGNER]).delete()
        self.assertEqual(timesheet(self.users[Role.DESIGNER], date(2026, 11, 1))["rows"], [])

    def test_workers_page_edit_rights(self):
        url = reverse("field_workers")
        self.client.force_login(self.site)
        self.assertNotContains(self.client.get(url), "500.000")
        self.assertEqual(self.client.post(url, {"id": self.daily.pk, "day_rate": "1"}).status_code, 403)
        self.client.force_login(self.users[Role.FINANCE_ACCOUNTANT])
        self.assertContains(self.client.get(url), "500.000 ₫")
        self.assertEqual(self.client.post(url, {"id": self.daily.pk, "day_rate": "1"}).status_code, 403)
        self.client.force_login(self.users[Role.DESIGNER])
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.users[Role.INTERNAL_ACCOUNTANT])
        self.client.post(url, {"id": self.norate.pk, "day_rate": "350.000", "pay": "cong_nhat", "kind": "phu", "team": "xây", "active": "1"})
        self.norate.refresh_from_db()
        self.assertEqual(self.norate.day_rate, Decimal("350000"))
        log = AuditLog.objects.get(table="field.Worker", object_id=str(self.norate.pk))
        self.assertEqual((log.before["day_rate"], log.after["day_rate"], log.channel), (None, "350000", "web"))

    def test_photo_requires_project_access(self):
        report = Report.objects.create(project=self.b, author=self.site, work_date=DAY, client_key=uuid4())
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "JPEG")
        photo = Photo(report=report)
        photo.image.save("a.jpg", ContentFile(buffer.getvalue()))
        self.client.force_login(self.users[Role.DESIGNER])
        self.assertEqual(self.client.get(reverse("field_photo", args=[photo.pk])).status_code, 404)
        self.client.force_login(self.site)
        self.assertEqual(self.client.get(reverse("field_photo", args=[photo.pk]))["Content-Type"], "image/jpeg")


class ServiceTests(TestCase):
    def setUp(self):
        self.site = User.objects.create_user("gs", password="x-123-abcd", role=Role.SUPERVISOR)
        self.project = Project.objects.create(code="A", name="A")
        Member.objects.create(project=self.project, user=self.site)
        self.item = Item.objects.create(project=self.project, code="T1.1", work="Xây", unit="m2", design_quantity=10)

    def test_days_from_hours_and_permissions(self):
        self.assertEqual(services.days_from(hours=4), Decimal("0.50"))
        self.assertEqual(services.days_from(hours=10), Decimal("1.25"))
        self.assertEqual(services.days_from(), Decimal("1.00"))
        other = User.objects.create_user("gs2", password="x-123-abcd", role=Role.SUPERVISOR)
        with self.assertRaises(PermissionDenied):
            services.submit_report(other, self.project, {"note": "x"}, client_key=uuid4())
        with self.assertRaises(PermissionDenied):
            services.set_worker(self.site, Worker.objects.create(name="X"), day_rate=1)

    def test_resend_same_key_and_decrease_rule(self):
        key = uuid4()
        payload = {"lines": [{"item_id": self.item.pk, "quantity": 6}]}
        first = services.submit_report(self.site, self.project, payload, client_key=key)
        again = services.submit_report(self.site, self.project, payload, client_key=key)
        self.assertEqual((first.pk, Report.objects.count()), (again.pk, 1))
        with self.assertRaisesMessage(ValidationError, "cần lý do"):
            services.submit_report(self.site, self.project, {"lines": [{"item_id": self.item.pk, "percent": 50}]}, client_key=uuid4())
        self.assertEqual(Report.objects.count(), 1)                 # cả giao dịch không ghi
        services.submit_report(self.site, self.project, {"lines": [{"item_id": self.item.pk, "percent": 50, "reason": "đo lại"}]},
                               client_key=uuid4())
        self.assertEqual(Attendance.objects.filter(staff=self.site).count(), 1)


class StaffCodeRateSalaryTests(TestCase):
    """Nhân sự: mã NS cố định; đơn giá theo ngày hiệu lực; lương tháng chia theo ngày có mặt; bot khớp theo mã."""

    def setUp(self):
        self.director = User.objects.create_user("gd", password="x-123-abcd-xyz", role=Role.DIRECTOR)
        self.a = Project.objects.create(code="TN-22", name="A")
        self.b = Project.objects.create(code="BH-01", name="B")

    def day(self, project, worker, d, days=1, staff=None):
        Attendance.objects.create(project=project, work_date=d, worker=worker, staff=staff, days=Decimal(days),
                                  recorded_by=self.director)

    def test_codes_sequential_fixed_and_rename_logged(self):
        one = services.add_worker(self.director, "Thắng", kind="phu")
        two = services.add_worker(self.director, "Thuận")
        self.assertEqual((one.code, two.code), ("NS-0001", "NS-0002"))
        services.rename_worker(self.director, one, "Nguyễn Văn Thắng")
        one.refresh_from_db()
        self.assertEqual((one.name, one.code), ("Nguyễn Văn Thắng", "NS-0001"))
        self.assertTrue(AuditLog.objects.filter(table="field.Worker", reason__startswith="Đổi họ tên NS-0001").exists())
        Worker.objects.filter(pk=two.pk).update(active=False)                      # người nghỉ: mã không dùng lại
        self.assertEqual(services.add_worker(self.director, "Tâm").code, "NS-0003")
        from .workers import match
        self.assertEqual(match("ns1")["worker"], one)
        self.assertEqual(match("NS-0003")["worker"].name, "Tâm")

    def test_rate_change_keeps_past_cost(self):
        w = services.add_worker(self.director, "Thắng")
        self.day(self.a, w, date(2026, 10, 10))
        services.set_rate(self.director, w, 300_000, pay=Worker.Pay.DAILY)     # giá đầu tiên: áp cho cả công trước
        self.day(self.a, w, date(2026, 11, 10))
        services.set_rate(self.director, w, 400_000, start=date(2026, 11, 15))
        self.day(self.a, w, date(2026, 11, 16))
        cost, missing = labor_cost(self.a)
        self.assertEqual((cost, missing), (Decimal(1_000_000), []))           # 300k + 300k + 400k
        w.refresh_from_db()
        self.assertEqual(w.rates.count(), 2)

    def test_monthly_salary_split_by_days_present(self):
        from overview.compute import profit, salary_cost
        sup = User.objects.create_user("gs", password="x-123-abcd-xyz", role=Role.SUPERVISOR)
        profile = services.ensure_profile(self.director, sup)
        self.assertEqual((profile.pay, profile.user, profile.code), (Worker.Pay.MONTHLY, sup, "NS-0001"))
        services.set_rate(self.director, profile, 12_000_000, pay=Worker.Pay.MONTHLY)
        for d in range(1, 4):                                                   # 3 ngày ở A (theo báo cáo: tài khoản)
            self.day(self.a, None, date(2026, 11, d), staff=sup)
        self.day(self.b, None, date(2026, 11, 5), staff=sup)                    # 1 ngày ở B
        self.assertEqual(salary_cost(self.a)[0], Decimal(9_000_000))
        self.assertEqual(salary_cost(self.b)[0], Decimal(3_000_000))
        self.assertEqual(profit(self.a, date(2026, 11, 30))["parts"]["salary"], Decimal(9_000_000))
        self.assertEqual(labor_cost(self.a)[0], Decimal(0))                    # lương tháng không tính vào công nhật

    def test_supervisor_account_gets_profile(self):
        from core import accounts
        person = accounts.create(self.director, "gs.moi", "Nam", "", Role.SUPERVISOR, "", "mat-khau-tam-123")
        self.assertTrue(Worker.objects.filter(user=person, pay=Worker.Pay.MONTHLY, trade__name="Giám sát").exists())
