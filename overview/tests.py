"""Tổng quan năm câu hỏi trên dữ liệu tính tay; quyền theo vai; GET không ghi."""
import re
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from core.models import AuditLog, Role, User
from drawings.models import Item
from field.models import Attendance, Report, ReportLine, Worker
from money.models import Entry, Kind
from projects.models import Member, Project

from .compute import answers

M = Decimal(1_000_000)
WRITE = re.compile(r"\s*(INSERT|UPDATE|DELETE)\b", re.I)


class Fixture:
    def setUp(self):
        self.today = timezone.localdate()
        self.users = {role: User.objects.create_user(f"u-{role}", password="mat-khau-thu-123", role=role) for role in Role.values}
        self.director = self.users[Role.DIRECTOR]
        self.site = self.users[Role.SUPERVISOR]
        self.project = Project.objects.create(code="TN-22", name="Thiên Nga 22", start_date=self.today - timedelta(days=10),
                                              handover_date=self.today + timedelta(days=20))
        for role, user in self.users.items():
            if role != Role.DIRECTOR:
                Member.objects.create(project=self.project, user=user)
        self.wall = Item.objects.create(project=self.project, code="T2.5", floor="Tầng 2", work="Xây tường", unit="m2",
                                        design_quantity=100, sale_price=1_000_000, cost_price=600_000,
                                        plan_start=self.today - timedelta(days=10), plan_end=self.today + timedelta(days=10))
        self.slab = Item.objects.create(project=self.project, code="S2.1", floor="Tầng 2", work="Đổ sàn", unit="m3",
                                        design_quantity=10, sale_price=2_000_000, cost_price=1_500_000,
                                        plan_start=self.today - timedelta(days=5), plan_end=self.today - timedelta(days=1))
        report = Report.objects.create(project=self.project, author=self.site, work_date=self.today - timedelta(days=5),
                                       client_key=uuid4())
        ReportLine.objects.create(report=report, item=self.wall, percent=50)
        hung = Worker.objects.create(name="Hùng", team="Tổ xây", kind="tho", day_rate=500_000)
        lan = Worker.objects.create(name="Lan", team="Tổ xây", kind="phu")
        for worker, days in ((hung, 1), (lan, Decimal("0.5"))):
            Attendance.objects.create(project=self.project, work_date=self.today - timedelta(days=1), worker=worker, days=days,
                                      recorded_by=self.site)
        Attendance.objects.create(project=self.project, work_date=self.today - timedelta(days=1), staff=self.site, recorded_by=self.site)

        def money(kind, amount, party):
            Entry.objects.create(kind=kind, project=self.project, date=self.today, party=party, content="thử",
                                 amount=amount, entered_by=self.users[Role.INTERNAL_ACCOUNTANT], client_key=uuid4())
        money(Kind.CUSTOMER_RECEIPT, 20 * M, "Chủ nhà")
        money(Kind.OTHER_RECEIPT, 5 * M, "Bán phế liệu")
        money(Kind.SUPPLIER_GOODS, 5 * M, "NCC Cát")
        money(Kind.SUPPLIER_PAYMENT, 2 * M, "NCC Cát")
        money(Kind.TEAM_WORK, 25 * M, "Tổ xây")
        money(Kind.TEAM_PAYMENT, 10 * M, "Tổ xây")
        money(Kind.OTHER_EXPENSE, 1 * M, "Điện nước")


class OverviewNumbersTests(Fixture, TestCase):
    def test_five_answers_from_raw_data(self):
        data = answers(self.director, self.project, self.today)
        p = data["progress"]
        self.assertEqual(p["actual"], Decimal("41.7"))      # 50% × 100tr / 120tr
        self.assertEqual(p["planned"], Decimal("60.3"))     # tường 11/21 ngày, sàn quá hạn 100%
        self.assertEqual(p["forecast"], self.today + timedelta(days=14))
        self.assertEqual(p["late"], -6)
        self.assertEqual([x["code"] for x in p["overdue"]], ["S2.1"])
        self.assertEqual(data["labor"]["total"], Decimal("2.5"))
        self.assertEqual(data["labor"]["cost"], 500_000)
        self.assertEqual(data["labor"]["missing_rates"], ["Lan"])
        pay = data["payables"]
        self.assertEqual((pay["supplier_debt"], pay["team_debt"], pay["labor_debt"]), (3 * M, 15 * M, Decimal(500_000)))
        self.assertEqual(data["customer"], {"contract": 120 * M, "received": 20 * M, "remaining": 100 * M})
        pro = data["profit"]
        # 120 − (5 hàng + 25 khoán + 0,5 công + 1 chi khác) − (60 × 50% + 15) = 43,5 triệu
        self.assertEqual((pro["incurred"], pro["remaining_cost"], pro["forecast"]), (Decimal("31500000"), 45 * M, Decimal("43500000")))

    def test_missing_data_named_not_zero(self):
        empty = Project.objects.create(code="TRONG", name="Chưa nhập bản vẽ")
        data = answers(self.director, empty, self.today)
        self.assertIsNone(data["progress"]["actual"])
        self.assertIn("Chưa có chi tiết", data["progress"]["missing"])
        self.assertIsNone(data["customer"]["remaining"])
        self.assertIsNone(data["profit"]["forecast"])
        Item.objects.filter(pk=self.slab.pk).update(cost_price=None)
        self.assertEqual(answers(self.director, self.project, self.today)["profit"]["missing_cost"], 1)
        self.assertIsNone(answers(self.director, self.project, self.today)["profit"]["forecast"])

    def test_decrease_report_lowers_progress_and_quantity_counts_as_cumulative(self):
        later = Report.objects.create(project=self.project, author=self.site, work_date=self.today, client_key=uuid4())
        ReportLine.objects.create(report=later, item=self.wall, percent=40, decrease_reason="Đập đi xây lại")
        ReportLine.objects.create(report=later, item=self.slab, quantity=5)
        p = answers(self.director, self.project, self.today)["progress"]
        self.assertEqual(p["actual"], Decimal("41.7"))       # (40 + 20×50%) / 120


class OverviewPageTests(Fixture, TestCase):
    def page(self, user, *args):
        self.client.force_login(user)
        response = self.client.get(reverse("overview_project", args=args) if args else reverse("overview"))
        self.assertEqual(response.status_code, 200)
        return response

    def test_each_role_sees_only_its_money(self):
        expected = {Role.SUPERVISOR: set(), Role.DESIGNER: set(), Role.INTERNAL_ACCOUNTANT: {"payables"},
                    Role.FINANCE_ACCOUNTANT: {"payables", "customer", "profit"}, Role.DIRECTOR: {"payables", "customer", "profit"}}
        for role, sections in expected.items():
            with self.subTest(role=role):
                response = self.page(self.users[role], "TN-22")
                self.assertEqual(set(response.context["data"]) & {"payables", "customer", "profit"}, sections)
                html = response.content.decode()
                if not sections:
                    self.assertNotIn("₫", html)
                if "profit" not in sections:
                    self.assertNotIn("Lãi lỗ", html)
                if role == Role.SUPERVISOR:
                    self.assertNotIn("Chi phí công nhật", html)

    def test_company_page_text_and_vietnamese_numbers(self):
        html = self.page(self.director).content.decode()
        for text in ("Tổng quan", "41,7%", "▼ chậm", "3.000.000 ₫", "100.000.000 ₫", "Lãi 43.500.000 ₫"):
            self.assertIn(text, html)

    def test_project_outside_assignment_is_404_and_hidden(self):
        Project.objects.create(code="KHAC", name="Không giao")
        self.client.force_login(self.site)
        self.assertEqual(self.client.get(reverse("overview_project", args=["KHAC"])).status_code, 404)
        self.assertNotIn("KHAC", self.client.get(reverse("overview")).content.decode())

    def test_get_writes_nothing_and_login_required(self):
        self.assertEqual(self.client.get(reverse("overview")).status_code, 302)
        self.client.force_login(self.director)
        with CaptureQueriesContext(connection) as captured:
            self.client.get(reverse("overview"))
            self.client.get(reverse("overview_project", args=["TN-22"]))
        self.assertEqual([q["sql"] for q in captured if WRITE.match(q["sql"]) and "django_session" not in q["sql"]], [])

    def test_tabs_by_role(self):
        tabs = lambda user: [t["label"] for t in self.page(user).context["tabs"]]
        self.assertEqual(tabs(self.site), ["Tổng quan", "Bản vẽ", "Tài khoản"])
        self.assertEqual(tabs(self.users[Role.INTERNAL_ACCOUNTANT]), ["Tổng quan", "Báo cáo", "Tài khoản"])


class AuditTests(Fixture, TestCase):
    def test_record_save_logs_before_after_and_log_is_append_only(self):
        from core.audit import record_save
        self.wall.plan_end = self.today + timedelta(days=12)
        record_save(self.wall, self.director, "Lùi kế hoạch", channel="web")
        log = AuditLog.objects.get()
        self.assertEqual((log.table, log.action, log.reason), ("drawings.Item", "update", "Lùi kế hoạch"))
        self.assertNotEqual(log.before["plan_end"], log.after["plan_end"])
        with self.assertRaises(RuntimeError):
            log.save()
        with self.assertRaises(RuntimeError):
            log.delete()
