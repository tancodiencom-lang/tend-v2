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
        self.chats[user.pk] = str(user.pk + 500)
        self.message(user, f"/ketnoi {bot.new_code(user)}")

    def proposal(self):
        return Proposal.objects.latest("created_at")


class LinkTests(BotCase):
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

    def test_group_chat_ignored_and_update_once(self):
        bot.handle_update({"update_id": 5000, "message": {"chat": {"id": 1, "type": "group"}, "from": {"id": 1}, "text": "/tongquan"}})
        bot.handle_update({"update_id": 5000, "message": {"chat": {"id": 1, "type": "private"}, "from": {"id": 1}, "text": "x"}})
        self.assertEqual(self.sent, [])

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
