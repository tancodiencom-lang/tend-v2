"""Nhận ra mật khẩu/khoá trong chữ (tin nhắn, góp ý, sổ tài khoản) để KHÔNG lưu, không gửi sang AI, xoá tin và nhắc đổi khoá.

Thà báo nhầm một chuỗi dài lạ còn hơn để lọt khoá thật. Mật khẩu lưu ở trình quản lý mật khẩu (Bitwarden), không ở chat.
"""
import re

PATTERNS = [
    ("khoá Anthropic", r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    ("khoá OpenAI", r"(?<![\w-])sk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    ("mã GitHub", r"(?<!\w)(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("mã bot Telegram", r"(?<![\w:])\d{8,10}:[A-Za-z0-9_-]{30,50}(?![\w-])"),
    ("khoá Facebook/Meta", r"(?<!\w)EAA[A-Za-z0-9]{30,}"),
    ("khoá Google", r"(?<!\w)AIza[0-9A-Za-z_\-]{35}"),
    ("khoá AWS", r"(?<!\w)AKIA[0-9A-Z]{16}(?!\w)"),
    ("khoá Slack", r"(?<!\w)xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("khoá riêng (private key)", r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    ("mật khẩu", r"(?i)(?<!\w)(?:mật khẩu|mat khau|password|passwd|pass|pwd|mk)\s*(?:là|la|:|=)\s*\S{4,}"),
    ("chuỗi giống khoá", r"(?<![\w/.=-])(?=[A-Za-z0-9_\-]{40,})(?=\S*[a-z])(?=\S*[A-Z])(?=\S*\d)[A-Za-z0-9_\-]{40,}(?![\w/.-])"),
]
COMPILED = [(label, re.compile(pattern)) for label, pattern in PATTERNS]
MASK = "[mật khẩu/khoá đã che]"


def find(text):
    """→ danh sách loại bí mật thấy trong chữ (rỗng nếu không có)."""
    text = str(text or "")
    return list(dict.fromkeys(label for label, pattern in COMPILED if pattern.search(text)))


def mask(text):
    out = str(text or "")
    for _, pattern in COMPILED:
        out = pattern.sub(MASK, out)
    return out
