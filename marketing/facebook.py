"""Gọi Facebook Graph API cho Trang. Khoá chỉ trong .env, không lưu CSDL, không in ra log.

- Tài khoản Meta: TEND_META_TOKEN (mặc định) hoặc TEND_META_TOKEN_<TÊN> (nhiều tài khoản). Khoá người dùng dài hạn hoặc
  khoá hệ thống (Business) có quyền pages_manage_posts, pages_read_engagement, pages_show_list trên các Trang.
- Khoá của Trang lấy lúc cần (GET /{trang}?fields=access_token), chỉ giữ trong bộ nhớ vài phút.
- Bài nhiều ảnh: tải từng ảnh ở trạng thái chưa đăng (POST /{trang}/photos published=false), rồi tạo MỘT bài
  POST /{trang}/feed với attached_media. Ảnh chưa đăng chỉ sống khoảng 24 giờ.
- Lỗi chia hai loại để sổ đăng quyết định đúng: `sent=False` chắc chắn chưa tới Facebook (thử lại an toàn);
  `sent=True` có thể Facebook đã làm (hết giờ chờ sau khi gửi) → không gửi lại lệnh tạo bài, phải đối soát.
Bài kiểm thay `request` bằng hàm giả, không gọi mạng.
"""
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

log = logging.getLogger(__name__)
GRAPH = "https://graph.facebook.com"
VERSION = "v26.0"            # v26.0 ra 29/7/2026; v20.0 gỡ 24/9/2026, v21.0 gỡ 21/1/2027 (bot cũ đã kiểm 14/10/2026)
PAGE_TOKEN_TTL = 600
_page_tokens = {}


class FacebookError(Exception):
    def __init__(self, message, status=None, sent=False):
        super().__init__(message)
        self.status, self.sent = status, sent


def account_token(account=""):
    name = "TEND_META_TOKEN" + (f"_{account.strip().upper()}" if account and account.strip() else "")
    return os.environ.get(name, "").strip()


def _multipart(fields, files):
    boundary = uuid4().hex
    out = []
    for key, value in fields.items():
        out += [f"--{boundary}".encode(), f'Content-Disposition: form-data; name="{key}"'.encode(), b"", str(value).encode()]
    for key, (filename, data, mime) in files.items():
        out += [f"--{boundary}".encode(), f'Content-Disposition: form-data; name="{key}"; filename="{filename}"'.encode(),
                f"Content-Type: {mime}".encode(), b"", data]
    out += [f"--{boundary}--".encode(), b""]
    return b"\r\n".join(out), f"multipart/form-data; boundary={boundary}"


def request(method, path, params=None, token="", files=None):
    """Một lời gọi Graph API → dict. Lỗi → FacebookError (không kèm khoá)."""
    params = dict(params or {})
    params["access_token"] = token
    url = f"{GRAPH}/{VERSION}/{path.lstrip('/')}"
    if method == "GET":
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), method="GET")
    elif files:
        body, ctype = _multipart(params, files)
        req = urllib.request.Request(url, data=body, method="POST", headers={"Content-Type": ctype})
    else:
        req = urllib.request.Request(url, data=urllib.parse.urlencode(params).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as error:       # Facebook trả lỗi: lệnh không thực hiện (5xx: chưa chắc)
        try:
            detail = json.loads(error.read().decode()).get("error", {})
            message = f"{detail.get('type', 'Lỗi')} {detail.get('code', '')}: {detail.get('message', '')}".strip()
        except Exception:
            message = f"HTTP {error.code}"
        raise FacebookError(message[:400], error.code, sent=error.code >= 500)
    except (socket.timeout, TimeoutError, ConnectionResetError) as error:   # đã gửi, không rõ Facebook làm chưa
        raise FacebookError(f"Hết giờ chờ Facebook ({type(error).__name__})", sent=True)
    except urllib.error.URLError as error:        # không kết nối được: chưa gửi
        sent = isinstance(error.reason, (socket.timeout, TimeoutError))
        raise FacebookError(f"Không kết nối được Facebook ({type(error.reason).__name__})", sent=sent)


def page_token(channel):
    """Khoá Trang (giữ trong bộ nhớ 10 phút) + mã số Trang. Khoá trong .env là khoá Trang thì dùng luôn."""
    key = (channel.account or "", channel.page_id or channel.page)
    cached = _page_tokens.get(key)
    if cached and cached[2] > time.monotonic():
        return cached[0], cached[1]
    token = account_token(channel.account)
    if not token:
        name = "TEND_META_TOKEN" + (f"_{channel.account.upper()}" if channel.account else "")
        raise FacebookError(f"Chưa có khoá {name} trong .env trên máy chủ.")
    data = request("GET", channel.page_id or channel.page, {"fields": "id,name,access_token"}, token)
    result = (data.get("access_token") or token, str(data.get("id") or channel.page_id or channel.page))
    _page_tokens[key] = (*result, time.monotonic() + PAGE_TOKEN_TTL)
    return result


def check(channel):
    """Kiểm tra kết nối: → {"page_id", "name"}; lỗi → FacebookError."""
    token = account_token(channel.account)
    if not token:
        raise FacebookError("Chưa có khoá Meta trong .env cho tài khoản này.")
    data = request("GET", channel.page_id or channel.page, {"fields": "id,name"}, token)
    page_token(channel)
    return {"page_id": str(data.get("id") or ""), "name": data.get("name") or ""}


def upload_photo(page_id, token, filename, data, mime="image/jpeg"):
    """Tải một ảnh ở trạng thái CHƯA ĐĂNG → mã ảnh."""
    result = request("POST", f"{page_id}/photos", {"published": "false"}, token, files={"source": (filename, data, mime)})
    if not result.get("id"):
        raise FacebookError("Facebook không trả mã ảnh.", sent=True)
    return str(result["id"])


def create_post(page_id, token, message, photo_ids):
    params = {"message": message}
    for index, photo in enumerate(photo_ids):
        params[f"attached_media[{index}]"] = json.dumps({"media_fbid": photo})
    result = request("POST", f"{page_id}/feed", params, token)
    if not result.get("id"):
        raise FacebookError("Facebook không trả mã bài.", sent=True)
    return str(result["id"])


def read_post(post_id, token):
    return request("GET", post_id, {"fields": "message,permalink_url,attachments{subattachments}"}, token)


def recent_posts(page_id, token, since_ts, limit=25):
    """Bài của Trang từ thời điểm `since_ts` (để đối soát bài có thể đã tạo)."""
    data = request("GET", f"{page_id}/feed", {"fields": "id,message,created_time,permalink_url", "since": int(since_ts),
                                               "limit": limit}, token)
    return data.get("data") or []


def page_posts(page_id, token, limit=30):
    """Bài cũ của Trang (làm mẫu giọng văn)."""
    data = request("GET", f"{page_id}/posts", {"fields": "id,message,created_time", "limit": limit}, token)
    return [p for p in data.get("data") or [] if (p.get("message") or "").strip()]
