"""Trang Nội dung (Giám đốc, vai Marketing): bài chờ duyệt / đã đăng, sửa chữ, đổi ảnh, duyệt, ý tưởng, kho ảnh, Trang đăng,
giọng văn. Mọi thao tác đi qua `services` (giao dịch + nhật ký kênh web), quyền kiểm ở máy chủ."""
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from projects.models import Project

from . import assets as asset_files
from . import bot, publish, services, writer
from .models import Asset, Channel, Idea, Post, PostTarget, StyleSample


def _marketing(request):
    if not request.user.does_marketing:
        raise PermissionDenied("Trang Nội dung dành cho Giám đốc và nhân viên Marketing.")


def _say(request, call, done):
    try:
        result = call()
    except (PermissionDenied, ValidationError, ValueError) as error:
        messages.error(request, "; ".join(getattr(error, "messages", [str(error)])))
        return None
    if done:
        messages.success(request, done)
    return result


@login_required
@require_GET
def index(request):
    _marketing(request)
    since = timezone.localdate() - timedelta(days=30)
    rows = Post.objects.prefetch_related("items__asset", "channels", "targets__channel").select_related("created_by", "approved_by")
    return render(request, "marketing/index.html", {
        "waiting": rows.filter(state__in=(Post.State.DRAFT, Post.State.FAILED, Post.State.UNKNOWN)).order_by("for_date", "pk"),
        "going": rows.filter(state__in=(Post.State.APPROVED, Post.State.PUBLISHING)),
        "done": rows.filter(state=Post.State.PUBLISHED, for_date__gte=since).order_by("-for_date", "-pk")[:40],
        "ideas": Idea.objects.filter(state=Idea.State.OPEN).select_related("created_by", "project"),
        "projects": Project.objects.filter(active=True).order_by("code"),
        "channels": Channel.objects.filter(active=True),
        "asset_count": Asset.objects.filter(blocked=False).count(),
        "sample_count": StyleSample.objects.filter(active=True).count(),
    })


@login_required
@require_GET
def post_detail(request, pk):
    _marketing(request)
    post = get_object_or_404(Post.objects.prefetch_related("channels"), pk=pk)
    return render(request, "marketing/post.html", {
        "post": post, "items": post.items.select_related("asset__project").order_by("order"),
        "channels": Channel.objects.filter(active=True), "chosen": set(post.channels.values_list("pk", flat=True)),
        "targets": post.targets.select_related("channel").prefetch_related("steps").order_by("-pk"),
        "editable": post.state in services.EDITABLE, "anchor": bot.anchor_text(post),
    })


@login_required
@require_POST
def post_action(request, pk, action):
    _marketing(request)
    post = get_object_or_404(Post, pk=pk)
    user, data = request.user, request.POST
    index = int(data.get("i", -1)) if str(data.get("i", "")).lstrip("-").isdecimal() else -1
    calls = {
        "sua": (lambda: services.set_text(user, post, data.get("text", ""), channel="web"), "Đã lưu chữ mới (cần duyệt lại)."),
        "ai-sua": (lambda: _ai_revise(user, post, data.get("instruction", "")), "Trợ lý đã sửa theo ý (cần duyệt lại)."),
        "duyet": (lambda: services.approve(user, post, data.get("sha", ""), channel="web"),
                  "Đã duyệt. Trợ lý đăng trong vài phút và báo đường dẫn qua Telegram."),
        "bo": (lambda: services.reject(user, post, data.get("reason", ""), channel="web"), "Đã bỏ bài."),
        "doi-anh": (lambda: services.swap_asset(user, post, index, block=data.get("block") == "1", channel="web"), "Đã đổi ảnh."),
        "bot-anh": (lambda: services.remove_asset(user, post, index, channel="web"), "Đã bớt ảnh."),
        "len-dau": (lambda: services.move_first(user, post, index, channel="web"), "Đã đưa ảnh lên đầu."),
        "kenh": (lambda: services.toggle_channel(user, post, get_object_or_404(Channel, pk=data.get("channel")), channel="web"),
                 "Đã đổi Trang đăng."),
        "thu-lai": (lambda: publish.retry(user, post, via="web"), "Bài sẽ đăng lại trong vài phút."),
        "doi-soat": (lambda: publish.resolve(user, get_object_or_404(PostTarget, pk=data.get("target"), post=post),
                                             data.get("published") == "1", via="web"), "Đã ghi kết quả đối soát."),
    }
    if action not in calls:
        raise Http404
    call, done = calls[action]
    _say(request, call, done)
    return redirect("marketing_post", pk=post.pk)


def _ai_revise(user, post, instruction):
    text = writer.revise(post, instruction)
    if text is None:
        raise ValidationError("Trợ lý AI chưa bật hoặc đang lỗi. Sửa chữ trực tiếp ở ô bên trên.")
    return services.set_text(user, post, text, channel="web")


@login_required
@require_POST
def idea_add(request):
    _marketing(request)
    data = request.POST
    project = Project.objects.filter(pk=data.get("project") or 0).first()
    kind = Idea.Kind.REQUEST if data.get("now") == "1" else Idea.Kind.IDEA
    idea = _say(request, lambda: services.add_idea(request.user, data.get("text", ""), kind, project, channel="web"),
                "Đã ghi ý tưởng." if kind == Idea.Kind.IDEA else "")
    if idea is not None and kind == Idea.Kind.REQUEST:
        post = writer.draft(timezone.localdate(), project=project, idea=idea, brief=idea.text, origin=Post.Origin.REQUEST,
                            user=request.user)
        if post is None:
            messages.error(request, "Kho chưa có ảnh phù hợp để soạn bài. Tải ảnh lên Kho ảnh rồi giao lại.")
            return redirect("marketing")
        bot.send_preview(post)
        messages.success(request, f"Trợ lý đã soạn bài #{post.pk}, gửi người duyệt qua Telegram.")
        return redirect("marketing_post", pk=post.pk)
    return redirect("marketing")


@login_required
@require_POST
def idea_drop(request, pk):
    _marketing(request)
    _say(request, lambda: services.drop_idea(request.user, get_object_or_404(Idea, pk=pk)), "Đã bỏ ý tưởng.")
    return redirect("marketing")


@login_required
def asset_list(request):
    _marketing(request)
    if request.method == "POST":
        project = Project.objects.filter(pk=request.POST.get("project") or 0).first()
        added = 0
        for upload in request.FILES.getlist("images")[:30]:
            if asset_files.add_upload(request.user, upload.read(), upload.name, project, request.POST.get("context", "")):
                added += 1
        messages.success(request, f"Đã thêm {added} ảnh vào kho (ảnh trùng hoặc không phải ảnh được bỏ qua).")
        return redirect("marketing_assets")
    rows = Asset.objects.select_related("project").order_by("-taken_on", "-pk")
    project = Project.objects.filter(code=request.GET.get("cong_trinh", "")).first()
    if project:
        rows = rows.filter(project=project)
    if request.GET.get("bo") == "1":
        rows = rows.filter(blocked=True)
    else:
        rows = rows.filter(blocked=False)
    return render(request, "marketing/assets.html", {"rows": rows[:120], "project": project, "used": writer.used_ids(),
                                                      "projects": Project.objects.filter(active=True).order_by("code"),
                                                      "blocked": request.GET.get("bo") == "1"})


@login_required
@require_POST
def asset_toggle(request, pk):
    _marketing(request)
    asset = get_object_or_404(Asset, pk=pk)
    if asset.blocked:
        _say(request, lambda: services.unblock_asset(request.user, asset), "Đã dùng lại ảnh.")
    else:
        _say(request, lambda: services.block_asset(request.user, asset, request.POST.get("reason", ""), channel="web"),
             "Đã loại ảnh khỏi bài đăng.")
    return redirect(request.POST.get("next") or "marketing_assets")


@login_required
@require_GET
def asset_image(request, pk):
    _marketing(request)
    asset = get_object_or_404(Asset, pk=pk)
    size = min(max(int(request.GET.get("w", 480)) if request.GET.get("w", "").isdecimal() else 480, 120), 1600)
    data = asset_files.thumbnail(asset, size, 82)
    if data is None:
        raise Http404
    response = HttpResponse(data, content_type="image/jpeg")
    response["Cache-Control"] = "private, max-age=3600"
    return response


@login_required
def settings_page(request):
    """Trang đăng (Giám đốc thêm/sửa/kiểm tra) và giọng văn (Marketing thêm mẫu, lấy bài cũ của Trang)."""
    _marketing(request)
    user, data = request.user, request.POST
    if request.method == "POST":
        action = data.get("action")
        if action == "channel":
            row = Channel.objects.filter(pk=data.get("id") or 0).first()
            _say(request, lambda: services.save_channel(user, row, name=data.get("name", ""), page=data.get("page", ""),
                                                        account=data.get("account", ""), default=data.get("default") == "1",
                                                        active=data.get("active", "1") == "1"), "Đã lưu Trang.")
        elif action == "check":
            row = _say(request, lambda: services.check_channel(user, get_object_or_404(Channel, pk=data.get("id"))), "")
            if row is not None:
                (messages.success if row.check_note.startswith("✓") else messages.error)(request, row.check_note)
        elif action == "import":
            count = _say(request, lambda: services.import_samples(user, get_object_or_404(Channel, pk=data.get("id"))), "")
            if count is not None:
                messages.success(request, f"Đã lấy {count} bài cũ của Trang làm mẫu giọng văn.")
        elif action == "sample":
            _say(request, lambda: services.add_sample(user, data.get("text", "")), "Đã thêm bài mẫu.")
        elif action == "toggle-sample":
            _say(request, lambda: services.toggle_sample(user, get_object_or_404(StyleSample, pk=data.get("id"))), "Đã lưu.")
        return redirect("marketing_settings")
    return render(request, "marketing/settings.html", {"channels": Channel.objects.all(),
                                                        "samples": StyleSample.objects.all()[:60]})
