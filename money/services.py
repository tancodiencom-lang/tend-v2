"""Ghi phiếu thu/chi, đối tượng, huỷ phiếu — đường ghi duy nhất (bot khi người nhập bấm Xác nhận; web trang Đối tượng).

Chủ dự án chốt 18/11/2026:
- Thu tiền khách: chỉ kế toán tài chính, Giám đốc. Kế toán nội bộ: hàng NCC, trả NCC, khoán tổ, trả tổ, lương công
  nhật, thu khác, chi khác.
- Phiếu chi không cần duyệt trước (ghi nhận việc đã xảy ra).
- Huỷ: người nhập tự huỷ phiếu của mình trong ngày (có lý do); phiếu cũ hơn hoặc đã xuất MISA cần Giám đốc duyệt.
  Không xoá phiếu, chỉ đánh dấu huỷ.
"""
from datetime import date
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import record_save
from core.models import Role
from core.text import fold
from projects.models import Project, projects_for

from .models import NEEDS_PROJECT, RECEIPTS, Entry, Kind, Party

MAX_DAYS_BACK = 62
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


def check_date(value):
    today = timezone.localdate()
    day = value if isinstance(value, date) else date.fromisoformat(str(value)[:10]) if value else today
    if day > today:
        raise ValidationError("Ngày phiếu không được sau hôm nay.")
    if (today - day).days > MAX_DAYS_BACK:
        raise ValidationError(f"Chỉ nhập phiếu trong {MAX_DAYS_BACK} ngày gần nhất; phiếu cũ hơn nhập trên MISA.")
    return day


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
        entry = Entry(kind=kind, project=project, date=day, number=number, party=label, party_ref=party,
                      content=str(row.get("content") or Kind(kind).label)[:500], amount=amount, method=method,
                      entered_by=user, client_key=key_)
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
