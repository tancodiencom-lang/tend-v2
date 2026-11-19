"""Trợ lý Giám đốc qua bot: giao việc (phiếu chờ Giám đốc xác nhận), nhận việc, báo xong, nhận/trả lại, huỷ, góp ý.

Đường ghi là `assistant.services` (giao dịch + nhật ký). Bot chỉ lập phiếu và chuyển tin; trợ lý không tự ghi gì.
"""
from datetime import datetime

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from assistant import parse as task_parse
from assistant import services as tasks
from assistant.models import Feedback, Task
from core.models import User
from projects.models import Project, projects_for

from . import telegram
from .models import ChatLink, Proposal

PREFIX = "🤖 Trợ lý: "
COMMANDS = ("/giao", "/viec", "/xong", "/huyviec", "/gopy")
HELP = ("• Trợ lý: /viec (việc được giao, Giám đốc xem tất cả), /xong 12 [ghi chú] (báo xong việc #12), /gopy <góp ý>. "
        "Giám đốc giao việc: \"/giao Nam gửi bản vẽ điện TN-22 hạn 20/11 9:00 - đủ ổ cắm, công tắc\" hoặc nói tự nhiên khi "
        "đã bật AI; /huyviec 12 lý do.")


def _tell(user, text, buttons=None):
    for row in ChatLink.objects.filter(user=user, active=True, user__is_active=True):
        telegram.send(row.chat_id, PREFIX + text, buttons)


def _tell_directors(text, buttons=None, exclude=None):
    for row in ChatLink.objects.filter(active=True, user__is_active=True).select_related("user"):
        if row.user.is_director and row.user != exclude:
            telegram.send(row.chat_id, PREFIX + text, buttons)


def people(user):
    return list(User.objects.filter(is_active=True).exclude(pk=user.pk))


# ---------- giao việc: phiếu chờ Giám đốc xác nhận ----------

def build(link_row, data, source_text):
    """data: {owner (User) | owner_name, title, due (datetime) | due_date + due_time, project_code, criteria}."""
    user = link_row.user
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc giao việc qua trợ lý. Cần giao việc thì nhắn Giám đốc, hoặc /gopy nếu là góp ý.")
    owner = data.get("owner")
    if owner is None:
        owner, _, candidates = task_parse.find_owner(str(data.get("owner_name") or "").split(), people(user))
        if owner is None:
            names = ", ".join(f"{u} (@{u.username})" for u in candidates[:6])
            raise ValidationError(f"Chưa rõ người nhận \"{data.get('owner_name')}\"" + (f": {names}." if names else "."))
    due = data.get("due")
    if due is None:
        try:
            day = datetime.strptime(str(data.get("due_date")), "%Y-%m-%d").date()
            hour, minute = (str(data.get("due_time") or "17:00").split(":") + ["0"])[:2]
            due = timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=int(hour), minute=int(minute))))
        except (TypeError, ValueError):
            raise ValidationError("Việc giao cần có hạn (ngày, giờ).")
    if due <= timezone.now():
        raise ValidationError(f"Hạn {timezone.localtime(due):%d/%m/%Y %H:%M} đã qua. Ghi hạn mới.")
    project = None
    if data.get("project_code"):
        project = Project.objects.filter(code__iexact=data["project_code"]).first()
        if project is None:
            raise ValidationError(f"Không có công trình {data['project_code']}.")
        if not projects_for(owner).filter(pk=project.pk).exists():
            raise ValidationError(f"{owner} không thuộc công trình {project.code}. Giao người khác hoặc thêm vào công trình trước.")
    title = " ".join(str(data.get("title") or "").split())
    if not title:
        raise ValidationError("Ghi rõ việc cần làm.")
    title = title[:1].upper() + title[1:]
    payload = {"owner_id": owner.pk, "owner": str(owner), "title": title[:300], "due": due.isoformat(),
               "criteria": " ".join(str(data.get("criteria") or "").split())[:500]}
    return Proposal.objects.create(link=link_row, project=project, kind=Proposal.Kind.TASK, payload=payload,
                                   source_text=source_text[:5000])


def summary(proposal):
    p = proposal.payload
    due = timezone.localtime(datetime.fromisoformat(p["due"]))
    return "\n".join(filter(None, [
        "VIỆC GIAO CHỜ ANH XÁC NHẬN",
        f"Người làm: {p['owner']}",
        f"Việc: {p['title']}",
        f"Công trình: {proposal.project.code}" if proposal.project_id else "",
        f"Hạn: {due:%d/%m/%Y %H:%M}",
        f"Tiêu chí xong: {p['criteria']}" if p.get("criteria") else "Tiêu chí xong: (chưa ghi — người làm tự báo xong)",
        "Bấm ✓ Xác nhận: trợ lý giao, nhắc trước hạn, báo anh nếu trễ."]))


def confirm(proposal, user):
    p = proposal.payload
    owner = User.objects.get(pk=p["owner_id"])
    task = tasks.create_task(user, owner, p["title"], datetime.fromisoformat(p["due"]), proposal.project,
                             p.get("criteria", ""), proposal.source_text)
    transaction.on_commit(lambda: notify_new(task))
    return {"task": task.pk, "url": "/tro-ly/"}


def notify_new(task):
    _tell(task.owner, "\n".join(filter(None, [
        f"VIỆC MỚI #{task.pk} từ {task.created_by}",
        task.title,
        f"Công trình: {task.project.code}" if task.project_id else "",
        f"Hạn: {timezone.localtime(task.due):%d/%m/%Y %H:%M}",
        f"Tiêu chí xong: {task.criteria}" if task.criteria else "",
        f"Bấm Nhận việc. Xong nhắn /xong {task.pk} [ghi chú]; vướng gì nhắn lại ngay."])),
        [[{"text": "👍 Nhận việc", "data": f"tn:{task.pk}"}]])


# ---------- lệnh ----------

def handle(link_row, command, text, state_set):
    """Trả (chữ trả lời, phiếu hoặc None) cho lệnh của trợ lý."""
    user = link_row.user
    arg = text.split(maxsplit=1)[1] if len(text.split()) > 1 else ""
    now = timezone.now()
    if command == "/giao":
        if not user.is_director:
            return "Chỉ Giám đốc giao việc qua trợ lý.", None
        data = task_parse.task(text, people(user), list(Project.objects.values_list("code", flat=True)), now)
        if data.get("error"):
            return data["error"], None
        return None, build(link_row, data, text)
    if command == "/viec":
        rows = tasks.visible(user).filter(state__in=tasks.OPEN_STATES)
        if not rows:
            return "Không có việc giao nào đang mở." if user.is_director else "Anh/chị không có việc nào được giao đang mở.", None
        head = "VIỆC GIAO ĐANG MỞ" if user.is_director else "VIỆC CỦA ANH/CHỊ"
        return head + "\n" + "\n".join(f"• {tasks.task_line(t, now)}" for t in rows[:30]) + f"\nXem: {settings.APP_URL}/tro-ly/", None
    if command == "/xong":
        ref, _, note = arg.partition(" ")
        task = tasks.find(user, ref)
        if task is None:
            return "Cú pháp: /xong 12 [ghi chú] (12 là số việc trong /viec).", None
        task = tasks.report_done(user, task, note)
        _tell_directors(f"{task.owner} báo XONG việc #{task.pk}: {task.title}"
                        + (f"\nGhi chú: {task.report_note}" if task.report_note else "")
                        + (f"\nTiêu chí: {task.criteria}" if task.criteria else ""),
                        [[{"text": "✓ Nhận kết quả", "data": f"ta:{task.pk}"}, {"text": "↩ Trả lại", "data": f"tr:{task.pk}"}]],
                        exclude=user)
        return f"Đã báo Giám đốc việc #{task.pk} xong. Trợ lý sẽ báo lại khi Giám đốc nhận kết quả.", None
    if command == "/huyviec":
        ref, _, reason = arg.partition(" ")
        task = tasks.find(user, ref)
        if task is None:
            return "Cú pháp: /huyviec 12 lý do.", None
        task = tasks.cancel(user, task, reason)
        _tell(task.owner, f"Giám đốc huỷ việc #{task.pk} \"{task.title}\": {task.close_note}. Không cần làm tiếp.")
        return f"Đã huỷ việc #{task.pk}.", None
    if command == "/gopy":
        tasks.add_feedback(user, arg, context="bot /gopy")
        return "Cảm ơn anh/chị. Trợ lý đã ghi góp ý; sáng thứ Hai Giám đốc nhận tổng hợp.", None
    return None


def send_back_reason(link_row, task_id, reason):
    task = tasks.find(link_row.user, task_id)
    if task is None:
        return "Không tìm thấy việc."
    task = tasks.send_back(link_row.user, task, reason)
    _tell(task.owner, f"Giám đốc TRẢ LẠI việc #{task.pk} \"{task.title}\": {task.close_note}. Làm tiếp rồi /xong {task.pk} lần nữa.")
    return f"Đã trả lại việc #{task.pk} cho {task.owner}."


def callback(link_row, kind, value, state_set):
    """Nút: tn (nhận việc), ta (Giám đốc nhận kết quả), tr (trả lại, hỏi lý do). Trả chữ ngắn."""
    task = tasks.find(link_row.user, value)
    if task is None:
        return "Không tìm thấy việc."
    try:
        if kind == "tn":
            tasks.accept(link_row.user, task)
            return f"✓ Đã nhận việc #{task.pk}. Trợ lý sẽ nhắc trước hạn."
        if kind == "ta":
            task = tasks.approve(link_row.user, task)
            _tell(task.owner, f"✓ Giám đốc đã nhận kết quả việc #{task.pk} \"{task.title}\". Việc đã xong.")
            return f"✓ Việc #{task.pk} đã xong."
        if kind == "tr":
            if not link_row.user.is_director or task.state != Task.State.REPORTED:
                return "Việc này không chờ anh nhận kết quả."
            state_set(send_back=str(task.pk))
            return f"Nhắn lý do trả lại việc #{task.pk}."
    except (PermissionDenied, ValidationError) as error:
        return "; ".join(getattr(error, "messages", [str(error)]))
    return "Không rõ thao tác."


def friction(user, text, context):
    """Ghi chỗ người dùng vướng (bot không hiểu, AI lỗi, phiếu không ghi được) để báo cáo cải tiến."""
    try:
        tasks.add_feedback(user, text, kind=Feedback.Kind.FRICTION, context=context)
    except ValidationError:
        pass
