"""Đợt 8a: báo cáo nhắn tự do (việc ngoài bản vẽ, kế hoạch ngày mai, nhân sự không tên), chấm mức đầy đủ, thống kê tuần,
chỗ vướng mỗi lần bot hỏi lại, che số điện thoại."""
import importlib
from datetime import datetime, timedelta
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest import mock

from django.apps import apps
from django.urls import reverse
from django.utils import timezone
from openpyxl import load_workbook

from chat import ai, bot, parse
from chat.models import Proposal
from chat.tests import BotCase
from core.models import AuditLog
from core.text import mask_phones
from drawings.models import Item
from field import quality
from field.models import ExtraWork, PlanLine, Report, ReportLine
from overview import compute, exports
from projects.models import Member, Project

from . import schedule, stats
from .models import Feedback

EXAMPLE = ("Báo cáo cv {day}\n- Nhân sự: thợ xây: 3 thợ 2 phụ\n- Công việc\n+ Trát thô tường phòng ngoài tầng 5.\n+ Trát wc tầng 5.\n"
           "+ Đánh lạnh tường phòng trong t5.\n+ Cắt đục sàn bê tông sàn mái\n- cv dự kiến {plan}\n+ Đánh lạnh phòng ngoài tầng 5.\n"
           "+ Trát tường tầng 4.")


def at(day, hour, minute=0):
    return timezone.make_aware(datetime(day.year, day.month, day.day, hour, minute))


class ParseTests(BotCase):
    def test_owner_example_flat_and_multiline(self):
        projects = [SimpleNamespace(code="TN-22")]
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        multiline = EXAMPLE.format(day=f"{yesterday:%d/%m}", plan=f"{today:%d/%m}")
        for text in (multiline, multiline.replace("\n", " ")):
            data = parse.report(text, projects, today=today)
            self.assertEqual(data["lines"], [], text)                                   # không mã: không ép gắn chi tiết
            self.assertEqual([(e["floor"], e["description"]) for e in data["extra"]], [
                ("Tầng 5", "Trát thô tường phòng ngoài tầng 5"), ("Tầng 5", "Trát wc tầng 5"),
                ("Tầng 5", "Đánh lạnh tường phòng trong t5"), ("Mái", "Cắt đục sàn bê tông sàn mái")], text)
            self.assertEqual(data["plan"]["date"], today.isoformat())
            self.assertEqual([(l["floor"], l["description"]) for l in data["plan"]["lines"]],
                             [("Tầng 5", "Đánh lạnh phòng ngoài tầng 5"), ("Tầng 4", "Trát tường tầng 4")])
            self.assertEqual(data["crew"], "thợ xây: 3 thợ 2 phụ")
            self.assertEqual(data["people"], [])                                       # "3 thợ 2 phụ" không phải tên
            self.assertEqual(data["work_date"], yesterday.isoformat())

    def test_codes_without_dot_plan_not_done_and_old_syntax_kept(self):
        projects = [SimpleNamespace(code="TN-22")]
        known = lambda project: ["D179", "D207", "T1.2"]
        data = parse.report("TN-22 19/11 D179 20%; D207 30%. Công: Hậu thợ; Thắng phụ, Thuận phụ", projects, codes=known)
        self.assertEqual(data["lines"], [{"code": "D179", "percent": 20.0}, {"code": "D207", "percent": 30.0}])
        self.assertEqual([(p["name"], p.get("kind")) for p in data["people"]], [("Hậu", "tho"), ("Thắng", "phu"), ("Thuận", "phu")])
        data = parse.report("TN-22 T1.2 50%\n+ D179 trát xong\nDự kiến mai: D207, đổ bê tông sàn mái\nCông: Hùng, Nam", projects,
                            codes=known)
        self.assertEqual([l["code"] for l in data["lines"]], ["T1.2", "D179"])          # D207 chỉ là dự kiến
        self.assertEqual([(l.get("code"), l["description"]) for l in data["plan"]["lines"]],
                         [("D207", "D207"), (None, "đổ bê tông sàn mái")])
        self.assertEqual([p["name"] for p in data["people"]], ["Hùng", "Nam"])         # chấm công sau phần dự kiến
        self.assertIsNone(parse.report("TN-22 hôm nay mưa to nghỉ", projects))           # chuyện thường không thành báo cáo
        self.assertEqual(parse.report("TN-22 T1.2 40%; Thắng, Thuận", projects)["unread"], "Thắng, Thuận")
        self.assertEqual(parse.report("Nhân công: 3 thợ 2 phụ. TN-22 T1.2 40%", projects)["crew"], "3 thợ 2 phụ")


class FreeReportTests(BotCase):
    def send_example(self, user=None):
        today = timezone.localdate()
        yesterday = today - timedelta(days=1)
        self.message(user or self.site, EXAMPLE.format(day=f"{yesterday:%d/%m}", plan=f"{today:%d/%m}"))
        return self.proposal()

    def test_free_report_keeps_extra_work_plan_and_crew_scores_low(self):
        proposal = self.send_example()
        summary = bot.summary(proposal)
        self.assertIn("Việc ngoài bản vẽ (ghi lại, không tính % tiến độ):", summary)
        self.assertIn("• Cắt đục sàn bê tông sàn mái", summary)
        self.assertIn("• Tầng 5 · Đánh lạnh tường phòng trong t5", summary)
        self.assertIn(f"Kế hoạch {timezone.localdate():%d/%m} (dự kiến, chưa phải việc đã làm):", summary)
        self.assertIn("Nhân sự theo tin: thợ xây: 3 thợ 2 phụ", summary)
        self.assertIn("Lần sau nhắn theo mẫu này", summary)
        self.assertIn("\n+ Cắt đục sàn bê tông sàn mái\n", bot.standard_text(proposal))
        self.assertTrue(any("chưa có tên thợ" in t for t in self.texts(self.chats[self.site.pk])))     # danh sách để bấm
        self.assertEqual(list(Feedback.objects.values_list("topic", flat=True)), [Feedback.Topic.NO_STAFF])
        with self.captureOnCommitCallbacks(execute=True):
            self.press(self.site, f"ok:{proposal.pk}")
        report = Report.objects.get()
        self.assertEqual((report.work_date, report.via, report.asked, report.named, report.crew),
                         (timezone.localdate() - timedelta(days=1), "tu_do", 1, 0, "thợ xây: 3 thợ 2 phụ"))
        self.assertEqual(ExtraWork.objects.filter(report=report).count(), 4)
        self.assertEqual(list(PlanLine.objects.values_list("for_date", "floor")),
                         [(timezone.localdate(), "Tầng 5"), (timezone.localdate(), "Tầng 4")])
        self.assertEqual(AuditLog.objects.filter(table="field.ExtraWork", action="create").count(), 4)
        self.assertEqual(AuditLog.objects.filter(table="field.PlanLine", action="create").count(), 2)
        self.assertFalse(ReportLine.objects.exists())
        self.assertTrue(all(not r.history for r in compute.rows(self.project)))           # không tính vào % tiến độ
        score = quality.of(report)
        self.assertEqual((score.total, score.label), (5, "▼ cần hướng dẫn"))
        reply = self.texts(self.chats[self.site.pk], "editMessageText")[-1]
        self.assertIn("Mức đầy đủ báo cáo: 5/100 ▼ cần hướng dẫn", reply)
        self.assertIn("thiếu mã chi tiết (0/4 việc có mã)", reply)
        news = self.texts(self.chats[self.director.pk])[-1]
        self.assertIn("Việc ngoài bản vẽ: Trát thô tường phòng ngoài tầng 5", news)
        self.assertIn(f"Kế hoạch {timezone.localdate():%d/%m}: Đánh lạnh phòng ngoài tầng 5; Trát tường tầng 4", news)
        self.assertIn("Mức đầy đủ: 5/100", news)

    def test_next_day_report_compared_with_plan_and_evening_lists_not_done(self):
        proposal = self.send_example()
        self.press(self.site, f"ok:{proposal.pk}")
        self.message(self.site, "Báo cáo TN-22\n+ Đánh lạnh tường phòng ngoài tầng 5\nCông: Nam")
        proposal = self.proposal()
        summary = bot.summary(proposal)
        day = f"{timezone.localdate():%d/%m}"
        self.assertIn(f"So với kế hoạch đã báo cho {day}: 1/2 việc thấy đã làm.", summary)
        self.assertIn("✓ Đánh lạnh phòng ngoài tầng 5", summary)
        self.assertIn("● chưa thấy trong báo cáo: Trát tường tầng 4", summary)
        self.press(self.site, f"ok:{proposal.pk}")
        evening = schedule.evening_text(self.director, at(timezone.localdate(), 18, 30))
        self.assertIn("● Dự kiến hôm nay mà chưa thấy làm (1/2): Trát tường tầng 4", evening)
        self.assertIn("1 việc ngoài bản vẽ", evening)

    def test_full_report_scores_100_and_clarifications_are_counted(self):
        self.message(self.site, "T1.2", photo="p1")
        self.message(self.site, "TN-22 T1.2 40%. Công: Nam")
        proposal = self.proposal()
        self.assertNotIn("Lần sau nhắn theo mẫu", bot.summary(proposal))
        self.press(self.site, f"ok:{proposal.pk}")
        self.assertEqual(quality.of(Report.objects.get()).text(), "100/100 ▲ tốt")
        self.message(self.site, "", photo="p2")                                         # ảnh không chú thích, 2 chi tiết
        self.message(self.site, "TN-22 T1.2 30% S1.1 20%. Công: Hung")                  # báo giảm + tên gần giống
        proposal = self.proposal()
        topics = sorted(Feedback.objects.values_list("topic", flat=True))
        self.assertEqual(topics, sorted([Feedback.Topic.PHOTO, Feedback.Topic.PERSON, Feedback.Topic.REASON]))
        self.assertTrue(all(f.project == self.project and f.kind == Feedback.Kind.FRICTION for f in Feedback.objects.all()))
        photo = proposal.photos.get()
        self.press(self.site, f"ph:{photo.pk}:0")
        self.press(self.site, f"wk:{str(proposal.pk)[:8]}:0:{self.hung.pk}")
        self.message(self.site, "đục ra làm lại")
        self.assertEqual(Feedback.objects.count(), 3)                                   # không ghi lại khi phiếu gửi lại
        self.press(self.site, f"ok:{proposal.pk}")
        report = Report.objects.latest("pk")
        self.assertEqual(report.asked, 3)
        score = quality.of(report)
        self.assertEqual(score.total, 90)
        self.assertIn("bot phải hỏi lại 3 lần", score.missing)

    def test_project_asked_with_buttons_when_code_missing(self):
        other = Project.objects.create(code="HB-01", name="Hà Bắc")
        Member.objects.create(project=other, user=self.site)
        today = timezone.localdate()
        self.message(self.site, f"Báo cáo {today:%d/%m}\n+ Cắt đục sàn mái\n+ Dọn vệ sinh tầng 3")
        self.assertFalse(Proposal.objects.exists())
        buttons = self.last_buttons(self.chats[self.site.pk])
        self.assertEqual(set(buttons), {"TN-22", "HB-01"})
        self.assertEqual(Feedback.objects.get().topic, Feedback.Topic.PROJECT)
        self.press(self.site, buttons["HB-01"])
        proposal = self.proposal()
        self.assertEqual(proposal.project, other)
        self.assertEqual([e["description"] for e in proposal.payload["extra"]], ["Cắt đục sàn mái", "Dọn vệ sinh tầng 3"])
        self.assertEqual(proposal.payload["asks"][0]["topic"], Feedback.Topic.PROJECT)
        self.assertEqual(Feedback.objects.filter(topic=Feedback.Topic.PROJECT).count(), 1)  # đếm vào phiếu, không ghi hai lần

    def test_ai_report_with_extra_plan_and_crew(self):
        tomorrow = timezone.localdate() + timedelta(days=1)
        result = {"type": "final", "tool": "propose_report", "input": {
            "project_code": "TN-22", "lines": [{"code": "T1.2", "percent": 20}], "people": [], "note": "",
            "extra": [{"floor": "Mái", "description": "Cắt đục sàn mái"}], "crew": "3 thợ 2 phụ",
            "plan": {"date": tomorrow.isoformat(), "lines": [{"code": "S1.1", "description": "Lát nền phòng khách"}]}}}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run", return_value=result):
            self.message(self.site, "trát tường tầng 1 được 20%, cắt đục sàn mái, mai lát nền, 3 thợ 2 phụ")
        proposal = self.proposal()
        self.assertEqual(proposal.payload["via"], "ai")
        self.assertEqual(proposal.payload["plan"]["lines"][0]["item_id"], Item.objects.get(code="S1.1").pk)
        self.press(self.site, f"ok:{proposal.pk}")
        report = Report.objects.get()
        self.assertEqual((report.via, report.crew), ("ai", "3 thợ 2 phụ"))
        self.assertEqual(PlanLine.objects.get().item.code, "S1.1")
        self.assertEqual(PlanLine.objects.get().for_date, tomorrow)


class PhoneMaskTests(BotCase):
    def test_mask_variants(self):
        for text in ("gọi 0912345678", "số +84 912 345 678", "0912.345.678", "024-3826-1234", "84912345678", "sdt0912345678"):
            self.assertNotRegex(mask_phones(text), r"\d{3}", text)
        for text in ("trả NCC 5000000 TN-22", "NS-0012", "TN-22 T2.5 40%", "1.250.000 ₫", "19/11/2026"):
            self.assertEqual(mask_phones(text), text)

    def test_friction_and_feedback_saved_without_phone(self):
        self.message(self.site, "anh gọi em số 0912 345 678 nhé")                       # bot không hiểu
        self.message(self.site, "/gopy số kế toán 0987.654.321 không nghe máy")
        rows = list(Feedback.objects.order_by("pk"))
        self.assertEqual([r.topic for r in rows], [Feedback.Topic.NOT_UNDERSTOOD, ""])
        for row in rows:
            self.assertIn("[số ĐT đã che]", row.text)
            self.assertNotRegex(row.text, r"\d{3}")
        for log in AuditLog.objects.filter(table="assistant.Feedback"):
            self.assertNotIn("345", str(log.after))
            self.assertNotIn("654", str(log.after))

    def test_migration_masks_old_rows_and_keeps_old_log(self):
        old = Feedback.objects.create(kind=Feedback.Kind.FRICTION, author=self.site, text="em là 0912345678",
                                      context="bot không hiểu tin nhắn")
        clean = Feedback.objects.create(kind=Feedback.Kind.USER, author=self.site, text="nút nhỏ quá")
        migration = importlib.import_module("assistant.migrations.0003_loai_cho_vuong_che_so")
        migration.forward(apps, None)
        old.refresh_from_db()
        clean.refresh_from_db()
        self.assertEqual((old.text, old.topic), ("em là [số ĐT đã che]", Feedback.Topic.NOT_UNDERSTOOD))
        self.assertEqual(clean.text, "nút nhỏ quá")
        log = AuditLog.objects.get(table="assistant.Feedback", object_id=str(old.pk), channel="migration")
        self.assertNotIn("0912", str(log.before) + str(log.after))


class WeeklyStatsTests(BotCase):
    def setUp(self):
        super().setUp()
        self.message(self.site, "T1.2", photo="w1")
        self.message(self.site, "TN-22 T1.2 40%. Công: Nam")
        self.press(self.site, f"ok:{self.proposal().pk}")                               # 100 điểm
        today = timezone.localdate()
        self.message(self.site, EXAMPLE.format(day=f"{today:%d/%m}", plan=f"{today + timedelta(days=1):%d/%m}"))
        self.press(self.site, f"ok:{self.proposal().pk}")                               # 5 điểm
        self.monday = today + timedelta(days=7 - today.weekday())

    def test_monday_bulletin_lists_each_supervisor(self):
        text = schedule.morning_text(self.director, at(self.monday, 7, 30))
        start, end = stats.week_of(timezone.localdate())
        self.assertIn(f"⑤ Báo cáo của giám sát tuần trước ({start:%d/%m}–{end:%d/%m})", text)
        self.assertIn("• Minh: 2 báo cáo · TB 53/100 ▼ cần hướng dẫn", text)
        self.assertIn("thiếu mã chi tiết 1/2", text)
        self.assertIn("4 việc ngoài bản vẽ", text)
        self.assertIn("Cần hướng dẫn cách báo cáo: Minh", text)
        self.assertIn("Chỗ vướng tuần trước theo loại: Báo cáo chưa có tên thợ 1", text)
        tuesday = schedule.morning_text(self.director, at(self.monday + timedelta(days=1), 7, 30))
        self.assertNotIn("⑤", tuesday)

    def test_assistant_page_director_all_supervisor_own_only(self):
        self.client.force_login(self.director)
        page = self.client.get(reverse("assistant"))
        self.assertContains(page, "Mức đầy đủ báo cáo của giám sát")
        self.assertContains(page, "53/100 ▼ cần hướng dẫn")
        self.assertContains(page, "Từng báo cáo của Minh (2)")
        self.assertContains(page, "Báo cáo chưa có tên thợ 1")
        self.client.force_login(self.site)
        page = self.client.get(reverse("assistant"))
        self.assertContains(page, "Mức đầy đủ báo cáo của anh/chị")
        self.assertNotContains(page, "Chỗ vướng bot tự ghi")
        rows = stats.supervisor_rows(self.site, *stats.week_of(timezone.localdate()))
        self.assertEqual([r["user"] for r in rows], [self.site])
        self.client.force_login(self.accountant)
        self.assertNotContains(self.client.get(reverse("assistant")), "Mức đầy đủ báo cáo")
        self.client.force_login(self.director)
        last_week = timezone.localdate() - timedelta(days=7)
        self.assertContains(self.client.get(reverse("assistant"), {"tuan": last_week.isoformat()}), "● không có báo cáo")

    def test_month_excel_has_extra_work_plan_and_score_for_director_only(self):
        first = timezone.localdate().replace(day=1)
        if Report.objects.filter(work_date__lt=first).exists():
            first = Report.objects.order_by("work_date").first().work_date.replace(day=1)
        book = load_workbook(BytesIO(exports.month_book(self.director, self.project, first)))
        extra = [row for row in book["Việc ngoài bản vẽ"].iter_rows(min_row=5, values_only=True) if row[0]]
        self.assertEqual(len(extra), 4)
        self.assertEqual(extra[3][2:4], ("Mái", "Cắt đục sàn bê tông sàn mái"))
        log = book["Báo cáo ngày"]
        head = [c.value for c in log[4]]
        self.assertIn("Mức đầy đủ", head)
        self.assertIn("5/100 ▼ cần hướng dẫn", str([c.value for c in log[6]]))
        site_book = load_workbook(BytesIO(exports.month_book(self.site, self.project, first)))
        self.assertNotIn("Mức đầy đủ", [c.value for c in site_book["Báo cáo ngày"][4]])


class GroupFreeReportTests(BotCase):
    def say(self, user, text):
        from chat.tests_group import GROUP
        msg = {"message_id": next(self.next_id), "chat": {"id": GROUP, "type": "supergroup", "title": "Công trường TN-22"},
               "from": {"id": user.pk + 9000, "first_name": user.username}, "text": text}
        with self.captureOnCommitCallbacks(execute=True):
            bot.handle_update({"update_id": next(self.update_id), "message": msg})

    def test_listening_group_needs_report_heading_and_uses_group_project(self):
        other = Project.objects.create(code="HB-01", name="Hà Bắc")
        Member.objects.create(project=other, user=self.site)
        self.say(self.director, "/nhom TN-22")
        self.say(self.site, "Mọi người nhớ:\n+ mang đồ bảo hộ\n+ dọn vệ sinh cuối ngày")         # dặn dò: im lặng
        self.assertFalse(Proposal.objects.exists())
        self.say(self.site, "Báo cáo hôm nay\n+ Cắt đục sàn bê tông sàn mái")                     # không ghi mã công trình
        proposal = self.proposal()
        self.assertEqual((proposal.project, proposal.link.user), (self.project, self.site))     # công trình của nhóm
        self.assertEqual(proposal.payload["extra"][0]["floor"], "Mái")
