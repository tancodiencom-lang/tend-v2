"""Trợ lý nội dung trên Telegram: gửi bản duyệt (album ảnh + tin neo có nút), nút duyệt/sửa/đổi ảnh/bỏ/kênh, lệnh
/ytuong, /baiviet, /noidung. Người duyệt: Giám đốc và vai Marketing đã kết nối Telegram.

Nút Duyệt mang 10 ký tự đầu của dấu bản bài: bấm nút của bản cũ (sau khi đã sửa chữ/đổi ảnh) thì bị từ chối.
"""
import re
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from chat import telegram
from chat.models import ChatLink
from projects.models import Project

from . import assets as asset_files
from . import publish, services, writer
from .models import Channel, Idea, Post, PostTarget

PREFIX = "📣 Nội dung: "
COMMANDS = ("/ytuong", "/baiviet", "/noidung")
HELP = ("• Nội dung số (Giám đốc, Marketing): /baiviet [TN-22] <yêu cầu> — trợ lý soạn bài ngay (gửi ảnh trước nếu có); "
        "/ytuong <ý tưởng> — để dành cho bài hằng ngày; /noidung — bài chờ duyệt, /noidung 12 — xem lại bài #12.")


def reviewers():
    rows = ChatLink.objects.filter(active=True, user__is_active=True).select_related("user")
    return [row for row in rows if row.user.does_marketing]


def _short(post):
    return post.package_sha[:10]


def anchor_text(post):
    items = post.items.count()
    channels = ", ".join(c.name for c in post.channels.all()) or "chưa chọn Trang"
    who = "trợ lý soạn" if post.created_by is None else f"{post.created_by} giao" if post.origin == Post.Origin.REQUEST else str(post.created_by)
    lines = [f"BÀI NHÁP #{post.pk} · ngày {post.for_date:%d/%m} · {who}" + (" · AI viết" if post.ai_written else ""),
             f"Đăng lên: {channels}", "", post.text, "",
             f"Ảnh: {items} (ảnh 1 hiện đầu tiên; Facebook có thể tự sắp bố cục khác)" if items else "Bài không có ảnh."]
    if post.note:
        lines.append(f"Ghi chú trợ lý: {post.note}")
    lines.append("Bấm ✓ Duyệt và đăng khi chữ, ảnh, thứ tự và Trang đều đúng. Sửa chữ/đổi ảnh xong bot gửi lại bản mới.")
    return "\n".join(lines)


def buttons(post):
    rows = [[{"text": "✓ Duyệt và đăng", "data": f"mk:a:{post.pk}:{_short(post)}"}],
            [{"text": "✍ Sửa chữ", "data": f"mk:e:{post.pk}"}, {"text": "🔄 Đổi/bớt ảnh", "data": f"mk:s:{post.pk}"},
             {"text": "× Bỏ bài", "data": f"mk:x:{post.pk}"}]]
    channels = list(Channel.objects.filter(active=True))
    if len(channels) > 1:
        chosen = set(post.channels.values_list("pk", flat=True))
        rows.append([{"text": ("☑ " if c.pk in chosen else "☐ ") + c.name, "data": f"mk:c:{post.pk}:{c.pk}"} for c in channels[:4]])
    return rows


def send_preview(post, links=None):
    """Album ảnh (ảnh nhỏ 1280 px) rồi tin neo có nút, tới từng người duyệt."""
    photos = [data for data in (asset_files.thumbnail(item.asset, 1280, 82) for item in
                                post.items.select_related("asset").order_by("order")) if data]
    for row in (links if links is not None else reviewers()):
        if photos:
            telegram.send_media_group(row.chat_id, photos)
        telegram.send(row.chat_id, PREFIX + anchor_text(post), buttons(post))


def notify(text, exclude=None):
    for row in reviewers():
        if exclude is None or row.user_id != exclude.pk:
            telegram.send(row.chat_id, PREFIX + text)


def photo_menu(chat_id, post):
    count = post.items.count()
    if not count:
        telegram.send(chat_id, PREFIX + f"Bài #{post.pk} không có ảnh.")
        return
    rows, numbers = [], range(count)
    for label, op in (("🔄", "r"), ("⬆", "f"), ("🗑", "d"), ("🚫", "b")):
        for start in range(0, count, 5):
            rows.append([{"text": f"{label} {i + 1}", "data": f"mk:{op}:{post.pk}:{i}"} for i in numbers[start:start + 5]
                         if not (op == "f" and i == 0) and not (op == "d" and count == 1)])
    telegram.send(chat_id, PREFIX + f"Bài #{post.pk}: 🔄 đổi ảnh số đó bằng ảnh khác trong kho · ⬆ đưa lên đầu · 🗑 bớt ảnh · "
                  "🚫 không dùng ảnh đó nữa (đổi ảnh khác).", [r for r in rows if r])


def _post(value):
    return Post.objects.filter(pk=int(value) if str(value).isdecimal() else 0).first()


def callback(link_row, data, chat_id, state_set):
    """Nút mk:*. Trả chữ ngắn cho thông báo nút; tự gửi bản mới khi bài đổi."""
    parts = data.split(":")
    op, post = parts[1] if len(parts) > 1 else "", _post(parts[2] if len(parts) > 2 else "")
    arg = parts[3] if len(parts) > 3 else ""
    user = link_row.user
    if op == "m":                     # đối soát bằng tay: mk:m:<kênh của bài>:y|n
        target = PostTarget.objects.filter(pk=int(parts[2]) if parts[2].isdecimal() else 0).first()
        if target is None:
            return "Không tìm thấy."
        try:
            post = publish.resolve(user, target, arg == "y")
        except (PermissionDenied, ValueError) as error:
            return str(error)
        return f"✓ Bài #{post.pk}: {post.get_state_display().lower()}."
    if post is None:
        return "Không tìm thấy bài."
    try:
        if op == "a":
            post = services.approve(user, post, arg)
            notify(f"{user} đã duyệt bài #{post.pk}. Trợ lý đăng trong vài phút và báo lại đường dẫn.", exclude=user)
            return f"✓ Đã duyệt bài #{post.pk}, đang chờ đăng."
        if op == "e":
            services._can(user)
            state_set(mkt_edit=str(post.pk))
            telegram.send(chat_id, PREFIX + f"Nhắn chữ mới cho bài #{post.pk} (thay nguyên văn). Muốn trợ lý sửa theo ý thì bắt đầu "
                                            "bằng \"AI:\", vd \"AI: ngắn hơn, thêm câu mời xem nhà mẫu\".")
            return "Nhắn chữ mới."
        if op == "s":
            services._can(user)
            photo_menu(chat_id, post)
            return "Chọn ảnh cần đổi."
        if op in ("r", "b", "d", "f"):
            index = int(arg) if arg.isdecimal() else -1
            if op == "d":
                post = services.remove_asset(user, post, index)
            elif op == "f":
                post = services.move_first(user, post, index)
            else:
                post = services.swap_asset(user, post, index, block=op == "b")
            send_preview(post, [link_row])
            return "✓ Đã đổi, bot gửi lại bản mới."
        if op == "x":
            post = services.reject(user, post, "Bỏ trên Telegram")
            return f"✓ Đã bỏ bài #{post.pk}."
        if op == "c":
            target = Channel.objects.filter(pk=int(arg) if arg.isdecimal() else 0).first()
            if target is None:
                return "Không tìm thấy Trang."
            post = services.toggle_channel(user, post, target)
            telegram.send(chat_id, PREFIX + anchor_text(post), buttons(post))
            return "✓ Đã đổi Trang đăng."
        if op == "t":
            post = publish.retry(user, post)
            return f"✓ Bài #{post.pk} sẽ đăng lại trong vài phút."
    except (PermissionDenied, ValidationError, ValueError) as error:
        return "; ".join(getattr(error, "messages", [str(error)]))
    return "Không rõ thao tác."


def edit_text(link_row, post_id, text):
    """Tin nhắn sau khi bấm ✍ Sửa chữ: thay nguyên văn, hoặc "AI: …" để trợ lý sửa theo ý."""
    post = _post(post_id)
    if post is None:
        return "Không tìm thấy bài."
    try:
        if text.strip().casefold().startswith("ai:"):
            text = writer.revise(post, text.strip()[3:].strip())
            if text is None:
                return "Trợ lý AI chưa bật hoặc đang lỗi: nhắn chữ mới (thay nguyên văn)."
        post = services.set_text(link_row.user, post, text)
    except (PermissionDenied, ValidationError) as error:
        return "; ".join(getattr(error, "messages", [str(error)]))
    send_preview(post, [link_row])
    return None


def _project(text):
    for project in Project.objects.filter(active=True):
        if re.search(rf"(?<![\w-]){re.escape(project.code)}(?![\w-])", text, re.I):
            return project, re.sub(rf"(?<![\w-]){re.escape(project.code)}(?![\w-])", " ", text, flags=re.I)
    return None, text


def _sent_photos(link_row):
    """Ảnh người giao bài vừa gửi (30 phút, chưa gắn phiếu) → kho ảnh; đánh dấu để không gắn vào báo cáo nữa."""
    from chat.bot import PHOTO_WINDOW
    items = []
    for photo in link_row.photos.filter(proposal__isnull=True, received_at__gte=timezone.now() - PHOTO_WINDOW).exclude(target="noi-dung"):
        try:
            with photo.image.open("rb") as handle:
                data = handle.read()
        except OSError:
            continue
        asset = asset_files.add_upload(link_row.user, data, photo.image.name.rsplit("/", 1)[-1], context=photo.caption)
        photo.target = "noi-dung"
        photo.save(update_fields=["target"])
        if asset is not None:
            items.append(asset)
    return items


def command(link_row, name, text):
    """Lệnh nội dung số → chữ trả lời (bản duyệt tự gửi)."""
    user = link_row.user
    if not user.does_marketing:
        return "Lệnh nội dung số dành cho Giám đốc và nhân viên Marketing."
    arg = text.split(maxsplit=1)[1].strip() if len(text.split()) > 1 else ""
    if name == "/noidung":
        if arg.lstrip("#").isdecimal():
            post = _post(arg.lstrip("#"))
            if post is None:
                return "Không tìm thấy bài."
            send_preview(post, [link_row])
            return None
        waiting = Post.objects.filter(state__in=(Post.State.DRAFT, Post.State.FAILED, Post.State.UNKNOWN)).order_by("for_date")
        lines = [f"• #{p.pk} · {p.for_date:%d/%m} · {p.get_state_display().lower()} · {p.items.count()} ảnh · {p.text[:60]}…"
                 for p in waiting[:15]]
        return ("BÀI CHỜ XỬ LÝ\n" + "\n".join(lines) + "\nXem lại bản duyệt: /noidung <số bài>" if lines
                else "Không có bài nào chờ duyệt.") + f"\nTrang nội dung: {settings.APP_URL}/noi-dung/"
    project, rest = _project(arg)
    rest = " ".join(rest.split())
    if name == "/ytuong":
        idea = services.add_idea(user, rest, Idea.Kind.IDEA, project)
        return f"✓ Đã ghi ý tưởng #{idea.pk}. Trợ lý dùng cho bài hằng ngày (ưu tiên ý tưởng cũ trước)."
    if name == "/baiviet":
        items = _sent_photos(link_row)
        idea = services.add_idea(user, rest or "Bài từ ảnh vừa gửi", Idea.Kind.REQUEST, project)
        post = writer.draft(timezone.localdate(), project=project, idea=idea, brief=rest, origin=Post.Origin.REQUEST, user=user,
                            items=items)
        if post is None:
            return "Chưa soạn được: kho chưa có ảnh phù hợp. Gửi ảnh trước rồi nhắn /baiviet <yêu cầu>."
        send_preview(post)
        return None
    return None


def report_results(results):
    """Báo người duyệt kết quả đăng (sau mỗi lượt dang_bai)."""
    for post, channel, state in results:
        if state == PostTarget.State.PUBLISHED:
            target = post.targets.filter(channel=channel, state=PostTarget.State.PUBLISHED).order_by("-pk").first()
            notify(f"✓ Đã đăng bài #{post.pk} lên {channel.name}: {target.permalink if target else ''}")
        elif state == PostTarget.State.FAILED:
            target = post.targets.filter(channel=channel).order_by("-pk").first()
            for row in reviewers():
                telegram.send(row.chat_id, PREFIX + f"✗ Chưa đăng được bài #{post.pk} lên {channel.name}: {target.error if target else ''}. "
                              "Chưa có bài nào được tạo; bấm Thử lại khi đã xử lý.", [[{"text": "↻ Thử lại", "data": f"mk:t:{post.pk}"}]])
        elif state == PostTarget.State.UNKNOWN:
            target = post.targets.filter(channel=channel, state=PostTarget.State.UNKNOWN).order_by("-pk").first()
            if target is None:
                continue
            for row in reviewers():
                telegram.send(row.chat_id, PREFIX + f"⚠ Bài #{post.pk} trên {channel.name}: Facebook không trả lời kịp sau khi gửi lệnh tạo bài. "
                              "Trợ lý KHÔNG tự đăng lại (tránh đăng trùng) và sẽ tự đọc Trang để tìm. Nếu anh/chị kiểm tra Trang thấy rồi "
                              "hoặc chắc chắn chưa có, bấm nút dưới.",
                              [[{"text": "✓ Đã có trên Trang", "data": f"mk:m:{target.pk}:y"},
                                {"text": "✗ Chưa có, cho thử lại", "data": f"mk:m:{target.pk}:n"}]])


def waiting_count():
    return Post.objects.filter(state=Post.State.DRAFT).count()


def stale_drafts(now, hours=6):
    return Post.objects.filter(state=Post.State.DRAFT, created_at__lte=now - timedelta(hours=hours))
