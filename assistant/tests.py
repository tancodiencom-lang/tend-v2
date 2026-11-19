"""Trợ lý Giám đốc: giao việc qua bot, nhận/báo xong/trả lại/nhận kết quả, lịch nhắc, cảnh báo, bản tin, góp ý, trang web."""
from datetime import date, datetime, timedelta
from unittest import mock

from django.urls import reverse
from django.utils import timezone

from chat import ai
from chat.models import Proposal
from chat.tests import BotCase
from core.models import AuditLog
from drawings.models import Item
from field.models import Report
from projects.models import Project

from . import schedule, services
from .models import Feedback, Notice, Task

MONDAY = date(2026, 11, 23)


def at(day, hour, minute=0):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


class AssistantCase(BotCase):
    def assistant_texts(self, user):
        return [t for t in self.texts(self.chats[user.pk]) if t.startswith(schedule.PREFIX)]

    def give(self, due, title="Kiểm tra chống thấm sàn vệ sinh"):
        return services.create_task(self.director, self.site, title, due, self.project, "có ảnh thử nước 24 giờ")


class TaskFlowTests(AssistantCase):
    def test_give_accept_report_send_back_and_approve(self):
        self.message(self.director, "/giao Minh kiểm tra chống thấm sàn vệ sinh TN-22 hạn mai 9:00 - có ảnh thử nước 24 giờ")
        proposal = self.proposal()
        self.assertEqual(proposal.kind, Proposal.Kind.TASK)
        text = self.texts(self.chats[self.director.pk])[-1]
        self.assertIn("Người làm: Minh", text)
        self.assertIn("Công trình: TN-22", text)
        self.assertIn("09:00", text)
        self.assertEqual(Task.objects.count(), 0)                         # chưa xác nhận: chưa giao
        with self.captureOnCommitCallbacks(execute=True):
            self.press(self.director, f"ok:{proposal.pk}")
        task = Task.objects.get()
        self.assertEqual((task.owner, task.project, task.criteria), (self.site, self.project, "có ảnh thử nước 24 giờ"))
        self.assertTrue(AuditLog.objects.filter(table="assistant.Task", action="create", actor=self.director).exists())
        news = self.assistant_texts(self.site)[-1]
        self.assertIn(f"VIỆC MỚI #{task.pk}", news)
        self.assertEqual(self.last_buttons(self.chats[self.site.pk]), {"👍 Nhận việc": f"tn:{task.pk}"})
        self.press(self.site, f"tn:{task.pk}")
        task.refresh_from_db()
        self.assertIsNotNone(task.accepted_at)
        self.message(self.site, "/viec")
        self.assertIn(f"#{task.pk} Kiểm tra chống thấm", self.texts(self.chats[self.site.pk])[-1])
        self.message(self.site, f"/xong {task.pk} đã thử nước, ảnh gửi kèm báo cáo")
        task.refresh_from_db()
        self.assertEqual(task.state, Task.State.REPORTED)
        self.assertEqual(self.last_buttons(self.chats[self.director.pk]), {"✓ Nhận kết quả": f"ta:{task.pk}", "↩ Trả lại": f"tr:{task.pk}"})
        self.press(self.director, f"tr:{task.pk}")
        self.assertIn("Nhắn lý do trả lại", self.texts(self.chats[self.director.pk])[-1])
        self.message(self.director, "thiếu ảnh góc sàn phía cửa")
        task.refresh_from_db()
        self.assertEqual((task.state, task.close_note), (Task.State.OPEN, "thiếu ảnh góc sàn phía cửa"))
        self.assertIn("TRẢ LẠI", self.assistant_texts(self.site)[-1])
        self.message(self.site, f"/xong {task.pk} đã bổ sung ảnh")
        self.press(self.director, f"ta:{task.pk}")
        task.refresh_from_db()
        self.assertEqual(task.state, Task.State.DONE)
        self.assertIn("đã nhận kết quả", self.assistant_texts(self.site)[-1])
        self.press(self.site, f"ta:{task.pk}")                             # người làm không tự nhận kết quả
        self.assertEqual(Task.objects.get().state, Task.State.DONE)

    def test_rules_for_giving_and_cancelling(self):
        self.message(self.site, "/giao Minh làm gì đó hạn mai")
        self.assertIn("Chỉ Giám đốc", self.texts(self.chats[self.site.pk])[-1])
        self.message(self.director, "/giao Hoa làm gì đó hạn mai")
        self.assertIn("Không thấy người tên", self.texts()[-1])
        self.message(self.director, "/giao Minh làm gì đó")
        self.assertIn("cần có hạn", self.texts()[-1])
        Project.objects.create(code="BH-01", name="Biệt thự")
        self.message(self.director, "/giao Minh khảo sát BH-01 hạn mai")
        self.assertIn("không thuộc công trình BH-01", self.texts()[-1])
        task = self.give(timezone.now() + timedelta(days=1))
        self.message(self.director, f"/huyviec {task.pk}")
        self.assertIn("lý do", self.texts()[-1])
        self.message(self.director, f"/huyviec {task.pk} khách đổi thiết kế")
        task.refresh_from_db()
        self.assertEqual((task.state, task.close_note), (Task.State.CANCELLED, "khách đổi thiết kế"))
        self.assertIn("huỷ việc", self.assistant_texts(self.site)[-1])
        self.message(self.designer, f"/xong {task.pk}")                      # không phải việc của mình: không thấy
        self.assertIn("Cú pháp", self.texts(self.chats[self.designer.pk])[-1])

    def test_ai_turns_instruction_into_task_proposal(self):
        due = (timezone.localdate() + timedelta(days=2)).isoformat()
        result = {"type": "final", "tool": "propose_task", "input": {"owner_name": "giamsat", "title": "Gửi bản vẽ điện tầng 2",
                                                                     "due_date": due, "due_time": "10:00", "project_code": "TN-22"}}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run", return_value=result):
            self.message(self.director, "nhắc Minh gửi bản vẽ điện tầng 2 TN-22 trước 10 giờ ngày kia")
        proposal = self.proposal()
        self.assertEqual((proposal.kind, proposal.payload["owner_id"]), (Proposal.Kind.TASK, self.site.pk))
        self.assertIn("10:00", self.texts()[-1])
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run", return_value=result):
            self.message(self.site, "giao cho Nam việc này")
        self.assertIn("Chỉ Giám đốc", self.texts(self.chats[self.site.pk])[-1])

    def test_feedback_and_friction_logged(self):
        self.message(self.site, "/gopy nút xác nhận nhỏ quá trên điện thoại")
        self.message(self.site, "abc xyz không rõ là gì")
        self.assertEqual(list(Feedback.objects.order_by("pk").values_list("kind", flat=True)), ["user", "friction"])
        self.assertTrue(AuditLog.objects.filter(table="assistant.Feedback").exists())


class ScheduleTests(AssistantCase):
    def test_morning_reminders_escalation_without_duplicates(self):
        task = self.give(at(MONDAY, 10))
        Task.objects.filter(pk=task.pk).update(created_at=at(MONDAY, 6))
        done = schedule.run(at(MONDAY, 7, 35))
        self.assertIn(f"morning:{MONDAY}:{self.director.pk}", done)
        self.assertIn(f"today:{MONDAY}:{self.site.pk}", done)
        bulletin = next(t for t in self.assistant_texts(self.director) if "BẢN TIN SÁNG" in t)
        self.assertIn(f"Hạn việc: #{task.pk}", bulletin)
        self.assertIn(f"#{task.pk} Kiểm tra chống thấm", next(t for t in self.assistant_texts(self.site) if "VIỆC HÔM NAY" in t))
        self.assertEqual([k for k in schedule.run(at(MONDAY, 7, 45)) if k.startswith(("morning", "today"))], [])
        self.assertIn(f"task:{task.pk}:soon", schedule.run(at(MONDAY, 8, 10)))
        self.assertIn(f"task:{task.pk}:late", schedule.run(at(MONDAY, 10, 30)))
        self.assertNotIn(f"task:{task.pk}:esc:{self.director.pk}", schedule.run(at(MONDAY, 12, 0)))
        self.assertIn(f"task:{task.pk}:esc:{self.director.pk}", schedule.run(at(MONDAY, 14, 31)))
        self.assertIn("Việc trễ sau khi đã nhắc", self.assistant_texts(self.director)[-1])

    def test_quiet_on_day_off_and_at_night(self):
        self.give(at(MONDAY, 10))
        sunday = MONDAY - timedelta(days=1)
        self.assertEqual(schedule.run(at(sunday, 7, 35)), [])
        self.assertEqual([k for k in schedule.run(at(MONDAY, 22, 0))], [])

    def test_daily_report_reminders_then_director(self):
        self.assertIn(f"rp16:{MONDAY}:{self.project.pk}:{self.site.pk}", schedule.run(at(MONDAY, 16, 5)))
        self.assertIn(f"rp17:{MONDAY}:{self.project.pk}:{self.site.pk}", schedule.run(at(MONDAY, 17, 10)))
        self.assertIn(f"rp19:{MONDAY}:{self.director.pk}", schedule.run(at(MONDAY, 19, 5)))
        self.assertTrue(any("chưa có báo cáo hôm nay" in t and "TN-22 (Minh)" in t for t in self.assistant_texts(self.director)))
        tuesday = MONDAY + timedelta(days=1)
        Report.objects.create(project=self.project, author=self.site, work_date=tuesday,
                              client_key="00000000-0000-0000-0000-000000000001")
        self.assertEqual([k for k in schedule.run(at(tuesday, 16, 5)) if k.startswith("rp")], [])

    def test_voucher_reminder_and_evening_summary(self):
        from money.models import Party
        Party.objects.create(name="Cát Hùng Vương", type="ncc")
        self.message(self.accountant, "trả NCC 5tr Cát Hùng Vương TN-22 PC12 ck - cát vàng đợt 2")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        today = timezone.localdate()
        if today.isoweekday() not in schedule.work_days():
            today = today - timedelta(days=1)
        done = schedule.run(at(today, 18, 5))
        if today == timezone.localdate():
            self.assertIn(f"vc:{today}:{self.accountant.pk}", done)
            self.assertIn("PC12 5.000.000 ₫", self.assistant_texts(self.accountant)[-1])
        schedule.run(at(today, 18, 35))
        summary = next(t for t in self.assistant_texts(self.director) if "TÓM TẮT NGÀY" in t)
        self.assertIn("TN-22", summary)
        self.assertIn("CHƯA CÓ BÁO CÁO", summary)

    def test_urgent_loss_alert_once_a_week_and_money_only_to_director(self):
        Item.objects.filter(project=self.project).update(sale_price=100, cost_price=1000)
        found = schedule.alerts(MONDAY)
        self.assertIn("loss", [a["key"] for a in found])
        done = schedule.run(at(MONDAY, 9, 0))
        self.assertIn(f"alert:loss:{self.project.pk}:2026-48:{self.director.pk}", done)
        self.assertFalse(any("lãi dự báo âm" in t for t in self.assistant_texts(self.site)))
        self.assertEqual([k for k in schedule.run(at(MONDAY + timedelta(days=1), 9, 0)) if k.startswith("alert:loss")], [])
        self.assertTrue(Notice.objects.filter(key__startswith="alert:loss").exists())

    def test_monday_bulletin_lists_feedback(self):
        services.add_feedback(self.site, "nút xác nhận nhỏ quá")
        schedule.run(at(MONDAY, 7, 35))
        bulletin = next(t for t in self.assistant_texts(self.director) if "BẢN TIN SÁNG" in t)
        self.assertIn("Góp ý tuần qua: 1 góp ý", bulletin)
        self.assertIn("nút xác nhận nhỏ quá", bulletin)


class PageTests(AssistantCase):
    def test_director_sees_all_and_feedback_others_only_own(self):
        task = self.give(timezone.now() + timedelta(days=1))
        services.add_feedback(self.site, "góp ý thử")
        self.client.force_login(self.director)
        page = self.client.get(reverse("assistant"))
        self.assertContains(page, f"#{task.pk}")
        self.assertContains(page, "góp ý thử")
        self.client.force_login(self.site)
        page = self.client.get(reverse("assistant"))
        self.assertContains(page, f"#{task.pk}")
        self.assertNotContains(page, "góp ý thử")
        self.client.force_login(self.accountant)
        self.assertNotContains(self.client.get(reverse("assistant")), f"#{task.pk} ")


class WebActionTests(AssistantCase):
    def test_owner_accepts_and_reports_on_web_director_decides(self):
        task = self.give(timezone.now() + timedelta(days=1))
        self.client.force_login(self.site)
        page = self.client.get(reverse("assistant"))
        self.assertContains(page, "Người giao")
        self.assertContains(page, str(self.director))
        self.assertContains(page, "👍 Nhận việc")
        self.client.post(reverse("assistant_act", args=[task.pk, "nhan"]))
        task.refresh_from_db()
        self.assertIsNotNone(task.accepted_at)
        self.assertTrue(AuditLog.objects.filter(table="assistant.Task", reason="Nhận việc", channel="web").exists())
        self.assertEqual(self.client.post(reverse("assistant_act", args=[task.pk, "nhan-ket-qua"])).status_code, 302)
        self.assertEqual(Task.objects.get().state, Task.State.OPEN)                 # người làm không tự nhận kết quả
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("assistant_act", args=[task.pk, "xong"]), {"ghi_chu": "đã thử nước 24 giờ"})
        self.assertEqual(Task.objects.get().state, Task.State.REPORTED)
        self.assertEqual(self.last_buttons(self.chats[self.director.pk]), {"✓ Nhận kết quả": f"ta:{task.pk}", "↩ Trả lại": f"tr:{task.pk}"})
        self.client.force_login(self.director)
        self.client.post(reverse("assistant_act", args=[task.pk, "tra-lai"]), {"ghi_chu": ""})
        self.assertEqual(Task.objects.get().state, Task.State.REPORTED)             # trả lại phải có lý do
        with self.captureOnCommitCallbacks(execute=True):
            self.client.post(reverse("assistant_act", args=[task.pk, "nhan-ket-qua"]))
        self.assertEqual(Task.objects.get().state, Task.State.DONE)
        self.assertIn("đã nhận kết quả", self.assistant_texts(self.site)[-1])
        self.client.force_login(self.accountant)
        self.assertEqual(self.client.post(reverse("assistant_act", args=[task.pk, "nhan"])).status_code, 404)

    def test_staff_page_sets_phone_director_only(self):
        self.client.force_login(self.site)
        self.assertEqual(self.client.get(reverse("staff")).status_code, 403)
        self.client.force_login(self.director)
        page = self.client.get(reverse("staff"))
        self.assertContains(page, "✓ Đã kết nối")
        self.client.post(reverse("staff"), {"nguoi": self.designer.pk, "dien_thoai": "+84 912 000 111"})
        self.designer.refresh_from_db()
        self.assertEqual(self.designer.phone, "0912000111")
        self.assertTrue(AuditLog.objects.filter(table="core.User", reason__startswith="Giám đốc sửa số").exists())
        page = self.client.post(reverse("staff"), {"nguoi": self.accountant.pk, "dien_thoai": "0912000111"}, follow=True)
        self.assertContains(page, "đã ghi cho tài khoản khác")
        page = self.client.post(reverse("staff"), {"nguoi": self.accountant.pk, "dien_thoai": "12345"}, follow=True)
        self.assertContains(page, "không đúng dạng")
