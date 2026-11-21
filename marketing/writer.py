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
from django.db.models import Count, Q
from django.utils import timezone

from chat import ai

from . import assets as asset_files
from . import quality
from .models import Asset, Channel, Idea, Post, StyleSample

log = logging.getLogger(__name__)
CANDIDATES = 12
RECENT_DAYS = 21
MIN_SCORE = 3              # AI chấm dưới mức này: không đưa vào bài (mã tự loại, không trông vào AI)
MAX_PASSED = 2             # người duyệt đã đổi ảnh này ≥ 2 lần: thôi đề xuất
ENOUGH = 3                 # bài có ít hơn số ảnh đạt chuẩn này: ghi chú + nhắc giám sát chụp thêm

# Thương hiệu KHÔNG ghi cố định ở đây: mỗi Trang (Channel) có giới thiệu riêng (`brief`) và bài mẫu riêng — TenD, New Roma…
# là các thương hiệu khác nhau của cùng công ty (system_prompt).
# Chủ dự án 21/11/2026: bài cũ "nông", AI không khai thác ý người giao → yêu cầu là Ý CHÍNH, được dùng kiến thức chuyên môn
# chung (không bịa dữ kiện riêng của công trình); bài chuyên môn dài hơn bài cập nhật.
RULES = """CÁCH VIẾT
- YÊU CẦU CỦA NGƯỜI GIAO BÀI (nếu có) là Ý CHÍNH của bài: khai thác sâu, KHÔNG chép lại nguyên văn yêu cầu. Dùng kiến thức
  chuyên môn xây dựng, hoàn thiện nhà đúng thực tế Việt Nam để giải thích: vì sao hạng mục/vấn đề đó quan trọng, sai lầm
  hay gặp và hậu quả, dấu hiệu chủ nhà tự nhận biết, cách làm đúng và cách kiểm tra, lời khuyên cho chủ nhà. Ảnh (nếu có)
  minh hoạ cách đơn vị làm ở công trình thật.
- Chọn dạng bài:
  • BÀI CHUYÊN MÔN khi người giao nêu một hạng mục, một vấn đề, một câu hỏi hoặc yêu cầu viết chi tiết: 350–600 chữ; câu mở
    đầu gây chú ý (nêu điều chủ nhà hay gặp hoặc hay bỏ sót); 3–5 ý chính, mỗi ý cụ thể, ngắn gọn (được dùng gạch đầu dòng,
    biểu tượng tiết chế); liên hệ ảnh công trình; kết bằng lời khuyên và câu kêu gọi của Trang.
  • BÀI CẬP NHẬT CÔNG TRÌNH khi không có yêu cầu hoặc chỉ khoe ảnh: 100–200 chữ, nói cụ thể việc thấy trong ảnh.
  Có bài mẫu thì theo GIỌNG VĂN bài mẫu (cách mở bài, biểu tượng cảm xúc, hashtag, câu kêu gọi); độ dài theo dạng bài.
- Tiếng Việt có dấu, tự nhiên, ấm áp, chuyên nghiệp. Tránh câu sáo rỗng ("tỉ mỉ từng chi tiết", "chất lượng hàng đầu")
  nếu không kèm điều cụ thể.

SỰ THẬT, RIÊNG TƯ
- Kiến thức kỹ thuật chung được dùng. KHÔNG bịa dữ kiện riêng của công trình trong ảnh (kích thước, mác bê tông, số thép,
  vật liệu, diện tích, thời gian) khi ảnh và mô tả không cho biết; không trích số hiệu tiêu chuẩn, quy định nếu không chắc
  chắn; không hứa giá, bảo hành, tiến độ.
- KHÔNG nêu tên chủ nhà, địa chỉ, số nhà, tên đường, mã công trình nội bộ, số điện thoại cá nhân, giá tiền.
- Chỉ viết cho đúng Trang này: đúng thương hiệu, dịch vụ, cách xưng hô, câu kêu gọi, hashtag trong giới thiệu và bài mẫu
  của Trang. Không nhắc tên, phong cách, khẩu hiệu hay hashtag của thương hiệu/Trang khác.

ẢNH (đánh số từ 1 như người duyệt thấy)
- CHẤM TỪNG ẢNH (scores, mọi ảnh được đưa) từ 1 đến 5 theo bộ tiêu chuẩn cố định:
  ánh sáng đủ, rõ nét (ảnh đã được tự cân sáng/màu nhẹ); bố cục thẳng, thấy rõ hạng mục, không cắt cụt; GỌN GÀNG (không rác,
  bao bì, vữa vương vãi, dụng cụ bừa bộn, dây điện rối, quần áo phơi); thấy rõ thành quả thi công.
  5 = đăng được ngay; 4 = tốt; 3 = tạm được; 2 = không nên đăng; 1 = hỏng. Ảnh có mặt người nhìn rõ, giấy tờ, màn hình,
  biển số nhà, biển số xe: tối đa 1 điểm. "issue": lỗi chính của ảnh (để trống nếu không có).
- photos: chọn 1–10 ảnh từ 3 điểm trở lên, đúng chủ đề; ảnh đẹp nhất (tổng thể) đứng đầu. Không có ảnh: scores và photos
  là danh sách rỗng.
- "note": một câu cho người duyệt: dạng bài đã chọn, vì sao chọn các ảnh này, ảnh nào đã bỏ (nếu có)."""
PLACEHOLDER = "[CHỮ TẠM – CẦN VIẾT LẠI]"        # bài AI chưa viết được: không cho duyệt khi còn dòng này (services.approve)

SCHEMA = {"type": "json_schema", "schema": {
    "type": "object", "additionalProperties": False, "required": ["scores", "photos", "text", "note"],
    "properties": {"scores": {"type": "array", "description": "Điểm từng ảnh được đưa (1–5) theo bộ tiêu chuẩn; photo = số ảnh, từ 1",
                              "items": {"type": "object", "additionalProperties": False, "required": ["photo", "score", "issue"],
                                        "properties": {"photo": {"type": "integer"}, "score": {"type": "integer"},
                                                       "issue": {"type": "string"}}}},
                   "photos": {"type": "array", "items": {"type": "integer"},
                              "description": "Số ảnh được chọn (từ 1), theo thứ tự đăng (ảnh đầu hiện to nhất)"},
                   "text": {"type": "string", "description": "Nội dung bài đăng hoàn chỉnh"},
                   "note": {"type": "string", "description": "Ghi chú ngắn cho người duyệt"}}}}


def used_ids():
    """Ảnh đã nằm trong bài đã đăng hoặc đang chờ (không dùng lại)."""
    return set(Asset.objects.filter(uses__post__state__in=[s for s in Post.State.values if s != Post.State.REJECTED])
               .values_list("pk", flat=True))


def usable():
    """Ảnh được phép đề xuất: chưa loại, không hỏng kỹ thuật, AI chưa chấm dưới chuẩn, chưa bị người duyệt đổi đi nhiều lần."""
    return (Asset.objects.filter(blocked=False, broken=False, passed_over__lt=MAX_PASSED)
            .filter(Q(ai_score__isnull=True) | Q(ai_score__gte=MIN_SCORE)))


def candidates(project=None, exclude=(), limit=CANDIDATES):
    """Ảnh mới nhất đạt chuẩn (ảnh chưa đo thì đo ngay; hỏng thì bỏ qua). Ảnh từng bị đổi xếp sau."""
    rows = usable().exclude(pk__in=set(exclude) | used_ids()).select_related("project")
    if project is not None:
        rows = rows.filter(project=project)
    picked = []
    for asset in rows.order_by("passed_over", "-taken_on", "-pk")[: limit * 5]:
        if not asset.metrics:
            asset = asset_files.measure_asset(asset)
        if asset.broken:
            continue
        picked.append(asset)
        if len(picked) >= limit:
            break
    return picked


def next_candidate(project=None, exclude=()):
    found = candidates(project, exclude, 1) or candidates(None, exclude, 1)
    return found[0] if found else None


def pick_project(today):
    """Công trình có nhiều ảnh mới chưa dùng nhất trong 3 tuần gần đây (luân phiên tự nhiên giữa các công trình)."""
    since = today - timedelta(days=RECENT_DAYS)
    rows = (usable().filter(project__isnull=False, taken_on__gte=since).exclude(pk__in=used_ids())
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
    parts = [f"Bạn là biên tập viên nội dung, am hiểu kỹ thuật xây dựng và hoàn thiện nhà, của Trang Facebook \"{name}\". "
             "Bạn soạn bài đăng Facebook (từ ảnh công trình thật nếu có) để người duyệt xem trước khi đăng."]
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
    """Ảnh cho AI xem: bản ĐÃ tự chỉnh (đúng như sẽ đăng), kèm mô tả việc và lỗi kỹ thuật đo được."""
    blocks = []
    for index, asset in enumerate(items):
        data = asset_files.thumbnail(asset, 768, 78, enhanced=True)
        if data is None:
            continue
        when = f", ngày {asset.taken_on:%d/%m/%Y}" if asset.taken_on else ""
        flaws = f"; đo kỹ thuật: {asset.flaws}" if asset.flaws else ""
        blocks.append({"type": "text", "text": f"Ảnh số {index + 1}: {asset.context or 'không có mô tả'}{when}{flaws}"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(data).decode()}})
    return blocks


def template_text(items, brief="", channel=None):
    """AI chưa viết được: CHỮ TẠM ghi rõ (không chép yêu cầu thành bài, không giả là bài đã viết); không duyệt được tới khi sửa."""
    works = list(dict.fromkeys(a.context.split(" · ")[0] for a in items if a.context and not a.context.startswith("ảnh chung")))
    lines = [PLACEHOLDER]
    if brief:
        lines.append(f"Chủ đề được giao: {brief}")
    if works:
        lines.append("Ảnh: " + ", ".join(works[:3]).lower())
    lines.append("Bấm ✍ Sửa chữ để viết bài, hoặc trả lời tin này để nhờ trợ lý viết lại.")
    return "\n".join(lines)


def why_no_ai():
    """Vì sao trợ lý nội dung không gọi được AI (để ghi chú cho người duyệt, không giả là đã viết)."""
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "chưa bật trợ lý AI (máy chủ chưa có khoá Claude)"
    if ai.month_cost(purpose=ai.CONTENT) >= ai.cap(ai.CONTENT):
        return f"đã dùng hết trần {ai.cap(ai.CONTENT):g} USD tháng này cho bài đăng"
    return "chưa bật trợ lý AI"


def _record_scores(items, scores):
    from core.audit import record_save
    for index, score in scores.items():
        asset = items[index]
        if asset.ai_score != score:
            asset.ai_score = score
            record_save(asset, None, f"AI chấm ảnh {score}/5", channel="tu_dong")


def write(items, brief="", channel=None, pinned=()):
    """→ {"text", "assets": [Asset theo thứ tự], "note", "ai": bool}. AI lỗi hoặc chưa bật: chữ mẫu, giữ tối đa 6 ảnh.
    `channel`: Trang sẽ đăng — trợ lý viết theo giới thiệu và bài mẫu của Trang đó. `pinned`: ảnh người giao bài gửi kèm —
    luôn giữ (AI vẫn chấm điểm để báo người duyệt), ảnh kho thì AI chấm dưới MIN_SCORE bị loại."""
    items = list(items)
    pinned = {a.pk for a in pinned}
    system, known = system_prompt(channel)
    problem = ""
    if ai.available(ai.CONTENT):
        ask = ("Soạn một bài đăng từ các ảnh trên." if items else "Soạn một bài đăng (chưa có ảnh kèm).")
        if brief:
            ask += f"\n\nYÊU CẦU CỦA NGƯỜI GIAO BÀI (ý chính, khai thác sâu theo CÁCH VIẾT): {brief}"
        ask += "\n\nTrả về JSON theo khuôn."
        content = [*_image_blocks(items), {"type": "text", "text": ask}] if items else ask
        name = ai.model(ai.CONTENT)
        try:      # bài dài + suy nghĩ thích ứng: chạy nền (jobs), chờ tới 4 phút
            response = ai.create_message(model=name, max_tokens=16000, timeout=240.0,
                                         system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                         messages=[{"role": "user", "content": content}],
                                         output_config={"effort": "high", "format": SCHEMA})
            ai._record(response.usage, ai.CONTENT, name)
            if response.stop_reason == "refusal":
                raise ValueError("refusal")
            data = json.loads(next(b.text for b in response.content if b.type == "text"))
            scores = {}
            for row in data.get("scores") or []:
                number, score = row.get("photo"), row.get("score")
                if isinstance(number, int) and 1 <= number <= len(items) and isinstance(score, int):
                    scores[number - 1] = min(5, max(1, score))
            _record_scores(items, scores)
            wanted = [n - 1 for n in dict.fromkeys(data.get("photos") or []) if isinstance(n, int) and 1 <= n <= len(items)]
            chosen = [items[i] for i in wanted if items[i].pk in pinned or scores.get(i, MIN_SCORE) >= MIN_SCORE]
            dropped = [i + 1 for i in wanted if items[i] not in chosen]
            text = str(data.get("text") or "").strip()
            if text:
                note = str(data.get("note") or "")
                if dropped:
                    note += f" Đã bỏ ảnh AI chọn nhưng chấm dưới {MIN_SCORE} điểm."
                weak = [f"ảnh {n + 1} ({scores.get(items.index(a), '?')} điểm)" for n, a in enumerate(chosen)
                        if a.pk in pinned and scores.get(items.index(a), MIN_SCORE) < MIN_SCORE]
                if weak:
                    note += " ⚠ Ảnh người giao bài gửi bị chấm thấp: " + ", ".join(weak) + "."
                if not chosen:
                    note += " Bài chưa có ảnh: trả lời (reply) tin BÀI NHÁP bằng ảnh để thêm ảnh."
                return {"text": text, "assets": chosen[:10], "note": (note + _voice_note(channel, known)).strip()[:1000],
                        "ai": True}
            problem = "AI trả về bài trống"
        except Exception as error:      # mạng/khoá/khuôn lỗi: vẫn có bài nháp (chữ tạm) để người duyệt xử lý
            log.warning("AI soạn bài lỗi (%s)", type(error).__name__)
            problem = "AI từ chối viết chủ đề này" if str(error) == "refusal" else f"AI lỗi ({type(error).__name__})"
    note = f"⚠ AI chưa viết được bài này ({problem or why_no_ai()}): đây là CHỮ TẠM, không duyệt được tới khi sửa chữ."
    if not items:
        note += " Bài chưa có ảnh."
    return {"text": template_text(items[:6], brief, channel), "assets": items[:6], "note": note + _voice_note(channel, known),
            "ai": False}


def draft(today, project=None, idea=None, brief="", origin=Post.Origin.DAILY, user=None, items=None, channel=None):
    """Lập một bài nháp cho MỘT Trang (`channel`, mặc định primary_channel). `items`: ảnh người giao bài gửi kèm (ưu tiên),
    không thì lấy từ kho theo công trình."""
    from .services import create_post
    channel = channel or primary_channel()
    project = project or (idea.project if idea else None)
    pinned = list(items or [])
    pool = list(pinned)
    if len(pool) < 3:
        pool += [a for a in candidates(project or (None if pool else pick_project(today)), [a.pk for a in pool])
                 if a not in pool][: CANDIDATES - len(pool)]
    if not pool and not brief and idea is None:
        return None
    result = write(pool, brief or (idea.text if idea else ""), channel, pinned)
    chosen_project = project or next((a.project for a in result["assets"] if a.project_id), None)
    note = result["note"]
    good = [a for a in result["assets"] if not a.broken]
    if chosen_project is not None and len(good) < ENOUGH:
        reminded = photo_tip(chosen_project)
        note += (f" ⚠ Công trình {chosen_project.code} chỉ có {len(good)} ảnh đạt chuẩn để đăng"
                 + ("; trợ lý đã nhắc giám sát chụp thêm." if reminded else "."))
    return create_post(user, result["text"], result["assets"], today, project=chosen_project, origin=origin, idea=idea,
                       note=note.strip(), ai_written=result["ai"], channels=[channel] if channel else None)


TIP = ("📸 Ảnh công trình {code} dùng đăng Facebook: gần đây ít ảnh đủ đẹp{why}. Lần chụp tới nhờ anh/chị:\n"
       "• chụp ban ngày hoặc bật đủ đèn, tránh chụp ngược sáng cửa sổ;\n"
       "• cầm chắc máy, chạm màn hình vào chỗ cần nét, lau ống kính;\n"
       "• dọn gọn rác, bao bì, dụng cụ trước khi chụp;\n"
       "• chụp một ảnh toàn cảnh thẳng góc + vài ảnh cận phần đã hoàn thiện;\n"
       "• gửi ảnh dạng Tệp để giữ độ nét.")


def photo_tip(project, now=None):
    """Nhắc giám sát công trình chụp ảnh đẹp hơn: mỗi người mỗi công trình tối đa 1 lần/tuần, trong giờ nhận tin.
    → có gửi cho ai không."""
    from assistant import schedule
    now = now or timezone.now()
    local = timezone.localtime(now)
    year, week, _ = local.isocalendar()
    recent = Asset.objects.filter(project=project, taken_on__gte=local.date() - timedelta(days=RECENT_DAYS)).exclude(flaws="")
    counts = {}
    for text in recent.values_list("flaws", flat=True):
        for name in text.split(","):
            if name.strip() in quality.HARD or name.strip() in ("hơi tối", "lệch màu"):
                counts[name.strip()] = counts.get(name.strip(), 0) + 1
    common = [name for name, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:2]]
    why = f" (nhiều ảnh {', '.join(common)})" if common else ""
    sent = False
    for user in schedule.supervisors(project):
        if schedule.awake(user, local):
            sent = schedule.send(f"mkt-photo-tip:{project.pk}:{year}-{week}:{user.pk}", user,
                                 TIP.format(code=project.code, why=why), now=now) or sent
    return sent


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
    """Người duyệt nhắn "AI: ngắn hơn…" / trả lời bản nháp → chữ mới. None nếu chưa bật AI hoặc lỗi (lý do: why_no_ai)."""
    if not ai.available(ai.CONTENT) or not instruction:
        return None
    system, _ = system_prompt(post.channels.order_by("name").first() or primary_channel(), 4)
    current = post.text if PLACEHOLDER not in post.text else "(chưa có bài: viết mới theo yêu cầu)\n" + post.text
    ask = (f"Bài hiện tại:\n{current}\n\nYÊU CẦU SỬA CỦA NGƯỜI DUYỆT: {instruction}\n\n"
           "Làm đúng yêu cầu. Yêu cầu thêm ý, viết sâu hơn thì khai thác bằng kiến thức chuyên môn theo CÁCH VIẾT. Người duyệt "
           "quyết định nội dung: họ yêu cầu nêu tên khu đô thị, dự án thì được nêu; vẫn không nêu tên chủ nhà, số nhà, số điện "
           "thoại, giá. Trả về JSON với bài đã sửa hoàn chỉnh.")
    name = ai.model(ai.CONTENT)
    try:
        response = ai.create_message(model=name, max_tokens=8000, timeout=180.0,
                                     system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                                     messages=[{"role": "user", "content": ask}],
                                     output_config={"effort": "medium", "format": REVISE})
        ai._record(response.usage, ai.CONTENT, name)
        if response.stop_reason == "refusal":
            return None
        text = str(json.loads(next(b.text for b in response.content if b.type == "text")).get("text") or "").strip()
        return text or None
    except Exception as error:
        log.warning("AI sửa bài lỗi (%s)", type(error).__name__)
        return None
