"""Soạn bài nháp: chọn ảnh từ kho, AI viết theo giọng văn các bài mẫu, hoặc chữ mẫu khi chưa bật AI.

AI (cùng mô hình và trần chi phí tháng với bot: chat.ai) chỉ được thấy ảnh thu nhỏ + mô tả việc (công việc, tầng, phòng),
KHÔNG được thấy tên/mã công trình, tên chủ nhà, địa chỉ, giá. Kết quả theo khuôn JSON cố định (structured outputs).
AI không tự đăng: mọi bài là nháp chờ người duyệt.
"""
import base64
import json
import logging
from datetime import timedelta

from django.conf import settings
from django.db.models import Count
from django.utils import timezone

from chat import ai

from . import assets as asset_files
from .models import Asset, Channel, Idea, Post, StyleSample

log = logging.getLogger(__name__)
CANDIDATES = 12
RECENT_DAYS = 21

# Thương hiệu KHÔNG ghi cố định ở đây: mỗi Trang (Channel) có giới thiệu riêng (`brief`) và bài mẫu riêng — TenD, New Roma…
# là các thương hiệu khác nhau của cùng công ty (system_prompt).
RULES = """Quy tắc bắt buộc:
- Chỉ viết cho đúng Trang này: đúng thương hiệu, dịch vụ, cách xưng hô, câu kêu gọi, hashtag trong giới thiệu và bài mẫu
  của Trang. Không nhắc tên, phong cách, khẩu hiệu hay hashtag của thương hiệu/Trang khác.
- Tiếng Việt có dấu, viết theo GIỌNG VĂN của các bài mẫu (độ dài, cách mở bài, biểu tượng cảm xúc, hashtag, câu kêu gọi).
  Không có bài mẫu thì viết 80–180 chữ, ấm áp, chuyên nghiệp, kết bằng lời mời nhắn tin tư vấn.
- KHÔNG nêu tên chủ nhà, địa chỉ, số nhà, tên đường, mã công trình nội bộ, số điện thoại cá nhân, giá tiền; không hứa giá
  hay tiến độ. Chỉ nói điều thấy được trong ảnh và mô tả việc kèm theo; không bịa vật liệu, diện tích, thời gian.
- Chọn 1–10 ảnh đẹp, rõ, đúng chủ đề; ảnh tổng thể đẹp nhất đứng đầu. BỎ ảnh mờ, tối, lộn xộn, ảnh có mặt người nhìn rõ,
  giấy tờ, màn hình, biển số nhà, biển số xe.
- "note": một câu cho người duyệt biết vì sao chọn chủ đề/ảnh này và ảnh nào đã bỏ (nếu có)."""

SCHEMA = {"type": "json_schema", "schema": {
    "type": "object", "additionalProperties": False, "required": ["photos", "text", "note"],
    "properties": {"photos": {"type": "array", "items": {"type": "integer"},
                              "description": "Số thứ tự ảnh được chọn, theo thứ tự đăng (ảnh đầu hiện to nhất)"},
                   "text": {"type": "string", "description": "Nội dung bài đăng hoàn chỉnh"},
                   "note": {"type": "string", "description": "Ghi chú ngắn cho người duyệt"}}}}


def used_ids():
    """Ảnh đã nằm trong bài đã đăng hoặc đang chờ (không dùng lại)."""
    return set(Asset.objects.filter(uses__post__state__in=[s for s in Post.State.values if s != Post.State.REJECTED])
               .values_list("pk", flat=True))


def candidates(project=None, exclude=(), limit=CANDIDATES):
    rows = Asset.objects.filter(blocked=False).exclude(pk__in=set(exclude) | used_ids()).select_related("project")
    if project is not None:
        rows = rows.filter(project=project)
    return list(rows.order_by("-taken_on", "-pk")[:limit])


def next_candidate(project=None, exclude=()):
    found = candidates(project, exclude, 1) or candidates(None, exclude, 1)
    return found[0] if found else None


def pick_project(today):
    """Công trình có nhiều ảnh mới chưa dùng nhất trong 3 tuần gần đây (luân phiên tự nhiên giữa các công trình)."""
    since = today - timedelta(days=RECENT_DAYS)
    rows = (Asset.objects.filter(blocked=False, project__isnull=False, taken_on__gte=since).exclude(pk__in=used_ids())
            .values("project").annotate(n=Count("pk")).order_by("-n"))
    first = rows.first()
    if first is None:
        return None
    from projects.models import Project
    return Project.objects.filter(pk=first["project"]).first()


def primary_channel():
    """Trang soạn bài khi không chỉ rõ: Trang mặc định đang dùng đầu tiên (theo tên), không có thì Trang đang dùng đầu tiên."""
    rows = Channel.objects.filter(active=True).order_by("name")
    return rows.filter(default=True).first() or rows.first()


def samples(channel=None, limit=8):
    """Bài mẫu của đúng Trang trước, rồi bài mẫu dùng chung (không gắn Trang). Không lấy bài mẫu của Trang khác."""
    rows = StyleSample.objects.filter(active=True).order_by("-created_at")
    own = list(rows.filter(channel=channel)[:limit]) if channel is not None else []
    shared = list(rows.filter(channel__isnull=True)[:limit - len(own)]) if len(own) < limit else []
    return [s.text[:900] for s in own + shared]


def system_prompt(channel, limit=8):
    """→ (lời dặn hệ thống, có giới thiệu hoặc bài mẫu không)."""
    name = channel.name if channel else "của công ty"
    parts = [f"Bạn là biên tập viên nội dung của Trang Facebook \"{name}\". Bạn soạn bài đăng Facebook từ ảnh công trình thật "
             "để người duyệt xem trước khi đăng."]
    brief = (channel.brief or "").strip() if channel else ""
    if brief:
        parts.append("GIỚI THIỆU TRANG (người quản lý Trang viết; viết đúng thương hiệu, dịch vụ, cách xưng hô, câu kêu gọi, "
                     "hashtag ở đây):\n" + brief)
    else:
        parts.append(f"Trang chưa có giới thiệu: chỉ xưng tên Trang \"{name}\"; không tự gán dòng sản phẩm, phong cách, khẩu hiệu "
                     "hay hashtag thương hiệu nào.")
    parts.append(RULES)
    style = samples(channel, limit)
    if style:
        parts.append("CÁC BÀI MẪU GIỌNG VĂN CỦA TRANG:\n" + "\n---\n".join(style))
    return "\n\n".join(parts), bool(brief or style)


def _voice_note(channel, known):
    if known or channel is None:
        return ""
    return (f" ⚠ Trang \"{channel.name}\" chưa có giới thiệu và bài mẫu nên giọng văn, thương hiệu có thể chưa đúng: vào "
            "Nội dung › Cài đặt điền Giới thiệu Trang hoặc lấy bài cũ làm mẫu.")


def _image_blocks(items):
    blocks = []
    for index, asset in enumerate(items):
        data = asset_files.thumbnail(asset, 768, 78)
        if data is None:
            continue
        when = f", ngày {asset.taken_on:%d/%m/%Y}" if asset.taken_on else ""
        blocks.append({"type": "text", "text": f"Ảnh số {index}: {asset.context or 'không có mô tả'}{when}"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(data).decode()}})
    return blocks


def template_text(items, brief="", channel=None):
    """Chữ mẫu khi chưa bật AI: không gắn thương hiệu nào ngoài tên Trang (người duyệt sửa trước khi đăng)."""
    works = list(dict.fromkeys(a.context.split(" · ")[0] for a in items if a.context and not a.context.startswith("ảnh chung")))
    topic = brief or (", ".join(works[:3]).lower() if works else "hình ảnh thi công")
    who = channel.name if channel else "chúng tôi"
    return (f"✨ Cập nhật công trình: {topic}.\n\nTừng chi tiết được đội ngũ thi công tỉ mỉ, đúng thiết kế. "
            f"Anh chị cần tư vấn, nhắn tin cho {who} nhé!")


def write(items, brief="", channel=None):
    """→ {"text", "assets": [Asset theo thứ tự], "note", "ai": bool}. AI lỗi hoặc chưa bật: chữ mẫu, giữ tối đa 6 ảnh.
    `channel`: Trang sẽ đăng — trợ lý viết theo giới thiệu và bài mẫu của Trang đó."""
    items = list(items)
    system, known = system_prompt(channel)
    if ai.available() and items:
        ask = ("Soạn một bài đăng từ các ảnh trên." + (f" Yêu cầu của người giao bài: {brief}" if brief else "")
               + " Trả về JSON theo khuôn.")
        try:
            response = ai.create_message(model=ai.model(), max_tokens=4000,
                                         system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                         messages=[{"role": "user", "content": [*_image_blocks(items), {"type": "text", "text": ask}]}],
                                         output_config={"effort": "medium", "format": SCHEMA})
            ai._record(response.usage)
            if response.stop_reason == "refusal":
                raise ValueError("refusal")
            data = json.loads(next(b.text for b in response.content if b.type == "text"))
            chosen = [items[i] for i in dict.fromkeys(data.get("photos") or []) if isinstance(i, int) and 0 <= i < len(items)]
            text = str(data.get("text") or "").strip()
            if chosen and text:
                note = (str(data.get("note") or "") + _voice_note(channel, known)).strip()
                return {"text": text, "assets": chosen[:10], "note": note[:1000], "ai": True}
        except Exception as error:      # mạng/khoá/khuôn lỗi: vẫn có bài nháp để người duyệt sửa
            log.warning("AI soạn bài lỗi (%s)", type(error).__name__)
    note = "Chưa bật trợ lý AI (hoặc AI lỗi): chữ mẫu, cần sửa trước khi duyệt." if items else "Kho chưa có ảnh phù hợp."
    return {"text": template_text(items[:6], brief, channel), "assets": items[:6], "note": note + _voice_note(channel, known),
            "ai": False}


def draft(today, project=None, idea=None, brief="", origin=Post.Origin.DAILY, user=None, items=None, channel=None):
    """Lập một bài nháp cho MỘT Trang (`channel`, mặc định primary_channel). `items`: ảnh người giao bài gửi kèm (ưu tiên),
    không thì lấy từ kho theo công trình."""
    from .services import create_post
    channel = channel or primary_channel()
    project = project or (idea.project if idea else None)
    pool = list(items or [])
    if len(pool) < 3:
        pool += [a for a in candidates(project or (None if pool else pick_project(today)), [a.pk for a in pool])
                 if a not in pool][: CANDIDATES - len(pool)]
    if not pool and not brief and idea is None:
        return None
    result = write(pool, brief or (idea.text if idea else ""), channel)
    chosen_project = project or next((a.project for a in result["assets"] if a.project_id), None)
    return create_post(user, result["text"], result["assets"], today, project=chosen_project, origin=origin, idea=idea,
                       note=result["note"], ai_written=result["ai"], channels=[channel] if channel else None)


def daily(now=None):
    """Bài nháp hằng ngày: MỖI Trang mặc định đủ TEND_MKT_DAILY bài (mặc định 1) cho hôm nay, viết theo giọng của Trang đó;
    ưu tiên yêu cầu và ý tưởng đang mở. Chưa có Trang nào: vẫn soạn (đếm chung), người duyệt chọn Trang sau."""
    today = timezone.localtime(now).date() if now else timezone.localdate()
    want = int(getattr(settings, "TEND_MKT_DAILY", 1) or 0)
    made = []
    for channel in list(Channel.objects.filter(active=True, default=True).order_by("name")) or [None]:
        rows = Post.objects.filter(for_date=today, origin=Post.Origin.DAILY)
        have = (rows.filter(channels=channel) if channel else rows).count()
        for _ in range(max(0, want - have)):
            idea = Idea.objects.filter(state=Idea.State.OPEN).order_by("-kind", "created_at").first()   # yêu cầu trước ý tưởng
            post = draft(today, idea=idea, channel=channel)
            if post is None:
                return made
            made.append(post)
    return made


REVISE = {"type": "json_schema", "schema": {"type": "object", "additionalProperties": False, "required": ["text"],
                                            "properties": {"text": {"type": "string"}}}}


def revise(post, instruction):
    """Người duyệt nhắn "AI: ngắn hơn…" → chữ mới (giữ quy tắc không tên/địa chỉ/giá). None nếu chưa bật AI hoặc lỗi."""
    if not ai.available() or not instruction:
        return None
    system, _ = system_prompt(post.channels.order_by("name").first() or primary_channel(), 4)
    try:
        response = ai.create_message(model=ai.model(), max_tokens=3000,
                                     system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                     messages=[{"role": "user", "content": f"Bài hiện tại:\n{post.text}\n\nYêu cầu sửa của người duyệt: "
                                                                           f"{instruction}\n\nTrả về JSON với bài đã sửa hoàn chỉnh."}],
                                     output_config={"effort": "low", "format": REVISE})
        ai._record(response.usage)
        if response.stop_reason == "refusal":
            return None
        text = str(json.loads(next(b.text for b in response.content if b.type == "text")).get("text") or "").strip()
        return text or None
    except Exception as error:
        log.warning("AI sửa bài lỗi (%s)", type(error).__name__)
        return None
