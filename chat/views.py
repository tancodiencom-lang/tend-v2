"""Đường nhận tin Telegram và trang Tài khoản › Kết nối Telegram."""
import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from . import ai, bot, telegram
from .models import ChatLink

log = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def telegram_webhook(request, secret):
    """Telegram gửi tin tới đây. Sai chuỗi bí mật (trên đường dẫn và trong header) thì 403, không đọc nội dung."""
    expected = telegram.webhook_secret()
    header = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    if not expected or not constant_time_compare(secret, expected) or not constant_time_compare(header, expected):
        return HttpResponseForbidden()
    try:
        update = json.loads(request.body.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        return HttpResponseBadRequest()
    if not isinstance(update, dict):
        return HttpResponseBadRequest()
    try:
        bot.handle_update(update)
    except Exception:   # lỗi bất ngờ: ghi log, không để Telegram gửi lại mãi; update đã ghi nhận ở trên
        log.exception('Bot Telegram lỗi khi xử lý một tin')
        _tell_failure(update)
    return JsonResponse({'ok': True})


def _tell_failure(update):
    """Báo người nhắn khi bot gặp lỗi bất ngờ (chỉ chat riêng), để họ không chờ mãi trong im lặng (21/11/2026).
    Chi tiết lỗi chỉ ở nhật ký máy chủ."""
    try:
        message = update.get('message') or (update.get('callback_query') or {}).get('message') or {}
        chat = message.get('chat') or {}
        if chat.get('id') and chat.get('type', 'private') == 'private':
            telegram.send(chat['id'], '⚠ Tend gặp lỗi khi xử lý tin này (đã ghi lại để sửa). Anh/chị gửi lại sau ít phút; '
                                      'vẫn lỗi thì báo Giám đốc.')
    except Exception:
        log.exception('Không báo được lỗi cho người nhắn')


@login_required
@require_http_methods(['GET', 'POST'])
def connect(request):
    """Lấy mã một lần để kết nối Telegram; xem và ngắt kết nối hiện có. GET không tạo dữ liệu."""
    code = ''
    if request.method == 'POST':
        if request.POST.get('action') == 'unlink':
            for row in ChatLink.objects.filter(user=request.user, active=True):
                bot.unlink(row, request.user, 'Ngắt kết nối Telegram trên web')
            messages.success(request, 'Đã ngắt kết nối Telegram.')
            return redirect('chat_connect')
        if not request.user.phone.strip():
            messages.error(request, 'Tài khoản chưa có số điện thoại nên chưa lấy mã được. Nhờ Giám đốc nhập số ở Tài khoản › Nhân viên.')
            return redirect('chat_connect')
        code = bot.new_code(request.user)
    username = telegram.bot_username()
    return render(request, 'chat/connect.html', {
        'code': code, 'bot_username': username,
        'deep_link': f'https://t.me/{username}?start={code}' if username and code else '',
        'links': ChatLink.objects.filter(user=request.user, active=True),
        'ready': bool(telegram.token() and telegram.webhook_secret()),
        'ai_ready': ai.available()})
