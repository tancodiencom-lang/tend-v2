"""Khung chung: đăng nhập, thanh mục theo vai."""
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.decorators.http import require_GET

from .avatar import initials
from .models import User


def shell(request):
    """Mục điều hướng theo vai. Nhập liệu làm qua chat; web chỉ xem (trừ nhập bản vẽ trên máy tính)."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    tabs = [{"key": "overview", "label": "Tổng quan", "url": reverse("overview")}]
    if user.is_director or user.role in ("supervisor", "designer"):
        tabs.append({"key": "drawings", "label": "Bản vẽ", "url": reverse("drawings_index")})
    if user.sees_payables:
        tabs.append({"key": "field_attendance", "label": "Công", "url": reverse("field_attendance")})
    # Báo cáo: mọi vai (phần tiền theo quyền)
    tabs.append({"key": "reports", "label": "Báo cáo", "url": reverse("reports"), "match": ("reports", "money_")})
    tabs.append({"key": "assistant", "label": "Trợ lý", "url": reverse("assistant")})
    role = "Giám đốc" if user.is_superuser else user.get_role_display()
    tabs.append({"key": "account", "label": user.first_name or user.get_full_name() or user.username, "role": role,
                 "url": reverse("account"), "person": True,
                 "avatar": reverse("user_avatar", args=[user.pk]) if user.avatar else "",
                 "initials": initials(user), "title": f"{user} · {role} · đăng nhập {user.username}"})
    match = getattr(request, "resolver_match", None)
    current = (match.url_name or "") if match else ""
    for tab in tabs:
        tab["active"] = any(current.startswith(key) for key in tab.get("match", (tab["key"],)))
    return {"tabs": tabs, "role_label": "Giám đốc" if user.is_superuser else user.get_role_display(), "initials": initials(user)}


class Login(auth_views.LoginView):
    template_name = "core/login.html"
    redirect_authenticated_user = True


def home(request):
    return redirect("overview")


@login_required
@require_GET
def avatar(request, pk):
    """Ảnh nhân viên: người đã đăng nhập xem được ảnh đồng nghiệp (không có số liệu nghiệp vụ)."""
    person = get_object_or_404(User, pk=pk, is_active=True)
    if not person.avatar:
        raise Http404
    try:
        response = FileResponse(person.avatar.open("rb"), content_type="image/jpeg")
    except FileNotFoundError:
        raise Http404
    response["Cache-Control"] = "private, max-age=3600"
    return response



@login_required
def staff(request):
    """Nhân viên (chỉ Giám đốc): số điện thoại dùng xác minh Telegram và tình trạng kết nối. Sửa số có nhật ký."""
    from django.contrib import messages
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import render

    from .audit import record_save
    from .text import phone as phone_digits
    if not request.user.is_director:
        raise PermissionDenied("Trang này dành cho Giám đốc.")
    if request.method == "POST":
        person = get_object_or_404(User, pk=request.POST.get("nguoi"))
        number = phone_digits(request.POST.get("dien_thoai"))
        if number and not (len(number) == 10 and number.startswith("0")):
            messages.error(request, f"Số \"{request.POST.get('dien_thoai')}\" không đúng dạng số di động 10 chữ số (vd 0912345678).")
        elif number and any(phone_digits(u.phone) == number for u in User.objects.filter(is_active=True).exclude(pk=person.pk)):
            messages.error(request, f"Số {number} đã ghi cho tài khoản khác. Mỗi số chỉ dùng cho một người.")
        elif number != phone_digits(person.phone):
            person.phone = number
            record_save(person, request.user, "Giám đốc sửa số điện thoại (xác minh Telegram)")
            messages.success(request, f"Đã lưu số của {person}." + (" Người này cần kết nối Telegram lại nếu đổi số Telegram." if number else ""))
        return redirect("staff")
    people = (User.objects.filter(is_active=True).order_by("role", "first_name", "username")
              .prefetch_related("chat_links"))
    rows = [{"user": u, "linked": any(link.active for link in u.chat_links.all())} for u in people]
    return render(request, "core/staff.html", {"rows": rows})
