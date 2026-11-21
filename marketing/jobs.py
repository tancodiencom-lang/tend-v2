"""Viết bài chạy nền. Mô hình viết bài (Sonnet 5.5, suy nghĩ thích ứng) mất 1–3 phút cho một bài dài: quá giờ chờ của máy
chủ web (90 giây) và của Telegram. Nên: bot/web ghi một WriteJob rồi trả lời ngay "trợ lý đang viết"; sau khi ghi xong
(on_commit) mở một tiến trình nền `manage.py viet_bai` làm hết việc đang chờ; hẹn giờ đăng bài (mỗi 2 phút) chạy bù việc
chưa ai làm và gỡ việc kẹt (tiến trình bị dừng khi triển khai lại). Mỗi việc chỉ một tiến trình nhận (select_for_update
skip_locked); chạy lại tối đa 2 lần rồi báo lỗi cho người yêu cầu.
"""
import logging
import subprocess
import sys
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from chat import telegram
from chat.models import ChatLink
from core.audit import record_save

from . import services, writer
from .models import Asset, Idea, Post, WriteJob

log = logging.getLogger(__name__)
STUCK = timedelta(minutes=10)
MAX_ATTEMPTS = 2
PREFIX = "📣 Nội dung: "


def request_draft(user, brief, project=None, channel=None, photos=(), via="telegram"):
    """Giao bài: ghi ý tưởng (giao bài ngay) + việc soạn bài; trợ lý viết nền rồi gửi bản duyệt cho người duyệt."""
    services._can(user)
    with transaction.atomic():
        idea = services.add_idea(user, brief or "Bài từ ảnh vừa gửi", Idea.Kind.REQUEST, project, channel=via)
        job = record_save(WriteJob(kind=WriteJob.Kind.DRAFT, idea=idea, project=project, channel=channel, instruction=brief,
                                   photos=[a.pk for a in photos], requested_by=user, via=via),
                          user, "Giao trợ lý viết bài", channel=via)
        kick()
    return job


def request_revise(user, post, instruction, via="telegram"):
    """Nhờ trợ lý sửa bài theo ý (trả lời bản nháp, "AI: …", nút trên web)."""
    services._can(user)
    instruction = (instruction or "").strip()
    if not instruction:
        raise ValidationError("Ghi rõ muốn sửa thế nào.")
    if post.state not in services.EDITABLE:
        raise ValidationError(f"Bài #{post.pk} đang ở trạng thái \"{post.get_state_display().lower()}\", không sửa được.")
    with transaction.atomic():
        job = record_save(WriteJob(kind=WriteJob.Kind.REVISE, post=post, instruction=instruction[:4000], requested_by=user, via=via),
                          user, f"Nhờ trợ lý sửa bài #{post.pk}", channel=via)
        kick()
    return job


def kick():
    """Sau khi ghi xong: mở tiến trình nền nếu chưa có tiến trình nào đang viết (bài kiểm thay bằng chạy ngay)."""
    transaction.on_commit(_spawn_if_idle)


def _spawn_if_idle():
    if WriteJob.objects.filter(state=WriteJob.State.RUNNING, started_at__gte=timezone.now() - STUCK).exists():
        return                                   # tiến trình đang chạy sẽ làm tiếp việc mới
    spawn()


def spawn():
    try:
        subprocess.Popen([sys.executable, str(settings.BASE_DIR / "manage.py"), "viet_bai"], cwd=str(settings.BASE_DIR),
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True, close_fds=True)
    except OSError as error:                     # hẹn giờ 2 phút sẽ chạy bù
        log.warning("Không mở được tiến trình viết bài (%s)", type(error).__name__)


def recover(now=None):
    """Việc "đang viết" quá 10 phút (tiến trình bị dừng): cho viết lại, quá số lần thì báo lỗi."""
    now = now or timezone.now()
    for job in WriteJob.objects.filter(state=WriteJob.State.RUNNING, started_at__lt=now - STUCK):
        if job.attempts < MAX_ATTEMPTS:
            job.state = WriteJob.State.WAITING
            record_save(job, None, "Việc viết bài bị gián đoạn: viết lại", channel="tu_dong")
        else:
            _fail(job, "trợ lý viết quá lâu hoặc bị gián đoạn nhiều lần")


def _claim(now):
    with transaction.atomic():
        job = (WriteJob.objects.select_for_update(skip_locked=True).filter(state=WriteJob.State.WAITING).order_by("pk").first())
        if job is None:
            return None
        job.state, job.started_at, job.attempts = WriteJob.State.RUNNING, now, job.attempts + 1
        job.save(update_fields=["state", "started_at", "attempts"])
        return job


def run_pending(limit=10):
    """Làm các việc đang chờ (tiến trình nền, hẹn giờ chạy bù). → danh sách việc đã làm."""
    recover()
    done = []
    while len(done) < limit:
        job = _claim(timezone.now())
        if job is None:
            break
        done.append(run(job))
    return done


def _tell(user, text):
    for row in ChatLink.objects.filter(user=user, active=True):
        telegram.send(row.chat_id, PREFIX + text)


def _fail(job, reason):
    job.state, job.error, job.finished_at = WriteJob.State.FAILED, reason[:500], timezone.now()
    record_save(job, None, "Trợ lý không viết được bài", channel="tu_dong")
    what = f"sửa bài #{job.post_id}" if job.kind == WriteJob.Kind.REVISE else f"viết bài \"{job.instruction[:60]}\""
    _tell(job.requested_by, f"✗ Trợ lý chưa {what}: {reason}. "
          + ("Bấm ✍ Sửa chữ để sửa trực tiếp." if job.kind == WriteJob.Kind.REVISE else "Giao lại sau hoặc viết tay trên trang Nội dung."))
    return job


def run(job):
    from . import bot
    try:
        if job.kind == WriteJob.Kind.DRAFT:
            order = {pk: i for i, pk in enumerate(job.photos or [])}
            photos = sorted(Asset.objects.filter(pk__in=order), key=lambda a: order[a.pk])
            post = writer.draft(timezone.localdate(), project=job.project, idea=job.idea, brief=job.instruction,
                                origin=Post.Origin.REQUEST, user=job.requested_by, items=photos, channel=job.channel)
            if post is None:
                return _fail(job, "kho chưa có ảnh phù hợp và chưa có chủ đề")
            job.post = post
            bot.send_preview(post)
        else:
            new_text = writer.revise(job.post, job.instruction)
            if new_text is None:
                return _fail(job, writer.why_no_ai() if not _ai_ready() else "AI lỗi hoặc từ chối yêu cầu này")
            post = services.set_text(job.requested_by, job.post, new_text, channel=job.via)
            bot.send_preview(post, list(ChatLink.objects.filter(user=job.requested_by, active=True)))
    except (PermissionDenied, ValidationError) as error:
        return _fail(job, "; ".join(getattr(error, "messages", [str(error)])))
    except Exception as error:                   # lỗi bất ngờ: ghi, báo người yêu cầu; không làm hỏng việc khác
        log.exception("Viết bài lỗi")
        return _fail(job, f"lỗi máy chủ ({type(error).__name__})")
    job.state, job.finished_at = WriteJob.State.DONE, timezone.now()
    return record_save(job, None, "Trợ lý viết xong", channel="tu_dong")


def _ai_ready():
    from chat import ai
    return ai.available(ai.CONTENT)


def recent(hours=24):
    """Việc viết gần đây chưa xong (hiện trên trang Nội dung)."""
    since = timezone.now() - timedelta(hours=hours)
    return (WriteJob.objects.filter(created_at__gte=since).exclude(state=WriteJob.State.DONE)
            .select_related("requested_by", "post")[:10])
