"""Đăng bài đã duyệt lên Trang Facebook, có sổ từng bước (`PublishStep`). Chạy bởi `manage.py dang_bai` (hẹn giờ 2 phút).

Từng kênh của bài (PostTarget):
1. Mỗi ảnh: ghi "sắp tải ảnh i" → tải ảnh chưa đăng → ghi "đã tải ảnh i" (mã ảnh).
2. Ghi "sắp tạo bài" → POST /feed với các mã ảnh → ghi "đã tạo bài" (mã bài) → đọc lại lấy đường dẫn.
Lỗi ở bước 1 hoặc lỗi chắc chắn chưa tới Facebook ở bước 2 → "lỗi trước khi tạo bài": người duyệt bấm Thử lại.
Lỗi có thể Facebook đã tạo bài (hết giờ chờ sau khi gửi) → "cần đối soát": KHÔNG tự đăng lại; đọc bài mới của Trang để tìm,
không thấy thì hỏi người duyệt. Bài chỉ đăng khi dấu bản duyệt khớp dấu bản hiện tại.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.audit import record_save

from . import assets as asset_files
from . import facebook
from .models import Post, PostTarget, PublishStep

log = logging.getLogger(__name__)
STALE = timedelta(minutes=15)
RECONCILE_WAIT = timedelta(minutes=10)


def _step(target, step, ordinal=None, remote_id="", detail=""):
    actor = target.post.approved_by
    return record_save(PublishStep(target=target, step=step, ordinal=ordinal, remote_id=str(remote_id)[:80], detail=str(detail)[:500]),
                       actor, f"Sổ đăng: {PublishStep.Step(step).label}", channel="tu_dong")


def _set_target(target, actor, reason, **fields):
    for key, value in fields.items():
        setattr(target, key, value)
    return record_save(target, actor, reason, channel="tu_dong")


def _finish_post(post):
    """Cập nhật trạng thái bài theo các kênh của bản đã duyệt."""
    post = Post.objects.get(pk=post.pk)
    targets = list(post.targets.filter(package_sha=post.approved_sha))
    latest = {}
    for t in sorted(targets, key=lambda t: t.attempt):
        latest[t.channel_id] = t
    states = {t.state for t in latest.values()}
    if not states or post.state == Post.State.REJECTED:
        return post
    if states == {PostTarget.State.PUBLISHED}:
        new = Post.State.PUBLISHED
    elif PostTarget.State.UNKNOWN in states:
        new = Post.State.UNKNOWN
    elif PostTarget.State.PENDING in states:
        new = Post.State.APPROVED
    else:
        new = Post.State.FAILED
    if new != post.state:
        post.state = new
        record_save(post, post.approved_by, f"Bài: {post.get_state_display().lower()}", channel="tu_dong")
    return post


def publish_target(target):
    """Đăng một bài lên một Trang theo sổ. Trả trạng thái mới của PostTarget."""
    post, channel = target.post, target.channel
    actor = post.approved_by
    items = list(post.items.select_related("asset").order_by("order"))
    try:
        token, page_id = facebook.page_token(channel)
    except facebook.FacebookError as error:
        _step(target, PublishStep.Step.ERROR, detail=f"Khoá Trang: {error}")
        return _set_target(target, actor, "Lỗi trước khi tạo bài", state=PostTarget.State.FAILED, error=str(error)[:500]).state
    photo_ids = []
    for index, item in enumerate(items):
        _step(target, PublishStep.Step.UPLOAD_INTENT, index)
        data = asset_files.for_publish(item.asset, enhanced=not item.original)     # đúng bản người duyệt đã xem
        try:
            if data is None:
                raise facebook.FacebookError(f"Ảnh {index + 1} không đọc được trên máy chủ.")
            photo = facebook.upload_photo(page_id, token, f"tend-{post.pk}-{index + 1}.jpg", data)
        except facebook.FacebookError as error:        # ảnh chưa đăng tự hết hạn; chưa có bài nào: thử lại an toàn
            _step(target, PublishStep.Step.ERROR, index, detail=str(error))
            return _set_target(target, actor, "Lỗi tải ảnh, chưa tạo bài", state=PostTarget.State.FAILED,
                               error=f"Ảnh {index + 1}: {error}"[:500]).state
        _step(target, PublishStep.Step.UPLOAD_DONE, index, photo)
        photo_ids.append(photo)
    _step(target, PublishStep.Step.FEED_INTENT, detail=f"{len(photo_ids)} ảnh")
    try:
        post_id = facebook.create_post(page_id, token, post.text, photo_ids)
    except facebook.FacebookError as error:
        _step(target, PublishStep.Step.ERROR, detail=str(error))
        if error.sent:
            return _set_target(target, actor, "Có thể Facebook đã tạo bài: cần đối soát, không tự đăng lại",
                               state=PostTarget.State.UNKNOWN, error=str(error)[:500]).state
        return _set_target(target, actor, "Lỗi trước khi tạo bài", state=PostTarget.State.FAILED, error=str(error)[:500]).state
    _step(target, PublishStep.Step.FEED_DONE, remote_id=post_id)
    link = f"https://www.facebook.com/{post_id}"
    try:
        link = facebook.read_post(post_id, token).get("permalink_url") or link
        _step(target, PublishStep.Step.VERIFY, remote_id=post_id, detail="Đọc lại bài được")
    except facebook.FacebookError as error:   # bài đã có mã: vẫn là đã đăng, chỉ chưa đọc lại được
        _step(target, PublishStep.Step.VERIFY, remote_id=post_id, detail=f"Chưa đọc lại được: {error}")
    return _set_target(target, actor, f"Đã đăng lên {channel.name}", state=PostTarget.State.PUBLISHED, remote_id=post_id,
                       permalink=link[:500], error="", published_at=timezone.now()).state


def _claim():
    """Nhận các bài đã duyệt (dấu khớp) để đăng; hai lần chạy cùng lúc không nhận trùng."""
    with transaction.atomic():
        rows = list(Post.objects.select_for_update(skip_locked=True).filter(state=Post.State.APPROVED))
        claimed = []
        for post in rows:
            if not post.approved_sha or post.approved_sha != post.package_sha:
                post.state = Post.State.DRAFT
                record_save(post, post.approved_by, "Bản đã đổi sau khi duyệt: chờ duyệt lại", channel="tu_dong")
                continue
            post.state = Post.State.PUBLISHING
            record_save(post, post.approved_by, "Bắt đầu đăng", channel="tu_dong")
            claimed.append(post)
        return claimed


def recover(now=None):
    """Bài kẹt "đang đăng" quá 15 phút (máy chủ khởi động lại giữa chừng): chưa gửi lệnh tạo bài thì đăng tiếp được;
    đã gửi mà chưa có kết quả thì cần đối soát."""
    now = now or timezone.now()
    for post in Post.objects.filter(state=Post.State.PUBLISHING):
        last = post.targets.filter(package_sha=post.approved_sha).order_by("-pk").first()
        stamp = max([s.at for t in post.targets.all() for s in t.steps.all()] or [post.approved_at or now])
        if now - stamp < STALE:
            continue
        for target in post.targets.filter(package_sha=post.approved_sha, state=PostTarget.State.PENDING):
            steps = set(target.steps.values_list("step", flat=True))
            if PublishStep.Step.FEED_INTENT in steps and PublishStep.Step.FEED_DONE not in steps:
                _set_target(target, post.approved_by, "Khởi động lại giữa lúc tạo bài: cần đối soát", state=PostTarget.State.UNKNOWN,
                            error="Máy chủ dừng giữa lúc tạo bài")
        if last is not None:
            with transaction.atomic():
                post = Post.objects.select_for_update().get(pk=post.pk)
                if post.state == Post.State.PUBLISHING:
                    post.state = Post.State.APPROVED
                    record_save(post, post.approved_by, "Đăng tiếp sau khi máy chủ khởi động lại", channel="tu_dong")
            _finish_post(post)


def reconcile(target, now=None):
    """Tìm bài có thể đã tạo: bài mới của Trang từ lúc "sắp tạo bài", cùng nội dung. Thấy → đã đăng; không thấy → giữ đối soát."""
    now = now or timezone.now()
    intent = target.steps.filter(step=PublishStep.Step.FEED_INTENT).order_by("-pk").first()
    if intent is None:
        return target.state
    try:
        token, page_id = facebook.page_token(target.channel)
        rows = facebook.recent_posts(page_id, token, (intent.at - timedelta(minutes=2)).timestamp())
    except facebook.FacebookError as error:
        _step(target, PublishStep.Step.RECONCILE, detail=f"Chưa đọc được Trang: {error}")
        return target.state
    text = " ".join(target.post.text.split())
    found = [r for r in rows if " ".join(str(r.get("message") or "").split()) == text]
    if len(found) == 1:
        _step(target, PublishStep.Step.RECONCILE, remote_id=found[0]["id"], detail="Thấy đúng 1 bài cùng nội dung")
        return _set_target(target, target.post.approved_by, "Đối soát: bài đã có trên Trang", state=PostTarget.State.PUBLISHED,
                           remote_id=found[0]["id"], permalink=(found[0].get("permalink_url") or "")[:500], error="",
                           published_at=timezone.now()).state
    _step(target, PublishStep.Step.RECONCILE, detail=f"Thấy {len(found)} bài cùng nội dung")
    return target.state


def run(now=None):
    """Một lượt: khôi phục bài kẹt, đối soát, đăng bài đã duyệt. Trả [(bài, kênh, trạng thái)] để báo người duyệt."""
    now = now or timezone.now()
    recover(now)
    results = []
    for target in PostTarget.objects.filter(state=PostTarget.State.UNKNOWN).select_related("post", "channel"):
        before = target.state
        if reconcile(target, now) != before:
            results.append((target.post, target.channel, PostTarget.State.PUBLISHED))
            _finish_post(target.post)
    for post in _claim():
        for target in post.targets.filter(package_sha=post.approved_sha, state=PostTarget.State.PENDING).select_related("channel"):
            target.post = post
            results.append((post, target.channel, publish_target(target)))
        _finish_post(post)
    return results


def retry(user, post, via="telegram"):
    """Người duyệt bấm Thử lại sau "lỗi trước khi tạo bài": tạo lượt đăng mới cho các kênh lỗi (bản duyệt vẫn đúng)."""
    from .services import _can
    _can(user)
    with transaction.atomic():
        post = Post.objects.select_for_update().get(pk=post.pk)
        if post.state != Post.State.FAILED or post.approved_sha != post.package_sha:
            raise ValueError("Bài này không ở trạng thái thử lại được.")
        failed = [t for t in post.targets.filter(package_sha=post.approved_sha, state=PostTarget.State.FAILED)]
        latest = {}
        for t in sorted(post.targets.filter(package_sha=post.approved_sha), key=lambda t: t.attempt):
            latest[t.channel_id] = t
        for target in failed:
            if latest[target.channel_id].pk != target.pk:
                continue
            record_save(PostTarget(post=post, channel=target.channel, package_sha=post.approved_sha, attempt=target.attempt + 1),
                        user, f"Thử đăng lại lên {target.channel.name}", channel=via)
        post.state = Post.State.APPROVED
        return record_save(post, user, "Thử đăng lại", channel=via)


def resolve(user, target, published, via="telegram"):
    """Người duyệt tự kiểm tra Trang sau khi đối soát không chắc: có bài rồi → đã đăng; chưa có → lỗi (thử lại được)."""
    from .services import _can
    _can(user)
    with transaction.atomic():
        target = PostTarget.objects.select_for_update().select_related("post", "channel").get(pk=target.pk)
        if target.state != PostTarget.State.UNKNOWN:
            raise ValueError("Kênh này không chờ đối soát.")
        _step(target, PublishStep.Step.RECONCILE, detail=f"{user} xác nhận: " + ("đã có trên Trang" if published else "chưa có"))
        if published:
            _set_target(target, user, "Người duyệt xác nhận bài đã có trên Trang", state=PostTarget.State.PUBLISHED,
                        published_at=timezone.now(), error="")
        else:
            _set_target(target, user, "Người duyệt xác nhận chưa có bài: cho thử lại", state=PostTarget.State.FAILED,
                        error="Chưa có trên Trang (người duyệt kiểm)")
    return _finish_post(target.post)
