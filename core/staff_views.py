"""Nhân viên (chỉ Giám đốc): danh sách, thêm tài khoản, sửa tên/vai/số điện thoại/công trình, khoá/mở, mật khẩu tạm.
Đổi mật khẩu của chính mình: mọi người. Đường ghi: core/accounts.py (nhật ký, không ghi mật khẩu)."""
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from projects.models import Member, Project

from . import accounts
from .models import Role, User


def _director(request):
    if not request.user.is_director:
        raise PermissionDenied("Trang này dành cho Giám đốc.")


def _errors(error):
    return "; ".join(getattr(error, "messages", [str(error)]))


@login_required
@require_http_methods(["GET"])
def staff(request):
    _director(request)
    people = User.objects.order_by("-is_active", "role", "first_name", "username").prefetch_related("chat_links", "memberships__project")
    rows = [{"user": u, "linked": any(link.active for link in u.chat_links.all()),
             "projects": [m.project.code for m in u.memberships.all() if m.active]} for u in people]
    return render(request, "core/staff.html", {"rows": rows})


@login_required
@require_http_methods(["GET", "POST"])
def staff_new(request):
    _director(request)
    form = {"role": Role.SUPERVISOR}
    if request.method == "POST":
        form = request.POST
        projects = Project.objects.filter(pk__in=request.POST.getlist("cong_trinh"))
        try:
            person = accounts.create(request.user, form.get("username"), form.get("first_name"), form.get("last_name"),
                                     form.get("role"), form.get("phone"), form.get("password"), projects)
        except ValidationError as error:
            messages.error(request, _errors(error))
        else:
            messages.success(request, f"Đã thêm tài khoản {person.username}. Báo người đó đăng nhập bằng mật khẩu tạm; "
                                      "lần đầu đăng nhập hệ thống bắt đổi mật khẩu.")
            return redirect("staff")
    return render(request, "core/staff_form.html", {"form": form, "person": None, "roles": Role.choices,
                                                    "projects": Project.objects.order_by("-active", "code"),
                                                    "chosen": {int(x) for x in request.POST.getlist("cong_trinh") if x.isdigit()}})


@login_required
@require_http_methods(["GET", "POST"])
def staff_edit(request, pk):
    _director(request)
    person = get_object_or_404(User, pk=pk)
    action = request.POST.get("action", "")
    if request.method == "POST":
        try:
            if action == "save":
                accounts.update(request.user, person, request.POST.get("first_name"), request.POST.get("last_name"),
                                request.POST.get("role"), request.POST.get("phone"))
                accounts.set_projects(request.user, person, Project.objects.filter(pk__in=request.POST.getlist("cong_trinh")))
                messages.success(request, f"Đã lưu tài khoản {person.username}.")
            elif action == "password":
                accounts.reset_password(request.user, person, request.POST.get("password"))
                messages.success(request, f"Đã đặt mật khẩu tạm cho {person.username}. Lần đăng nhập tới phải đổi mật khẩu.")
            elif action == "lock":
                accounts.set_active(request.user, person, False, request.POST.get("reason"))
                messages.success(request, f"Đã khoá {person.username}: không đăng nhập được, bot ngừng làm việc với người này.")
            elif action == "unlock":
                accounts.set_active(request.user, person, True)
                messages.success(request, f"Đã mở khoá {person.username}.")
            return redirect("staff_edit", pk=person.pk)
        except ValidationError as error:
            messages.error(request, _errors(error))
        person.refresh_from_db()
    chosen = set(Member.objects.filter(user=person, active=True).values_list("project_id", flat=True))
    return render(request, "core/staff_form.html", {"form": person, "person": person, "roles": Role.choices,
                                                    "projects": Project.objects.order_by("-active", "code"), "chosen": chosen})


@login_required
@require_http_methods(["GET", "POST"])
def password(request):
    form = PasswordChangeForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        accounts.password_changed(user)
        messages.success(request, "Đã đổi mật khẩu.")
        return redirect("account")
    return render(request, "core/password.html", {"form": form, "forced": request.user.must_change_password})


class ForcePasswordChange:
    """Đăng nhập bằng mật khẩu tạm: chỉ mở được trang đổi mật khẩu (và đăng xuất) cho tới khi đổi."""
    ALLOWED = ("/doi-mat-khau/", "/dang-xuat/", "/static/", "/chat/telegram/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.must_change_password \
                and not request.path.startswith(self.ALLOWED):
            return redirect("password")
        return self.get_response(request)
