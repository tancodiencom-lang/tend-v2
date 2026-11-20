"""Ghi dữ liệu nghiệp vụ kèm nhật ký trong cùng giao dịch.

Mọi chỗ ghi (web, bot, lệnh) gọi `record_save` / `record_delete`, không gọi `.save()` trần.
"""
import threading
from decimal import Decimal
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.db.models import Model
from django.db.models.fields.files import FieldFile
from django.forms.models import model_to_dict

_local = threading.local()


class RequestMiddleware:
    """Giữ request hiện tại để nhật ký lấy IP."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _local.request = request
        try:
            return self.get_response(request)
        finally:
            _local.request = None


def client_ip():
    request = getattr(_local, "request", None)
    if request is None:
        return None
    if getattr(settings, "BEHIND_PROXY", False) and request.META.get("HTTP_X_FORWARDED_FOR"):
        return request.META["HTTP_X_FORWARDED_FOR"].split(",")[-1].strip()
    return request.META.get("REMOTE_ADDR")


def _plain(value):
    if isinstance(value, (Decimal, UUID)):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, FieldFile):   # tệp (có thể trống)
        return value.name or ""
    if isinstance(value, Model):       # trường nhiều-nhiều (model_to_dict trả bản ghi): ghi mã
        return value.pk
    return value


SECRET_FIELDS = {"password"}


def snapshot(obj):
    """Giá trị các trường để ghi nhật ký; không bao giờ ghi mật khẩu (kể cả bản băm)."""
    return {k: _plain(v) for k, v in model_to_dict(obj).items() if k not in SECRET_FIELDS}


@transaction.atomic
def record_save(obj, actor, reason="", channel="web", **save_kwargs):
    before = None
    if obj.pk:
        old = type(obj).objects.filter(pk=obj.pk).first()
        before = snapshot(old) if old else None
    obj.full_clean()
    obj.save(**save_kwargs)
    from .models import AuditLog
    AuditLog.objects.create(actor=actor, table=obj._meta.label, object_id=str(obj.pk), action="update" if before else "create",
                            before=before, after=snapshot(obj), ip=client_ip(), channel=channel, reason=reason[:300])
    return obj


@transaction.atomic
def record_delete(obj, actor, reason, channel="web"):
    from .models import AuditLog
    AuditLog.objects.create(actor=actor, table=obj._meta.label, object_id=str(obj.pk), action="delete",
                            before=snapshot(obj), after=None, ip=client_ip(), channel=channel, reason=reason[:300])
    obj.delete()
