"""Trợ lý luôn xưng "em", không bao giờ tự gọi là "bot" với người dùng (chủ dự án 21/11/2026).

Quét mọi chuỗi chữ trong mã (trừ docstring, bài kiểm, migration) và mọi trang web: không có từ "bot" đứng riêng, trừ các chỗ
thuần kỹ thuật liệt kê dưới đây (tên loại khoá, nhật ký máy chủ, lệnh quản trị, tên biến trong chuỗi định dạng).
"""
import ast
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

WORD = re.compile(r"\bbot\b", re.I)
ALLOWED = (
    ("chat/ai.py", 'tự gọi mình là "bot"'),                    # chính câu dặn AI không tự gọi là bot
    ("chat/group_bot.py", "@{bot}"),                            # tên tài khoản Telegram điền vào lúc chạy
    ("chat/views.py", "Bot Telegram lỗi khi xử lý một tin"),    # nhật ký máy chủ
    ("chat/management/commands/telegram_webhook.py", ""),      # lệnh quản trị chạy trên máy chủ
    ("core/secretscan.py", "mã bot Telegram"),                  # tên loại khoá
    ("itdesk/models.py", "mã bot"),                             # tên loại khoá trong sổ tài khoản
    ("marketing/bot.py", "bot "),                               # chữ "bớt" viết không dấu trong ý sửa bài
)
TEMPLATE_ALLOWED = ("bot_username", "bot-anh")


def _docstrings(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                out.add(id(first.value))
    return out


class WordingTests(SimpleTestCase):
    def test_assistant_never_calls_itself_bot(self):
        root = Path(settings.BASE_DIR)
        found = []
        for path in sorted(root.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if rel.startswith((".venv/", "venv/")) or "/migrations/" in rel or path.name.startswith("tests"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            skip = _docstrings(tree)
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in skip:
                    continue
                text = node.value
                if not WORD.search(text) or re.fullmatch(r"[\w./:{}%-]*", text):
                    continue                      # tên lệnh, đường dẫn, khoá kỹ thuật như "chat_bot", "/bot"
                if any(rel == f and part in text for f, part in ALLOWED):
                    continue
                found.append(f"{rel}:{node.lineno}: {text[:80]!r}")
        for path in sorted((root / "templates").rglob("*.html")):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                cleaned = line
                for part in TEMPLATE_ALLOWED:
                    cleaned = cleaned.replace(part, "")
                if WORD.search(cleaned):
                    found.append(f"{path.relative_to(root)}:{number}: {line.strip()[:80]!r}")
        self.assertEqual(found, [], "Trợ lý xưng \"em\" (tin Telegram) hoặc \"trợ lý\" (trang web), không gọi \"bot\":\n"
                         + "\n".join(found))
