"""Bot Telegram với Telegram giả, AI giả: kết nối, phiếu báo cáo + chấm công, ảnh, lý do giảm, thợ mới, sửa công, đơn giá."""
import io
import tempfile
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from asks.models import Answer, Ask
from core.models import AuditLog, Role, User
from drawings import autocad, dxf_read, importer
from drawings.models import Item
from field.models import Attendance, Photo, Report, ReportLine, Worker
from projects.models import Member, Project

from . import ai, bot, reads, telegram
from .models import ChatLink, Proposal

TMP = tempfile.mkdtemp(prefix="tend-chat-")


def jpeg():
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (120, 90, 60)).save(buffer, "JPEG")
    return buffer.getvalue()


@override_settings(MEDIA_ROOT=TMP, TELEGRAM_TOKEN="123:thu", TELEGRAM_WEBHOOK_SECRET="", TELEGRAM_BOT_USERNAME="tend_thu_bot")
class BotCase(TestCase):
    def setUp(self):
        self.sent = []
        self.next_id = iter(range(1000, 100000))
        self.update_id = iter(range(1, 100000))
        post = mock.patch.object(telegram, "post", side_effect=self.fake_post)
        download = mock.patch.object(telegram, "download", side_effect=lambda file_id: (jpeg(), f"{file_id}.jpg"))
        no_ai = mock.patch.object(ai, "available", return_value=False)
        for patcher in (post, download, no_ai):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.director = User.objects.create_user("giamdoc", password="x-123-abcd", role=Role.DIRECTOR, first_name="Quân")
        self.site = User.objects.create_user("giamsat", password="x-123-abcd", role=Role.SUPERVISOR, first_name="Minh")
        self.designer = User.objects.create_user("thietke", password="x-123-abcd", role=Role.DESIGNER)
        self.accountant = User.objects.create_user("ketoan", password="x-123-abcd", role=Role.INTERNAL_ACCOUNTANT)
        self.project = Project.objects.create(code="TN-22", name="Thiên Nga 22")
        for user in (self.site, self.designer, self.accountant):
            Member.objects.create(project=self.project, user=user)
        importer.apply(self.project, dxf_read.read_doc(autocad.sample_doc(), "mau"), {"1": 3300}, self.director)
        self.nam = Worker.objects.create(name="Nam", team="xây", kind="phu", day_rate=300_000)
        self.hung = Worker.objects.create(name="Nguyễn Văn Hùng", team="xây", kind="tho", day_rate=500_000)
        self.chats = {}
        for user in (self.site, self.director, self.accountant, self.designer):
            self.connect(user)
        self.sent.clear()

    # --- Telegram giả ---
    def fake_post(self, method, data):
        self.sent.append((method, data))
        return {"message_id": next(self.next_id), "username": "tend_thu_bot"}

    def texts(self, chat=None, method="sendMessage"):
        return [d["text"] for m, d in self.sent if m == method and (chat is None or d["chat_id"] == chat)]

    def last_buttons(self, chat=None):
        for method, data in reversed(self.sent):
            if method in ("sendMessage", "editMessageText") and (chat is None or data["chat_id"] == chat) and data.get("reply_markup"):
                buttons = [b for row in data["reply_markup"]["inline_keyboard"] for b in row]
                if buttons:
                    return {b["text"]: b["callback_data"] for b in buttons}
        return {}

    def message(self, user, text="", photo=None, group=None):
        chat = self.chats.get(user.pk, str(user.pk + 500))
        msg = {"message_id": next(self.next_id), "chat": {"id": int(chat), "type": "private"},
               "from": {"id": user.pk + 9000, "first_name": user.username}}
        if photo:
            msg["photo"] = [{"file_id": photo, "file_unique_id": photo, "file_size": 100, "width": 8}]
            if text:
                msg["caption"] = text
            if group:
                msg["media_group_id"] = group
        else:
            msg["text"] = text
        bot.handle_update({"update_id": next(self.update_id), "message": msg})

    def press(self, user, data):
        chat = self.chats[user.pk]
        bot.handle_update({"update_id": next(self.update_id), "callback_query": {
            "id": "cb", "data": data, "from": {"id": user.pk + 9000},
            "message": {"message_id": 1, "chat": {"id": int(chat)}, "text": "phiếu"}}})

    def connect(self, user):
        """Kết nối như thật: mã một lần, rồi bấm nút gửi số điện thoại Telegram (trùng số công ty ghi)."""
        self.chats[user.pk] = str(user.pk + 500)
        if not user.phone:
            user.phone = f"09{user.pk:08d}"
            user.save(update_fields=["phone"])
        self.message(user, f"/ketnoi {bot.new_code(user)}")
        self.share_phone(user, "+84" + user.phone[1:])

    def share_phone(self, user, number, owner_id=None):
        chat = self.chats.get(user.pk, str(user.pk + 500))
        with self.captureOnCommitCallbacks(execute=True):
            bot.handle_update({"update_id": next(self.update_id), "message": {
                "message_id": next(self.next_id), "chat": {"id": int(chat), "type": "private"},
                "from": {"id": user.pk + 9000, "first_name": user.username},
                "contact": {"phone_number": number, "user_id": owner_id or user.pk + 9000}}})

    def proposal(self):
        return Proposal.objects.latest("created_at")


class LinkTests(BotCase):
    def test_leaked_code_useless_without_matching_phone(self):
        from core.models import User as U
        intruder = U(username="la", pk=4242)
        code = bot.new_code(self.site)
        self.chats[intruder.pk] = "777"
        self.message(intruder, f"/ketnoi {code}")                           # người khác cầm được mã
        self.assertIn("bấm nút 📱", self.texts("777")[-1])
        self.share_phone(intruder, "0987654321", owner_id=4242 + 9000)
        self.assertIn("không trùng", self.texts("777")[-1])
        self.assertFalse(ChatLink.objects.filter(external_id=str(4242 + 9000)).exists())
        warned = self.texts(self.chats[self.director.pk])[-1]
        self.assertIn("KHÔNG trùng", warned)
        self.assertIn("098…321", warned)                                     # số che bớt
        self.share_phone(intruder, "+84" + self.site.phone[1:], owner_id=4242 + 9000)
        self.assertIn("Chưa có mã đang chờ", self.texts("777")[-1])          # mã đã hỏng sau lần sai

    def test_must_share_own_number_and_need_phone_on_file(self):
        code = bot.new_code(self.site)
        self.message(self.site, f"/ketnoi {code}")
        self.share_phone(self.site, self.site.phone, owner_id=12345)        # gửi danh bạ người khác
        self.assertIn("chính anh/chị", self.texts()[-1])
        self.share_phone(self.site, "84" + self.site.phone[1:])            # 84… cũng là 0…
        self.assertTrue(ChatLink.objects.filter(user=self.site, active=True, external_id=str(self.site.pk + 9000)).exists())
        nophone = User.objects.create_user("moi", password="x-123-abcd", role=Role.SUPERVISOR)
        self.message(nophone, f"/ketnoi {bot.new_code(nophone)}")
        self.assertIn("chưa có số điện thoại", self.texts()[-1])
        self.client.force_login(nophone)
        page = self.client.post(reverse("chat_connect"), follow=True)
        self.assertContains(page, "chưa có số điện thoại")
        self.assertNotContains(page, "/ketnoi ")

    def test_code_once_unlink_and_unknown_user(self):
        self.assertTrue(ChatLink.objects.filter(user=self.site, active=True).exists())
        code = bot.new_code(self.site)
        self.message(self.site, f"/ketnoi {code}")
        self.message(self.site, f"/ketnoi {code}")
        self.assertIn("Mã không đúng hoặc đã hết hạn", self.texts()[-1])
        self.message(self.site, "/ngatketnoi")
        self.assertFalse(ChatLink.objects.filter(user=self.site, active=True).exists())
        self.message(self.site, "TN-22 T1.2 40%")
        self.assertIn("chưa kết nối", self.texts()[-1])
        self.assertTrue(AuditLog.objects.filter(table="chat.ChatLink", reason="Ngắt kết nối Telegram").exists())

    def test_unlinked_group_only_hints_and_update_once(self):
        bot.handle_update({"update_id": 5000, "message": {"chat": {"id": 1, "type": "group"}, "from": {"id": 1}, "text": "/tongquan"}})
        self.assertEqual(len(self.sent), 1)
        self.assertIn("chưa được gắn với Tend", self.sent[0][1]["text"])       # không trả số liệu trong nhóm chưa gắn
        bot.handle_update({"update_id": 5000, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "x"}})
        self.assertEqual(len(self.sent), 1)                                    # cùng update_id: bỏ qua

    def test_webhook_secret(self):
        url = reverse("chat_telegram", args=[telegram.webhook_secret()])
        body = {"update_id": 7000, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "x"}}
        self.assertEqual(self.client.post(url, body, content_type="application/json").status_code, 403)
        ok = self.client.post(url, body, content_type="application/json", HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN=telegram.webhook_secret())
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(self.client.post(reverse("chat_telegram", args=["sai"]), body, content_type="application/json",
                                          HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="sai").status_code, 403)

    def test_connect_page_get_writes_nothing(self):
        self.client.force_login(self.site)
        page = self.client.get(reverse("chat_connect"))
        self.assertContains(page, "Đang kết nối")
        response = self.client.post(reverse("chat_connect"))
        self.assertContains(response, "https://t.me/tend_thu_bot?start=")


class ReportTests(BotCase):
    def test_report_photos_new_workers_and_attendance(self):
        today = timezone.localdate()
        self.message(self.site, "T1.2", photo="a1", group="g1")
        self.message(self.site, "", photo="a2", group="g1")
        self.message(self.site, "TN-22 T1.2 40% S1.1 xong. Công: tổ xây: Nam, Lan nửa ngày; Tâm 4 giờ")
        proposal = self.proposal()
        self.assertEqual({p.target for p in proposal.photos.all()},
                         {f"item:{Item.objects.get(code='T1.2').pk}", f"item:{Item.objects.get(code='S1.1').pk}"})
        summary = bot.summary(proposal)
        self.assertIn("Nam (phụ, xây): 1 công", summary)
        self.assertIn("Lan: 0,5 công · ? chưa rõ người", summary)
        self.assertIn("Còn thiếu: xác định người", summary)
        asked = [t for t in self.texts() if "chưa có trong danh sách thợ" in t]
        self.assertEqual(len(asked), 2)
        self.assertNotIn("✓ Xác nhận", self.last_buttons(self.chats[self.site.pk]))
        short = str(proposal.pk)[:8]
        self.press(self.site, f"wk:{short}:1:phu")
        self.press(self.site, f"wk:{short}:2:tho")
        self.assertEqual(Report.objects.count(), 0)                      # chưa xác nhận: chưa ghi
        buttons = self.last_buttons(self.chats[self.site.pk])
        self.assertIn("✓ Xác nhận", buttons)
        with self.captureOnCommitCallbacks(execute=True):
            self.press(self.site, buttons["✓ Xác nhận"])
        report = Report.objects.get()
        self.assertEqual(sorted((l.item.code, l.percent) for l in report.lines.all()), [("S1.1", Decimal("100.0")), ("T1.2", Decimal("40.0"))])
        self.assertEqual(Photo.objects.filter(line__item__code="T1.2").count(), 1)
        days = {str(a.worker or a.staff): a.days for a in Attendance.objects.filter(work_date=today)}
        self.assertEqual(days, {"Minh": Decimal("1.00"), "Nam (phụ, xây)": Decimal("1.00"), "Lan (phụ, xây)": Decimal("0.50"),
                                "Tâm (thợ)": Decimal("0.50")})                     # sau ";" là nhóm mới, chưa có tổ
        self.assertTrue(AuditLog.objects.filter(table="field.Worker", action="create", channel="telegram").exists())
        self.assertTrue(any("BÁO CÁO TN-22" in t for t in self.texts(self.chats[self.director.pk])))
        self.press(self.site, f"ok:{proposal.pk}")
        self.assertEqual(Report.objects.count(), 1)                      # xác nhận lại không ghi trùng
        self.assertIn("Phiếu đã ghi", self.texts(method="editMessageText")[-1])

    def test_ambiguous_photos_must_be_chosen(self):
        self.message(self.site, "", photo="b1")
        self.message(self.site, "", photo="b2")
        self.message(self.site, "TN-22 T1.2 40% S1.1 xong")
        proposal = self.proposal()
        self.assertEqual([p.target for p in proposal.photos.all()], ["?", "?"])
        self.assertEqual(sum(1 for m, _ in self.sent if m == "sendPhoto"), 2)
        for photo in proposal.photos.all():
            self.press(self.site, f"ph:{photo.pk}:1")
        self.assertIn("✓ Xác nhận", self.last_buttons())
        self.press(self.site, f"ok:{proposal.pk}")
        self.assertEqual(Photo.objects.filter(line__item__code="S1.1").count(), 2)

    def test_decrease_needs_reason_and_cancel_writes_nothing(self):
        self.message(self.site, "TN-22 T1.2 40%")
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.message(self.site, "TN-22 T1.2 30%")
        proposal = self.proposal()
        self.assertIn("Thấp hơn mức đã báo 40", bot.summary(proposal))
        self.assertNotIn("✓ Xác nhận", self.last_buttons())
        self.message(self.site, "đập đi xây lại do lệch tim")
        self.assertIn("✓ Xác nhận", self.last_buttons())
        self.press(self.site, f"ok:{proposal.pk}")
        line = ReportLine.objects.latest("pk")
        self.assertEqual((line.percent, line.decrease_reason), (Decimal("30.0"), "đập đi xây lại do lệch tim"))
        self.message(self.site, "TN-22 S1.1 50%")
        self.press(self.site, f"no:{self.proposal().pk}")
        self.assertEqual(Report.objects.count(), 2)
        self.message(self.site, "TN-22 S1.1 50%")
        Proposal.objects.filter(pk=self.proposal().pk).update(created_at=timezone.now() - timedelta(hours=25))
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.assertEqual(Report.objects.count(), 2)

    def test_same_day_attendance_same_is_kept_different_needs_reason(self):
        self.message(self.site, "TN-22 T1.2 40%. Công: Nam")
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.message(self.site, "TN-22 S1.1 20%. Công: Nam")
        self.assertIn("đã chấm 1 công, giữ nguyên", bot.summary(self.proposal()))
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.message(self.site, "TN-22 S1.1 30%. Công: Nam nửa ngày")
        proposal = self.proposal()
        self.assertIn("đã chấm 1 → sửa", bot.summary(proposal))
        self.message(self.site, "chiều về sớm")
        self.press(self.site, f"ok:{proposal.pk}")
        row = Attendance.objects.get(worker=self.nam)
        self.assertEqual((row.days, row.correction_reason), (Decimal("0.50"), "chiều về sớm"))
        self.assertEqual(Attendance.objects.filter(staff=self.site).count(), 1)

    def test_similar_name_asks_which_person(self):
        self.message(self.site, "TN-22 T1.2 40%. Công: Hung")
        proposal = self.proposal()
        self.assertTrue(any('"Hung" là ai?' in t for t in self.texts()))
        self.press(self.site, f"wk:{str(proposal.pk)[:8]}:0:{self.hung.pk}")
        self.press(self.site, f"ok:{proposal.pk}")
        self.assertEqual(Attendance.objects.get(worker=self.hung).days, Decimal("1.00"))
        self.assertEqual(Worker.objects.count(), 2)                       # không tạo trùng người

    def test_designer_cannot_report_and_unknown_code(self):
        self.message(self.designer, "TN-22 T1.2 40%")
        self.assertIn("không được báo cáo", self.texts()[-1])
        self.message(self.site, "TN-22 X9.9 40%")
        self.assertFalse(Proposal.objects.filter(link__user=self.site).exists())


class FixAndRateTests(BotCase):
    def test_fix_attendance_and_rate_commands(self):
        day = timezone.localdate()
        Attendance.objects.create(project=self.project, work_date=day, worker=self.nam, days=1, recorded_by=self.site)
        self.message(self.site, f"/suacong TN-22 {day:%d/%m} Nam 0,5 lý do chiều nghỉ")
        proposal = self.proposal()
        self.assertIn("1 → 0,5 công", bot.summary(proposal))
        self.press(self.site, f"ok:{proposal.pk}")
        self.assertEqual(Attendance.objects.get(worker=self.nam).days, Decimal("0.50"))
        self.message(self.site, "/dongia Nam 350000")
        self.assertIn("Chỉ Giám đốc và kế toán nội bộ", self.texts()[-1])
        self.message(self.accountant, "/dongia Nam 350.000 khoán")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.nam.refresh_from_db()
        self.assertEqual((self.nam.day_rate, self.nam.pay), (Decimal("350000"), "khoan"))
        self.assertTrue(AuditLog.objects.filter(table="field.Worker", actor=self.accountant).exists())

    def test_month_command_hides_money_from_supervisor(self):
        Attendance.objects.create(project=self.project, work_date=timezone.localdate(), worker=self.hung, days=1, recorded_by=self.site)
        self.message(self.site, "/cong")
        self.assertIn("Nguyễn Văn Hùng (xây): 1 công", self.texts()[-1])
        self.assertNotIn("₫", self.texts()[-1])
        self.message(self.director, "/cong")
        self.assertIn("500.000 ₫", self.texts()[-1])
        site = reads.attendance_summary(self.site)["people"][0]
        self.assertNotIn("labor_cost", site)


class AiTests(BotCase):
    def fake(self, *responses):
        queue = list(responses)

        def create(**kwargs):
            return queue.pop(0)
        return mock.patch.object(ai, "create_message", side_effect=create)

    @staticmethod
    def tool(name, data, block_id="t1"):
        return SimpleNamespace(stop_reason="tool_use", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                               content=[SimpleNamespace(type="tool_use", name=name, input=data, id=block_id)])

    def test_ai_builds_report_with_people(self):
        reply = self.tool("propose_report", {"project_code": "TN-22", "lines": [{"code": "T1.2", "percent": 60}],
                                             "people": [{"name": "Nam"}, {"name": "Nguyễn Văn Hùng", "hours": 4}], "note": ""})
        with mock.patch.object(ai, "available", return_value=True), self.fake(reply):
            self.message(self.site, "hôm nay tường T1.2 được 60%, Nam cả ngày, anh Hùng làm buổi sáng 4 tiếng")
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.assertEqual(Attendance.objects.get(worker=self.hung).days, Decimal("0.50"))
        self.assertEqual(ReportLine.objects.get().percent, Decimal("60.0"))

    def test_ai_question_tools_run_as_user(self):
        lookup = self.tool("attendance_summary", {"project_code": "TN-22"})
        answer = SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1),
                                 content=[SimpleNamespace(type="text", text="Tháng này Hùng 1 công.")])
        Attendance.objects.create(project=self.project, work_date=timezone.localdate(), worker=self.hung, days=1, recorded_by=self.site)
        with mock.patch.object(ai, "available", return_value=True), self.fake(lookup, answer) as fake:
            self.message(self.site, "tháng này Hùng bao nhiêu công")
        self.assertEqual(self.texts()[-1], "Tháng này Hùng 1 công.")
        tool_result = fake.call_args_list[1].kwargs["messages"][-1]["content"][0]["content"]
        self.assertIn("Nguyễn Văn Hùng", tool_result)
        self.assertNotIn("labor_cost", tool_result)


class AskTests(BotCase):
    def test_director_asks_supervisor_answers_by_report_and_designer_by_text(self):
        item = Item.objects.get(code="T1.2")
        self.client.force_login(self.director)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("asks_create", args=["TN-22"]), {"item_id": item.pk, "assignee": self.site.pk, "note": "Chụp mạch vữa"})
        ask = Ask.objects.get()
        self.assertTrue(any("YÊU CẦU BÁO CÁO · TN-22" in t for t in self.texts(self.chats[self.site.pk])))
        from asks.services import status
        self.assertEqual(status(ask)[0], "open")
        self.message(self.site, "TN-22 T1.2 50%")
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.assertEqual(status(ask)[0], "done")
        Ask.objects.create(project=self.project, requester=self.director, assignee=self.designer, note="Bản vẽ trần WC?")
        other = Ask.objects.get(assignee=self.designer)
        self.press(self.designer, f"ans:{other.pk}")
        self.message(self.designer, "Trần WC thạch cao chống ẩm, cao 2,6 m")
        self.press(self.designer, f"ok:{self.proposal().pk}")
        self.assertEqual(Answer.objects.get().note, "Trần WC thạch cao chống ẩm, cao 2,6 m")
        self.client.force_login(self.site)
        self.assertEqual(self.client.post(reverse("asks_create", args=["TN-22"]),
                                          {"item_id": item.pk, "assignee": self.site.pk, "note": "x"}).status_code, 403)


class MoneyBotTests(BotCase):
    def setUp(self):
        super().setUp()
        from money.models import Party
        self.cat = Party.objects.create(name="Cát Hùng Vương", type="ncc")
        self.finance = User.objects.create_user("ktt", password="x-123-abcd", role=Role.FINANCE_ACCOUNTANT)
        Member.objects.create(project=self.project, user=self.finance)
        self.connect(self.finance)
        self.sent.clear()

    def entries(self):
        from money.models import Entry
        return Entry.objects.order_by("pk")

    def test_short_syntax_confirm_and_duplicate(self):
        self.message(self.accountant, "trả NCC 5tr Cát Hùng Vương TN-22 PC12 ck - cát vàng đợt 2")
        proposal = self.proposal()
        self.assertIn("Trả nhà cung cấp · PC12", bot.summary(proposal))
        self.assertIn("5.000.000 ₫", bot.summary(proposal))
        self.press(self.accountant, self.last_buttons()["✓ Xác nhận"])
        entry = self.entries().get()
        self.assertEqual((entry.party_ref, entry.amount, entry.method, entry.content), (self.cat, Decimal(5_000_000), "chuyen_khoan", "cát vàng đợt 2"))
        self.assertTrue(AuditLog.objects.filter(table="money.Entry", channel="telegram").exists())
        self.message(self.accountant, "trả NCC 1tr Cát Hùng Vương TN-22 PC12")
        proposal = self.proposal()
        self.assertIn("Nghi trùng", bot.summary(proposal))
        self.assertNotIn("✓ Xác nhận", self.last_buttons(self.chats[self.accountant.pk]))
        self.press(self.accountant, f"md:{str(proposal.pk)[:8]}:0")
        self.press(self.accountant, f"ok:{proposal.pk}")
        self.assertEqual(self.entries().count(), 2)

    def test_new_party_near_name_and_project_choice(self):
        other = Project.objects.create(code="BH-01", name="Biệt thự")
        Member.objects.create(project=other, user=self.accountant)
        self.message(self.accountant, "trả tổ 3tr Tổ điện Bình tm")
        proposal = self.proposal()
        short = str(proposal.pk)[:8]
        self.assertTrue(any("chưa có trong danh sách đối tượng" in t for t in self.texts()))
        self.assertTrue(any("thuộc công trình nào" in t for t in self.texts()))
        self.press(self.accountant, f"mp:{short}:0:n-to")
        self.press(self.accountant, f"mj:{short}:0:{other.pk}")
        self.press(self.accountant, f"ok:{proposal.pk}")
        entry = self.entries().get()
        self.assertEqual((entry.project, entry.party_ref.name, entry.party_ref.type), (other, "Tổ điện Bình", "to"))
        self.message(self.accountant, "trả NCC 1tr Cat Hung Vuon TN-22")
        proposal = self.proposal()
        self.assertTrue(any("là đối tượng nào" in t for t in self.texts()))
        self.press(self.accountant, f"mp:{str(proposal.pk)[:8]}:0:{self.cat.pk}")
        self.press(self.accountant, f"ok:{proposal.pk}")
        self.assertEqual(self.entries().last().party_ref, self.cat)

    def test_customer_receipt_rights_and_lookups(self):
        Project.objects.filter(pk=self.project.pk).update(contract_value=100_000_000)
        self.message(self.accountant, "thu khách 10tr TN-22")
        self.assertIn("thu tiền khách do kế toán tài chính", self.texts()[-1])
        self.message(self.finance, "thu khách 30tr TN-22 PT01 ck")
        self.press(self.finance, f"mp:{str(self.proposal().pk)[:8]}:0:n-khach")
        self.press(self.finance, f"ok:{self.proposal().pk}")
        self.message(self.accountant, "nhận hàng 8tr Cát Hùng Vương TN-22")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.message(self.accountant, "/thuchi hôm nay")
        self.assertIn("Cát Hùng Vương", self.texts()[-1])
        self.assertNotIn("Khách TN-22", self.texts()[-1])
        self.message(self.accountant, "/congno")
        self.assertIn("NCC Cát Hùng Vương: 8.000.000 ₫", self.texts()[-1])
        self.assertNotIn("Khách còn", self.texts()[-1])
        self.message(self.finance, "/congno TN-22")
        self.assertIn("Khách còn phải trả: 70.000.000 ₫", self.texts()[-1])
        self.message(self.site, "/congno")
        self.assertIn("không có quyền", self.texts()[-1])

    def test_ai_reads_voucher_photo(self):
        self.message(self.accountant, "", photo="phieu1")
        reply = AiTests.tool("propose_entries", {"entries": [{"kind": "tra_ncc", "project_code": "TN-22", "number": "PC20",
                                                              "party": "Cát Hùng Vương", "amount": 2_500_000, "method": "tien_mat",
                                                              "photo_index": 0}]})
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "create_message", return_value=reply) as fake:
            self.message(self.accountant, "nhập phiếu này giúp")
        content = fake.call_args.kwargs["messages"][-1]["content"]
        self.assertEqual(content[0]["type"], "image")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        entry = self.entries().get()
        self.assertTrue(entry.voucher.name.startswith("chung-tu/"))
        self.assertEqual(entry.number, "PC20")

    def test_void_same_day_and_director_approval(self):
        from money.models import Entry
        self.message(self.accountant, "trả NCC 5tr Cát Hùng Vương TN-22 PC12")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.message(self.accountant, "/huy PC12 nhập nhầm số tiền")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.assertIsNotNone(Entry.objects.get(number="PC12").voided_at)
        self.message(self.accountant, "trả NCC 4tr Cát Hùng Vương TN-22 PC13")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        Entry.objects.filter(number="PC13").update(created_at=timezone.now() - timedelta(days=3))
        self.message(self.accountant, "/huy PC13 sai đối tượng")
        self.assertIn("đã gửi Giám đốc duyệt", self.texts(self.chats[self.accountant.pk])[-1])
        request = self.proposal()
        self.assertTrue(any("XIN HUỶ PHIẾU" in t for t in self.texts(self.chats[self.director.pk])))
        with self.captureOnCommitCallbacks(execute=True):
            self.press(self.director, f"dx:{request.pk}")
        self.assertIsNone(Entry.objects.get(number="PC13").voided_at)
        self.assertIn("không duyệt", self.texts(self.chats[self.accountant.pk])[-1])
        self.message(self.accountant, "/huy PC13 sai đối tượng")
        with self.captureOnCommitCallbacks(execute=True):
            self.press(self.director, f"dv:{self.proposal().pk}")
        entry = Entry.objects.get(number="PC13")
        self.assertIsNotNone(entry.voided_at)
        self.assertIn("đã duyệt huỷ", self.texts(self.chats[self.accountant.pk])[-1])
        self.press(self.accountant, f"dv:{self.proposal().pk}")      # kế toán bấm nút duyệt: không có quyền
        self.assertTrue(AuditLog.objects.filter(table="money.Entry", object_id=str(entry.pk), actor=self.director).exists())

    def test_daily_summary(self):
        from chat.money_bot import send_daily
        self.message(self.accountant, "chi 500k tiền điện văn phòng")
        self.press(self.accountant, f"mp:{str(self.proposal().pk)[:8]}:0:n-khac")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.sent.clear()
        self.assertEqual(send_daily(), 1)
        text = self.texts(self.chats[self.director.pk])[-1]
        self.assertIn("TÓM TẮT THU CHI", text)
        self.assertIn("chi 500.000 ₫", text)


class ArchiveTests(BotCase):
    """Kho tài liệu gốc: ảnh nhận qua chat xếp theo loại/ngày, danh sách CSV, ảnh dạng tệp."""

    def setUp(self):
        super().setUp()
        from money.models import Party
        Party.objects.create(name="Cát Hùng Vương", type="ncc")
        self.finance = User.objects.create_user("ktt", password="x-123-abcd", role=Role.FINANCE_ACCOUNTANT)
        Member.objects.create(project=self.project, user=self.finance)
        self.connect(self.finance)
        self.out = tempfile.mkdtemp(prefix="tend-goc-")
        patcher = override_settings(TEND_ORIGINALS_DIR=self.out)
        patcher.enable()
        self.addCleanup(patcher.disable)

    def files(self):
        from pathlib import Path
        return sorted(str(p.relative_to(self.out)) for p in Path(self.out).rglob("*") if p.is_file())

    def csv_text(self, relative):
        from pathlib import Path
        return (Path(self.out) / relative).read_text(encoding="utf-8-sig")

    def test_confirmed_photos_filed_by_type_and_day_with_index(self):
        import os
        from pathlib import Path

        from chat import archive
        from chat.models import ChatPhoto
        day = timezone.localdate()
        month = f"{day:%Y-%m}"
        self.message(self.site, "T1.2", photo="a1")
        self.message(self.site, "TN-22 T1.2 40%")
        self.press(self.site, f"ok:{self.proposal().pk}")
        self.message(self.accountant, "", photo="phieu1")
        self.message(self.accountant, "trả NCC 5tr Cát Hùng Vương TN-22 PC12 ck - cát vàng đợt 2")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.message(self.finance, "thu khách 30tr TN-22 PT01 ck - khách chuyển khoản đợt 1")     # chỉ có chữ
        self.press(self.finance, f"mp:{str(self.proposal().pk)[:8]}:0:n-khach")
        self.press(self.finance, f"ok:{self.proposal().pk}")
        self.assertEqual(archive.export(), 2)
        site_photo, voucher = ChatPhoto.objects.order_by("pk")
        files = self.files()
        site_dir = f"Hiện trường/TN-22/{month}/{day.isoformat()}"
        money_dir = f"Chứng từ thu chi/{month}/{day.isoformat()}"
        customer_dir = f"Thu tiền khách/{month}/{day.isoformat()}"
        self.assertEqual(files, sorted([
            f"{customer_dir}/danh-sach.csv", f"{money_dir}/danh-sach.csv", f"{site_dir}/danh-sach.csv",
            site_photo.archived_as, voucher.archived_as]))
        self.assertTrue(site_photo.archived_as.startswith(f"{site_dir}/"))
        self.assertTrue(site_photo.archived_as.endswith(f"_Minh_T1.2_{site_photo.pk}.jpg"))
        self.assertTrue(voucher.archived_as.endswith(f"_PC12_{voucher.pk}.jpg"))
        copied = Path(self.out) / site_photo.archived_as
        self.assertEqual(copied.read_bytes(), Path(site_photo.image.path).read_bytes())          # nguyên byte
        self.assertEqual(os.stat(copied).st_ino, os.stat(site_photo.image.path).st_ino)          # liên kết cứng
        money_csv = self.csv_text(f"{money_dir}/danh-sach.csv")
        self.assertIn("5.000.000 ₫", money_csv)
        self.assertIn("Cát Hùng Vương", money_csv)
        self.assertIn("cát vàng đợt 2", money_csv)
        self.assertNotIn("khách chuyển khoản", money_csv)                  # thu khách để thư mục riêng
        customer_csv = self.csv_text(f"{customer_dir}/danh-sach.csv")
        self.assertIn("30.000.000 ₫", customer_csv)
        self.assertIn("(chỉ có chữ)", customer_csv)
        self.assertIn("TN-22 T1.2 40%", self.csv_text(f"{site_dir}/danh-sach.csv"))
        self.assertEqual(archive.export(), 0)                               # chạy lại: không chép trùng
        self.assertEqual(self.files(), files)

    def test_loose_and_pending_photos_wait_then_go_to_unrecorded(self):
        from chat import archive
        from chat.models import ChatPhoto
        self.message(self.accountant, "", photo="le1")                    # ảnh lẻ, không ai lập phiếu
        self.message(self.site, "S1.1", photo="cho1")
        self.message(self.site, "TN-22 S1.1 50%")                          # phiếu chờ xác nhận
        self.assertEqual(archive.export(), 0)                               # chưa chốt: chưa xuất
        later = timezone.now() + timedelta(hours=26)
        self.assertEqual(archive.export(now=later), 2)
        loose, pending = ChatPhoto.objects.order_by("pk")
        day = timezone.localdate()
        self.assertTrue(loose.archived_as.startswith(f"Chưa ghi phiếu/{day:%Y-%m}/{day.isoformat()}/"))
        self.assertIn("_chưa gắn phiếu_", loose.archived_as)
        self.assertIn("_phiếu hết hạn_", pending.archived_as)
        index = self.csv_text(f"Chưa ghi phiếu/{day:%Y-%m}/{day.isoformat()}/danh-sach.csv")
        self.assertIn("Hết hạn, không ghi", index)
        self.assertIn("TN-22 S1.1 50%", index)

    def test_image_file_kept_other_files_refused(self):
        from chat.models import ChatPhoto

        def send(document):
            bot.handle_update({"update_id": next(self.update_id), "message": {
                "message_id": next(self.next_id), "chat": {"id": int(self.chats[self.accountant.pk]), "type": "private"},
                "from": {"id": self.accountant.pk + 9000}, "document": document, "caption": "phiếu chi viết tay"}})
        send({"file_id": "goc1", "file_unique_id": "goc1", "file_size": 100, "mime_type": "image/png"})
        photo = ChatPhoto.objects.get()
        self.assertEqual(photo.caption, "phiếu chi viết tay")
        send({"file_id": "pdf1", "file_unique_id": "pdf1", "file_size": 100, "mime_type": "application/pdf"})
        self.assertEqual(ChatPhoto.objects.count(), 1)
        self.assertIn("JPG, PNG hoặc WEBP", self.texts()[-1])


class AttendanceWordingTests(BotCase):
    """Tin thật của Giám đốc 20/11/2026: chấm công không có dấu hai chấm, có chữ "thợ phụ"/"thợ chính"."""

    def test_owner_message_reads_people_and_kinds(self):
        from field.models import Attendance, Worker
        self.message(self.director, "Báo cáo + chấm công hôm nay tn-22 t1.2 58% Công Thắng thợ phụ, Thuận thợ chính")
        proposal = self.proposal()
        people = {p["name"]: (p["status"], p.get("new", {}).get("kind")) for p in proposal.payload["people"]}
        self.assertEqual(people, {"Thắng": ("new_ok", "phu"), "Thuận": ("new_ok", "tho")})
        summary = bot.summary(proposal)
        self.assertIn("(giám đốc, có mặt) + 2 người", summary)
        self.assertIn("Thắng: 1 công · thêm thợ mới (phụ)", summary)
        self.assertIn("✓ Xác nhận", self.last_buttons(self.chats[self.director.pk]))
        self.press(self.director, f"ok:{proposal.pk}")
        self.assertEqual({(w.name, w.kind) for w in Worker.objects.filter(name__in=["Thắng", "Thuận"])}, {("Thắng", "phu"), ("Thuận", "tho")})
        self.assertEqual(Attendance.objects.filter(worker__name__in=["Thắng", "Thuận"]).count(), 2)
        self.message(self.site, "TN-22 T1.2 60% công Thắng, Nam phụ")              # lần sau: khớp người có sẵn
        names = {p["name"]: p["status"] for p in self.proposal().payload["people"]}
        self.assertEqual(names, {"Thắng": "same", "Nam": "ok"})                    # Thắng đã chấm hôm nay: khớp người, giữ nguyên

    def test_unread_part_is_flagged_not_silently_dropped(self):
        self.message(self.site, "TN-22 T1.2 40% Thắng, Thuận")
        summary = bot.summary(self.proposal())
        self.assertIn("CHƯA ĐỌC được phần: \"Thắng, Thuận\"", summary)
        self.assertIn("chưa có thợ nào", summary)

    def test_parse_variants(self):
        from types import SimpleNamespace
        from . import parse
        projects = [SimpleNamespace(code="TN-22")]
        cases = {
            "TN-22 T2.5 40% S2.1 xong. Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ": ["Hùng", "Nam", "Lan", "Tâm"],
            "chấm công hôm nay TN-22: Hùng, Nam phụ": ["Hùng", "Nam"],
            "TN-22 T1.2 40% công nhân: Phú 4h": ["Phú"],                         # tên một chữ "Phú" không bị coi là "phụ"
            "TN-22 công trình T1.2 40% xong sớm": [],                             # "công trình" không phải chấm công
        }
        for text, names in cases.items():
            self.assertEqual([p["name"] for p in parse.report(text, projects)["people"]], names, text)


class WorkerPageAddTests(BotCase):
    def test_add_worker_on_web(self):
        from field.models import Worker
        self.client.force_login(self.accountant)
        self.client.post(reverse("field_workers"), {"action": "add", "name": "Thắng", "team": "xây", "kind": "phu",
                                                    "pay": "cong_nhat", "day_rate": "350.000"})
        worker = Worker.objects.get(name="Thắng")
        self.assertEqual((worker.kind, worker.team, worker.day_rate), ("phu", "xây", Decimal(350000)))
        self.assertTrue(AuditLog.objects.filter(table="field.Worker", object_id=str(worker.pk), action="create", channel="web").exists())
        page = self.client.post(reverse("field_workers"), {"action": "add", "name": "thang"}, follow=True)
        self.assertContains(page, "Đã có thợ tên")
        self.client.force_login(self.site)
        self.client.post(reverse("field_workers"), {"action": "add", "name": "Tuấn", "kind": "tho", "day_rate": "999"})
        self.assertIsNone(Worker.objects.get(name="Tuấn").day_rate)              # giám sát thêm thợ, không đặt giá
        self.client.force_login(self.designer)
        self.assertEqual(self.client.post(reverse("field_workers"), {"action": "add", "name": "X"}).status_code, 403)
