"""Ghi sổ tài khoản (chỉ Giám đốc): giao dịch + nhật ký. Ô nào có dạng mật khẩu/khoá thì từ chối — lưu ở Bitwarden."""
from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.audit import record_save
from core.secretscan import find

from .models import ServiceAccount

SOON = timedelta(days=30)
TEXT_FIELDS = ("name", "login", "vault_ref", "used_by", "note")


def _director(user):
    if not (user and user.is_active and user.is_director):
        raise PermissionDenied("Chỉ Giám đốc xem và sửa sổ tài khoản.")


@transaction.atomic
def save(user, row=None, **fields):
    _director(user)
    for key in TEXT_FIELDS:
        value = " ".join(str(fields.get(key) or "").split())
        if find(value):
            raise ValidationError("Không ghi mật khẩu hay khoá vào sổ tài khoản. Lưu trong Bitwarden, ở đây chỉ ghi tên mục.")
        fields[key] = value
    if not fields.get("name"):
        raise ValidationError("Ghi tên tài khoản/dịch vụ.")
    account = ServiceAccount.objects.select_for_update().get(pk=row.pk) if row else ServiceAccount(created_by=user)
    for key, value in fields.items():
        setattr(account, key, value)
    return record_save(account, user, "Sửa sổ tài khoản" if row else "Thêm vào sổ tài khoản", channel="web")


def status(account, today=None):
    """→ (ký hiệu, chữ, cần làm) theo hạn và lịch đổi khoá."""
    today = today or timezone.localdate()
    notes, level = [], "ok"
    if account.expires_on:
        left = (account.expires_on - today).days
        if left < 0:
            notes.append(f"đã hết hạn {account.expires_on:%d/%m/%Y}")
            level = "bad"
        elif left <= SOON.days:
            notes.append(f"hết hạn {account.expires_on:%d/%m/%Y} (còn {left} ngày)")
            level = "soon" if level == "ok" else level
    if account.rotate_days:
        due = (account.rotated_on or account.created_at.date()) + timedelta(days=account.rotate_days)
        if due < today:
            notes.append(f"quá hạn đổi mật khẩu/khoá từ {due:%d/%m/%Y}")
            level = "bad"
        elif (due - today) <= timedelta(days=7):
            notes.append(f"sắp tới hạn đổi mật khẩu/khoá ({due:%d/%m/%Y})")
            level = "soon" if level == "ok" else level
    if account.two_factor == ServiceAccount.TwoFactor.NO:
        notes.append("chưa bật xác thực 2 lớp")
        level = "soon" if level == "ok" else level
    mark = {"ok": "✓ ổn", "soon": "● cần để ý", "bad": "▼ cần làm ngay"}[level]
    return level, mark, notes


def attention(today=None):
    """Tài khoản cần làm (hết hạn, quá hạn đổi khoá, sắp hết hạn, chưa bật 2 lớp) cho bản tin sáng."""
    out = []
    for account in ServiceAccount.objects.filter(active=True):
        level, mark, notes = status(account, today)
        if level != "ok":
            out.append((level, f"{mark} {account.name}: {'; '.join(notes)}" + (f" · {account.owner}" if account.owner_id else "")))
    out.sort(key=lambda row: row[0] != "bad")
    return [text for _, text in out]
