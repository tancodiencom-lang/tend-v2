"""Sổ tài khoản (trợ lý IT) — chỉ Giám đốc. Không có ô mật khẩu; mật khẩu ở Bitwarden."""
from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render

from core.models import User

from . import services
from .models import ServiceAccount


def _date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        raise ValidationError("Ngày không hợp lệ.")


@login_required
def book(request):
    if not request.user.is_director:
        raise PermissionDenied("Chỉ Giám đốc xem sổ tài khoản.")
    if request.method == "POST":
        data = request.POST
        row = ServiceAccount.objects.filter(pk=data.get("id") or 0).first()
        try:
            services.save(request.user, row, name=data.get("name"), kind=data.get("kind") if data.get("kind") in ServiceAccount.Kind.values
                          else ServiceAccount.Kind.OTHER, login=data.get("login"), vault_ref=data.get("vault_ref"),
                          used_by=data.get("used_by"), note=data.get("note"),
                          owner=User.objects.filter(pk=data.get("owner") or 0, is_active=True).first(),
                          two_factor=data.get("two_factor") if data.get("two_factor") in ServiceAccount.TwoFactor.values else "?",
                          expires_on=_date(data.get("expires_on")), rotated_on=_date(data.get("rotated_on")),
                          rotate_days=int(data["rotate_days"]) if (data.get("rotate_days") or "").isdecimal() else None,
                          active=data.get("active", "1") == "1")
        except (ValidationError, PermissionDenied) as error:
            messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
        else:
            messages.success(request, "Đã lưu sổ tài khoản.")
        return redirect("itdesk")
    rows = [{"row": a, "status": services.status(a)} for a in ServiceAccount.objects.select_related("owner")]
    return render(request, "itdesk/book.html", {"rows": rows, "kinds": ServiceAccount.Kind.choices,
                                                "factors": ServiceAccount.TwoFactor.choices,
                                                "people": User.objects.filter(is_active=True).order_by("first_name")})
