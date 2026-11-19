"""Ghi báo cáo hiện trường, chấm công, danh sách thợ — đường ghi duy nhất (bot gọi khi người gửi bấm Xác nhận).

Mỗi hàm một giao dịch, nhật ký từng bản ghi (`record_save`/`record_delete`), kiểm quyền ở đây chứ không chỉ ở bot.
Báo cáo đã gửi không sửa; báo thấp hơn mức đã báo phải có lý do. Chấm công: giám sát gửi báo cáo là tự có mặt
tại công trình đó hôm đó; thợ chấm theo tên. 8 giờ = 1 công (chủ dự án chốt 18/11/2026).
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import record_delete, record_save
from core.models import Role
from drawings.models import Item
from projects.models import Project, projects_for

from .models import Attendance, Photo, Report, ReportLine, Trade, Worker, WorkerRate

HOURS_PER_DAY = Decimal(8)
HUNDRED = Decimal(100)
MAX_DAYS_BACK = 31


def days_from(days=None, hours=None):
    """Số công từ "cả ngày / nửa ngày / số giờ"."""
    if hours not in (None, ""):
        value = Decimal(str(hours)) / HOURS_PER_DAY
    elif days not in (None, ""):
        value = Decimal(str(days))
    else:
        value = Decimal(1)
    return value.quantize(Decimal("0.01"))


def can_report(user, project):
    return (user.is_active and (user.is_director or user.role == Role.SUPERVISOR)
            and projects_for(user).filter(pk=project.pk).exists())


def can_set_rates(user):
    return user.is_active and (user.is_director or user.role == Role.INTERNAL_ACCOUNTANT)


def can_add_workers(user):
    return user.is_active and (user.is_director or user.role in (Role.SUPERVISOR, Role.INTERNAL_ACCOUNTANT))


def _work_date(value):
    today = timezone.localdate()
    if not value:
        return today
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if day > today:
        raise ValidationError("Ngày báo cáo không được sau hôm nay.")
    if (today - day).days > MAX_DAYS_BACK:
        raise ValidationError(f"Chỉ báo bù trong {MAX_DAYS_BACK} ngày gần nhất.")
    return day


def percent_of(item, percent=None, quantity=None):
    if percent is not None:
        return Decimal(str(percent))
    if quantity is not None and item.design_quantity:
        return Decimal(str(quantity)) / item.design_quantity * HUNDRED
    return None


def latest_line(item):
    return (ReportLine.objects.filter(item=item).select_related("report")
            .order_by("-report__work_date", "-report__created_at", "-pk").first())


def decrease_from(item, percent=None, quantity=None):
    """Mức đã báo nếu lần này thấp hơn (để bắt ghi lý do), không thì None."""
    last = latest_line(item)
    if last is None:
        return None
    if percent is not None and last.percent is not None:
        return last.percent if Decimal(str(percent)) < last.percent else None
    if quantity is not None and last.quantity is not None:
        return last.quantity if Decimal(str(quantity)) < last.quantity else None
    before, now = percent_of(item, last.percent, last.quantity), percent_of(item, percent, quantity)
    return before if before is not None and now is not None and now < before - Decimal("0.05") else None


def worker_day_total(worker, work_date, exclude_project=None):
    rows = Attendance.objects.filter(worker=worker, work_date=work_date)
    if exclude_project is not None:
        rows = rows.exclude(project=exclude_project)
    return sum((r.days for r in rows), Decimal(0))


@transaction.atomic
def add_worker(user, name, team="", kind=Worker.Kind.SKILLED, channel="telegram", trade=None, pay=None, person=None):
    """Thêm hồ sơ nhân sự, cấp mã NS-xxxx (một lần, không đổi, không dùng lại)."""
    if not can_add_workers(user):
        raise PermissionDenied("Anh/chị không được thêm nhân sự.")
    name = " ".join(str(name).split())[:100]
    if not name:
        raise ValidationError("Thiếu họ tên.")
    worker = Worker(name=name, team=" ".join(str(team or "").split())[:100],
                    kind=kind if kind in Worker.Kind.values else Worker.Kind.SKILLED, trade=trade, user=person, code=next_code())
    if pay in Worker.Pay.values:
        worker.pay = pay
    return record_save(worker, user, f"Thêm nhân sự {worker.code}", channel=channel)


def next_code():
    """NS-0001, NS-0002…: số lớn nhất đã cấp (kể cả người đã nghỉ) + 1; khoá các dòng để hai người thêm cùng lúc không trùng."""
    last = 0
    for code in Worker.objects.select_for_update().exclude(code__isnull=True).values_list("code", flat=True):
        if code.startswith("NS-") and code[3:].isdigit():
            last = max(last, int(code[3:]))
    return f"NS-{last + 1:04d}"


@transaction.atomic
def ensure_profile(actor, person):
    """Tài khoản giám sát có hồ sơ nhân sự (nghề Giám sát, lương tháng) để chấm công và chia lương vào công trình."""
    existing = Worker.objects.filter(user=person).first()
    if existing is not None or person.role != Role.SUPERVISOR:
        return existing
    name = " ".join(filter(None, [person.last_name, person.first_name])) or person.username
    worker = Worker(name=name[:100], trade=Trade.objects.filter(name="Giám sát").first(), pay=Worker.Pay.MONTHLY, user=person,
                    code=next_code())
    return record_save(worker, actor, f"Tạo hồ sơ nhân sự {worker.code} cho tài khoản {person.username}", channel="web")


@transaction.atomic
def rename_worker(user, worker, name, channel="web"):
    if not can_set_rates(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ sửa họ tên nhân sự.")
    name = " ".join(str(name).split())[:100]
    if not name:
        raise ValidationError("Thiếu họ tên.")
    worker = Worker.objects.select_for_update().get(pk=worker.pk)
    old = worker.name
    worker.name = name
    return record_save(worker, user, f"Đổi họ tên {worker.code}: {old} → {name}", channel=channel)


@transaction.atomic
def set_rate(user, worker, amount, start=None, pay=None, channel="web"):
    """Đơn giá mới áp dụng từ ngày `start` (mặc định hôm nay); đơn giá đầu tiên áp dụng cho cả công trước đó.
    Công đã qua giữ giá cũ. Cùng ngày hiệu lực thì thay giá ngày đó; mọi lần đều có nhật ký."""
    if not can_set_rates(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ đặt đơn giá.")
    worker = Worker.objects.select_for_update().get(pk=worker.pk)
    if amount is not None and not (0 <= Decimal(amount) <= 200_000_000):
        raise ValidationError("Đơn giá không hợp lệ.")
    pay = pay if pay in Worker.Pay.values else worker.pay
    first = not worker.rates.exists()
    start = date(2000, 1, 1) if first else (start or timezone.localdate())
    rate = WorkerRate.objects.filter(worker=worker, start=start).first() or WorkerRate(worker=worker, start=start)
    rate.pay, rate.amount, rate.created_by = pay, (Decimal(amount) if amount is not None else None), user
    record_save(rate, user, f"Đơn giá đầu tiên {worker.code}" if first else f"Đơn giá {worker.code} từ {start:%d/%m/%Y}",
                channel=channel)
    latest = worker.rates.filter(start__lte=timezone.localdate()).order_by("-start").first()
    if latest is not None and (worker.day_rate, worker.pay) != (latest.amount, latest.pay):
        worker.day_rate, worker.pay = latest.amount, latest.pay
        record_save(worker, user, f"Đơn giá hiện hành {worker.code}", channel=channel)
    return rate


@transaction.atomic
def set_worker(user, worker, *, day_rate=None, pay=None, team=None, kind=None, active=None, clear_rate=False, trade=None,
               rate_start=None, channel="web"):
    """Sửa tổ, bậc, nghề, đang làm; đơn giá/hình thức trả qua set_rate (có ngày hiệu lực, không đổi công đã qua)."""
    if not can_set_rates(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ sửa đơn giá, hình thức trả của nhân sự.")
    worker = Worker.objects.select_for_update().get(pk=worker.pk)
    if clear_rate or day_rate is not None or (pay is not None and pay != worker.pay):
        set_rate(user, worker, None if clear_rate else (day_rate if day_rate is not None else worker.day_rate), rate_start,
                 pay or worker.pay, channel=channel)
        worker.refresh_from_db()
    changes = []
    for field, value in (("team", team), ("kind", kind), ("active", active), ("trade", trade)):
        if value is not None and getattr(worker, field) != value:
            setattr(worker, field, value)
            changes.append(field)
    if changes:
        record_save(worker, user, "Sửa nhân sự: " + ", ".join(changes), channel=channel)
    return worker


@transaction.atomic
def submit_report(user, project, payload, photos=(), client_key=None, channel="telegram"):
    """payload: {work_date, note, lines: [{item_id, percent | quantity, note, reason}],
    attendance: [{worker_id | new: {name, team, kind}, days, reason}]}; photos: [(File, item_id | None)].

    Gửi lại cùng `client_key` trả báo cáo đã ghi, không ghi lần hai. Trả Report.
    """
    existing = Report.objects.filter(client_key=client_key).first()
    if existing is not None:
        return existing
    project = Project.objects.select_for_update().get(pk=project.pk)
    if not can_report(user, project):
        raise PermissionDenied("Anh/chị không được báo cáo công trình này.")
    work_date = _work_date(payload.get("work_date"))
    lines = payload.get("lines") or []
    attendance = payload.get("attendance") or []
    if not lines and not attendance and not payload.get("note") and not photos:
        raise ValidationError("Báo cáo trống.")
    items = {i.pk: i for i in Item.objects.filter(project=project, active=True, pk__in=[l.get("item_id") for l in lines])}
    report = record_save(Report(project=project, author=user, work_date=work_date, note=str(payload.get("note") or "")[:5000],
                                channel=channel, client_key=client_key), user, "Gửi báo cáo ngày", channel=channel)
    by_item = {}
    for line in lines:
        item = items.get(line.get("item_id"))
        if item is None:
            raise ValidationError("Chi tiết trong báo cáo không còn trên bản vẽ của công trình.")
        percent = Decimal(str(line["percent"])) if line.get("percent") not in (None, "") else None
        quantity = Decimal(str(line["quantity"])) if percent is None and line.get("quantity") not in (None, "") else None
        if percent is None and quantity is None:
            raise ValidationError(f"{item.code}: thiếu % hoặc khối lượng.")
        if percent is not None and not 0 <= percent <= 100:
            raise ValidationError(f"{item.code}: phần trăm phải từ 0 đến 100.")
        if quantity is not None and quantity < 0:
            raise ValidationError(f"{item.code}: khối lượng không âm.")
        reason = str(line.get("reason") or "").strip()[:1000]
        if decrease_from(item, percent, quantity) is not None and not reason:
            raise ValidationError(f"{item.code}: thấp hơn mức đã báo, cần lý do điều chỉnh.")
        row = record_save(ReportLine(report=report, item=item, percent=percent, quantity=quantity,
                                     note=str(line.get("note") or "")[:2000], decrease_reason=reason), user, "Dòng báo cáo",
                          channel=channel)
        by_item[item.pk] = row
    for file, item_id in photos:
        photo = Photo(report=report, line=by_item.get(item_id))
        photo.image.save(getattr(file, "name", "anh.jpg"), file, save=False)
        record_save(photo, user, "Ảnh báo cáo", channel=channel)
    if not Attendance.objects.filter(project=project, work_date=work_date, staff=user).exists():
        record_save(Attendance(project=project, work_date=work_date, staff=user, days=1, report=report, recorded_by=user),
                    user, "Có mặt khi gửi báo cáo", channel=channel)
    for entry in attendance:
        if entry.get("new"):
            new = entry["new"]
            worker = add_worker(user, new.get("name"), new.get("team", ""), new.get("kind") or Worker.Kind.SKILLED, channel)
        else:
            worker = Worker.objects.filter(pk=entry.get("worker_id"), active=True).first()
            if worker is None:
                raise ValidationError("Thợ trong phiếu không còn trong danh sách.")
        set_attendance(user, project, work_date, worker, days_from(entry.get("days")), entry.get("reason", ""), report, channel)
    return report


def set_attendance(user, project, work_date, worker, days, reason="", report=None, channel="telegram"):
    """Ghi công một thợ; đã có cùng số công thì thôi, khác số công thì là sửa (cần lý do), 0 công là bỏ công."""
    if not 0 <= days <= 3:
        raise ValidationError(f"{worker.name}: số công phải từ 0 đến 3.")
    row = Attendance.objects.select_for_update().filter(project=project, work_date=work_date, worker=worker).first()
    reason = str(reason or "").strip()[:500]
    if row is not None:
        if row.days == days:
            return row
        if not reason:
            raise ValidationError(f"{worker.name} đã chấm {row.days.normalize():f} công ngày {work_date:%d/%m/%Y}: sửa cần lý do.")
        if days == 0:
            record_delete(row, user, f"Bỏ công: {reason}", channel=channel)
            return None
        row.days, row.correction_reason, row.recorded_by = days, reason, user
        return record_save(row, user, f"Sửa công: {reason}", channel=channel)
    if days == 0:
        return None
    return record_save(Attendance(project=project, work_date=work_date, worker=worker, days=days, report=report, recorded_by=user,
                                  correction_reason="" if report else reason),
                       user, "Chấm công theo báo cáo" if report else f"Chấm công bổ sung: {reason}", channel=channel)


@transaction.atomic
def correct_attendance(user, project, work_date, worker, days, reason, channel="telegram"):
    if not can_report(user, project):
        raise PermissionDenied("Anh/chị không được chấm công công trình này.")
    if not str(reason or "").strip():
        raise ValidationError("Sửa công cần lý do.")
    work_date = _work_date(work_date)
    if Attendance.objects.filter(project=project, work_date=work_date, worker=worker).exists() or days > 0:
        reason_text = str(reason).strip()
        return set_attendance(user, project, work_date, worker, days, reason_text, None, channel)
    return None
