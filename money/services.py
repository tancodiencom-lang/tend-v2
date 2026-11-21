"""Ghi phiếu thu/chi, đối tượng, huỷ phiếu — đường ghi duy nhất (bot khi người nhập bấm Xác nhận; web trang Đối tượng).

Chủ dự án chốt 18/11/2026:
- Thu tiền khách: chỉ kế toán tài chính, Giám đốc. Kế toán nội bộ: hàng NCC, trả NCC, khoán tổ, trả tổ, lương công
  nhật, thu khác, chi khác.
- Phiếu chi không cần duyệt trước (ghi nhận việc đã xảy ra).
- Huỷ: người nhập tự huỷ phiếu của mình trong ngày (có lý do); phiếu cũ hơn hoặc đã xuất MISA cần Giám đốc duyệt.
  Không xoá phiếu, chỉ đánh dấu huỷ.
Chủ dự án chốt 21/11/2026 (báo cáo công nợ tuần): hạn trả nhà cung cấp, tổ thợ do kế toán nội bộ (và Giám đốc) nhập; đợt
thanh toán của khách do kế toán tài chính và Giám đốc nhập; cả hai qua bot và web.
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import record_save
from core.models import Role
from core.text import fold
from projects.models import Project, projects_for

from .models import ACCRUALS, NEEDS_PROJECT, RECEIPTS, Entry, Installment, Kind, Party

RECENT_DAYS = 62          # cũ hơn: vẫn nhập được (nhập bù) nhưng phiếu hiện cảnh báo để kiểm kỳ MISA đã khoá sổ chưa
MAX_DAYS_BACK = 730       # hơn 2 năm: gần như chắc gõ nhầm năm
INTERNAL_KINDS = set(Kind.values) - {Kind.CUSTOMER_RECEIPT}


def kinds_for(user):
    """Loại phiếu người dùng được nhập (và được xem trong sổ thu chi)."""
    if not user.is_active:
        return set()
    if user.is_director:
        return set(Kind.values)
    if user.role == Role.FINANCE_ACCOUNTANT:
        return {Kind.CUSTOMER_RECEIPT}
    if user.role == Role.INTERNAL_ACCOUNTANT:
        return INTERNAL_KINDS
    return set()


def visible_kinds(user):
    """Loại phiếu được xem: kế toán tài chính xem tất cả; kế toán nội bộ không xem thu khách."""
    if user.is_director or (user.is_active and user.role == Role.FINANCE_ACCOUNTANT):
        return set(Kind.values)
    if user.is_active and user.role == Role.INTERNAL_ACCOUNTANT:
        return INTERNAL_KINDS
    return set()


def can_manage_parties(user):
    return user.is_active and (user.is_director or user.role in (Role.INTERNAL_ACCOUNTANT, Role.FINANCE_ACCOUNTANT))


def can_set_due(user):
    """Hạn trả nhà cung cấp, tổ thợ: Giám đốc và kế toán nội bộ."""
    return user.is_active and (user.is_director or user.role == Role.INTERNAL_ACCOUNTANT)


def can_plan_receivables(user):
    """Đợt thanh toán của khách: Giám đốc và kế toán tài chính (kế toán nội bộ không xem tiền khách)."""
    return user.is_active and (user.is_director or user.role == Role.FINANCE_ACCOUNTANT)


def effective_due(entry):
    """Hạn trả của khoản phát sinh nợ: hạn ghi riêng, không thì ngày phiếu + số ngày được nợ của đối tượng; None = chưa có."""
    if entry.due_on:
        return entry.due_on
    days = entry.party_ref.terms_days if entry.party_ref_id else None
    return entry.date + timedelta(days=days) if days is not None else None


def due_date(value, start):
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    if day < start:
        raise ValidationError(f"Hạn trả {day:%d/%m/%Y} trước ngày phiếu {start:%d/%m/%Y}.")
    if (day - start).days > 730:
        raise ValidationError(f"Hạn trả {day:%d/%m/%Y} quá xa (hơn 2 năm) — kiểm tra lại năm.")
    return day


@transaction.atomic
def set_due(user, entry, due_on, reason="", channel="web"):
    """Đặt hoặc đổi hạn trả một khoản nhận hàng NCC / khoán tổ thợ. Đổi hạn đã có phải ghi lý do; nhật ký giữ trước/sau."""
    if not can_set_due(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ đặt hạn trả.")
    entry = Entry.objects.select_for_update().get(pk=entry.pk)
    if entry.kind not in ACCRUALS:
        raise ValidationError("Chỉ đặt hạn trả cho khoản nhận hàng nhà cung cấp hoặc giá trị khoán tổ thợ.")
    if entry.voided_at:
        raise ValidationError("Phiếu đã huỷ.")
    if entry.project_id and not projects_for(user).filter(pk=entry.project_id).exists():
        raise PermissionDenied("Công trình không thuộc phạm vi của anh/chị.")
    day = due_date(due_on, entry.date)
    reason = " ".join(str(reason or "").split())[:300]
    if entry.due_on and entry.due_on != day and not reason:
        raise ValidationError(f"Khoản này đã có hạn {entry.due_on:%d/%m/%Y}; đổi hạn phải ghi lý do (vd nhà cung cấp đồng ý lùi hạn).")
    entry.due_on = day
    return record_save(entry, user, (f"Đổi hạn trả: {reason}" if reason else "Đặt hạn trả"), channel=channel)


@transaction.atomic
def add_installment(user, project, name, amount, due_on, note="", channel="web"):
    if not can_plan_receivables(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán tài chính nhập đợt thanh toán của khách.")
    if not projects_for(user).filter(pk=project.pk).exists():
        raise PermissionDenied("Công trình không thuộc phạm vi của anh/chị.")
    name = " ".join(str(name or "").split())[:100]
    if not name:
        raise ValidationError("Ghi tên đợt (vd Đợt 3 – xong phần thô).")
    amount = Decimal(str(amount or 0)).quantize(Decimal(1))
    if amount <= 0:
        raise ValidationError("Số tiền đợt phải lớn hơn 0.")
    day = due_on if isinstance(due_on, date) else date.fromisoformat(str(due_on)[:10])
    return record_save(Installment(project=project, name=name, amount=amount, due_on=day, note=" ".join(str(note or "").split())[:300],
                                   created_by=user), user, "Thêm đợt thanh toán của khách", channel=channel)


@transaction.atomic
def cancel_installment(user, installment, reason, channel="web"):
    if not can_plan_receivables(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán tài chính huỷ đợt thanh toán của khách.")
    installment = Installment.objects.select_for_update().get(pk=installment.pk)
    if installment.cancelled_at:
        raise ValidationError("Đợt này đã huỷ.")
    reason = " ".join(str(reason or "").split())[:300]
    if not reason:
        raise ValidationError("Huỷ đợt thanh toán phải ghi lý do (vd ký phụ lục đổi tiến độ thanh toán).")
    installment.cancelled_at, installment.cancel_reason = timezone.now(), reason
    return record_save(installment, user, f"Huỷ đợt thanh toán: {reason}", channel=channel)


def check_date(value):
    today = timezone.localdate()
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10]) if value else today
    if day > today:
        raise ValidationError("Ngày phiếu không được sau hôm nay.")
    if (today - day).days > MAX_DAYS_BACK:
        raise ValidationError(f"Ngày phiếu {day:%d/%m/%Y} cách hôm nay hơn 2 năm — kiểm tra lại năm. Phiếu cũ hơn nữa nhập trên MISA.")
    return day


def backfill_days(day):
    """Số ngày phiếu cũ hơn mốc nhập thường (62 ngày); 0 nếu là phiếu gần đây."""
    age = (timezone.localdate() - day).days
    return age if age > RECENT_DAYS else 0


def same_group(kind):
    return RECEIPTS if kind in RECEIPTS else set(Kind.values) - RECEIPTS


def duplicate_of(kind, number, day, amount, party_id=None, party="", exclude=None):
    """Phiếu có thể trùng: cùng số phiếu cùng nhóm thu/chi trong năm; hoặc không số mà cùng ngày, số tiền, đối tượng."""
    rows = Entry.objects.filter(voided_at__isnull=True, kind__in=same_group(kind))
    if exclude:
        rows = rows.exclude(pk__in=exclude)
    number = (number or "").strip()
    if number:
        found = [e for e in rows.filter(date__year=day.year) if fold(e.number).replace(" ", "") == fold(number).replace(" ", "")]
        if found:
            return found[0]
    same = rows.filter(date=day, amount=amount)
    same = same.filter(party_ref_id=party_id) if party_id else same.filter(party__iexact=party)
    return same.first()


@transaction.atomic
def add_party(user, name, type_, channel="telegram", **fields):
    if not can_manage_parties(user) and not kinds_for(user):
        raise PermissionDenied("Anh/chị không được thêm đối tượng.")
    name = " ".join(str(name or "").split())[:200]
    if not name:
        raise ValidationError("Thiếu tên đối tượng.")
    if type_ not in Party.Type.values:
        raise ValidationError("Loại đối tượng không hợp lệ.")
    return record_save(Party(name=name, type=type_, **fields), user, "Thêm đối tượng", channel=channel)


@transaction.atomic
def set_party(user, party, channel="web", **fields):
    if not can_manage_parties(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán sửa đối tượng.")
    party = Party.objects.select_for_update().get(pk=party.pk)
    for field, value in fields.items():
        setattr(party, field, value)
    return record_save(party, user, "Sửa đối tượng: " + ", ".join(fields), channel=channel)


@transaction.atomic
def add_entries(user, rows, client_key, channel="telegram"):
    """rows: [{kind, project_id, date, number, party_id | new_party: {name, type} | party, content, amount, method,
    voucher: ContentFile | None, allow_duplicate}]. Mỗi phiếu có mã chống gửi trùng suy từ client_key và thứ tự.
    Gửi lại cùng client_key trả đúng các phiếu đã ghi. Trả danh sách Entry."""
    from uuid import NAMESPACE_URL, uuid5
    keys = [uuid5(NAMESPACE_URL, f"tend-money:{client_key}:{i}") for i in range(len(rows))]
    done = list(Entry.objects.filter(client_key__in=keys))
    if done:
        return sorted(done, key=lambda e: keys.index(e.client_key))
    allowed = kinds_for(user)
    projects = {p.pk: p for p in projects_for(user)}
    out = []
    for key_, row in zip(keys, rows):
        kind = row.get("kind")
        if kind not in Kind.values:
            raise ValidationError("Loại phiếu không hợp lệ.")
        if kind not in allowed:
            raise PermissionDenied(f"Anh/chị không được nhập phiếu \"{Kind(kind).label}\".")
        project = None
        if row.get("project_id"):
            project = projects.get(int(row["project_id"]))
            if project is None:
                raise PermissionDenied("Công trình không thuộc phạm vi của anh/chị.")
        if kind in NEEDS_PROJECT and project is None:
            raise ValidationError(f"Phiếu \"{Kind(kind).label}\" cần công trình.")
        amount = Decimal(str(row.get("amount") or 0)).quantize(Decimal(1))
        if amount <= 0:
            raise ValidationError("Số tiền phải lớn hơn 0.")
        day = check_date(row.get("date"))
        if row.get("new_party"):
            party = add_party(user, row["new_party"]["name"], row["new_party"]["type"], channel)
        else:
            party = Party.objects.filter(pk=row.get("party_id")).first() if row.get("party_id") else None
        label = party.name if party else str(row.get("party") or "").strip()[:200]
        if not label:
            raise ValidationError("Phiếu thiếu đối tượng.")
        number = str(row.get("number") or "").strip()[:40]
        twin = duplicate_of(kind, number, day, amount, party.pk if party else None, label, exclude=[e.pk for e in out])
        if twin and not row.get("allow_duplicate"):
            raise ValidationError(f"Có thể trùng phiếu đã ghi: {twin} ngày {twin.date:%d/%m/%Y}.")
        method = row.get("method") or (Entry.Method.NONE if kind in (Kind.SUPPLIER_GOODS, Kind.TEAM_WORK) else Entry.Method.CASH)
        due_on = due_date(row["due_on"], day) if row.get("due_on") and kind in ACCRUALS else None
        entry = Entry(kind=kind, project=project, date=day, number=number, party=label, party_ref=party,
                      content=str(row.get("content") or Kind(kind).label)[:500], amount=amount, method=method,
                      entered_by=user, client_key=key_, due_on=due_on)
        if row.get("voucher") is not None:
            entry.voucher.save(getattr(row["voucher"], "name", "chung-tu.jpg"), row["voucher"], save=False)
        out.append(record_save(entry, user, "Nhập phiếu qua chat" if channel == "telegram" else "Nhập phiếu", channel=channel))
    return out


def void_needs_director(user, entry):
    """Tự huỷ được: phiếu của chính mình, nhập trong ngày, chưa xuất MISA. Giám đốc luôn tự huỷ được."""
    if user.is_director:
        return False
    same_day = timezone.localtime(entry.created_at).date() == timezone.localdate()
    return not (entry.entered_by_id == user.pk and same_day and not entry.misa_export_id and not entry.misa_exported_at)


def can_request_void(user, entry):
    return entry.kind in visible_kinds(user) and (entry.project_id is None or projects_for(user).filter(pk=entry.project_id).exists())


@transaction.atomic
def void(user, entry, reason, approver=None, channel="telegram"):
    """Huỷ phiếu. `approver` là Giám đốc đã bấm duyệt (khi người xin huỷ không tự huỷ được)."""
    entry = Entry.objects.select_for_update().get(pk=entry.pk)
    reason = str(reason or "").strip()
    if not reason:
        raise ValidationError("Huỷ phiếu cần lý do.")
    if entry.voided_at:
        raise ValidationError("Phiếu đã huỷ trước đó.")
    if not can_request_void(user, entry):
        raise PermissionDenied("Anh/chị không được huỷ phiếu này.")
    if void_needs_director(user, entry) and not (approver and approver.is_director):
        raise PermissionDenied("Phiếu cũ hoặc đã xuất MISA: cần Giám đốc duyệt huỷ.")
    entry.voided_at, entry.voided_by = timezone.now(), user
    entry.void_reason = (reason + (f" (Giám đốc {approver} duyệt)" if approver and approver != user else ""))[:500]
    return record_save(entry, approver or user, f"Huỷ phiếu: {reason}", channel=channel)


def find_entry(user, ref):
    """Tìm phiếu theo số phiếu (vd PC12) hoặc #id trong phạm vi được xem, mới nhất trước."""
    ref = str(ref or "").strip()
    rows = Entry.objects.filter(kind__in=visible_kinds(user)).filter(
        project__in=projects_for(user)) | Entry.objects.filter(kind__in=visible_kinds(user), project__isnull=True)
    if ref.startswith("#") and ref[1:].isdigit():
        return rows.filter(pk=int(ref[1:])).first()
    wanted = fold(ref).replace(" ", "")
    return next((e for e in rows.order_by("-date", "-pk")[:2000] if fold(e.number).replace(" ", "") == wanted), None)
