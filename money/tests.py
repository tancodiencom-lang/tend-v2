"""Phiếu thu chi: quyền theo loại, trùng phiếu, huỷ, công nợ bỏ phiếu huỷ, xuất MISA, trang web theo vai."""
import io
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from core.models import AuditLog, Role, User
from overview.compute import answers
from projects.models import Member, Project

from . import misa, services
from .models import AccountingSettings, Entry, Kind, Party
from .parties import match

M = 1_000_000


class MoneyCase(TestCase):
    def setUp(self):
        self.users = {r: User.objects.create_user(f"m-{r}", password="x-123-abcd", role=r) for r in Role.values}
        self.director = self.users[Role.DIRECTOR]
        self.internal = self.users[Role.INTERNAL_ACCOUNTANT]
        self.finance = self.users[Role.FINANCE_ACCOUNTANT]
        self.project = Project.objects.create(code="TN-22", name="Thiên Nga 22", contract_value=100 * M)
        for user in self.users.values():
            if user.role != Role.DIRECTOR:
                Member.objects.create(project=self.project, user=user)
        self.cat = Party.objects.create(name="Cát Hùng Vương", type="ncc", misa_code="NCC001")
        self.team = Party.objects.create(name="Tổ xây anh Tư", type="to")
        self.today = timezone.localdate()

    def add(self, user, **row):
        row.setdefault("project_id", self.project.pk)
        row.setdefault("date", self.today.isoformat())
        return services.add_entries(user, [row], client_key=uuid4(), channel="web")[0]


class ServiceTests(MoneyCase):
    def test_kind_rights(self):
        with self.assertRaises(PermissionDenied):
            self.add(self.internal, kind=Kind.CUSTOMER_RECEIPT, party="Khách", amount=10 * M)
        with self.assertRaises(PermissionDenied):
            self.add(self.finance, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=1 * M)
        with self.assertRaises(PermissionDenied):
            self.add(self.users[Role.SUPERVISOR], kind=Kind.OTHER_EXPENSE, party="x", amount=1)
        self.add(self.finance, kind=Kind.CUSTOMER_RECEIPT, party="Khách TN-22", amount=10 * M)
        with self.assertRaisesMessage(ValidationError, "cần công trình"):
            self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=1 * M, project_id=None)
        self.add(self.internal, kind=Kind.OTHER_EXPENSE, party="Điện lực", amount=500_000, project_id=None)
        with self.assertRaisesMessage(ValidationError, "sau hôm nay"):
            self.add(self.internal, kind=Kind.OTHER_EXPENSE, party="x", amount=1, date=(self.today + timedelta(days=1)).isoformat())

    def test_duplicate_and_resend(self):
        key = uuid4()
        row = {"kind": Kind.SUPPLIER_PAYMENT, "project_id": self.project.pk, "number": "PC12", "party_id": self.cat.pk, "amount": 5 * M}
        first = services.add_entries(self.internal, [row], key)
        self.assertEqual(services.add_entries(self.internal, [row], key), first)          # gửi lại không ghi trùng
        with self.assertRaisesMessage(ValidationError, "Có thể trùng"):
            services.add_entries(self.internal, [dict(row, number="pc 12", amount=1 * M)], uuid4())
        services.add_entries(self.internal, [dict(row, number="PC12", allow_duplicate=True)], uuid4())
        self.assertEqual(Entry.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(table="money.Entry", action="create", actor=self.internal).exists())

    def test_void_rules(self):
        entry = self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=5 * M)
        self.assertFalse(services.void_needs_director(self.internal, entry))
        Entry.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(days=2))
        entry.refresh_from_db()
        self.assertTrue(services.void_needs_director(self.internal, entry))
        with self.assertRaises(PermissionDenied):
            services.void(self.internal, entry, "nhầm")
        with self.assertRaisesMessage(ValidationError, "lý do"):
            services.void(self.internal, entry, " ", approver=self.director)
        services.void(self.internal, entry, "nhập nhầm số tiền", approver=self.director)
        entry.refresh_from_db()
        self.assertIn("Giám đốc", entry.void_reason)
        with self.assertRaises(PermissionDenied):     # kế toán nội bộ không huỷ được phiếu thu khách (không được xem)
            services.void(self.internal, self.add(self.finance, kind=Kind.CUSTOMER_RECEIPT, party="K", amount=1), "x")

    def test_party_match(self):
        self.assertEqual(match("NCC Cát Hùng Vương")["party"], self.cat)
        self.assertEqual(match("cat hung vuong", "ncc")["party"], self.cat)
        self.assertIn(self.cat, match("Cát Hùng Vươn")["candidates"])
        self.assertEqual(match("Thép Hòa Phát"), {})


class NumbersTests(MoneyCase):
    def test_debts_exclude_voided_and_group_by_party(self):
        self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=8 * M)
        self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=3 * M, number="PC1")
        wrong = self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=2 * M, number="PC2")
        self.add(self.internal, kind=Kind.TEAM_WORK, party_id=self.team.pk, amount=20 * M)
        self.add(self.internal, kind=Kind.TEAM_PAYMENT, party_id=self.team.pk, amount=12 * M)
        self.add(self.finance, kind=Kind.CUSTOMER_RECEIPT, party="Khách TN-22", amount=30 * M)
        services.void(self.internal, wrong, "trùng phiếu PC1")
        data = answers(self.director, self.project, self.today)
        pay = data["payables"]
        self.assertEqual((pay["supplier_debt"], pay["team_debt"]), (5 * M, 8 * M))     # 8 − 3 (PC2 đã huỷ không trừ)
        self.assertEqual(data["customer"]["remaining"], 70 * M)
        self.assertEqual([s["name"] for s in pay["suppliers"]], ["Cát Hùng Vương"])


class MisaTests(MoneyCase):
    def test_export_columns_accounts_notes_and_reexport_warning(self):
        AccountingSettings.objects.create(bank_number="0123", bank_name="Vietcombank")
        self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=5 * M, number="PC12", method="tien_mat")
        self.add(self.internal, kind=Kind.TEAM_PAYMENT, party_id=self.team.pk, amount=2 * M, method="tien_mat")
        voided = self.add(self.internal, kind=Kind.OTHER_EXPENSE, party="x", amount=1 * M, method="tien_mat", project_id=None)
        services.void(self.internal, voided, "nhầm")
        start = self.today.replace(day=1)
        name, content, count, notes = misa.export(self.internal, misa.CASH_OUT, start, self.today)
        self.assertEqual(count, 2)
        sheet = load_workbook(io.BytesIO(content)).active
        self.assertEqual(sheet["I8"].value, "TK Nợ (*)")
        rows = {sheet.cell(row=r, column=3).value: [c.value for c in sheet[r]] for r in (9, 10)}
        self.assertEqual(rows["PC12"][8:12], ["331", "1111", 5 * M, "NCC001"])
        made = next(k for k in rows if k.startswith("TEND-C"))
        self.assertEqual(rows[made][8], "154")
        self.assertTrue(any("thiếu mã trong MISA" in n for n in notes))
        self.assertIsNone(misa.export(self.internal, misa.CASH_OUT, start, self.today))     # đã xuất: lần sau không xuất trùng
        again = misa.export(self.internal, misa.CASH_OUT, start, self.today, include_exported=True)
        self.assertTrue(any("ĐÃ XUẤT MISA" in n for n in again[3]))

    def test_purchase_and_receipt_rights(self):
        self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=8 * M, content="Cát vàng 10 m3")
        self.add(self.finance, kind=Kind.CUSTOMER_RECEIPT, party="Khách", amount=30 * M, method="chuyen_khoan")
        start = self.today.replace(day=1)
        sheet = load_workbook(io.BytesIO(misa.export(self.internal, misa.PURCHASE, start, self.today)[1])).active
        self.assertEqual([sheet.cell(row=9, column=c).value for c in (18, 20, 21, 25)], ["Cát vàng 10 m3", "154", "331", 8 * M])
        self.assertIsNone(misa.export(self.internal, misa.BANK_IN, start, self.today))       # nội bộ không xuất thu khách
        sheet = load_workbook(io.BytesIO(misa.export(self.finance, misa.BANK_IN, start, self.today)[1])).active
        self.assertEqual([sheet.cell(row=9, column=c).value for c in (10, 11, 12)], ["1121", "131", 30 * M])


class WebTests(MoneyCase):
    def setUp(self):
        super().setUp()
        self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=5 * M, number="PC12")
        self.add(self.finance, kind=Kind.CUSTOMER_RECEIPT, party="Khách TN-22", amount=30 * M)

    def test_ledger_by_role(self):
        self.client.force_login(self.internal)
        html = self.client.get(reverse("money_ledger")).content.decode()
        self.assertIn("PC12", html)
        self.assertNotIn("Khách TN-22", html)
        self.client.force_login(self.finance)
        self.assertContains(self.client.get(reverse("money_ledger")), "Khách TN-22")
        book = load_workbook(io.BytesIO(self.client.get(reverse("money_ledger"), {"tai": "xlsx"}).content)).active
        self.assertEqual(book.max_row, 4)
        for role in (Role.SUPERVISOR, Role.DESIGNER):
            self.client.force_login(self.users[role])
            self.assertEqual(self.client.get(reverse("money_ledger")).status_code, 403)

    def test_debts_page_customer_only_for_receivable_roles(self):
        self.client.force_login(self.internal)
        html = self.client.get(reverse("money_debts")).content.decode()
        self.assertIn("Cát Hùng Vương", html)
        self.assertNotIn("Khách hàng", html)
        self.client.force_login(self.finance)
        self.assertContains(self.client.get(reverse("money_debts")), "còn <b>70.000.000 ₫</b>")

    def test_misa_post_downloads_and_marks(self):
        self.client.force_login(self.internal)
        response = self.client.post(reverse("money_misa"), {"loai": misa.CASH_OUT, "tu": self.today.replace(day=1).isoformat(),
                                                            "den": self.today.isoformat()})
        self.assertEqual(response["Content-Type"], "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertTrue(Entry.objects.get(number="PC12").misa_export_id)
        self.assertEqual(self.client.get(reverse("money_misa")).status_code, 200)

    def test_parties_edit_rights_and_log(self):
        self.client.force_login(self.internal)
        self.client.post(reverse("money_parties"), {"id": self.team.pk, "name": "Tổ xây anh Tư", "type": "to", "misa_code": "TO01",
                                                    "active": "1"})
        self.team.refresh_from_db()
        self.assertEqual(self.team.misa_code, "TO01")
        self.assertTrue(AuditLog.objects.filter(table="money.Party", object_id=str(self.team.pk), action="update").exists())
        self.client.force_login(self.users[Role.SUPERVISOR])
        self.assertEqual(self.client.get(reverse("money_parties")).status_code, 403)

    def test_voucher_hidden_from_other_roles(self):
        from django.core.files.base import ContentFile
        from PIL import Image
        buffer = io.BytesIO()
        Image.new("RGB", (4, 4)).save(buffer, "JPEG")
        entry = Entry.objects.get(party="Khách TN-22")
        entry.voucher.save("pt.jpg", ContentFile(buffer.getvalue()))
        self.client.force_login(self.internal)
        self.assertEqual(self.client.get(reverse("money_voucher", args=[entry.pk])).status_code, 404)
        self.client.force_login(self.finance)
        self.assertEqual(self.client.get(reverse("money_voucher", args=[entry.pk])).status_code, 200)
