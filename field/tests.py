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
