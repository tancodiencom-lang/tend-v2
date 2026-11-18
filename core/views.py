"""Khung chung: đăng nhập, thanh mục theo vai."""
from django.contrib.auth import views as auth_views
from django.shortcuts import redirect
from django.urls import reverse


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
    if user.sees_payables or user.sees_receivables:
        tabs.append({"key": "reports", "label": "Báo cáo", "url": reverse("reports")})
    tabs.append({"key": "account", "label": "Tài khoản", "url": reverse("account")})
    match = getattr(request, "resolver_match", None)
    current = (match.url_name or "") if match else ""
    for tab in tabs:
        tab["active"] = current.startswith(tab["key"])
    return {"tabs": tabs, "role_label": "Giám đốc" if user.is_superuser else user.get_role_display()}


class Login(auth_views.LoginView):
    template_name = "core/login.html"
    redirect_authenticated_user = True


def home(request):
    return redirect("overview")
