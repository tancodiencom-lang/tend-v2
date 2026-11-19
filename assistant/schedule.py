"""Lịch làm việc của trợ lý. `run()` chạy mỗi 10 phút (tend-v2-troly.timer); mỗi tin có khoá riêng trong `Notice`
nên chạy lại không gửi trùng, máy chủ tắt một lúc thì lần chạy sau bù trong khung giờ còn hợp lệ.

Giờ (ngày làm việc TEND_WORK_DAYS, mặc định thứ Hai–thứ Bảy):
    07:30  bản tin sáng cho Giám đốc; việc hôm nay cho từng người
    16:00  nhắc giám sát công trình chưa có báo cáo ngày; 17:00 nhắc lần hai; 19:00 báo Giám đốc
    18:00  nhắc người nhập phiếu chi trong ngày còn thiếu ảnh chứng từ
    18:30  tóm tắt ngày cho Giám đốc (tiến độ, nhân công, thu chi, việc còn mở)
    liên tục trong giờ: nhắc việc sắp đến hạn/quá hạn, leo thang lên Giám đốc, cảnh báo Gấp (mỗi tuần một lần mỗi loại)
Nhân viên chỉ nhận tin 07:00–19:30 ngày làm việc; Giám đốc 07:00–21:30.
"""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from asks import services as asks
from asks.models import Ask
from chat import money_bot, telegram
from chat.models import ChatLink, Proposal
from core.models import Role, User
from core.text import vn_number, vnd
from field.models import Attendance, Report, ReportLine, Worker
from money.models import Entry, Kind
from overview import compute
from projects.models import Project

from .models import Feedback, Notice, Task
from .services import task_line

PREFIX = "🤖 Trợ lý: "
PAYMENT_KINDS = (Kind.SUPPLIER_PAYMENT, Kind.TEAM_PAYMENT, Kind.LABOR_PAYMENT, Kind.OTHER_EXPENSE)
LATE_DAYS = 7                  # ngày xong dự kiến trễ hẹn bàn giao từ 7 ngày: Gấp
COST_OVERRUN = Decimal("1.10")  # chi phí đã phát sinh vượt giá vốn phần đã làm từ 10%: Gấp
ESCALATE_AFTER = timedelta(hours=4)
RATE_GRACE = timedelta(days=2)


# ---------- giờ giấc ----------

def work_days():
    raw = str(getattr(settings, "TEND_WORK_DAYS", "1,2,3,4,5,6"))
    return {int(x) for x in raw.replace(" ", "").split(",") if x.isdigit()}


def is_workday(local):
    return local.isoweekday() in work_days()


def window(local, hour, minute, hours=3):
    start = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return start <= local < start + timedelta(hours=hours)


def awake(user, local):
    end = time(21, 30) if user.is_director else time(19, 30)
    return is_workday(local) and time(7, 0) <= local.time() < end


# ---------- gửi ----------

def links(user):
    return list(ChatLink.objects.filter(user=user, active=True, user__is_active=True))


def send(key, user, text, buttons=None, now=None):
    """Gửi một lần cho mỗi khoá. Người chưa kết nối Telegram: vẫn ghi khoá (để không thử lại), không gửi."""
    try:
        with transaction.atomic():
            Notice.objects.create(key=key[:120], user=user, text=text[:4000], sent_at=now or timezone.now())
    except IntegrityError:
        return False
    for row in links(user):
        telegram.send(row.chat_id, PREFIX + text, buttons)
    return True


def sent(key):
    return Notice.objects.filter(key=key[:120]).first()


def directors():
    return list(User.objects.filter(is_active=True).filter(Q(role=Role.DIRECTOR) | Q(is_superuser=True)))


# ---------- dữ liệu ----------

def running_projects(today):
    """Công trình đang thi công: còn hoạt động, đã khởi công (hoặc chưa ghi ngày), có chi tiết khối lượng."""
    rows = Project.objects.filter(active=True, items__active=True).distinct()
    return [p for p in rows if p.start_date is None or p.start_date <= today]


def supervisors(project):
    return list(User.objects.filter(is_active=True, role=Role.SUPERVISOR, memberships__project=project,
                                    memberships__active=True).distinct())


def members(project, role):
    return list(User.objects.filter(is_active=True, role=role, memberships__project=project, memberships__active=True).distinct())


def missing_reports(today):
    done = set(Report.objects.filter(work_date=today).values_list("project_id", flat=True))
    return [p for p in running_projects(today) if p.pk not in done]


def alerts(today):
    """Cảnh báo theo bảng Chỉ số theo dõi: [{level, project, text, to: [User], money: bool}] (Gấp trước)."""
    out = []
    for project in running_projects(today):
        p = compute.progress(project, today)
        data = p.pop("rows", None)
        sups = supervisors(project)
        if p.get("late") is not None and p["late"] >= LATE_DAYS:
            out.append({"level": "Gấp", "key": "late", "project": project, "money": False, "to": sups,
                        "text": f"{project.code}: dự kiến xong {p['forecast']:%d/%m/%Y}, trễ {p['late']} ngày so với hẹn bàn giao "
                                f"{project.handover_date:%d/%m/%Y} (đang {vn_number(p['actual'])}%)."})
        if p.get("overdue"):
            codes = ", ".join(f"{o['code']} ({vn_number(o['percent'])}%, quá {o['days']} ngày)" for o in p["overdue"][:5])
            out.append({"level": "Thường", "key": "overdue", "project": project, "money": False, "to": sups,
                        "text": f"{project.code}: {len(p['overdue'])} chi tiết quá hạn kế hoạch chưa xong: {codes}."})
        if data:
            profit = compute.profit(project, today, data)
            cost_done = sum(((r.item.cost_price or Decimal(0)) * (r.item.design_quantity or Decimal(0)) * r.percent_at(today)
                             / Decimal(100) for r in data), Decimal(0))
            if cost_done > 0 and profit["incurred"] > cost_done * COST_OVERRUN:
                over = (profit["incurred"] / cost_done - 1) * 100
                out.append({"level": "Gấp", "key": "cost", "project": project, "money": True,
                            "to": members(project, Role.INTERNAL_ACCOUNTANT),
                            "text": f"{project.code}: chi phí đã phát sinh {vnd(profit['incurred'])} vượt {vn_number(over, 0)}% "
                                    f"giá vốn phần việc đã làm ({vnd(cost_done)})."})
            if profit["forecast"] is not None and profit["forecast"] < 0:
                out.append({"level": "Gấp", "key": "loss", "project": project, "money": True, "to": [],
                            "text": f"{project.code}: lãi dự báo âm {vnd(profit['forecast'])}."})
    out.sort(key=lambda a: (a["level"] != "Gấp", a["project"].code))
    return out


def decreases(day):
    rows = (ReportLine.objects.filter(report__work_date=day).exclude(decrease_reason="")
            .select_related("item", "report__project", "report__author"))
    return [f"{r.report.project.code} {r.item.code} còn {vn_number(r.percent) + '%' if r.percent is not None else vn_number(r.quantity)}"
            f" ({r.report.author}): {r.decrease_reason}" for r in rows]


def workers_without_rate(now):
    used = Attendance.objects.filter(worker__isnull=False).values_list("worker_id", flat=True)
    return list(Worker.objects.filter(active=True, pay=Worker.Pay.DAILY, day_rate__isnull=True, pk__in=used,
                                      created_at__lt=now - RATE_GRACE))


def pending_voids():
    return Proposal.objects.filter(kind=Proposal.Kind.VOID, state=Proposal.State.PENDING, payload__needs_director=True,
                                   created_at__gte=timezone.now() - timedelta(hours=24)).count()


# ---------- bản tin ----------

def morning_text(user, now, found=None):
    local = timezone.localtime(now)
    today = local.date()
    out = [f"BẢN TIN SÁNG {today:%d/%m/%Y}"]
    waiting = list(Task.objects.filter(state=Task.State.REPORTED).select_related("owner", "project"))
    voids = pending_voids()
    out.append("\n① Việc cần anh quyết hôm nay")
    out += [f"• {task_line(t, now)}: {t.report_note or 'không ghi chú'} (bấm ✓ Nhận kết quả / ↩ Trả lại ở tin báo xong)"
            for t in waiting]
    if voids:
        out.append(f"• {voids} phiếu thu chi xin huỷ chờ anh duyệt (nút ở tin bot đã gửi).")
    if not waiting and not voids:
        out.append("• Không có.")
    found = alerts(today) if found is None else found
    out.append("\n② Cảnh báo")
    out += [f"• [{a['level']}] {a['text']}" for a in found[:12]] or ["• Không có cảnh báo."]
    drops = decreases(today - timedelta(days=1))
    if drops:
        out.append("Báo giảm hôm qua: " + "; ".join(drops[:5]))
    out.append("\n③ Lịch hôm nay và mốc gần")
    due_today = [t for t in Task.objects.filter(state=Task.State.OPEN, due__date=today).select_related("owner", "project")]
    out += [f"• Hạn việc: {task_line(t, now)}" for t in due_today]
    asks_due = Ask.objects.filter(cancelled_at__isnull=True, due=today).select_related("project", "assignee")
    out += [f"• Hạn yêu cầu báo cáo {a.project.code} · {a.assignee}: {a.note[:80]}" for a in asks_due if asks.status(a)[0] != "done"]
    for project in Project.objects.filter(active=True, handover_date__range=(today, today + timedelta(days=7))):
        out.append(f"• Hẹn bàn giao {project.code}: {project.handover_date:%d/%m/%Y}")
    if out[-1].startswith("\n③"):
        out.append("• Không có mốc nào.")
    late = [t for t in Task.objects.filter(state=Task.State.OPEN, due__lt=now).select_related("owner", "project")]
    out.append("\n④ Việc đã giao đang trễ")
    for t in late:
        escalated = Notice.objects.filter(key__startswith=f"task:{t.pk}:esc:").exists()
        did = "đã nhắc" + (", đã báo anh" if escalated else "") if sent(f"task:{t.pk}:late") else "sẽ nhắc trong giờ làm"
        out.append(f"• {task_line(t, now)} — trợ lý {did}")
    if not late:
        out.append("• Không có.")
    offline = User.objects.filter(is_active=True).exclude(chat_links__active=True).exclude(role=Role.DIRECTOR).exclude(is_superuser=True)
    if offline.exists():
        out.append("\nChưa kết nối Telegram (trợ lý không nhắn được): " + ", ".join(str(u) for u in offline[:10]))
    if local.isoweekday() == 1:
        week = Feedback.objects.filter(created_at__gte=now - timedelta(days=7))
        if week.exists():
            users = week.filter(kind=Feedback.Kind.USER)
            out.append(f"\nGóp ý tuần qua: {users.count()} góp ý, {week.count() - users.count()} chỗ vướng bot tự ghi.")
            out += [f"• {f.author}: {f.text[:150]}" for f in users[:5]]
    out.append(f"\nXem việc giao: {settings.APP_URL}/tro-ly/")
    return "\n".join(out)


def personal_text(user, now, found=None):
    """Việc hôm nay của một người (không phải Giám đốc); None nếu không có gì."""
    local = timezone.localtime(now)
    today = local.date()
    out = []
    tasks = Task.objects.filter(owner=user, state=Task.State.OPEN, due__lt=timezone.make_aware(
        datetime.combine(today + timedelta(days=2), time.min))).select_related("project")
    out += [f"• {task_line(t, now)}" for t in tasks]
    out += [f"• Yêu cầu báo cáo {a.project.code}{' · hạn ' + a.due.strftime('%d/%m') if a.due else ''}: {a.note[:100]}"
            for a in asks.pending_for(user)]
    found = alerts(today) if found is None else found
    if user.role == Role.SUPERVISOR:
        for alert in found:
            if user in alert["to"] and not alert["money"]:
                out.append(f"• {alert['text']}")
    if user.role == Role.INTERNAL_ACCOUNTANT:
        missing = workers_without_rate(now)
        if missing:
            out.append("• Thợ công nhật chưa có đơn giá (chấm công đã hơn 2 ngày): " + ", ".join(w.name for w in missing[:10])
                       + ". Đặt bằng /dongia <tên> <số tiền>.")
        for alert in found:
            if user in alert["to"]:
                out.append(f"• {alert['text']}")
    if not out:
        return None
    return f"VIỆC HÔM NAY {today:%d/%m/%Y}\n" + "\n".join(out) + "\nXong việc nào nhắn /xong #số [ghi chú]. Vướng gì nhắn lại ngay."


def evening_text(user, now):
    today = timezone.localtime(now).date()
    out = [f"TÓM TẮT NGÀY {today:%d/%m/%Y}"]
    for project in running_projects(today):
        p = compute.progress(project, today)
        p.pop("rows", None)
        reports = Report.objects.filter(project=project, work_date=today).count()
        people = sum((a.days for a in Attendance.objects.filter(project=project, work_date=today)), Decimal(0))
        gap = (f", {'▲ nhanh' if p['gap'] >= 0 else '▼ chậm'} {vn_number(abs(p['gap']))}% so kế hoạch" if p.get("gap") is not None else "")
        state = f"{vn_number(p['actual'])}%{gap}" if p.get("actual") is not None else p.get("missing") or "chưa tính được %"
        report = f"{reports} báo cáo, {vn_number(people)} công" if reports else "● CHƯA CÓ BÁO CÁO"
        out.append(f"• {project.code}: {state}; hôm nay {report}")
    if len(out) == 1:
        out.append("• Không có công trình đang thi công.")
    money = money_bot.daily_text(user, today)
    if money:
        out.append("\n" + money)
    open_ = Task.objects.filter(state__in=(Task.State.OPEN, Task.State.REPORTED))
    late = open_.filter(state=Task.State.OPEN, due__lt=now).count()
    out.append(f"\nViệc giao còn mở: {open_.count()}" + (f", trễ {late}" if late else "") + f" · {settings.APP_URL}/tro-ly/")
    return "\n".join(out)


# ---------- chạy ----------

def run(now=None):
    """Một lượt: trả danh sách khoá đã gửi (để kiểm thử, ghi log)."""
    now = now or timezone.now()
    local = timezone.localtime(now)
    today = local.date()
    stamp = today.isoformat()
    done = []

    def go(key, user, text, buttons=None):
        if text and awake(user, local) and send(key, user, text, buttons, now):
            done.append(key)

    cache = {}

    def found():
        if "alerts" not in cache:
            cache["alerts"] = alerts(today)
        return cache["alerts"]

    if window(local, 7, 30) and is_workday(local):
        for user in directors():
            if not sent(f"morning:{stamp}:{user.pk}"):
                go(f"morning:{stamp}:{user.pk}", user, morning_text(user, now, found()))
        for user in User.objects.filter(is_active=True).exclude(role=Role.DIRECTOR).exclude(is_superuser=True):
            if not sent(f"today:{stamp}:{user.pk}"):
                go(f"today:{stamp}:{user.pk}", user, personal_text(user, now, found()))

    if is_workday(local) and local.time() >= time(16, 0):
        missing = missing_reports(today)
        for project in missing:
            for user in supervisors(project):
                if window(local, 16, 0, 1):
                    go(f"rp16:{stamp}:{project.pk}:{user.pk}", user,
                       f"Công trình {project.code} chưa có báo cáo hôm nay. Chuẩn bị gửi trước 17:00 (tiến độ từng chi tiết, ảnh, chấm công).")
                elif window(local, 17, 0, 2):
                    go(f"rp17:{stamp}:{project.pk}:{user.pk}", user,
                       f"Nhắc lần hai: {project.code} vẫn chưa có báo cáo hôm nay. 19:00 trợ lý sẽ báo Giám đốc.")
        if missing and window(local, 19, 0):
            for user in directors():
                go(f"rp19:{stamp}:{user.pk}", user, "Công trình đang thi công chưa có báo cáo hôm nay (đã nhắc giám sát 16:00, 17:00): "
                   + ", ".join(f"{p.code} ({', '.join(str(s) for s in supervisors(p)) or 'chưa giao giám sát'})" for p in missing))

    if window(local, 18, 0, 2):
        rows = Entry.objects.filter(date=today, kind__in=PAYMENT_KINDS, voided_at__isnull=True, voucher="").select_related("entered_by")
        by_user = defaultdict(list)
        for entry in rows:
            by_user[entry.entered_by].append(entry)
        for user, entries in by_user.items():
            go(f"vc:{stamp}:{user.pk}", user, f"{len(entries)} phiếu chi hôm nay chưa có ảnh chứng từ: "
               + ", ".join(f"{e.number or '#' + str(e.pk)} {vnd(e.amount)}" for e in entries[:10])
               + ". Gửi ảnh phiếu/chuyển khoản (dạng Tệp) kèm số phiếu để lưu chứng từ gốc.")

    if window(local, 18, 30):
        for user in directors():
            go(f"evening:{stamp}:{user.pk}", user, evening_text(user, now))

    for task in Task.objects.filter(state=Task.State.OPEN).select_related("owner", "project"):
        if not task.accepted_at and now - task.created_at >= ESCALATE_AFTER:
            go(f"task:{task.pk}:ack", task.owner, f"Việc #{task.pk} \"{task.title}\" Giám đốc giao chưa được bấm nhận. "
               f"Hạn {timezone.localtime(task.due):%d/%m %H:%M}.", [[{"text": "👍 Nhận việc", "data": f"tn:{task.pk}"}]])
        if timedelta(0) < task.due - now <= timedelta(hours=2):
            go(f"task:{task.pk}:soon", task.owner, f"Việc #{task.pk} \"{task.title}\" đến hạn lúc {timezone.localtime(task.due):%H:%M}. "
               f"Xong thì nhắn /xong {task.pk} [ghi chú]; vướng gì nhắn lại ngay.")
        if now > task.due:
            go(f"task:{task.pk}:late", task.owner, f"Việc #{task.pk} \"{task.title}\" đã quá hạn "
               f"{timezone.localtime(task.due):%d/%m %H:%M}. Xong thì nhắn /xong {task.pk}; chưa xong thì nhắn lý do và ngày xong mới.")
            reminded = sent(f"task:{task.pk}:late")
            if reminded and now - reminded.sent_at >= ESCALATE_AFTER:
                for user in directors():
                    go(f"task:{task.pk}:esc:{user.pk}", user, f"Việc trễ sau khi đã nhắc: {task_line(task, now)}."
                       f"\nĐã nhắc {task.owner} lúc {timezone.localtime(reminded.sent_at):%d/%m %H:%M}, chưa báo xong.")

    if is_workday(local) and time(7, 30) <= local.time() < time(21, 0):
        week = f"{today.isocalendar().year}-{today.isocalendar().week}"
        for alert in found():
            if alert["level"] != "Gấp":
                continue
            for user in directors() + alert["to"]:
                go(f"alert:{alert['key']}:{alert['project'].pk}:{week}:{user.pk}", user, f"CẢNH BÁO GẤP · {alert['text']}")
    return done
