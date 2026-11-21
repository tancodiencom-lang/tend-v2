"""Đợt 10a: trợ lý nội dung (kho ảnh, bài nháp hằng ngày, duyệt đúng bản, đăng nhiều ảnh có sổ, đối soát) và trợ lý IT
(sổ tài khoản, xoá tin có mật khẩu/khoá). Telegram giả, Facebook giả, AI giả: không gọi mạng."""
import io
import json
import os
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from assistant import schedule
from assistant.models import Feedback
from chat import ai, telegram
from chat.models import ChatPhoto, Proposal
from chat.tests import BotCase
from core.models import AuditLog, Role, User
from field.models import Photo
from itdesk import services as it_services
from itdesk.models import ServiceAccount
from projects.models import Member

from . import assets, facebook, jobs, publish, services, writer
from . import bot as mkt_bot
from .models import Asset, Channel, Idea, Post, PostTarget, PublishStep, StyleSample, WriteJob


def exif_jpeg():
    picture = Image.new("RGB", (40, 30), (200, 180, 150))
    exif = Image.Exif()
    exif[0x010F] = "MayAnh"                       # hãng máy (đại diện cho thông tin EXIF/GPS phải bỏ)
    out = io.BytesIO()
    picture.save(out, "JPEG", exif=exif.tobytes())
    return out.getvalue()


class FakeFacebook:
    """Graph API giả: ghi lại lời gọi; có thể gây lỗi ở bước chỉ định."""

    def __init__(self):
        self.calls, self.fail_upload, self.fail_feed, self.feed_seen = [], None, None, []

    def __call__(self, method, path, params=None, token="", files=None):
        self.calls.append((method, path, dict(params or {}), bool(files)))
        if method == "GET" and path.endswith("/posts"):
            return {"data": [{"id": "1_1", "message": "Phòng khách tân cổ điển hoàn thiện, phào chỉ dát vàng tinh tế. Nhắn TenD để được tư vấn!"},
                             {"id": "1_2", "message": ""}]}
        if method == "GET" and path.endswith("/feed"):
            return {"data": self.feed_seen}
        if method == "GET" and "_" in path:
            return {"permalink_url": f"https://www.facebook.com/{path}"}
        if method == "GET":
            return {"id": "1234", "name": "TenD Công trình", "access_token": "PAGE-TOKEN"}
        if path.endswith("/photos"):
            if self.fail_upload:
                raise self.fail_upload
            return {"id": f"ph{sum(1 for c in self.calls if c[1].endswith('/photos'))}"}
        if path.endswith("/feed"):
            if self.fail_feed:
                error, self.fail_feed = self.fail_feed, None
                raise error
            return {"id": "1234_999"}
        raise AssertionError(path)

    def posts(self, kind):
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(kind)]


class MarketingCase(BotCase):
    def setUp(self):
        super().setUp()
        self.media = []
        distinct = mock.patch.object(telegram, "download", side_effect=lambda file_id: (self.colored(file_id), f"{file_id}.jpg"))
        distinct.start()
        self.addCleanup(distinct.stop)
        files = mock.patch.object(telegram, "post_files", side_effect=self.fake_files)
        files.start()
        self.addCleanup(files.stop)
        self.fb = FakeFacebook()
        graph = mock.patch.object(facebook, "request", side_effect=self.fb)
        graph.start()
        self.addCleanup(graph.stop)
        env = mock.patch.dict(os.environ, {"TEND_META_TOKEN": "USER-TOKEN"})
        env.start()
        self.addCleanup(env.stop)
        inline = mock.patch.object(jobs, "kick", side_effect=lambda: jobs.run_pending())   # việc viết bài chạy ngay
        inline.start()
        self.addCleanup(inline.stop)
        facebook._page_tokens.clear()
        self.mkt = User.objects.create_user("marketing", password="x-123-abcd", role=Role.MARKETING, first_name="Lan")
        self.connect(self.mkt)
        self.channel = services.save_channel(self.director, name="TenD Công trình", page="https://web.facebook.com/tendct")
        self.sent.clear()

    @staticmethod
    def colored(file_id, size=(900, 675), light=1.0, blur=0):
        """Ảnh mẫu cỡ thật, có mép rõ (đạt chuẩn đo kỹ thuật); màu theo file_id nên mỗi ảnh khác nhau."""
        import hashlib

        from PIL import ImageDraw, ImageFilter
        digest = hashlib.sha256(file_id.encode()).digest()
        rgb = tuple(int((120 + b % 90) * light) for b in digest[:3])
        width, height = size
        picture = Image.new("RGB", size, rgb)
        draw = ImageDraw.Draw(picture)
        draw.rectangle([width // 5, height // 5, width // 2, height * 3 // 4], fill=tuple(max(0, c - 90) for c in rgb),
                       outline=(30, 30, 30), width=4)
        draw.rectangle([width * 3 // 5, height // 3, width * 9 // 10, height * 4 // 5], fill=tuple(min(255, c + 50) for c in rgb))
        for x in range(0, width, 45):
            draw.line([x, height * 4 // 5, x + 20, height], fill=(60, 50, 40), width=2)
        if blur:
            picture = picture.filter(ImageFilter.GaussianBlur(blur))
        out = io.BytesIO()
        picture.save(out, "JPEG", quality=90)
        return out.getvalue()

    def fake_files(self, method, data, files):
        self.media.append((method, data, files))
        self.sent.append((method, {**data, "files": len(files)}))
        return [{"message_id": 1}]

    shots = 0

    def report_with_photos(self, count=3):
        for i in range(count):
            MarketingCase.shots += 1
            self.message(self.site, "T1.2" if i == 0 else "", photo=f"anh{MarketingCase.shots}")
        self.message(self.site, "TN-22 T1.2 40%. Công: Nam")
        proposal = self.proposal()
        for photo in proposal.photos.filter(target="?"):
            self.press(self.site, f"ph:{photo.pk}:0")
        self.press(self.site, f"ok:{proposal.pk}")
        return Photo.objects.count()

    def ready_post(self, photos=3):
        """Bài nháp hằng ngày (chưa bật AI → chữ tạm) rồi người duyệt viết chữ: bài sẵn sàng duyệt."""
        self.report_with_photos(photos)
        assets.collect()
        post = writer.daily()[0]
        return services.set_text(self.mkt, post, "Bài thử: phòng khách đang hoàn thiện, mời anh chị nhắn tin tư vấn.")


class AssetAndDraftTests(MarketingCase):
    def test_collect_report_photos_only_never_vouchers_and_no_duplicates(self):
        self.assertEqual(self.report_with_photos(2), 2)
        self.message(self.accountant, "", photo="phieuchi1")                   # ảnh phiếu chi: không bao giờ vào kho
        self.message(self.accountant, "chi khác 200k vật tư lặt vặt TN-22")
        self.press(self.accountant, f"ok:{self.proposal().pk}")
        self.assertEqual(assets.collect(), {"bao_cao": 2, "drive": 0, "do": 0})
        self.assertEqual(assets.collect(), {"bao_cao": 0, "drive": 0, "do": 0})
        rows = list(Asset.objects.all())
        self.assertEqual({a.source for a in rows}, {Asset.Source.REPORT})
        self.assertTrue(all(a.project == self.project for a in rows))
        self.assertIn("Tầng 1", [a for a in rows if a.context.startswith("Xây") or "Tầng" in a.context][0].context)

    def test_drive_folder_with_project_code(self):
        import shutil
        from uuid import uuid4

        from django.conf import settings
        from django.test import override_settings
        root = os.path.join(settings.MEDIA_ROOT, f"drive-{uuid4().hex[:8]}")
        self.addCleanup(shutil.rmtree, root, True)
        folder = os.path.join(root, "Ảnh công trình", "TN-22 phòng khách")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "pk1.jpg"), "wb") as handle:
            handle.write(exif_jpeg())
        with override_settings(TEND_MKT_DRIVE_DIR=root):
            self.assertEqual(assets.collect_drive(), 1)
            self.assertEqual(assets.collect_drive(), 0)
        asset = Asset.objects.get(source=Asset.Source.DRIVE)
        self.assertEqual((asset.project, asset.context), (self.project, "Ảnh công trình / TN-22 phòng khách"))
        self.assertTrue(asset.image.name.startswith(os.path.basename(root) + "/"))    # trỏ tệp gốc, không chép

    def test_daily_draft_without_ai_preview_to_reviewers_once_a_day(self):
        self.report_with_photos(3)
        with self.captureOnCommitCallbacks(execute=True):
            done = schedule.run(timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time())) + timedelta(hours=8, minutes=5))
        post = Post.objects.get()
        self.assertIn(f"mkt-draft:{post.pk}", done)
        self.assertEqual((post.state, post.ai_written, post.items.count()), (Post.State.DRAFT, False, 3))
        self.assertIn("AI chưa viết được bài này (chưa bật trợ lý AI", post.note)
        self.assertIn(writer.PLACEHOLDER, post.text)
        self.assertIn("chưa có giới thiệu và bài mẫu", post.note)                  # nhắc người duyệt điền giọng văn Trang
        self.assertNotIn("tân cổ điển", post.text.casefold())                         # chữ mẫu không gắn thương hiệu khác
        self.assertNotIn("#tancodien", post.text)
        self.assertEqual(list(post.channels.all()), [self.channel])
        for user in (self.director, self.mkt):
            chat = self.chats[user.pk]
            self.assertTrue(any(m == "sendMediaGroup" and d["chat_id"] == chat and d["files"] == 3 for m, d in self.sent))
            buttons = self.last_buttons(chat)
            self.assertEqual(buttons["✓ Duyệt và đăng"], f"mk:a:{post.pk}:{post.package_sha[:10]}")
        self.assertNotIn(self.chats[self.site.pk], [d["chat_id"] for m, d in self.sent if m == "sendMediaGroup"])
        schedule.run(timezone.make_aware(datetime.combine(timezone.localdate(), datetime.min.time())) + timedelta(hours=9))
        self.assertEqual(Post.objects.count(), 1)                                   # mỗi ngày một lần
        self.assertEqual(self.channel.page, "tendct")

    def test_ai_gets_photos_and_work_context_but_no_project_name(self):
        self.report_with_photos(2)
        assets.collect()
        captured = {}

        def fake_create(**kwargs):
            captured.update(kwargs)
            text = json.dumps({"photos": [2, 1], "text": "Phòng khách đang hoàn thiện ✨", "note": "Chọn ảnh rõ nhất"})
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                                   content=[SimpleNamespace(type="text", text=text)])
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "create_message", side_effect=fake_create):
            post = writer.daily()[0]
        self.assertEqual((post.text, post.ai_written), ("Phòng khách đang hoàn thiện ✨", True))
        self.assertTrue(post.note.startswith("Chọn ảnh rõ nhất ⚠ Trang \"TenD Công trình\" chưa có giới thiệu"))
        self.assertEqual(captured["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("tool_choice", captured)
        content = captured["messages"][0]["content"]
        self.assertEqual(sum(1 for b in content if b["type"] == "image"), 2)
        prompt = json.dumps(captured, ensure_ascii=False, default=str)
        self.assertNotIn("Thiên Nga", prompt)
        self.assertNotIn("TN-22", prompt)
        self.assertIn("KHÔNG nêu tên chủ nhà", captured["system"][0]["text"])
        first = [i.asset for i in post.items.order_by("order")]
        self.assertEqual(len(first), 2)


class ApprovalTests(MarketingCase):
    def test_approval_binds_to_seen_version_and_rights(self):
        post = self.ready_post(2)
        old = f"mk:a:{post.pk}:{post.package_sha[:10]}"
        self.press(self.mkt, f"mk:e:{post.pk}")
        self.message(self.mkt, "Chữ mới của bài, ngắn gọn hơn.")
        post.refresh_from_db()
        self.assertEqual(post.text, "Chữ mới của bài, ngắn gọn hơn.")
        self.assertEqual(Proposal.objects.filter(link__user=self.mkt).count(), 0)    # tin sửa chữ không thành phiếu báo cáo
        self.press(self.mkt, old)
        post.refresh_from_db()
        self.assertEqual(post.state, Post.State.DRAFT)                               # nút của bản cũ bị từ chối
        self.press(self.site, f"mk:a:{post.pk}:{post.package_sha[:10]}")
        post.refresh_from_db()
        self.assertEqual(post.state, Post.State.DRAFT)                               # giám sát không duyệt được
        self.press(self.mkt, f"mk:a:{post.pk}:{post.package_sha[:10]}")
        post.refresh_from_db()
        self.assertEqual((post.state, post.approved_by, post.approved_sha), (Post.State.APPROVED, self.mkt, post.package_sha))
        self.assertEqual(PostTarget.objects.get(post=post).state, PostTarget.State.PENDING)
        self.assertIn("Lan đã duyệt bài", self.texts(self.chats[self.director.pk])[-1])

    def test_swap_block_remove_move_first_change_version(self):
        post = self.ready_post(4)
        self.report_with_photos(2)
        assets.collect()
        shas, first = {post.package_sha}, [i.asset_id for i in post.items.order_by("order")]
        post = services.swap_asset(self.mkt, post, 0, block=True)
        self.assertTrue(Asset.objects.get(pk=first[0]).blocked)
        self.assertNotIn(first[0], [i.asset_id for i in post.items.all()])
        shas.add(post.package_sha)
        post = services.move_first(self.mkt, post, 2)
        shas.add(post.package_sha)
        post = services.remove_asset(self.mkt, post, 1)
        shas.add(post.package_sha)
        self.assertEqual(len(shas), 4)
        self.assertEqual(post.items.count(), 3)
        self.assertTrue(AuditLog.objects.filter(table="marketing.Asset", reason="Ảnh không dùng cho bài đăng nữa").exists())


class PublishTests(MarketingCase):
    def approve(self, post):
        return services.approve(self.director, post, post.package_sha[:10])

    def test_multi_photo_post_with_ledger_no_exif_and_link(self):
        post = self.ready_post(3)
        self.approve(post)
        uploads, modes = [], []
        real = assets.for_publish
        with mock.patch.object(assets, "for_publish",
                               side_effect=lambda a, **kw: modes.append(kw.get("enhanced")) or uploads.append(real(a, **kw)) or uploads[-1]):
            with self.captureOnCommitCallbacks(execute=True):
                call_command("dang_bai", stdout=io.StringIO())
        post.refresh_from_db()
        target = PostTarget.objects.get(post=post)
        self.assertEqual((post.state, target.state, target.remote_id), (Post.State.PUBLISHED, PostTarget.State.PUBLISHED, "1234_999"))
        self.assertEqual(target.permalink, "https://www.facebook.com/1234_999")
        photos = self.fb.posts("/photos")
        self.assertEqual(len(photos), 3)
        self.assertTrue(all(c[2]["published"] == "false" and c[3] for c in photos))
        feed = self.fb.posts("/feed")
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed[0][1], "1234/feed")
        self.assertEqual([json.loads(feed[0][2][f"attached_media[{i}]"])["media_fbid"] for i in range(3)], ["ph1", "ph2", "ph3"])
        steps = list(PublishStep.objects.filter(target=target).values_list("step", flat=True))
        self.assertEqual(steps, ["upload_intent", "upload_done"] * 3 + ["feed_intent", "feed_done", "verify"])
        self.assertTrue(all(not Image.open(io.BytesIO(data)).getexif() for data in uploads))   # không còn EXIF
        self.assertEqual(modes, [True, True, True])                                    # đăng đúng bản tự chỉnh đã duyệt
        self.assertTrue(any("✓ Đã đăng bài" in t and "1234_999" in t for t in self.texts(self.chats[self.mkt.pk])), self.texts())
        call_command("dang_bai", stdout=io.StringIO())
        self.assertEqual(len(self.fb.posts("/feed")), 1)                              # chạy lại không đăng trùng

    def test_timeout_after_feed_is_reconciled_never_reposted(self):
        post = self.ready_post(2)
        self.approve(post)
        self.fb.fail_feed = facebook.FacebookError("Hết giờ chờ", sent=True)
        call_command("dang_bai", stdout=io.StringIO())
        post.refresh_from_db()
        target = PostTarget.objects.get(post=post)
        self.assertEqual((post.state, target.state), (Post.State.UNKNOWN, PostTarget.State.UNKNOWN))
        self.assertIn("✓ Đã có trên Trang", self.last_buttons(self.chats[self.director.pk]))
        call_command("dang_bai", stdout=io.StringIO())                               # chưa thấy bài: vẫn đối soát
        self.assertEqual(len(self.fb.posts("/feed")), 1)
        self.fb.feed_seen = [{"id": "1234_777", "message": post.text, "permalink_url": "https://facebook.com/1234_777"}]
        call_command("dang_bai", stdout=io.StringIO())
        target.refresh_from_db()
        post.refresh_from_db()
        self.assertEqual((target.state, target.remote_id, post.state), (PostTarget.State.PUBLISHED, "1234_777", Post.State.PUBLISHED))
        self.assertEqual(len(self.fb.posts("/feed")), 1)                              # không bao giờ POST lại

    def test_upload_error_fails_without_post_then_retry(self):
        post = self.ready_post(2)
        self.approve(post)
        self.fb.fail_upload = facebook.FacebookError("Không kết nối được Facebook", sent=False)
        call_command("dang_bai", stdout=io.StringIO())
        post.refresh_from_db()
        self.assertEqual(post.state, Post.State.FAILED)
        self.assertEqual(self.fb.posts("/feed"), [])
        self.assertEqual(self.last_buttons(self.chats[self.director.pk]), {"↻ Thử lại": f"mk:t:{post.pk}"})
        self.fb.fail_upload = None
        self.press(self.director, f"mk:t:{post.pk}")
        call_command("dang_bai", stdout=io.StringIO())
        post.refresh_from_db()
        self.assertEqual(post.state, Post.State.PUBLISHED)
        self.assertEqual(list(PostTarget.objects.filter(post=post).values_list("attempt", "state")),
                         [(1, PostTarget.State.FAILED), (2, PostTarget.State.PUBLISHED)])

    def test_missing_token_is_clear_error(self):
        post = self.ready_post(1)
        self.approve(post)
        with mock.patch.dict(os.environ, {"TEND_META_TOKEN": ""}):
            facebook._page_tokens.clear()
            call_command("dang_bai", stdout=io.StringIO())
        self.assertIn("TEND_META_TOKEN", PostTarget.objects.get(post=post).error)


class CommandAndWebTests(MarketingCase):
    def test_baiviet_with_sent_photo_and_ytuong(self):
        self.message(self.mkt, "", photo="mkt1")
        self.message(self.mkt, "/baiviet TN-22 khoe phòng khách mới hoàn thiện")
        post = Post.objects.get()
        self.assertEqual((post.origin, post.project, post.idea.state, post.idea.kind),
                         (Post.Origin.REQUEST, self.project, Idea.State.USED, Idea.Kind.REQUEST))
        self.assertEqual(post.items.order_by("order").first().asset.source, Asset.Source.UPLOAD)
        self.assertEqual(ChatPhoto.objects.get(file_unique_id="mkt1").target, "noi-dung")
        self.assertTrue(any(m == "sendPhoto" for m, _, _ in self.media))
        self.message(self.mkt, "/ytuong Trước–sau mặt tiền, nhấn phào chỉ")
        self.assertEqual(Idea.objects.filter(state=Idea.State.OPEN).get().text, "Trước–sau mặt tiền, nhấn phào chỉ")
        self.message(self.site, "/ytuong thử")
        self.assertIn("Giám đốc và nhân viên Marketing", self.texts(self.chats[self.site.pk])[-1])
        self.message(self.mkt, "/noidung")
        self.assertIn(f"#{post.pk}", self.texts(self.chats[self.mkt.pk])[-1])

    def test_web_pages_rights_and_actions(self):
        post = self.ready_post(2)
        self.client.force_login(self.site)
        self.assertEqual(self.client.get(reverse("marketing")).status_code, 403)
        asset = post.items.first().asset
        self.assertEqual(self.client.get(reverse("marketing_image", args=[asset.pk])).status_code, 403)
        self.client.force_login(self.mkt)
        self.assertContains(self.client.get(reverse("marketing")), f"Bài #{post.pk}")
        self.assertEqual(self.client.get(reverse("marketing_image", args=[asset.pk]), {"w": 200})["Content-Type"], "image/jpeg")
        self.client.post(reverse("marketing_post_action", args=[post.pk, "sua"]), {"text": "Chữ sửa trên web"})
        post.refresh_from_db()
        self.client.post(reverse("marketing_post_action", args=[post.pk, "duyet"]), {"sha": post.package_sha[:10]})
        post.refresh_from_db()
        self.assertEqual((post.text, post.state), ("Chữ sửa trên web", Post.State.APPROVED))
        page = self.client.post(reverse("marketing_settings"), {"action": "channel", "name": "Trang 2", "page": "trang2"}, follow=True)
        self.assertContains(page, "Chỉ Giám đốc")
        self.client.force_login(self.director)
        self.client.post(reverse("marketing_settings"), {"action": "check", "id": self.channel.pk})
        self.channel.refresh_from_db()
        self.assertEqual(self.channel.page_id, "1234")
        self.assertIn("✓ Kết nối được Trang", self.channel.check_note)
        self.client.post(reverse("marketing_settings"), {"action": "import", "id": self.channel.pk})
        self.client.post(reverse("marketing_settings"), {"action": "import", "id": self.channel.pk})
        self.assertEqual(StyleSample.objects.count(), 1)                              # bài trống bỏ, chạy lại không trùng
        self.assertIn("Nội dung", self.client.get(reverse("overview")).content.decode())


class PageVoiceTests(MarketingCase):
    """Mỗi Trang một thương hiệu, một giọng văn: trợ lý viết theo giới thiệu + bài mẫu của đúng Trang sẽ đăng."""

    @staticmethod
    def ai_reply(captured, payload):
        def fake_create(**kwargs):
            captured.append(kwargs)
            return SimpleNamespace(stop_reason="end_turn", usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                                   content=[SimpleNamespace(type="text", text=json.dumps(payload))])
        return fake_create

    def uploads(self, count):
        for i in range(count):
            MarketingCase.shots += 1
            assets.add_upload(self.mkt, self.colored(f"up{MarketingCase.shots}"), f"up{i}.jpg", None, "ảnh công trình")

    def test_writer_uses_brief_and_samples_of_target_page_only(self):
        roma = services.save_channel(self.director, name="New Roma classic", page="newromaclassic", default=False)
        with self.assertRaises(PermissionDenied):
            services.set_brief(self.site, self.channel, "không được sửa")
        services.set_brief(self.mkt, self.channel, "TenD – hoàn thiện nhà trọn gói. Xưng TenD, gọi khách anh chị.\n"
                                                   "Hashtag: #TenD #HoanThienNhaTronGoi")
        self.assertTrue(AuditLog.objects.filter(reason="Sửa giới thiệu Trang").exists())
        services.add_sample(self.mkt, "TenD bàn giao phần thô tầng 3, chuẩn bị trát. Anh chị nhắn TenD để được tư vấn trọn gói!",
                            channel_row=self.channel)
        services.add_sample(self.mkt, "New Roma classic: phòng khách tân cổ điển, phào chỉ dát vàng sang trọng quý phái.",
                            channel_row=roma)
        self.uploads(2)
        captured = []
        payload = {"photos": [1], "text": "TenD hoàn thiện tường tầng 4 ✨ #TenD", "note": "Ảnh rõ"}
        with mock.patch.object(ai, "available", return_value=True), \
                mock.patch.object(ai, "create_message", side_effect=self.ai_reply(captured, payload)):
            post = writer.daily()[0]
        system = captured[0]["system"][0]["text"]
        self.assertIn('Trang Facebook "TenD Công trình"', system)
        self.assertIn("hoàn thiện nhà trọn gói", system)
        self.assertIn("TenD bàn giao phần thô", system)
        self.assertNotIn("New Roma", system)                                          # không lẫn giọng Trang khác
        self.assertNotIn("tân cổ điển", system.casefold())
        self.assertEqual((list(post.channels.all()), post.note), ([self.channel], "Ảnh rõ"))
        captured.clear()                                                              # sửa theo ý cũng dùng giọng Trang của bài
        with mock.patch.object(ai, "available", return_value=True), \
                mock.patch.object(ai, "create_message", side_effect=self.ai_reply(captured, {"text": "Bài sửa"})):
            self.assertEqual(writer.revise(post, "ngắn hơn"), "Bài sửa")
        self.assertIn("hoàn thiện nhà trọn gói", captured[0]["system"][0]["text"])
        self.client.force_login(self.mkt)                                             # sửa giới thiệu trên web
        page = self.client.post(reverse("marketing_settings"), {"action": "brief", "id": roma.pk,
                                                                "brief": "New Roma – nội thất tân cổ điển"}, follow=True)
        self.assertContains(page, "Đã lưu giới thiệu Trang")
        roma.refresh_from_db()
        self.assertEqual(roma.brief, "New Roma – nội thất tân cổ điển")
        self.client.post(reverse("marketing_settings"), {"action": "sample", "channel": roma.pk,
                                                         "text": "Mẫu thứ hai của New Roma, câu đủ dài để làm bài mẫu giọng văn."})
        self.assertEqual(StyleSample.objects.filter(channel=roma).count(), 2)

    def test_daily_one_post_per_default_page(self):
        second = services.save_channel(self.director, name="TenD xây nhà", page="tendxaynha")
        self.uploads(8)
        made = writer.daily()
        self.assertEqual(sorted([c.pk for c in p.channels.all()] for p in made), sorted([[self.channel.pk], [second.pk]]))
        self.assertTrue(all("Trang \"" in p.note for p in made))
        self.assertEqual(writer.daily(), [])                                          # đủ bài hôm nay cho mỗi Trang

    def reply(self, user, text, quoted):
        msg = {"message_id": 900, "chat": {"id": int(self.chats[user.pk]), "type": "private"},
               "from": {"id": user.pk + 9000, "first_name": user.username}, "text": text,
               "reply_to_message": {"message_id": 5, "from": {"id": 1, "is_bot": True}, "text": quoted}}
        from chat import bot as chat_bot
        chat_bot.handle_update({"update_id": next(self.update_id), "message": msg})

    def test_reply_to_draft_asks_assistant_to_revise_that_post(self):
        post = self.ready_post(2)
        proposals = Proposal.objects.count()
        quoted = PREFIX_ANCHOR(post)
        old_sha = post.package_sha
        self.reply(self.director, "bỏ ngày, ghi chung là công trình đang hoàn thiện", quoted)
        self.assertIn("chưa sửa bài", self.texts(self.chats[self.director.pk])[-1])     # chưa bật AI: chỉ cách khác, không giả
        captured = []
        with mock.patch.object(ai, "available", return_value=True), \
                mock.patch.object(ai, "create_message", side_effect=self.ai_reply(captured, {"text": "Bài đã sửa theo ý"})):
            self.reply(self.director, "bỏ ngày, ghi chung là công trình đang hoàn thiện", quoted)
        post.refresh_from_db()
        self.assertEqual(post.text, "Bài đã sửa theo ý")
        self.assertNotEqual(post.package_sha, old_sha)                                # bản mới: phải duyệt lại
        self.assertIn("bỏ ngày", captured[0]["messages"][0]["content"])
        last = self.texts(self.chats[self.director.pk])[-2:]                          # bản mới + tin "đang sửa"
        self.assertTrue(any(f"BÀI NHÁP #{post.pk}" in t for t in last), last)
        self.assertTrue(any("đang sửa bài" in t for t in last), last)
        self.reply(self.site, "sửa giúp", quoted)
        self.assertIn("Giám đốc và nhân viên Marketing", self.texts(self.chats[self.site.pk])[-1])
        self.assertEqual(Proposal.objects.count(), proposals)                       # không thành phiếu báo cáo


class PhotoQualityTests(MarketingCase):
    """Chọn ảnh 3 lớp (chủ dự án 21/11/2026): đo kỹ thuật → AI chấm 1–5 → tự chỉnh nhẹ (có nút dùng ảnh gốc); thiếu ảnh
    đạt chuẩn: đăng ít ảnh, ghi chú, nhắc giám sát chụp lại. Không dùng AI tạo/vẽ lại ảnh."""

    def add(self, name, project=None, **kwargs):
        MarketingCase.shots += 1
        return assets.add_upload(self.mkt, self.colored(f"{name}{MarketingCase.shots}", **kwargs), f"{name}.jpg", project, name)

    def test_measure_flaws_and_light_auto_fix(self):
        from . import enhance, quality
        good = quality.open_image(self.colored("tot"))
        self.assertEqual(quality.flaws(quality.measure(good)), [])
        cases = {"quá tối": self.colored("den", light=0.15), "nhoè": self.colored("nhoe", blur=8),
                 "quá nhỏ": self.colored("nho", size=(300, 200))}
        for flaw, data in cases.items():
            metrics = quality.measure(quality.open_image(data))
            self.assertIn(flaw, quality.broken(metrics), (flaw, metrics))
        dim = quality.open_image(self.colored("toi", light=0.42))
        before = quality.measure(dim)
        self.assertIn("hơi tối", quality.flaws(before))                              # sửa được, không phải hỏng
        self.assertEqual(quality.broken(before), [])
        after = quality.measure(enhance.enhance(dim))
        self.assertGreater(after["brightness"], before["brightness"] + 25)
        yellow = good.convert("L").convert("RGB").point([min(255, int(v * f)) for f in (1.12, 1.0, 0.72) for v in range(256)])
        self.assertIn("lệch màu", quality.flaws(quality.measure(yellow)))
        self.assertLess(quality.measure(enhance.enhance(yellow))["cast"], quality.measure(yellow)["cast"] * 0.7)
        cream = good.convert("L").convert("RGB").point([min(255, int(v * f)) for f in (1.06, 1.0, 0.9) for v in range(256)])
        self.assertEqual(enhance.params(cream)["gains"], [1.0, 1.0, 1.0])              # tông kem nhẹ là màu thật: không cân màu
        self.assertEqual(quality.labels("nhoè, lệch màu"), ["▼ nhoè", "● lệch màu"])

    def test_broken_photos_never_chosen_and_shown_in_library(self):
        good = self.add("tot")
        blurry, tiny = self.add("nhoe", blur=8), self.add("nho", size=(300, 200))
        self.assertEqual([(a.broken, "nhoè" in a.flaws) for a in (good, blurry)], [(False, False), (True, True)])
        self.assertTrue(tiny.broken)
        self.assertEqual(writer.candidates(), [good])
        Asset.objects.filter(pk=good.pk).update(metrics={}, flaws="", broken=False)     # ảnh vào kho trước khi có phần đo
        self.assertEqual(assets.measure_pending(), 1)
        self.assertTrue(Asset.objects.get(pk=good.pk).metrics)
        self.assertTrue(AuditLog.objects.filter(reason="Đo chất lượng ảnh").exists())
        self.client.force_login(self.mkt)
        page = self.client.get(reverse("marketing_assets"), {"loi": "1"})
        self.assertContains(page, "▼ nhoè")
        self.assertContains(page, "Trợ lý không chọn ảnh này")
        self.assertNotContains(page, f'anh/{good.pk}/')

    def ai(self, captured, payload):
        return PageVoiceTests.ai_reply(captured, payload)

    def test_ai_scores_each_photo_and_code_drops_low_scores(self):
        photos = [self.add(f"a{i}") for i in range(3)]
        captured = []
        payload = {"scores": [{"photo": 1, "score": 5, "issue": ""}, {"photo": 2, "score": 2, "issue": "bừa bộn bao xi măng"},
                              {"photo": 3, "score": 4, "issue": ""}],
                   "photos": [2, 1, 3], "text": "Bài thử", "note": "Chọn ảnh rõ"}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "create_message", side_effect=self.ai(captured, payload)):
            post = writer.daily()[0]
        shown = list(reversed(photos))                                                 # AI xem ảnh mới nhất trước
        self.assertEqual([i.asset for i in post.items.order_by("order")], [shown[0], shown[2]])   # ảnh 2 điểm bị mã loại
        self.assertIn("dưới 3 điểm", post.note)
        self.assertEqual([Asset.objects.get(pk=a.pk).ai_score for a in shown], [5, 2, 4])
        self.assertNotIn(shown[1], writer.candidates())                               # lần sau không đề xuất lại
        system = captured[0]["system"][0]["text"]
        self.assertIn("CHẤM TỪNG ẢNH", system)
        self.assertIn("GỌN GÀNG", system)
        self.assertIn("scores", captured[0]["output_config"]["format"]["schema"]["required"])
        sent = self.add("gui")                                                        # ảnh người giao bài gửi: luôn giữ, có cảnh báo
        payload = {"scores": [{"photo": 1, "score": 1, "issue": "tối"}], "photos": [1], "text": "Bài theo ảnh gửi", "note": ""}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "create_message", side_effect=self.ai([], payload)):
            post = writer.draft(timezone.localdate(), items=[sent], brief="ảnh gửi", origin=Post.Origin.REQUEST, user=self.mkt)
        self.assertEqual([i.asset for i in post.items.all()], [sent])
        self.assertIn("Ảnh người giao bài gửi bị chấm thấp", post.note)

    def test_auto_fixed_by_default_original_on_request_and_approval_binds(self):
        post = self.ready_post(3)
        mkt_bot.send_preview(post)
        self.assertEqual(list(post.items.values_list("original", flat=True)), [False, False, False])
        self.assertIn("Ảnh đã tự chỉnh nhẹ", self.texts(self.chats[self.mkt.pk])[-1])
        old = post.package_sha
        self.press(self.mkt, f"mk:o:{post.pk}:1")                                     # ↩ dùng ảnh gốc cho ảnh 2
        post.refresh_from_db()
        self.assertEqual(list(post.items.order_by("order").values_list("original", flat=True)), [False, True, False])
        self.assertNotEqual(post.package_sha, old)
        self.assertIn("ảnh dùng bản gốc: 2", self.texts(self.chats[self.mkt.pk])[-1])
        with self.assertRaises(ValidationError):
            services.approve(self.director, post, old[:10])                            # nút duyệt bản cũ không dùng được
        services.move_first(self.mkt, post, 1)                                         # đổi thứ tự vẫn giữ lựa chọn ảnh gốc
        post.refresh_from_db()
        self.assertEqual(list(post.items.order_by("order").values_list("original", flat=True)), [True, False, False])
        self.client.force_login(self.mkt)
        page = self.client.get(reverse("marketing_post", args=[post.pk]))
        self.assertContains(page, "✨ Dùng bản tự chỉnh")
        self.assertContains(page, "↩ Dùng ảnh gốc")
        asset = post.items.order_by("order").last().asset
        plain = self.client.get(reverse("marketing_image", args=[asset.pk]), {"w": 300}).content
        fixed = self.client.get(reverse("marketing_image", args=[asset.pk]), {"w": 300, "chinh": "1"}).content
        self.assertNotEqual(plain, fixed)
        self.client.post(reverse("marketing_post_action", args=[post.pk, "anh-goc"]), {"i": "0"})
        post.refresh_from_db()
        self.assertFalse(post.items.order_by("order").first().original)

    def test_legacy_posts_keep_their_version(self):
        post = self.ready_post(2)
        post.items.update(original=True)                                               # bài có từ trước khi có phần tự chỉnh
        import hashlib
        legacy = hashlib.sha256(json.dumps({"text": post.text, "assets": [i.asset.sha256 for i in post.items.order_by("order")],
                                            "channels": [self.channel.pk]}, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        self.assertEqual(services.package_sha(post), legacy)

    def test_swapped_photos_rank_later_then_stop(self):
        first = self.add("x1")
        for i in range(4):
            self.add(f"y{i}")
        for _ in range(2):
            post = services.create_post(self.mkt, "Bài thử đổi ảnh", [first], timezone.localdate())
            services.swap_asset(self.mkt, post, 0)
            Post.objects.filter(pk=post.pk).update(state=Post.State.REJECTED)
        first.refresh_from_db()
        self.assertEqual(first.passed_over, 2)
        self.assertNotIn(first, writer.candidates())

    def test_few_good_photos_note_and_supervisor_reminded_once_a_week(self):
        self.add("tot", self.project)
        for i in range(3):
            self.add(f"nhoe{i}", self.project, blur=8)
        with mock.patch("assistant.schedule.awake", return_value=True):
            post = writer.draft(timezone.localdate(), project=self.project, brief="tiến độ", origin=Post.Origin.REQUEST, user=self.mkt)
            self.assertEqual(post.items.count(), 1)                                    # đăng ít ảnh, không lấy ảnh hỏng
            self.assertIn("chỉ có 1 ảnh đạt chuẩn", post.note)
            self.assertIn("đã nhắc giám sát", post.note)
            tip = self.texts(self.chats[self.site.pk])[-1]
            self.assertIn("📸 Ảnh công trình TN-22", tip)
            self.assertIn("nhoè", tip)
            count = len(self.texts(self.chats[self.site.pk]))
            writer.draft(timezone.localdate(), project=self.project, brief="lần 2", origin=Post.Origin.REQUEST, user=self.mkt)
            self.assertEqual(len(self.texts(self.chats[self.site.pk])), count)       # một lần mỗi tuần
        self.assertFalse(any("📸" in t for t in self.texts(self.chats[self.designer.pk])))   # chỉ giám sát công trình


class DeepWritingTests(MarketingCase):
    """Chủ dự án 21/11/2026: bài nông, AI không khai thác ý giao; ảnh gửi kèm lệnh bị mất; bài không ảnh thành chữ mẫu chép
    lại yêu cầu. Chốt: Sonnet 5.5 cho bài đăng, trần riêng 30 USD/tháng; viết chạy nền."""

    def fake(self, captured, payload):
        return PageVoiceTests.ai_reply(captured, payload)

    def test_topic_without_photos_is_written_by_content_model_in_depth(self):
        captured = []
        payload = {"scores": [], "photos": [], "note": "Bài chuyên môn",
                   "text": "Dầm tum thang máy: hạng mục nhỏ nhưng quyết định an toàn cả hệ thang. " * 20}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "create_message", side_effect=self.fake(captured, payload)):
            self.message(self.director, "/baiviet dầm tum thang máy là hạng mục quan trọng hay bị bỏ sót. Viết chi tiết")
        post = Post.objects.get()
        call = captured[0]
        self.assertEqual(call["model"], "claude-sonnet-5-5")                             # mô hình riêng cho bài đăng
        self.assertGreaterEqual(call["max_tokens"], 8000)
        self.assertEqual(call["timeout"], 240.0)
        self.assertIsInstance(call["messages"][0]["content"], str)                       # không ảnh vẫn viết
        self.assertIn("YÊU CẦU CỦA NGƯỜI GIAO BÀI (ý chính", call["messages"][0]["content"])
        system = call["system"][0]["text"]
        for rule in ("BÀI CHUYÊN MÔN", "350–600 chữ", "KHÔNG chép lại nguyên văn yêu cầu", "KHÔNG bịa dữ kiện riêng"):
            self.assertIn(rule, system)
        self.assertTrue(post.ai_written)
        self.assertNotIn("Bạn hãy viết", post.text)
        self.assertIn("Bài chưa có ảnh", post.note)
        texts = self.texts(self.chats[self.director.pk])
        self.assertTrue(any("Trợ lý đang viết bài" in t for t in texts), texts)
        self.assertTrue(any(f"BÀI NHÁP #{post.pk}" in t for t in texts), texts)
        from chat.models import AiUsage
        self.assertEqual(list(AiUsage.objects.values_list("purpose", flat=True)), ["noi_dung"])
        self.assertEqual(ai.month_cost(purpose=ai.CHAT), 0)                              # trần bot báo cáo không bị ăn

    def test_budgets_are_separate_and_priced_per_model(self):
        from decimal import Decimal
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000, cache_read_input_tokens=0)
        ai._record(usage, ai.CONTENT, "claude-sonnet-5-5")                              # 2 + 1 = 3 USD
        ai._record(usage, ai.CHAT, "claude-haiku-5-5")                                  # 0,10 + 0,05 = 0,15 USD
        self.assertEqual(ai.month_cost(purpose=ai.CONTENT), Decimal("3"))
        self.assertEqual(ai.month_cost(purpose=ai.CHAT), Decimal("0.15"))
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "khoa-gia"}), \
                override_settings(TEND_MKT_AI_MONTHLY_USD=3, TEND_AI_MONTHLY_USD=4):
            self.assertFalse(ai.month_cost(purpose=ai.CONTENT) < ai.cap(ai.CONTENT))      # hết trần bài đăng
            self.assertTrue(ai.month_cost(purpose=ai.CHAT) < ai.cap(ai.CHAT))             # bot báo cáo vẫn chạy
            self.assertIn("hết trần 3 USD", writer.why_no_ai())

    def test_resent_photo_with_command_is_used_and_placeholder_cannot_be_approved(self):
        self.message(self.mkt, "/baiviet khoe phòng khách", photo="trung1")              # lần 1: ảnh vào kho
        first = Post.objects.get()
        self.assertEqual(first.items.count(), 1)
        self.assertIn(writer.PLACEHOLDER, first.text)                                    # chưa bật AI: chữ tạm ghi rõ
        self.assertNotIn("khoe phòng khách.\n\nTừng chi tiết", first.text)
        with self.assertRaises(ValidationError):
            services.approve(self.director, first, first.package_sha[:10])
        Post.objects.filter(pk=first.pk).update(state=Post.State.REJECTED)
        self.message(self.mkt, "/baiviet dầm tum thang máy", photo="trung1-lai")        # gửi lại đúng ảnh đó (khác mã Telegram)
        with mock.patch.object(telegram, "download", side_effect=lambda f: (self.colored("trung1"), "a.jpg")):
            self.message(self.mkt, "/baiviet dầm tum thang máy", photo="trung1-lan3")
        latest = Post.objects.order_by("-pk").first()
        self.assertEqual([i.asset for i in latest.items.all()], [i.asset for i in first.items.all()])   # dùng lại ảnh có sẵn
        self.assertEqual(Asset.objects.filter(source=Asset.Source.UPLOAD).count(), 2)

    def test_reply_photo_adds_to_draft_and_jobs_run_in_background(self):
        post = self.ready_post(1)
        before = post.package_sha
        quoted = PREFIX_ANCHOR(post)
        msg = {"message_id": 901, "chat": {"id": int(self.chats[self.mkt.pk]), "type": "private"},
               "from": {"id": self.mkt.pk + 9000, "first_name": "mkt"},
               "photo": [{"file_id": "them1", "file_unique_id": "them1", "file_size": 100, "width": 8}],
               "reply_to_message": {"message_id": 5, "from": {"id": 1, "is_bot": True}, "text": quoted}}
        from chat import bot as chat_bot
        chat_bot.handle_update({"update_id": next(self.update_id), "message": msg})
        post.refresh_from_db()
        self.assertEqual(post.items.count(), 2)
        self.assertNotEqual(post.package_sha, before)
        self.assertEqual(ChatPhoto.objects.get(file_unique_id="them1").target, "noi-dung")   # không gắn vào báo cáo
        self.assertIn(f"BÀI NHÁP #{post.pk}", self.texts(self.chats[self.mkt.pk])[-1])
        inline = mock.patch.object(jobs, "kick")                                         # tiến trình nền không chạy
        inline.start()
        try:
            job = jobs.request_draft(self.mkt, "chống thấm sàn vệ sinh")
        finally:
            inline.stop()
        self.assertEqual(WriteJob.objects.get(pk=job.pk).state, WriteJob.State.WAITING)
        call_command("dang_bai", stdout=io.StringIO())                                   # hẹn giờ 2 phút chạy bù
        job.refresh_from_db()
        self.assertEqual(job.state, WriteJob.State.DONE)
        self.assertIsNotNone(job.post)
        WriteJob.objects.filter(pk=job.pk).update(state=WriteJob.State.RUNNING, started_at=timezone.now() - timedelta(minutes=30),
                                                   attempts=2)
        jobs.recover()                                                                   # kẹt quá lâu, hết lượt: báo lỗi
        job.refresh_from_db()
        self.assertEqual(job.state, WriteJob.State.FAILED)
        self.assertIn("Trợ lý chưa viết bài", self.texts(self.chats[self.mkt.pk])[-1])
        with mock.patch("subprocess.Popen") as popen:                                     # thật: mở tiến trình nền sau khi ghi
            jobs._spawn_if_idle()
        self.assertIn("viet_bai", popen.call_args[0][0])

    def test_photo_sent_again_same_telegram_file_is_still_used(self):
        """Chủ dự án 21/11/2026: gửi lại đúng tấm ảnh (Telegram giữ nguyên mã tệp) 3 lần mà bài vẫn "không có ảnh"."""
        self.message(self.mkt, "", photo="cung-tep")                                   # lần đầu: ảnh lẻ (rơi vào luồng báo cáo)
        Post.objects.all().delete()
        from chat.models import ChatPhoto as Photo_
        Photo_.objects.filter(file_unique_id="cung-tep").update(received_at=timezone.now() - timedelta(hours=2), target="noi-dung")
        self.message(self.mkt, "/baiviet dầm tum thang máy", photo="cung-tep")         # gửi lại đúng tệp kèm lệnh
        post = Post.objects.get()
        self.assertEqual(post.items.count(), 1)
        self.assertEqual(Photo_.objects.filter(file_unique_id="cung-tep").count(), 1)  # không lưu trùng ảnh
        draft = services.create_post(self.mkt, "Bài thử thêm ảnh", [], timezone.localdate())
        msg = {"message_id": 902, "chat": {"id": int(self.chats[self.mkt.pk]), "type": "private"},
               "from": {"id": self.mkt.pk + 9000, "first_name": "mkt"},
               "photo": [{"file_id": "cung-tep", "file_unique_id": "cung-tep", "file_size": 100, "width": 8}],
               "reply_to_message": {"message_id": 6, "from": {"id": 1, "is_bot": True}, "text": PREFIX_ANCHOR(draft)}}
        from chat import bot as chat_bot
        chat_bot.handle_update({"update_id": next(self.update_id), "message": msg})    # trả lời bản nháp bằng ảnh đã gửi
        draft.refresh_from_db()
        self.assertEqual(draft.items.count(), 1)

    def test_facebook_errors_in_vietnamese(self):
        text = facebook.vietnamese(190, 463, "Error validating access token: Session has expired on Friday, 20-Nov-26 00:00:00 "
                                             "PST. The current time is Saturday, 21-Nov-26 00:28:02 PST.")
        self.assertIn("Khoá Facebook đã hết hạn lúc 15:00 20/11/2026 (giờ Việt Nam)", text)
        self.assertIn("không đúng dạng", facebook.vietnamese(190, None, "Invalid OAuth access token - Cannot parse access token"))
        self.assertIn("thiếu quyền", facebook.vietnamese(200, None, "Permissions error"))


class DeskTests(MarketingCase):
    """Chủ dự án 21/11/2026: một bot; gọi @mkt thì trợ lý Marketing làm việc với mình, gọi @ketoannoibo thì từ đó trao đổi
    với kế toán nội bộ; xong thì đóng lại — ảnh, tin không lẫn sang báo cáo hiện trường."""

    def last(self, user):
        return self.texts(self.chats[user.pk])[-1]

    def test_director_works_with_marketing_desk_then_closes(self):
        self.message(self.director, "@mkt")
        self.assertIn("📣 Trợ lý Marketing đang làm việc", self.last(self.director))
        self.message(self.director, "", photo="ban1")                                    # ảnh ở bàn: để dành cho bài sắp viết
        self.assertIn("Đã nhận ảnh cho bài sắp viết (1 ảnh)", self.last(self.director))
        self.assertFalse(Proposal.objects.exists())                                       # không thành phiếu báo cáo
        self.message(self.director, "dầm tum thang máy hay bị bỏ sót khi cải tạo nhà")    # chủ đề → bài mới kèm ảnh để dành
        post = Post.objects.get()
        self.assertEqual(post.items.count(), 1)
        self.message(self.director, "", photo="ban2")                                    # ảnh tiếp → thêm vào bài đang làm
        post.refresh_from_db()
        self.assertEqual(post.items.count(), 2)
        self.assertIn(f"BÀI NHÁP #{post.pk}", self.last(self.director))
        captured = []
        with mock.patch.object(ai, "available", return_value=True), \
                mock.patch.object(ai, "create_message", side_effect=PageVoiceTests.ai_reply(captured, {"text": "Bài ngắn hơn"})):
            self.message(self.director, "viết lại cho ngắn hơn")                          # ý sửa rõ → sửa bài đang làm
        post.refresh_from_db()
        self.assertEqual(post.text, "Bài ngắn hơn")
        self.message(self.director, "sửa chữa nhà cũ cần lưu ý gì")                       # chưa rõ → hỏi bằng 2 nút
        buttons = self.last_buttons(self.chats[self.director.pk])
        self.assertEqual(buttons["📝 Viết bài mới về chủ đề này"], "mk:dn:0")
        self.press(self.director, "mk:dn:0")
        self.assertEqual(Post.objects.count(), 2)
        self.assertIn("sửa chữa nhà cũ", Post.objects.order_by("-pk").first().idea.text)
        self.message(self.director, "@đóng")
        self.assertIn("Đã đóng 📣 Trợ lý Marketing", self.last(self.director))
        self.message(self.director, "", photo="ban3")                                    # đóng rồi: ảnh lại là ảnh báo cáo
        self.assertIn("Nhắn nội dung báo cáo", self.last(self.director))

    def test_accounting_desk_photo_is_voucher_and_report_text_not_misread(self):
        self.message(self.director, "@kế toán nội bộ")
        self.assertIn("💰 Trợ lý Kế toán nội bộ đang làm việc", self.last(self.director))
        self.message(self.director, "", photo="chungtu1")
        self.assertIn("Đã nhận ảnh chứng từ", self.last(self.director))
        self.message(self.director, "TN-22 T1.2 40%")                                    # cú pháp báo cáo: không thành báo cáo
        self.assertFalse(Proposal.objects.filter(kind=Proposal.Kind.REPORT).exists())
        self.assertTrue(self.last(self.director).startswith("💰"))
        self.assertIn("chưa hiểu tin này", self.last(self.director))
        self.message(self.director, "trả NCC 5tr Cát Hùng Vương TN-22 ck")                 # phiếu thu chi vẫn lập như thường
        self.assertTrue(Proposal.objects.filter(kind=Proposal.Kind.MONEY).exists())
        captured = {}

        def fake_run(text, history, context, execute, images=(), tools=None):
            captured.update(tools=[t["name"] for t in tools or []], context=context)
            return {"type": "text", "text": "Tuần này chi 12.000.000 ₫."}
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run", side_effect=fake_run):
            self.message(self.director, "tuần này chi bao nhiêu?")
        self.assertIn("propose_entries", captured["tools"])
        self.assertNotIn("propose_report", captured["tools"])                             # bàn kế toán không lập báo cáo
        self.assertIn("ĐANG LÀM VIỆC VỚI TRỢ LÝ KẾ TOÁN NỘI BỘ", captured["context"])
        self.assertEqual(self.last(self.director), "💰 Tuần này chi 12.000.000 ₫.")

    def test_rights_unknown_names_idle_close_and_marketing_default(self):
        self.message(self.site, "@ketoannoibo")
        self.assertIn("chưa dành cho vai", self.last(self.site))
        self.message(self.site, "@abc")
        self.assertIn("Chưa có trợ lý tên đó", self.last(self.site))
        self.message(self.site, "@giamsat TN-22 T1.2 40%")                                # gọi kèm tin: xử lý luôn ở bàn đó
        self.assertTrue(Proposal.objects.filter(kind=Proposal.Kind.REPORT).exists())
        from chat.models import ChatLink
        row = ChatLink.objects.get(user=self.site)
        row.state = {**row.state, "desk_at": (timezone.now() - timedelta(hours=4)).isoformat()}
        row.save(update_fields=["state"])
        self.message(self.site, "/tongquan")
        self.assertTrue(any("tự đóng sau 3 giờ" in t for t in self.texts(self.chats[self.site.pk])[-2:]))
        self.assertNotIn("desk", ChatLink.objects.get(user=self.site).state)
        self.message(self.mkt, "", photo="mkt-mac-dinh")                                 # vai Marketing luôn ở bàn Marketing
        self.assertIn("Đã nhận ảnh cho bài sắp viết", self.last(self.mkt))


def PREFIX_ANCHOR(post):
    from .bot import PREFIX, anchor_text
    return PREFIX + anchor_text(post)


class SecretTests(MarketingCase):
    def test_private_secret_deleted_warned_never_stored_or_sent_to_ai(self):
        with mock.patch.object(ai, "available", return_value=True), mock.patch.object(ai, "run") as run:
            self.message(self.site, "anh ơi mật khẩu: Tend@2026xyz của email công ty")
            run.assert_not_called()
        self.assertTrue(any(m == "deleteMessage" for m, _ in self.sent))
        self.assertIn("ĐỔI mật khẩu", self.texts(self.chats[self.site.pk])[-1])
        self.assertIn("Minh vừa nhắn mật khẩu", self.texts(self.chats[self.director.pk])[-1])
        self.assertFalse(Feedback.objects.exists())
        self.assertFalse(Proposal.objects.exists())
        self.assertFalse(any("Tend@2026xyz" in str(log.after) for log in AuditLog.objects.all()))
        self.message(self.site, "/gopy key mới là sk-ant-api03-abcdefghijklmnop nhé")       # bị xoá trước khi thành góp ý
        self.assertFalse(Feedback.objects.exists())
        from assistant import services as assistant_services
        row = assistant_services.add_feedback(self.site, "lỗi khi dán ghp_abcdefghijklmnopqrstuvwxyz123456")
        self.assertIn("[mật khẩu/khoá đã che]", row.text)

    def test_group_secret_alert(self):
        from chat.tests_group import GROUP
        self.message(self.director, "")      # no-op để có đủ kết nối
        msg = {"message_id": 77, "chat": {"id": GROUP, "type": "supergroup", "title": "Công trường TN-22"},
               "from": {"id": self.site.pk + 9000, "first_name": "giamsat"}, "text": "mã bot 123456789:AAEhBP0av28iQ9BsYc_ABCDEFGHIJKLMNOPQR"}
        from chat import bot
        bot.handle_update({"update_id": 99999, "message": msg})
        self.assertIn(("deleteMessage", {"chat_id": str(GROUP), "message_id": 77}), self.sent)
        self.assertIn("mã bot Telegram", [d["text"] for m, d in self.sent if m == "sendMessage" and str(d["chat_id"]) == str(GROUP)][-1])


class ItDeskTests(MarketingCase):
    def test_ledger_refuses_secrets_reminds_and_director_only(self):
        today = timezone.localdate()
        with self.assertRaises(Exception):
            it_services.save(self.director, name="Facebook tendct", note="mật khẩu: Abc12345!")
        it_services.save(self.director, name="Tên miền tend.vn", kind="ten_mien", expires_on=today + timedelta(days=12),
                         vault_ref="Tend / Tên miền", owner=self.director)
        it_services.save(self.director, name="Khoá Meta TEND_META_TOKEN", kind="khoa_api", rotated_on=today - timedelta(days=100),
                         rotate_days=90, two_factor="chua")
        lines = it_services.attention(today)
        self.assertTrue(lines[0].startswith("▼ cần làm ngay Khoá Meta"))
        self.assertIn("quá hạn đổi mật khẩu/khoá", lines[0])
        self.assertIn("chưa bật xác thực 2 lớp", lines[0])
        self.assertIn("● cần để ý Tên miền tend.vn: hết hạn", lines[1])
        text = schedule.morning_text(self.director, timezone.now())
        self.assertIn("Trợ lý IT — tài khoản cần làm", text)
        self.client.force_login(self.mkt)
        self.assertEqual(self.client.get(reverse("itdesk")).status_code, 403)
        self.client.force_login(self.director)
        page = self.client.post(reverse("itdesk"), {"name": "GitHub", "note": "token ghp_abcdefghijklmnopqrstuvwxyz123456"}, follow=True)
        self.assertContains(page, "Không ghi mật khẩu hay khoá")
        self.assertEqual(ServiceAccount.objects.count(), 2)
        self.assertTrue(AuditLog.objects.filter(table="itdesk.ServiceAccount", action="create").exists())
