"""Đường ghi duy nhất của trợ lý nội dung: mỗi hàm một giao dịch, nhật ký từng thay đổi, kiểm quyền ở đây.

Quyền: Giám đốc và vai Marketing (`User.does_marketing`) lập/sửa/duyệt/bỏ bài, giao ý tưởng; chỉ Giám đốc thêm/sửa kênh đăng.
Duyệt gắn với `package_sha` của đúng bản người duyệt đã xem (chữ + ảnh có thứ tự + kênh): bản đổi thì duyệt cũ hết hiệu lực.
"""
import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import record_save

from .models import Asset, Channel, Idea, Post, PostAsset, PostTarget, StyleSample

MAX_PHOTOS = 10          # bằng album Telegram; Facebook chưa thử nhiều hơn (bot cũ để Giám đốc quyết, giữ 10)
EDITABLE = (Post.State.DRAFT, Post.State.FAILED)


def _can(user):
    if not (user and user.is_active and user.does_marketing):
        raise PermissionDenied("Chỉ Giám đốc và nhân viên Marketing làm việc với bài đăng.")


def _clean(text, size):
    return "\n".join(" ".join(line.split()) for line in str(text or "").strip().splitlines()).strip()[:size]


def package_sha(post):
    """Dấu của bản bài: chữ + ảnh (sha từng ảnh, đúng thứ tự) + kênh + ảnh nào tự chỉnh (và phiên bản cách chỉnh). Bất kỳ
    thay đổi nào → dấu mới → phải duyệt lại. Bài cũ toàn ảnh gốc giữ đúng dấu như trước khi có phần tự chỉnh."""
    from . import enhance
    items = list(post.items.order_by("order").values_list("asset__sha256", "original"))
    data = {"text": post.text, "assets": [sha for sha, _ in items], "channels": sorted(post.channels.values_list("pk", flat=True))}
    enhanced = [index for index, (_, original) in enumerate(items) if not original]
    if enhanced:
        data["enhanced"] = {"items": enhanced, "v": enhance.VERSION}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _refresh(post, user, reason, channel):
    post.package_sha = package_sha(post)
    return record_save(post, user, reason, channel=channel)


def _locked(post):
    return Post.objects.select_for_update().get(pk=post.pk)


def _editable(post):
    if post.state not in EDITABLE:
        raise ValidationError(f"Bài #{post.pk} đang ở trạng thái \"{post.get_state_display().lower()}\", không sửa được.")


def _set_assets(post, assets):
    """Ghi lại danh sách ảnh theo thứ tự; ảnh còn trong bài giữ lựa chọn ảnh gốc/ảnh chỉnh, ảnh mới mặc định tự chỉnh."""
    keep = dict(post.items.values_list("asset_id", "original"))
    post.items.all().delete()          # bảng nối của bài nháp; nhật ký giữ ở dấu package_sha của bài
    for order, asset in enumerate(assets):
        PostAsset.objects.create(post=post, asset=asset, order=order, original=keep.get(asset.pk, False))


@transaction.atomic
def create_post(user, text, assets, for_date, project=None, origin=Post.Origin.DAILY, idea=None, note="", ai_written=False,
                channels=None, channel="telegram"):
    """`user` None = trợ lý tự soạn hằng ngày (không cần quyền người)."""
    if user is not None:
        _can(user)
    text = _clean(text, 5000)
    if not text:
        raise ValidationError("Bài chưa có chữ.")
    assets = list(dict.fromkeys(assets))[:MAX_PHOTOS]
    post = record_save(Post(text=text, project=project, origin=origin, idea=idea, for_date=for_date, note=_clean(note, 1000),
                            ai_written=ai_written, created_by=user), user, "Lập bài nháp", channel=channel)
    _set_assets(post, assets)
    post.channels.set(channels if channels is not None else Channel.objects.filter(active=True, default=True))
    if idea is not None and idea.state == Idea.State.OPEN:
        idea.state = Idea.State.USED
        record_save(idea, user, f"Ý tưởng thành bài #{post.pk}", channel=channel)
    return _refresh(post, user, "Dấu bản bài nháp", channel)


@transaction.atomic
def set_text(user, post, text, channel="telegram"):
    _can(user)
    post = _locked(post)
    _editable(post)
    text = _clean(text, 5000)
    if not text:
        raise ValidationError("Chữ của bài không được trống.")
    post.text, post.state = text, Post.State.DRAFT
    return _refresh(post, user, "Sửa chữ bài", channel)


def assets_of(post):
    return [item.asset for item in post.items.select_related("asset").order_by("order")]


@transaction.atomic
def swap_asset(user, post, index, replacement=None, block=False, reason="", channel="telegram"):
    """Đổi ảnh số `index` (0 là ảnh đầu) bằng `replacement` hoặc ảnh phù hợp kế tiếp; `block` = ảnh cũ không dùng nữa."""
    from .writer import next_candidate
    _can(user)
    post = _locked(post)
    _editable(post)
    current = assets_of(post)
    if not 0 <= index < len(current):
        raise ValidationError("Không có ảnh số đó trong bài.")
    old = current[index]
    if block:
        block_asset(user, old, reason or f"Người duyệt loại khỏi bài #{post.pk}", channel)
    else:                              # ảnh người duyệt đổi đi: xếp sau khi chọn lần sau, đổi 2 lần thì thôi đề xuất
        old = Asset.objects.select_for_update().get(pk=old.pk)
        old.passed_over += 1
        record_save(old, user, f"Người duyệt đổi ảnh này khỏi bài #{post.pk}", channel=channel)
    new = replacement or next_candidate(post.project, exclude=[a.pk for a in current])
    if new is None:
        raise ValidationError("Kho không còn ảnh phù hợp để đổi. Gửi thêm ảnh cho trợ lý hoặc bớt ảnh này.")
    current[index] = new
    _set_assets(post, current)
    post.state = Post.State.DRAFT
    return _refresh(post, user, f"Đổi ảnh {index + 1}" + (" (ảnh cũ không dùng nữa)" if block else ""), channel)


@transaction.atomic
def remove_asset(user, post, index, channel="telegram"):
    _can(user)
    post = _locked(post)
    _editable(post)
    current = assets_of(post)
    if not 0 <= index < len(current):
        raise ValidationError("Không có ảnh số đó trong bài.")
    del current[index]
    _set_assets(post, current)
    post.state = Post.State.DRAFT
    return _refresh(post, user, f"Bớt ảnh {index + 1}", channel)


@transaction.atomic
def toggle_original(user, post, index, channel="telegram"):
    """Ảnh số `index`: dùng ảnh gốc ↔ dùng bản tự chỉnh (sáng, màu, nét). Bản mới → phải duyệt lại."""
    _can(user)
    post = _locked(post)
    _editable(post)
    items = list(post.items.order_by("order"))
    if not 0 <= index < len(items):
        raise ValidationError("Không có ảnh số đó trong bài.")
    item = items[index]
    item.original = not item.original
    item.save(update_fields=["original"])
    post.state = Post.State.DRAFT
    return _refresh(post, user, f"Ảnh {index + 1}: " + ("dùng ảnh gốc" if item.original else "dùng bản tự chỉnh"), channel)


@transaction.atomic
def move_first(user, post, index, channel="telegram"):
    """Đưa ảnh số `index` lên đầu (ảnh đầu thường hiện to nhất trên Facebook)."""
    _can(user)
    post = _locked(post)
    _editable(post)
    current = assets_of(post)
    if not 0 <= index < len(current):
        raise ValidationError("Không có ảnh số đó trong bài.")
    current.insert(0, current.pop(index))
    _set_assets(post, current)
    post.state = Post.State.DRAFT
    return _refresh(post, user, f"Đưa ảnh {index + 1} lên đầu", channel)


@transaction.atomic
def toggle_channel(user, post, target, channel="telegram"):
    _can(user)
    post = _locked(post)
    _editable(post)
    if not target.active:
        raise ValidationError("Kênh này đang tắt.")
    if post.channels.filter(pk=target.pk).exists():
        post.channels.remove(target)
    else:
        post.channels.add(target)
    post.state = Post.State.DRAFT
    return _refresh(post, user, f"Đổi kênh đăng: {target.name}", channel)


@transaction.atomic
def approve(user, post, seen_sha, channel="telegram"):
    """Duyệt ĐÚNG bản đã xem. Bản đã đổi (dấu khác) thì từ chối: phải xem bản mới nhất."""
    _can(user)
    post = _locked(post)
    if post.state not in EDITABLE:
        raise ValidationError(f"Bài #{post.pk} {post.get_state_display().lower()}.")
    if not seen_sha or not post.package_sha.startswith(seen_sha):
        raise ValidationError(f"Bài #{post.pk} đã thay đổi sau bản anh/chị đang xem. Xem bản mới nhất rồi duyệt.")
    channels = list(post.channels.filter(active=True))
    if not channels:
        raise ValidationError("Chưa chọn Trang để đăng.")
    if post.items.count() > MAX_PHOTOS:
        raise ValidationError(f"Tối đa {MAX_PHOTOS} ảnh mỗi bài.")
    post.state, post.approved_sha, post.approved_by, post.approved_at = (Post.State.APPROVED, post.package_sha, user,
                                                                         timezone.now())
    record_save(post, user, "Duyệt bài (đúng bản đã xem)", channel=channel)
    for target in channels:
        attempt = PostTarget.objects.filter(post=post, channel=target, package_sha=post.package_sha).count() + 1
        if PostTarget.objects.filter(post=post, channel=target, package_sha=post.package_sha,
                                     state=PostTarget.State.PUBLISHED).exists():
            continue
        record_save(PostTarget(post=post, channel=target, package_sha=post.package_sha, attempt=attempt), user,
                    f"Chờ đăng lên {target.name}", channel=channel)
    return post


@transaction.atomic
def reject(user, post, reason="", channel="telegram"):
    _can(user)
    post = _locked(post)
    if post.state not in EDITABLE + (Post.State.APPROVED,):
        raise ValidationError(f"Bài #{post.pk} {post.get_state_display().lower()}, không bỏ được.")
    post.state, post.reject_reason = Post.State.REJECTED, _clean(reason, 500)
    PostTarget.objects.filter(post=post, state=PostTarget.State.PENDING).update(state=PostTarget.State.FAILED, error="Bài đã bỏ")
    return record_save(post, user, "Bỏ bài" + (f": {post.reject_reason}" if post.reject_reason else ""), channel=channel)


@transaction.atomic
def block_asset(user, asset, reason="", channel="telegram"):
    _can(user)
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    if not asset.blocked:
        asset.blocked, asset.block_reason = True, _clean(reason, 300)
        record_save(asset, user, "Ảnh không dùng cho bài đăng nữa", channel=channel)
    return asset


@transaction.atomic
def unblock_asset(user, asset, channel="web"):
    _can(user)
    asset = Asset.objects.select_for_update().get(pk=asset.pk)
    if asset.blocked:
        asset.blocked, asset.block_reason = False, ""
        record_save(asset, user, "Dùng lại ảnh cho bài đăng", channel=channel)
    return asset


@transaction.atomic
def add_idea(user, text, kind=Idea.Kind.IDEA, project=None, channel="telegram"):
    _can(user)
    text = _clean(text, 2000)
    if not text:
        raise ValidationError("Ghi nội dung ý tưởng, vd /ytuong Trước–sau phòng khách TN-22, nhấn phào chỉ")
    return record_save(Idea(kind=kind, text=text, project=project, created_by=user), user,
                       "Giao bài" if kind == Idea.Kind.REQUEST else "Thêm ý tưởng nội dung", channel=channel)


@transaction.atomic
def drop_idea(user, idea, channel="web"):
    _can(user)
    idea = Idea.objects.select_for_update().get(pk=idea.pk)
    if idea.state == Idea.State.OPEN:
        idea.state = Idea.State.DROPPED
        record_save(idea, user, "Bỏ ý tưởng", channel=channel)
    return idea


# ---------- kênh đăng (chỉ Giám đốc) ----------

def _director(user):
    if not (user and user.is_active and user.is_director):
        raise PermissionDenied("Chỉ Giám đốc thêm hoặc sửa Trang đăng bài.")


@transaction.atomic
def save_channel(user, channel=None, *, name, page, account="", default=True, active=True, via="web"):
    _director(user)
    page = page.strip().rstrip("/").rsplit("/", 1)[-1].split("?")[0]       # nhận cả đường dẫn facebook.com/tendct
    name, account = _clean(name, 100), "".join(ch for ch in account.upper() if ch.isalnum() or ch == "_")[:30]
    if not name or not page:
        raise ValidationError("Ghi tên hiển thị và mã Trang (vd tendct).")
    row = Channel.objects.select_for_update().get(pk=channel.pk) if channel else Channel(created_by=user)
    if row.page != page or row.account != account:
        row.page_id, row.checked_at, row.check_note = "", None, ""
    row.name, row.page, row.account, row.default, row.active = name, page, account, default, active
    return record_save(row, user, "Sửa Trang đăng bài" if channel else "Thêm Trang đăng bài", channel=via)


@transaction.atomic
def set_brief(user, channel, brief, via="web"):
    """Giới thiệu Trang (thương hiệu, dịch vụ, xưng hô, kêu gọi, hashtag): Giám đốc và Marketing sửa; trợ lý viết theo đây."""
    _can(user)
    row = Channel.objects.select_for_update().get(pk=channel.pk)
    row.brief = _clean(brief, 2000)
    return record_save(row, user, "Sửa giới thiệu Trang", channel=via)


@transaction.atomic
def check_channel(user, channel, via="web"):
    from . import facebook
    _director(user)
    row = Channel.objects.select_for_update().get(pk=channel.pk)
    try:
        info = facebook.check(row)
    except facebook.FacebookError as error:
        row.check_note = f"✗ {error}"[:300]
    else:
        row.page_id = info["page_id"] or row.page_id
        row.check_note = f"✓ Kết nối được Trang \"{info['name']}\" (mã {row.page_id})"[:300]
    row.checked_at = timezone.now()
    return record_save(row, user, "Kiểm tra kết nối Trang", channel=via)


# ---------- giọng văn ----------

def _digest(text):
    return hashlib.sha256(" ".join(text.split()).casefold().encode()).hexdigest()


@transaction.atomic
def add_sample(user, text, source=StyleSample.Source.MANUAL, channel_row=None, remote_id="", via="web"):
    if user is not None:
        _can(user)
    text = _clean(text, 5000)
    if len(text) < 40:
        raise ValidationError("Bài mẫu quá ngắn (cần ít nhất vài câu).")
    if StyleSample.objects.filter(digest=_digest(text)).exists():
        return None
    return record_save(StyleSample(channel=channel_row, source=source, text=text, digest=_digest(text), remote_id=remote_id),
                       user, "Thêm bài mẫu giọng văn", channel=via)


def import_samples(user, channel_row, limit=30):
    """Đọc bài cũ của Trang làm mẫu giọng văn → số bài mới thêm."""
    from . import facebook
    _can(user)
    token, page_id = facebook.page_token(channel_row)
    added = 0
    for row in facebook.page_posts(page_id, token, limit):
        try:
            if add_sample(user, row["message"], StyleSample.Source.PAGE, channel_row, str(row.get("id") or "")):
                added += 1
        except ValidationError:
            continue
    return added


@transaction.atomic
def toggle_sample(user, sample, via="web"):
    _can(user)
    sample = StyleSample.objects.select_for_update().get(pk=sample.pk)
    sample.active = not sample.active
    return record_save(sample, user, "Dùng làm mẫu" if sample.active else "Thôi dùng làm mẫu", channel=via)
