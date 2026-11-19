"""Gọi Telegram Bot API (bot đặt trong .env). Không in mã bot ra log.

Mọi hàm gửi trả về dữ liệu Telegram hoặc None khi chưa cấu hình/lỗi mạng; lỗi chỉ ghi tên loại lỗi.
Bài kiểm thay `post` bằng hàm giả, không gọi mạng.
"""
import hashlib
import hmac
import json
import logging
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.cache import cache

log = logging.getLogger(__name__)
API = 'https://api.telegram.org'
MAX_PHOTO = 15 * 1024 * 1024


def token():
    return getattr(settings, 'TELEGRAM_TOKEN', '') or ''


def webhook_secret():
    """Chuỗi bí mật của đường nhận tin: lấy từ .env nếu có, không thì suy ra từ mã bot (HMAC).

    Ai có mã bot đã điều khiển được bot, nên chuỗi suy ra không yếu hơn; nhờ vậy không phải sửa .env trên máy
    chủ mới bật được bot. Chỉ chữ, số, gạch dưới (yêu cầu của Telegram).
    """
    configured = getattr(settings, 'TELEGRAM_WEBHOOK_SECRET', '') or ''
    if configured or not token():
        return configured
    return hmac.new(token().encode(), b'tend-telegram-webhook', hashlib.sha256).hexdigest()[:48]


def bot_username():
    """Tên bot (không có @): từ .env, không thì hỏi Telegram một lần rồi nhớ 1 ngày."""
    configured = getattr(settings, 'TELEGRAM_BOT_USERNAME', '') or ''
    if configured or not token():
        return configured
    key = 'tend-telegram-username:' + hashlib.sha256(token().encode()).hexdigest()[:16]
    name = cache.get(key)
    if name is None:
        name = (post('getMe', {}) or {}).get('username') or ''
        cache.set(key, name, 86400 if name else 300)
    return name


def post(method, data):
    key = token()
    if not key:
        return None
    try:
        body = json.dumps(data, ensure_ascii=False).encode()
        request = urllib.request.Request(f'{API}/bot{key}/{method}', data=body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode()).get('result')
    except Exception as error:   # mạng/Telegram lỗi: không làm hỏng việc đã ghi
        log.warning('Telegram %s lỗi (%s)', method, type(error).__name__)
        return None


def keyboard(buttons):
    """buttons: [[{text, data}]] → nút bấm ngay dưới tin nhắn."""
    return {'inline_keyboard': [[{'text': b['text'][:60], 'callback_data': b['data'][:64]} for b in row] for row in buttons]}


def send(chat_id, text, buttons=None, reply_to=None):
    data = {'chat_id': chat_id, 'text': text[:4000], 'disable_web_page_preview': True}
    if buttons:
        data['reply_markup'] = keyboard(buttons)
    if reply_to:
        data['reply_parameters'] = {'message_id': int(reply_to), 'allow_sending_without_reply': True}
    return post('sendMessage', data)


def ask_contact(chat_id, text):
    """Nút "Gửi số điện thoại" của chính Telegram (người dùng bấm là Telegram gửi số đã đăng ký, không gõ tay được)."""
    return post('sendMessage', {'chat_id': chat_id, 'text': text[:4000], 'reply_markup': {
        'keyboard': [[{'text': '📱 Gửi số điện thoại để xác minh', 'request_contact': True}]],
        'one_time_keyboard': True, 'resize_keyboard': True}})


def send_plain(chat_id, text):
    """Tin kèm lệnh gỡ bàn phím xin số điện thoại."""
    return post('sendMessage', {'chat_id': chat_id, 'text': text[:4000], 'reply_markup': {'remove_keyboard': True}})


def edit(chat_id, message_id, text, buttons=None):
    data = {'chat_id': chat_id, 'message_id': message_id, 'text': text[:4000], 'disable_web_page_preview': True,
            'reply_markup': keyboard(buttons or [])}
    return post('editMessageText', data)


def send_photo(chat_id, file_id, caption, buttons=None):
    """Gửi lại ảnh người dùng vừa gửi (theo mã tệp của Telegram, không tải lên lại) kèm nút chọn."""
    data = {'chat_id': chat_id, 'photo': file_id, 'caption': caption[:1000]}
    if buttons:
        data['reply_markup'] = keyboard(buttons)
    return post('sendPhoto', data)


def edit_caption(chat_id, message_id, caption):
    return post('editMessageCaption', {'chat_id': chat_id, 'message_id': message_id, 'caption': caption[:1000],
                                       'reply_markup': keyboard([])})


def answer_callback(callback_id, text=''):
    return post('answerCallbackQuery', {'callback_query_id': callback_id, 'text': text[:190]})


def download(file_id):
    """Tải ảnh người dùng gửi về bộ nhớ (≤ 15 MB). Trả (bytes, tên tệp) hoặc None."""
    info = post('getFile', {'file_id': file_id})
    if not info or not info.get('file_path') or int(info.get('file_size') or 0) > MAX_PHOTO:
        return None
    try:
        with urllib.request.urlopen(f'{API}/file/bot{token()}/{urllib.parse.quote(info["file_path"])}', timeout=30) as response:
            data = response.read(MAX_PHOTO + 1)
    except Exception as error:
        log.warning('Telegram tải ảnh lỗi (%s)', type(error).__name__)
        return None
    if len(data) > MAX_PHOTO:
        return None
    return data, info['file_path'].rsplit('/', 1)[-1]


def set_webhook(url, secret):
    return post('setWebhook', {'url': url, 'secret_token': secret, 'allowed_updates': ['message', 'callback_query', 'my_chat_member'],
                               'drop_pending_updates': False})
