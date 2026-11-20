"""Báo cáo Excel: số tính tay, phần tiền theo vai, tháng chưa tới, lãi lỗ chỉ vai được xem."""
import io
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.test import TestCase
from django.urls import reverse
from openpyxl import load_workbook

from core.models import Role
from field.models import Report, ReportLine

from .tests import Fixture

M = 1_000_000


def book(response):
    return load_workbook(io.BytesIO(response.content))


def values(sheet):
    return [[c.value for c in row] for row in sheet.iter_rows()]


def flat(sheet):
    return [v for row in values(sheet) for v in row if v is not None]


class ExportTests(Fixture, TestCase):
    def get(self, user, name, **params):
        self.client.force_login(user)
        return self.client.get(reverse(name), params)

    def test_overview_book_sections_by_role(self):
        director = book(self.get(self.director, "reports_overview_xlsx"))
        self.assertEqual(director.sheetnames, ["Tổng quan", "TN-22"])
        head = values(director["Tổng quan"])[3]
        self.assertIn("Lãi dự báo", head)
        row = values(director["Tổng quan"])[4]
        self.assertAlmostEqual(row[1], 0.417)
        self.assertEqual(row[head.index("Lãi dự báo")], 43.5 * M)
        site = book(self.get(self.site, "reports_overview_xlsx"))
        site_head = values(site["Tổng quan"])[3]
        self.assertNotIn("Nợ NCC", site_head)
        self.assertFalse(any(isinstance(v, str) and "Nhà cung cấp" in v for v in flat(site["TN-22"])))
        internal = values(book(self.get(self.users[Role.INTERNAL_ACCOUNTANT], "reports_overview_xlsx"))["Tổng quan"])[3]
        self.assertIn("Nợ NCC", internal)
        self.assertNotIn("Khách còn phải trả", internal)

    def test_month_book_numbers(self):
        later = Report.objects.create(project=self.project, author=self.site, work_date=self.today, client_key=uuid4())
        ReportLine.objects.create(report=later, item=self.wall, percent=80)
        first = self.today.replace(day=1)
        response = self.get(self.director, "reports_month_xlsx", cong_trinh="TN-22", thang=f"{first:%Y-%m}")
        workbook = book(response)
        self.assertEqual(workbook.sheetnames, ["Tóm tắt", "Tiến độ", "Báo cáo ngày", "Việc ngoài bản vẽ", "Kế hoạch", "Chấm công", "Thu chi"])
        rows = {r[1]: r for r in values(workbook["Tiến độ"])[4:] if r[1]}
        wall = rows["T2.5"]
        start_pct = 0.0 if (self.today - timedelta(days=5)) >= first else 0.5   # 50% báo ngày −5: trong tháng thì đầu tháng là 0
        self.assertAlmostEqual(wall[6], start_pct)
        self.assertAlmostEqual(wall[7], 0.8)
        expected_qty = 100 * (0.8 - start_pct)
        self.assertAlmostEqual(wall[8], expected_qty)
        self.assertAlmostEqual(wall[9], expected_qty * 1_000_000)          # giá bán 1 triệu/m2
        self.assertAlmostEqual(wall[10], expected_qty * 600_000)           # giá vốn
        people = {r[0]: r for r in values(workbook["Chấm công"])[4:] if r[0]}
        if (self.today - timedelta(days=1)) >= first:
            self.assertEqual(people["Hùng"][4], 1)
            self.assertEqual(people["Hùng"][7], 500_000)
        site = book(self.get(self.site, "reports_month_xlsx", cong_trinh="TN-22", thang=f"{first:%Y-%m}"))
        self.assertNotIn("Thu chi", site.sheetnames)
        self.assertNotIn("Giá trị làm trong tháng (giá bán)", values(site["Tiến độ"])[3])
        self.assertNotIn("Đơn giá", values(site["Chấm công"])[3])
        internal = book(self.get(self.users[Role.INTERNAL_ACCOUNTANT], "reports_month_xlsx", cong_trinh="TN-22", thang=f"{first:%Y-%m}"))
        self.assertNotIn("Thu tiền khách", flat(internal["Thu chi"]))
        self.assertNotIn("Giá trị làm trong tháng (giá bán)", values(internal["Tiến độ"])[3])

    def test_month_errors_and_scope(self):
        future = (self.today.replace(day=28) + timedelta(days=5)).strftime("%Y-%m")
        self.assertEqual(self.get(self.director, "reports_month_xlsx", cong_trinh="TN-22", thang=future).status_code, 404)
        from projects.models import Project
        Project.objects.create(code="KHAC", name="Không giao")
        self.assertEqual(self.get(self.site, "reports_month_xlsx", cong_trinh="KHAC").status_code, 404)

    def test_profit_book(self):
        self.assertEqual(self.get(self.users[Role.INTERNAL_ACCOUNTANT], "reports_profit_xlsx").status_code, 403)
        self.assertEqual(self.get(self.site, "reports_profit_xlsx").status_code, 403)
        sheet = book(self.get(self.users[Role.FINANCE_ACCOUNTANT], "reports_profit_xlsx"))["Lãi lỗ"]
        head = values(sheet)[3]
        row = values(sheet)[4]
        get = dict(zip(head, row))
        self.assertEqual(get["Doanh thu (hợp đồng)"], 120 * M)
        self.assertEqual(get["Giá trị đã làm"], 50 * M)                 # tường 50% × 100 triệu
        self.assertEqual((get["Hàng NCC"], get["Khoán tổ thợ"], get["Công nhật (ước tính)"], get["Chi khác"]),
                         (5 * M, 25 * M, 500_000, 1 * M))
        self.assertEqual(get["Chi phí đã phát sinh"], 31.5 * M)
        self.assertEqual(get["Lãi theo tiến độ"], 18.5 * M)
        self.assertEqual(get["Lãi dự báo"], 43.5 * M)
        self.assertEqual(values(sheet)[5][0], "TỔNG")
        self.assertIn("Lan", get["Ghi chú"])

    def test_hub_for_every_role(self):
        for role in Role.values:
            with self.subTest(role=role):
                self.client.force_login(self.users[role])
                html = self.client.get(reverse("reports")).content.decode()
                self.assertIn("Tải tổng quan", html)
                self.assertEqual("Tải lãi lỗ" in html, role in (Role.DIRECTOR, Role.FINANCE_ACCOUNTANT))
                self.assertEqual("Sổ thu chi" in html, role in (Role.DIRECTOR, Role.FINANCE_ACCOUNTANT, Role.INTERNAL_ACCOUNTANT))
