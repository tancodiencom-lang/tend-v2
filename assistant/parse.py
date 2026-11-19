"""Đọc chỉ đạo giao việc không cần AI.

    /giao Nam gửi bản vẽ điện tầng 2 TN-22 hạn 20/11 9:00 - đủ mặt bằng ổ cắm, công tắc
    /giao @nam_tk gọi lại khách Thảo hạn mai

Người nhận: @tên đăng nhập, hoặc tên (một tới bốn chữ đầu) khớp tên/họ tên/tên đăng nhập. Hạn: "hạn dd/mm[/yyyy] [hh:mm]",
"hạn hôm nay", "hạn mai"; không ghi giờ thì 17:00. Tiêu chí xong: sau dấu " - ".
"""
import re
from datetime import datetime, time, timedelta

from django.utils import timezone

from core.text import fold

DEFAULT_TIME = time(17, 0)
DUE = re.compile(r"\b(?:hạn|han)\s+(hôm nay|hom nay|ngày mai|ngay mai|mai|\d{1,2}/\d{1,2}(?:/\d{2,4})?)"
                 r"(?:\s+(?:lúc\s+|luc\s+)?(\d{1,2})(?:[:hg](\d{2})?)?)?(?=\s|$|[,.;])", re.I)
CODE = re.compile(r"\b([A-Za-z]{1,4}-\d{1,4})\b")


def _names(user):
    full = " ".join(filter(None, [user.last_name, user.first_name]))
    return {fold(user.username), fold(user.first_name), fold(full), fold(user.get_full_name())} - {""}


def find_owner(words, users):
    """(người, số chữ đã dùng, ứng viên khi mơ hồ)."""
    if words and words[0].startswith("@"):
        name = fold(words[0][1:])
        found = [u for u in users if fold(u.username) == name]
        return (found[0], 1, []) if len(found) == 1 else (None, 0, found)
    for count in range(min(4, len(words)), 0, -1):
        prefix = fold(" ".join(words[:count]))
        found = [u for u in users if prefix in _names(u)]
        if len(found) == 1:
            return found[0], count, []
        if len(found) > 1:
            return None, 0, found
    return None, 0, []


def due_from(match, now):
    word, hour, minute = match.group(1), match.group(2), match.group(3)
    today = timezone.localtime(now).date()
    folded = fold(word)
    if folded in ("hom nay",):
        day = today
    elif folded in ("mai", "ngay mai"):
        day = today + timedelta(days=1)
    else:
        parts = [int(p) for p in word.split("/")]
        year = parts[2] if len(parts) == 3 else today.year
        year = year + 2000 if year < 100 else year
        day = datetime(year, parts[1], parts[0]).date()
        if len(parts) == 2 and day < today - timedelta(days=60):
            day = day.replace(year=year + 1)          # "hạn 05/01" gõ vào tháng 12 là năm sau
    clock = time(int(hour), int(minute or 0)) if hour is not None else DEFAULT_TIME
    return timezone.make_aware(datetime.combine(day, clock))


def task(text, users, project_codes, now=None):
    """→ dict(owner, title, due, project_code, criteria) hoặc dict(error=…)."""
    now = now or timezone.now()
    body = text.strip()
    body = re.sub(r"^/giao\b", "", body).strip()
    words = body.split()
    if not words:
        return {"error": "Cú pháp: /giao <người> <việc> [TN-22] hạn 20/11 [9:00] [- tiêu chí xong]"}
    owner, used, candidates = find_owner(words, users)
    if owner is None:
        if candidates:
            names = "; ".join(f"{u} (@{u.username}, {u.get_role_display().lower()})" for u in candidates[:6])
            return {"error": f"Có nhiều người tên \"{words[0]}\": {names}. Ghi @tên đăng nhập, ví dụ /giao @{candidates[0].username} …"}
        return {"error": f"Không thấy người tên \"{words[0]}\". Ghi tên hoặc @tên đăng nhập của người nhận việc."}
    rest = " ".join(words[used:])
    rest, _, criteria = rest.partition(" - ")
    match = DUE.search(rest)
    if not match:
        return {"error": "Việc giao cần có hạn: thêm \"hạn 20/11\" (17:00), \"hạn 20/11 9:00\", \"hạn hôm nay\" hoặc \"hạn mai\"."}
    try:
        due = due_from(match, now)
    except ValueError:
        return {"error": f"Ngày hạn \"{match.group(1)}\" không đúng."}
    rest = (rest[:match.start()] + rest[match.end():]).strip()
    code = None
    found = CODE.search(rest)
    if found and found.group(1).upper() in {c.upper() for c in project_codes}:
        code = next(c for c in project_codes if c.upper() == found.group(1).upper())
        rest = (rest[:found.start()] + rest[found.end():]).strip()
    title = " ".join(rest.strip(" ,.;").split())
    if not title:
        return {"error": "Ghi rõ việc cần làm sau tên người nhận."}
    return {"owner": owner, "title": title, "due": due, "project_code": code, "criteria": criteria.strip()}
