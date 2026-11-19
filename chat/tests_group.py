"""Nhóm Telegram nội bộ và câu hỏi chờ Giám đốc; ảnh nhân viên."""
import io
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from assistant import schedule
from assistant.models import Feedback, Question
from core.models import AuditLog, User
from field.models import Report
from projects.models import Project

from . import ai, bot, reads
from .models import Group, GroupViewer
from .tests import TMP, BotCase

GROUP = -100777


def png(size=(40, 30)):
    buffer = io.BytesIO()
    Image.new("RGB", size, (30, 90, 60)).save(buffer, "PNG")
    return buffer.getvalue()


class GroupCase(BotCase):
    def say(self, user, text="", photo=None, reply_to_bot=False, from_id=None):
        msg = {"message_id": next(self.next_id), "chat": {"id": GROUP, "type": "supergroup", "title": "Công trường TN-22"},
               "from": {"id": from_id or user.pk + 9000, "first_name": getattr(user, "username", "lạ")}}
        if photo:
            msg["photo"] = [{"file_id": photo, "file_unique_id": photo, "file_size": 100, "width": 8}]
            if text:
                msg["caption"] = text
        else:
            msg["text"] = text
        if reply_to_bot:
            msg["reply_to_message"] = {"message_id": 1, "from": {"id": 1, "is_bot": True, "username": "tend_thu_bot"}}
        with self.captureOnCommitCallbacks(execute=True):
            bot.handle_update({"update_id": next(self.update_id), "message": msg})
        return msg["message_id"]

    def group_texts(self):
        return [d["text"] for m, d in self.sent if m == "sendMessage" and str(d["chat_id"]) == str(GROUP)]

    def link_group(self):
        self.say(self.director, "/nhom TN-22")
        self.sent.clear()


class GroupTests(GroupCase):
    def test_added_intro_and_only_director_links(self):
        bot.handle_update({"update_id": next(self.update_id), "my_chat_member": {
            "chat": {"id": GROUP, "type": "supergroup", "title": "Công trường TN-22"},
            "new_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True}}}})
        self.assertIn("Giám đốc nhắn /nhom", self.group_texts()[-1])
        self.assertFalse(Group.objects.get().active)
        self.say(self.site, "@tend_thu_bot TN-22 đang bao nhiêu %?")
        self.assertIn("chưa được gắn", self.group_texts()[-1])
        self.say(self.site, "/nhom TN-22")
        self.assertIn("Chỉ Giám đốc", self.group_texts()[-1])
        self.say(self.director, "/nhom@tend_thu_bot TN-22")
        group = Group.objects.get()
        self.assertEqual((group.active, group.project, group.linked_by), (True, self.project, self.director))
        self.assertTrue(AuditLog.objects.filter(table="chat.Group", actor=self.director).exists())

    def test_report_in_group_becomes_private_proposal(self):
        self.link_group()
        self.say(self.site, "T1.2", photo="nhom1")
        self.say(self.site, "TN-22 T1.2 40%")
        proposal = self.proposal()
        self.assertEqual(proposal.link.user, self.site)
        self.assertEqual(proposal.photos.count(), 1)
        self.assertTrue(any("PHIẾU CHỜ XÁC NHẬN" in t for t in self.texts(self.chats[self.site.pk])))
        self.assertFalse(any("PHIẾU CHỜ XÁC NHẬN" in t for t in self.group_texts()))
        self.assertIn("gửi riêng Minh bấm Xác nhận", self.group_texts()[-1])
        self.press(self.site, f"ok:{proposal.pk}")
        self.assertEqual(Report.objects.count(), 1)

    def test_chit_chat_and_strangers_are_quiet(self):
        self.link_group()
        self.say(self.site, "ok anh, chiều em qua")
        self.assertEqual(self.sent, [])
        self.assertFalse(Feedback.objects.exists())
        stranger = User(username="la", pk=99999)
        self.say(stranger, "@tend_thu_bot cho hỏi giá", from_id=55555)
        self.assertIn("chưa kết nối Tend", self.group_texts()[-1])
        self.say(stranger, "@tend_thu_bot hỏi lại", from_id=55555)
        self.assertEqual(len(self.group_texts()), 1)                      # nhắc một lần mỗi ngày
        self.say(self.site, "/nhom khongnghe")
        self.say(self.director, "/nhom khongnghe")
        self.sent.clear()
        self.say(self.site, "TN-22 T1.2 40%")
        self.assertFalse(Group.objects.get().listening)
        self.assertEqual(self.sent, [])

    def test_command_in_group_is_answered_privately(self):
        self.link_group()
        self.say(self.site, "/viec@tend_thu_bot")
        self.assertIn("không có việc", self.texts(self.chats[self.site.pk])[-1])
        self.assertIn("trả lời riêng Minh", self.group_texts()[-1])

    def test_group_ai_has_no_money_tools_and_only_group_project(self):
        self.link_group()
        Project.objects.create(code="BH-01", name="Biệt thự")
        seen = {}

        def fake_run(text, history, context, execute, images=(), tools=None):
            seen["tools"] = {t["name"] for t in tools}
            seen["context"] = context
            seen["overview"] = execute("project_overview", {"project_code": "TN-22"})
            try:
                execute("project_overview", {"project_code": "BH-01"})
            except Exception as error:   # công trình khác nhóm: không cho xem
                seen["other"] = str(error)
            return {"type": "text", "text": "TN-22 đang 0%."}

        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run", side_effect=fake_run):
            self.say(self.director, "@tend_thu_bot TN-22 lãi bao nhiêu?")
        self.assertFalse(seen["tools"] & {"money_summary", "list_entries", "propose_entries"})
        self.assertNotIn("profit", seen["overview"])
        self.assertNotIn("payables", seen["overview"])
        self.assertIn("BH-01", seen["other"])
        self.assertIn("KHÔNG nêu số tiền", seen["context"])
        self.assertEqual(self.group_texts()[-1], "🤖 Trợ lý: TN-22 đang 0%.")
        viewer = GroupViewer.of(self.director, self.project)
        self.assertEqual([p.code for p in reads.projects(viewer)], ["TN-22"])


class QuestionTests(GroupCase):
    def test_group_question_goes_to_director_and_answer_comes_back(self):
        self.link_group()
        asked = self.say(self.site, "@tend_thu_bot khách muốn đổi màu sơn phòng ngủ được không?")
        question = Question.objects.get()
        self.assertEqual((question.asked_by, question.chat_id, question.message_id), (self.site, str(GROUP), asked))
        self.assertIn("em hỏi Giám đốc", self.group_texts()[-1])
        note = self.texts(self.chats[self.director.pk])[-1]
        self.assertIn(f"CÂU HỎI CHỜ ANH #{question.pk}", note)
        self.assertEqual(set(self.last_buttons(self.chats[self.director.pk])), {"✍ Tôi trả lời", "× Không cần"})
        self.press(self.director, f"qw:{question.pk}")
        with self.captureOnCommitCallbacks(execute=True):
            self.message(self.director, "Được, báo thiết kế cập nhật bản vẽ và báo lại khách giá chênh.")
        question.refresh_from_db()
        self.assertEqual(question.state, Question.State.ANSWERED)
        posted = [d for m, d in self.sent if m == "sendMessage" and str(d["chat_id"]) == str(GROUP)][-1]
        self.assertIn("Giám đốc trả lời câu hỏi của Minh", posted["text"])
        self.assertEqual(posted["reply_parameters"]["message_id"], asked)
        self.assertTrue(AuditLog.objects.filter(table="assistant.Question", action="update", actor=self.director).exists())
        self.press(self.director, f"qw:{question.pk}")                     # đã trả lời: không trả lời lần hai
        self.assertIn("không chờ anh", self.texts(self.chats[self.director.pk])[-1])

    def test_ai_draft_approved_with_one_tap(self):
        self.link_group()
        result = {"type": "final", "tool": "ask_director", "input": {"question": "Minh hỏi có cho tổ điện tăng ca CN không",
                                                                      "draft": "Được tăng ca, ghi công riêng ngày CN."}}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run", return_value=result):
            self.say(self.site, "@tend_thu_bot chủ nhật cho tổ điện tăng ca được không")
        question = Question.objects.get()
        self.assertIn("✓ Gửi bản nháp", self.last_buttons(self.chats[self.director.pk]))
        with self.captureOnCommitCallbacks(execute=True):
            self.press(self.director, f"qa:{question.pk}")
        self.assertIn("Được tăng ca, ghi công riêng ngày CN.", self.group_texts()[-1])

    def test_private_hoi_drop_and_reminder(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.message(self.site, "/hoi tuần sau có nghỉ lễ không anh")
        question = Question.objects.get()
        self.assertEqual(question.chat_id, self.chats[self.site.pk])
        Question.objects.filter(pk=question.pk).update(created_at=timezone.now() - timedelta(hours=3))
        done = schedule.run(timezone.make_aware(datetime(2026, 11, 23, 10, 0)))
        self.assertIn(f"q:{question.pk}:nhac:{self.director.pk}", done)
        self.press(self.director, f"qx:{question.pk}")
        question.refresh_from_db()
        self.assertEqual(question.state, Question.State.DROPPED)
        self.assertIn("không cần trả lời", self.texts(self.chats[self.site.pk])[-1])


@override_settings(MEDIA_ROOT=TMP)
class AvatarTests(BotCase):
    def test_upload_shows_in_tab_and_colleagues_can_see(self):
        self.client.force_login(self.site)
        page = self.client.get(reverse("account"))
        self.assertContains(page, '<span class="avatar" aria-hidden="true">MI</span>', html=False)
        self.assertContains(page, "<span>Minh</span>", html=False)
        response = self.client.post(reverse("account"), {"anh": SimpleUploadedFile("a.png", png((1200, 900)), "image/png")})
        self.assertEqual(response.status_code, 302)
        self.site.refresh_from_db()
        with Image.open(self.site.avatar.path) as image:
            self.assertEqual((image.format, max(image.size)), ("JPEG", 512))
        self.assertTrue(AuditLog.objects.filter(table="core.User", reason="Đổi ảnh nhân viên").exists())
        self.assertContains(self.client.get(reverse("overview")), reverse("user_avatar", args=[self.site.pk]))
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.get(reverse("user_avatar", args=[self.site.pk])).status_code, 200)
        self.client.logout()
        self.assertEqual(self.client.get(reverse("user_avatar", args=[self.site.pk])).status_code, 302)

    def test_bad_file_rejected_and_remove(self):
        self.client.force_login(self.site)
        page = self.client.post(reverse("account"), {"anh": SimpleUploadedFile("a.png", b"khong phai anh", "image/png")})
        self.assertContains(page, "không phải ảnh")
        self.client.post(reverse("account"), {"anh": SimpleUploadedFile("a.png", png(), "image/png")})
        self.client.post(reverse("account"), {"bo_anh": "1"})
        self.site.refresh_from_db()
        self.assertFalse(self.site.avatar)

    def test_copy_from_old_app(self):
        folder = Path(tempfile.mkdtemp(prefix="anh-cu-"))
        (folder / "nhan-vien/anh/7").mkdir(parents=True)
        (folder / "nhan-vien/anh/7/x.png").write_bytes(png())
        db = folder / "cu.sqlite3"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE core_user (username text, avatar text)")
        con.executemany("INSERT INTO core_user VALUES (?, ?)", [("giamsat", "nhan-vien/anh/7/x.png"), ("ketoan", "../../etc/passwd")])
        con.commit()
        con.close()
        out = io.StringIO()
        call_command("chep_anh_nhan_vien", "--nguon", f"sqlite:///{db}", "--thu-muc", str(folder), stdout=out)
        self.site.refresh_from_db()
        self.assertFalse(self.site.avatar)                                 # xem trước: chưa ghi
        call_command("chep_anh_nhan_vien", "--nguon", f"sqlite:///{db}", "--thu-muc", str(folder), "--thuc-hien", stdout=out)
        self.site.refresh_from_db()
        self.assertTrue(self.site.avatar)
        self.assertIn("không thấy tệp ../../etc/passwd", out.getvalue())    # không đọc ra ngoài thư mục ảnh
        self.assertFalse(User.objects.get(username="ketoan").avatar)
