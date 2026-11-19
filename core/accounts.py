"""Quản lý tài khoản (chỉ Giám đốc): thêm, đổi vai/tên/số điện thoại, giao công trình, khoá/mở, đặt mật khẩu tạm.

Mỗi thao tác một giao dịch, có nhật ký (không bao giờ ghi mật khẩu hay bản băm). Không xoá tài khoản: nghỉ việc thì khoá,
lịch sử giữ nguyên. Giám đốc không tự khoá / tự hạ vai mình (tránh mất quyền quản trị).
"""
import re

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from projects.models import Member, Project

from .audit import record_save
from .models import Role, User
from .text import phone as phone_digits

USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")


def _director(actor):
    if not (actor.is_active and actor.is_director):
        raise PermissionDenied("Chỉ Giám đốc quản lý tài khoản.")


def clean_phone(number, person=None):
    number = phone_digits(number)
    if not number:
        return ""
    if not (len(number) == 10 and number.startswith("0")):
        raise ValidationError(f"Số \"{number}\" không đúng dạng số di động 10 chữ số (vd 0912345678).")
    others = User.objects.filter(is_active=True).exclude(pk=getattr(person, "pk", None))
    if any(phone_digits(u.phone) == number for u in others):
        raise ValidationError(f"Số {number} đã ghi cho tài khoản khác. Mỗi số chỉ dùng cho một người.")
    return number


def _check_password(password, person):
    if not password:
        raise ValidationError("Nhập mật khẩu tạm.")
    validate_password(password, person)


@transaction.atomic
def create(actor, username, first_name, last_name, role, phone, password, projects=()):
    _director(actor)
    username = str(username or "").strip().lower()
    if not USERNAME.match(username):
        raise ValidationError("Tên đăng nhập 3–30 ký tự: chữ thường không dấu, số, dấu chấm, gạch (vd nam.gs).")
    if User.objects.filter(username=username).exists():
        raise ValidationError(f"Tên đăng nhập \"{username}\" đã có.")
    if role not in Role.values:
        raise ValidationError("Chọn vai.")
    person = User(username=username, first_name=str(first_name or "").strip()[:150], last_name=str(last_name or "").strip()[:150],
                  role=role, must_change_password=True)
    person.phone = clean_phone(phone, person)
    _check_password(password, person)
    person.set_password(password)
    record_save(person, actor, "Giám đốc thêm tài khoản")
    set_projects(actor, person, projects)
    return person


@transaction.atomic
def update(actor, person, first_name, last_name, role, phone):
    _director(actor)
    person = User.objects.select_for_update().get(pk=person.pk)
    if role not in Role.values:
        raise ValidationError("Chọn vai.")
    if person.pk == actor.pk and role != Role.DIRECTOR and not person.is_superuser:
        raise ValidationError("Anh không tự đổi vai của mình (sẽ mất quyền quản lý tài khoản).")
    person.first_name, person.last_name = str(first_name or "").strip()[:150], str(last_name or "").strip()[:150]
    person.role, person.phone = role, clean_phone(phone, person)
    return record_save(person, actor, "Giám đốc sửa tài khoản (tên, vai, số điện thoại)")


@transaction.atomic
def set_projects(actor, person, projects):
    """Giao đúng các công trình được chọn: thêm người được giao, thôi giao công trình bỏ chọn (giữ dòng, active=False)."""
    _director(actor)
    wanted = {p.pk for p in projects}
    for project in Project.objects.filter(pk__in=wanted):
        member = Member.objects.filter(project=project, user=person).first()
        if member is None:
            record_save(Member(project=project, user=person), actor, "Giao công trình")
        elif not member.active:
            member.active = True
            record_save(member, actor, "Giao lại công trình")
    for member in Member.objects.filter(user=person, active=True).exclude(project_id__in=wanted):
        member.active = False
        record_save(member, actor, "Thôi giao công trình")


@transaction.atomic
def set_active(actor, person, active, reason=""):
    _director(actor)
    person = User.objects.select_for_update().get(pk=person.pk)
    if person.pk == actor.pk:
        raise ValidationError("Anh không tự khoá tài khoản của mình.")
    if not active and not str(reason or "").strip():
        raise ValidationError("Khoá tài khoản cần ghi lý do (vd nghỉ việc).")
    person.is_active = bool(active)
    return record_save(person, actor, (f"Khoá tài khoản: {reason}" if not active else "Mở khoá tài khoản")[:300])


@transaction.atomic
def reset_password(actor, person, password):
    _director(actor)
    person = User.objects.select_for_update().get(pk=person.pk)
    _check_password(password, person)
    person.set_password(password)
    person.must_change_password = True
    return record_save(person, actor, "Giám đốc đặt mật khẩu tạm")


@transaction.atomic
def password_changed(person):
    """Sau khi người dùng tự đổi mật khẩu (form Django đã lưu mật khẩu mới)."""
    person = User.objects.select_for_update().get(pk=person.pk)
    person.must_change_password = False
    return record_save(person, person, "Tự đổi mật khẩu")
