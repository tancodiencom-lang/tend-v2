"""Trợ lý kế toán: tuổi nợ (trả vào khoản cũ nhất trước), hạn trả, đợt thanh toán khách, dòng tiền, báo cáo công nợ tuần
gửi người giữ vị trí + Giám đốc, lệnh bot, trang Công nợ / Đối tượng theo quyền."""
import io
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from openpyxl import load_workbook

from assistant import schedule
from assistant.tests import at
from chat.models import Proposal
from chat.tests import BotCase
from core.models import AuditLog, Role, User
from projects.models import Member

from . import aging, services, weekly
from .models import Entry, Installment, Kind, Party
from .tests import M, MoneyCase

FRIDAY, WEDNESDAY = date(2026, 11, 27), date(2026, 11, 25)


class AgingTests(MoneyCase):
    def setUp(self):
        super().setUp()
        self.other = Party.objects.create(name="Thép Hoà Phát", type="ncc")

    def test_payables_fifo_due_and_status(self):
        t = self.today
        old = self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=10 * M, number="PN1",
                       date=(t - timedelta(days=40)).isoformat(), due_on=(t - timedelta(days=10)).isoformat())
        new = self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=6 * M, number="PN2",
                       date=(t - timedelta(days=3)).isoformat(), due_on=(t + timedelta(days=3)).isoformat())
        self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=4 * M)
        self.team.terms_days = 30
        self.team.save()
        work = self.add(self.internal, kind=Kind.TEAM_WORK, party_id=self.team.pk, amount=8 * M, date=(t - timedelta(days=20)).isoformat())
        self.add(self.internal, kind=Kind.TEAM_PAYMENT, party_id=self.team.pk, amount=3 * M)
        self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.other.pk, amount=1 * M)
        voided = self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.other.pk, amount=50 * M, number="PN9")
        services.void(self.internal, voided, "nhập nhầm")
        rows = aging.payables([self.project], t)["rows"]
        got = [(r["entry"], r["remaining"], r["status"], r["days"]) for r in rows]
        self.assertEqual(got, [(old, 6 * M, "qua_han", 10), (new, 6 * M, "sap_han", 3), (work, 5 * M, "con_han", 10),
                               (rows[3]["entry"], 1 * M, "chua_han", None)])
        self.assertFalse(rows[2]["own_due"])                                     # hạn theo số ngày được nợ của tổ
        self.assertIn("▼ quá hạn 10 ngày", aging.label(rows[0]))
        self.assertEqual(aging.label(rows[3]), "? chưa có hạn trả")
        self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=14 * M)        # trả dư 2tr
        data = aging.payables([self.project], t)
        self.assertNotIn(old, [r["entry"] for r in data["rows"]])
        self.assertEqual([(a["name"], a["amount"]) for a in data["advances"]], [("Cát Hùng Vương", 2 * M)])

    def test_set_due_rights_and_reason(self):
        entry = self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=5 * M, number="PN3")
        pay = self.add(self.internal, kind=Kind.SUPPLIER_PAYMENT, party_id=self.cat.pk, amount=1 * M)
        for user in (self.finance, self.users[Role.SUPERVISOR]):
            with self.assertRaises(PermissionDenied):
                services.set_due(user, entry, self.today + timedelta(days=5))
        with self.assertRaisesMessage(ValidationError, "Chỉ đặt hạn trả"):
            services.set_due(self.internal, pay, self.today + timedelta(days=5))
        with self.assertRaisesMessage(ValidationError, "trước ngày phiếu"):
            services.set_due(self.internal, entry, self.today - timedelta(days=1))
        services.set_due(self.internal, entry, self.today + timedelta(days=5))
        with self.assertRaisesMessage(ValidationError, "phải ghi lý do"):
            services.set_due(self.director, entry, self.today + timedelta(days=9))
        services.set_due(self.director, entry, self.today + timedelta(days=9), "NCC đồng ý lùi hạn", channel="web")
        entry.refresh_from_db()
        self.assertEqual(entry.due_on, self.today + timedelta(days=9))
        log = AuditLog.objects.filter(table="money.Entry", object_id=str(entry.pk), actor=self.director).latest("pk")
        self.assertIn("NCC đồng ý lùi hạn", log.reason)
        self.assertEqual(log.before["due_on"], (self.today + timedelta(days=5)).isoformat())

    def test_installments_fifo_levels_and_rights(self):
        t = self.today
        with self.assertRaises(PermissionDenied):
            services.add_installment(self.internal, self.project, "Đợt 1", 30 * M, t)
        d1 = services.add_installment(self.finance, self.project, "Đợt 1 ký hợp đồng", 30 * M, t - timedelta(days=20))
        d2 = services.add_installment(self.finance, self.project, "Đợt 2 xong móng", 30 * M, t - timedelta(days=5))
        d3 = services.add_installment(self.director, self.project, "Đợt 3 xong thô", 40 * M, t + timedelta(days=5))
        self.add(self.finance, kind=Kind.CUSTOMER_RECEIPT, party="Khách TN-22", amount=40 * M)
        data = aging.receivables([self.project], t)[0]
        got = [(i["installment"], i["remaining"], i["status"], i["level"]) for i in data["items"]]
        self.assertEqual(got, [(d1, 0, "da_thu", ""), (d2, 20 * M, "qua_han", "nhac"), (d3, 40 * M, "sap_han", "")])
        self.assertIsNone(data["plan_gap"])                                     # tổng đợt = giá trị hợp đồng
        Installment.objects.filter(pk=d2.pk).update(due_on=t - timedelta(days=16))
        self.assertEqual(aging.receivables([self.project], t)[0]["items"][1]["level"], "gap")
        with self.assertRaisesMessage(ValidationError, "lý do"):
            services.cancel_installment(self.finance, d3, " ")
        with self.assertRaises(PermissionDenied):
            services.cancel_installment(self.internal, d3, "đổi phụ lục")
        services.cancel_installment(self.finance, d3, "ký phụ lục đổi tiến độ thanh toán")
        data = aging.receivables([self.project], t)[0]
        self.assertEqual(len(data["items"]), 2)
        self.assertEqual(data["plan_gap"], 40 * M)
        self.assertTrue(AuditLog.objects.filter(table="money.Installment", object_id=str(d3.pk), action="update").exists())

    def test_forecast_by_due_week(self):
        t = self.today
        self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=7 * M, due_on=(t + timedelta(days=2)).isoformat())
        self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.other.pk, amount=3 * M,
                 date=(t - timedelta(days=9)).isoformat(), due_on=(t - timedelta(days=1)).isoformat())
        self.add(self.internal, kind=Kind.TEAM_WORK, party_id=self.team.pk, amount=2 * M)
        services.add_installment(self.finance, self.project, "Đợt 2", 20 * M, t + timedelta(days=9))
        flow = aging.forecast(aging.payables([self.project], t)["rows"], aging.receivables([self.project], t), t)
        weeks = [(w["inflow"], w["outflow"], w["net"]) for w in flow["weeks"]]
        self.assertEqual(weeks, [(0, 7 * M, -7 * M), (20 * M, 0, 20 * M), (0, 0, 0), (0, 0, 0)])
        self.assertEqual((flow["late_out"], flow["no_due_out"], flow["late_in"]), (3 * M, 2 * M, 0))


class WeeklyTextTests(MoneyCase):
    def test_texts_follow_role(self):
        t = self.today
        self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=5 * M, number="PN7",
                 date=(t - timedelta(days=12)).isoformat(), due_on=(t - timedelta(days=2)).isoformat())
        self.add(self.internal, kind=Kind.TEAM_WORK, party_id=self.team.pk, amount=2 * M)
        services.add_installment(self.finance, self.project, "Đợt 1", 30 * M, t - timedelta(days=4))
        text = weekly.internal_text(self.internal, t)
        self.assertIn("▼ QUÁ HẠN: 1 khoản, 5.000.000 ₫", text)
        self.assertIn("PN7", text)
        self.assertIn("? CHƯA CÓ HẠN TRẢ: 1 khoản", text)
        self.assertIn("Tổ xây anh Tư (TN-22)", text)
        self.assertIn("Chỉ số vị trí kế toán nội bộ", text)
        self.assertNotIn("Đợt 1", text)
        self.assertIsNone(weekly.finance_text(self.internal, t))             # kế toán nội bộ không xem tiền khách
        self.assertIsNone(weekly.internal_text(self.users[Role.SUPERVISOR], t))
        money = weekly.finance_text(self.finance, t)
        self.assertIn("▼ quá hạn 4 ngày · Đợt 1", money)
        self.assertIn("DÒNG TIỀN 4 TUẦN TỚI", money)
        self.assertIn("nợ quá hạn chưa trả 5.000.000 ₫", money)
        both = weekly.text_for(self.director, t)
        self.assertIn("CÔNG NỢ PHẢI TRẢ", both)
        self.assertIn("CÔNG NỢ KHÁCH", both)
        self.assertNotIn("CÔNG NỢ KHÁCH", weekly.text_for(self.internal, t))

    def test_long_report_keeps_kpis_and_link(self):
        for i in range(60):
            party = Party.objects.create(name=f"Nhà cung cấp vật tư số {i}", type="ncc")
            self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=party.pk, amount=M + i, number=f"PN{100 + i}",
                     date=(self.today - timedelta(days=20)).isoformat(), due_on=(self.today - timedelta(days=1)).isoformat())
        text = weekly.internal_text(self.internal, self.today)
        self.assertLessEqual(len(text), 3900)
        self.assertIn("… và 48 khoản nữa", text)
        self.assertIn("Chỉ số vị trí kế toán nội bộ", text)
        self.assertTrue(text.endswith("/bao-cao/cong-no/"))
        short = weekly.internal_text(self.internal, self.today, limit=700)
        self.assertLessEqual(len(short), 700)
        self.assertIn("xem đủ ở trang Công nợ", short)
        self.assertTrue(short.endswith("/bao-cao/cong-no/"))
        self.assertLessEqual(len(weekly.text_for(self.director, self.today)), 3905)

    def test_missing_data_is_named(self):
        text = weekly.finance_text(self.finance, self.today)
        self.assertIn("Chưa có lịch đợt thanh toán: /dotthu TN-22", text)
        self.assertIn("chưa có số liệu", text)


class DebtBotTests(BotCase):
    def setUp(self):
        super().setUp()
        self.cat = Party.objects.create(name="Cát Hùng Vương", type="ncc")
        self.finance = User.objects.create_user("ktt", password="x-123-abcd", role=Role.FINANCE_ACCOUNTANT)
        Member.objects.create(project=self.project, user=self.finance)
        self.connect(self.finance)
        self.project.contract_value = 100 * M
        self.project.save()
        self.sent.clear()

    def confirm(self, user):
        proposal = self.proposal()
        self.assertEqual(self.last_buttons(self.chats[user.pk]).get("✓ Xác nhận"), f"ok:{proposal.pk}")
        self.press(user, f"ok:{proposal.pk}")
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, Proposal.State.CONFIRMED, proposal.error)
        return proposal

    def last(self, user):
        return self.texts(self.chats[user.pk])[-1]

    def test_entry_with_due_and_due_commands(self):
        self.message(self.accountant, "nhận hàng 10tr Cát Hùng Vương TN-22 PN5 hạn 30 ngày - cát vàng")
        self.assertIn("hạn trả " + f"{self.today_plus(30):%d/%m/%Y}", self.last(self.accountant))
        self.confirm(self.accountant)
        entry = Entry.objects.get(number="PN5")
        self.assertEqual((entry.due_on, entry.content), (self.today_plus(30), "cát vàng"))
        self.message(self.accountant, f"/hantra PN5 {self.today_plus(40):%d/%m}")
        self.assertIn("đổi hạn phải ghi lý do", self.last(self.accountant))
        self.message(self.accountant, f"/hantra PN5 {self.today_plus(40):%d/%m/%Y} NCC đồng ý lùi hạn")
        self.assertIn("ĐẶT HẠN TRẢ CHỜ XÁC NHẬN", self.last(self.accountant))
        self.assertEqual(Entry.objects.get(pk=entry.pk).due_on, self.today_plus(30))       # chưa xác nhận: chưa ghi
        self.confirm(self.accountant)
        self.assertEqual(Entry.objects.get(pk=entry.pk).due_on, self.today_plus(40))
        self.assertTrue(AuditLog.objects.filter(table="money.Entry", object_id=str(entry.pk), channel="telegram",
                                                reason__contains="lùi hạn").exists())
        self.message(self.finance, f"/hantra PN5 {self.today_plus(50):%d/%m} lý do")
        self.assertIn("Chỉ Giám đốc và kế toán nội bộ", self.last(self.finance))
        self.message(self.accountant, "/hanno Cát Hùng Vương 30 ngày")
        self.assertIn("chưa có → 30 ngày", self.last(self.accountant))
        self.confirm(self.accountant)
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.terms_days, 30)
        self.message(self.accountant, "/hanno")
        self.assertIn("Cát Hùng Vương (nhà cung cấp): 30 ngày", self.last(self.accountant))
        self.message(self.accountant, "/congnotuan")
        text = self.last(self.accountant)
        self.assertIn("CÔNG NỢ PHẢI TRẢ", text)
        self.assertNotIn("CÔNG NỢ KHÁCH", text)

    def today_plus(self, days):
        from django.utils import timezone
        return timezone.localdate() + timedelta(days=days)

    def test_installment_commands(self):
        self.message(self.accountant, "/dotthu TN-22 Đợt 1 50tr hạn 30/11")
        self.assertIn("Chỉ Giám đốc và kế toán tài chính", self.last(self.accountant))
        self.message(self.finance, "/dotthu TN-22")
        self.assertIn("chưa có đợt thanh toán nào", self.last(self.finance))
        self.message(self.finance, "/dotthu TN-22 Đợt 1 ký hợp đồng 50tr hạn 15/11/2026 - theo hợp đồng")
        text = self.last(self.finance)
        self.assertIn("TN-22 · Đợt 1 ký hợp đồng · 50.000.000 ₫ · hạn 15/11/2026", text)
        self.assertFalse(Installment.objects.exists())
        self.confirm(self.finance)
        first = Installment.objects.get()
        self.assertEqual((first.name, first.amount, first.note), ("Đợt 1 ký hợp đồng", 50 * M, "theo hợp đồng"))
        self.message(self.finance, "/dotthu TN-22 Đợt 2 xong thô 1.200.000.000 hạn 20/12/2026")
        self.assertIn("lớn hơn giá trị hợp đồng", self.last(self.finance))
        self.press(self.finance, f"no:{self.proposal().pk}")
        self.message(self.finance, "/dotthu TN-22")
        self.assertIn(f"số {first.pk} · Đợt 1 ký hợp đồng", self.last(self.finance))
        self.message(self.finance, f"/huydot {first.pk} ký phụ lục đổi lịch")
        self.confirm(self.finance)
        first.refresh_from_db()
        self.assertEqual(first.cancel_reason, "ký phụ lục đổi lịch")
        self.message(self.accountant, "/congnotuan")
        self.assertNotIn("Đợt 1", self.last(self.accountant))


class WeeklyScheduleTests(BotCase):
    def setUp(self):
        super().setUp()
        self.finance = User.objects.create_user("ktt", password="x-123-abcd", role=Role.FINANCE_ACCOUNTANT)
        Member.objects.create(project=self.project, user=self.finance)
        self.connect(self.finance)
        self.sent.clear()

    def keys(self, when):
        return [k for k in schedule.run(when) if k.startswith("weekly-")]

    def test_friday_and_wednesday_to_holder_and_director_once(self):
        self.assertEqual(self.keys(at(FRIDAY, 8, 0)), [])
        keys = self.keys(at(FRIDAY, 8, 35))
        self.assertEqual(sorted(keys), sorted(f"weekly-ktnb:2026-48:{u.pk}" for u in (self.director, self.accountant)))
        self.assertIn("CÔNG NỢ PHẢI TRẢ", self.texts(self.chats[self.accountant.pk])[-1])
        self.assertEqual(self.keys(at(FRIDAY, 9, 0)), [])                     # mỗi tuần một lần
        self.assertFalse(any("CÔNG NỢ" in t for t in self.texts(self.chats[self.finance.pk])))
        keys = self.keys(at(WEDNESDAY + timedelta(days=7), 8, 40))
        self.assertEqual(sorted(keys), sorted(f"weekly-kttc:2026-49:{u.pk}" for u in (self.director, self.finance)))
        self.assertIn("CÔNG NỢ KHÁCH", self.texts(self.chats[self.finance.pk])[-1])
        self.assertFalse(any("CÔNG NỢ KHÁCH" in t for t in self.texts(self.chats[self.accountant.pk])))


class DebtWebTests(MoneyCase):
    def setUp(self):
        super().setUp()
        self.entry = self.add(self.internal, kind=Kind.SUPPLIER_GOODS, party_id=self.cat.pk, amount=5 * M, number="PN8")

    def test_internal_sets_due_finance_plans(self):
        self.client.force_login(self.internal)
        page = self.client.get(reverse("money_debts")).content.decode()
        self.assertIn("Hạn trả từng khoản còn nợ (1)", page)
        self.assertIn(reverse("money_set_due", args=[self.entry.pk]), page)
        self.assertNotIn("Đợt thanh toán của khách", page)
        due = self.today + timedelta(days=15)
        self.client.post(reverse("money_set_due", args=[self.entry.pk]), {"han": due.isoformat(), "cong_trinh": self.project.pk})
        self.entry.refresh_from_db()
        self.assertEqual(self.entry.due_on, due)
        self.assertEqual(self.client.post(reverse("money_add_installment"), {"cong_trinh": self.project.pk, "ten": "Đợt 1",
                                                                            "so_tien": "10.000.000", "han": due.isoformat()}).status_code, 403)
        book = load_workbook(io.BytesIO(self.client.get(reverse("money_debts") + "?tai=tuoi-no").content))
        self.assertEqual([c.value for c in book.active[3]][:4], ["TN-22", "Nhà cung cấp", "Cát Hùng Vương", "PN8"])
        self.client.post(reverse("money_parties"), {"id": self.cat.pk, "name": self.cat.name, "type": "ncc", "active": "1",
                                                    "misa_code": "NCC001", "terms_days": "45"})
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.terms_days, 45)

        self.client.force_login(self.finance)
        self.assertEqual(self.client.post(reverse("money_set_due", args=[self.entry.pk]), {"han": due.isoformat()}).status_code, 403)
        self.client.post(reverse("money_parties"), {"id": self.cat.pk, "name": self.cat.name, "type": "ncc", "active": "1",
                                                    "misa_code": "NCC001", "terms_days": "5"})
        self.cat.refresh_from_db()
        self.assertEqual(self.cat.terms_days, 45)                              # kế toán tài chính không đổi số ngày được nợ
        page = self.client.get(reverse("money_debts")).content.decode()
        self.assertIn("Chưa có lịch đợt thanh toán", page)
        self.client.post(reverse("money_add_installment"), {"cong_trinh": self.project.pk, "ten": "Đợt 1 ký hợp đồng",
                                                             "so_tien": "30.000.000", "han": due.isoformat(), "ghi_chu": "HĐ số 12"})
        inst = Installment.objects.get()
        self.assertEqual((inst.amount, inst.created_by, inst.note), (Decimal(30 * M), self.finance, "HĐ số 12"))
        page = self.client.get(reverse("money_debts")).content.decode()
        self.assertIn("Đợt 1 ký hợp đồng", page)
        self.client.post(reverse("money_cancel_installment", args=[inst.pk]), {"ly_do": ""})
        inst.refresh_from_db()
        self.assertIsNone(inst.cancelled_at)
        self.client.post(reverse("money_cancel_installment", args=[inst.pk]), {"ly_do": "nhập nhầm số tiền"})
        inst.refresh_from_db()
        self.assertIsNotNone(inst.cancelled_at)

    def test_other_roles_blocked(self):
        self.client.force_login(self.users[Role.SUPERVISOR])
        self.assertEqual(self.client.get(reverse("money_debts")).status_code, 403)
        self.assertEqual(self.client.post(reverse("money_set_due", args=[self.entry.pk]),
                                          {"han": self.today.isoformat()}).status_code, 403)
