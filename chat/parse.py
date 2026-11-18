"""Cú pháp ngắn khi chưa bật trợ lý AI (hoặc đã chạm trần chi phí).

    TN-22 T2.5 40% S2.1 xong. Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ
    /suacong TN-22 17/11 Hùng 0,5 lý do về sớm
    /dongia Hùng 450000 khoán
"""
import re
from datetime import date

from django.utils import timezone

from core.text import fold, number

LINE = re.compile(r"(?<![\w.-])([A-Za-z]{1,3}[0-9M]\d*(?:\.\d+)+)\s*[:=]?\s*(?:(\d{1,3}(?:[.,]\d+)?)\s*%|(xong|hoan thanh|hoàn thành))", re.I)
PEOPLE_START = re.compile(r"\b(?:cong|công|cham cong|chấm công)\s*:", re.I)
TEAM = re.compile(r"^\s*(?:to|tổ)\s+([^:]{1,40}):\s*", re.I)
HALF = re.compile(r"\b(?:nua ngay|nửa ngày|1/2|0[.,]5\s*(?:cong|công)?)\s*$", re.I)
HOURS = re.compile(r"\b(\d{1,2}(?:[.,]\d+)?)\s*(?:h|gio|giờ|tieng|tiếng)\s*$", re.I)
DAYS = re.compile(r"\b(\d(?:[.,]\d+)?)\s*(?:cong|công)\s*$", re.I)


def find_project(text, projects):
    found = next((p for p in projects if re.search(rf"(?<![\w-]){re.escape(p.code)}(?![\w-])", text, re.I)), None)
    return found or (projects[0] if len(projects) == 1 else None)


def people(text):
    """"Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ" → [{name, team, days|hours}]."""
    m = PEOPLE_START.search(text)
    if not m:
        return []
    out = []
    for group in re.split(r"[;\n]", text[m.end():]):
        team = ""
        t = TEAM.match(group)
        if t:
            team, group = " ".join(t.group(1).split()), group[t.end():]
        for part in re.split(r",|\bva\b|\bvà\b", group):
            part = part.strip(" .")
            if not part:
                continue
            entry = {"team": team}
            for pattern, key in ((HALF, None), (HOURS, "hours"), (DAYS, "days")):
                hit = pattern.search(part)
                if hit:
                    entry.update({"days": 0.5} if key is None else {key: float(number(hit.group(1)))})
                    part = part[:hit.start()].strip()
                    break
            if part:
                entry["name"] = " ".join(part.split())
                out.append(entry)
    return out


def report(text, projects):
    project = find_project(text, projects)
    if project is None:
        return None
    body = PEOPLE_START.split(text, maxsplit=1)[0]
    lines = []
    for m in LINE.finditer(body):
        if m.group(1).casefold() == project.code.casefold():
            continue
        lines.append({"code": m.group(1), "percent": 100.0 if m.group(3) else float(number(m.group(2)))})
    who = people(text)
    if not lines and not who:
        return None
    return {"project_code": project.code, "lines": lines, "people": who, "note": text.strip()[:2000]}


def day(text, today=None):
    today = today or timezone.localdate()
    d, m, *y = [int(x) for x in text.split("/")]
    return date(y[0] if y else today.year, m, d)


FIX = re.compile(r"^/suacong\s+(\S+)\s+(\d{1,2}/\d{1,2}(?:/\d{4})?)\s+(.+?)\s+(\d+(?:[.,]\d+)?)\s*(?:cong|công)?\s+"
                 r"(?:(?:ly do|lý do|vi|vì)\s*:?\s*)?(.+)$", re.I)
RATE = re.compile(r"^/dongia\s+(.+?)\s+([\d.,]+)\s*(?:d|đ|₫)?\s*(khoan|khoán|cong nhat|công nhật)?\s*$", re.I)


def fix(text):
    m = FIX.match(text.strip())
    if not m:
        return None
    return {"project_code": m.group(1), "work_date": day(m.group(2)).isoformat(), "name": m.group(3).strip(),
            "days": float(number(m.group(4))), "reason": m.group(5).strip()}


def rate(text):
    m = RATE.match(text.strip())
    if not m or number(m.group(2)) is None:
        return None
    pay = None
    if m.group(3):
        pay = "khoan" if fold(m.group(3)).startswith("khoan") else "cong_nhat"
    return {"name": m.group(1).strip(), "day_rate": number(m.group(2)), "pay": pay}


# ---------- phiếu thu/chi ----------
# <loại> <số tiền> <đối tượng> [TN-22] [PC12] [ck|tm] [15/11] [- nội dung]
MONEY_KINDS = [("thu khach", "thu_khach"), ("thu tien khach", "thu_khach"), ("thu khac", "thu_khac"),
               ("nhan hang", "hang_ncc"), ("hang ncc", "hang_ncc"), ("khoan", "khoan_tinh"), ("tra ncc", "tra_ncc"),
               ("tra to", "tra_to"), ("tra luong", "tra_cong"), ("tra cong", "tra_cong"), ("chi khac", "chi_khac"), ("chi", "chi_khac")]
AMOUNT = re.compile(r"^(\d+(?:[.,]\d+)*)(tr|trieu|k|nghin|d)?$")
UNIT_WORDS = {"tr": 1_000_000, "trieu": 1_000_000, "k": 1000, "nghin": 1000, "d": 1, "dong": 1}
VOUCHER = re.compile(r"^(?:pc|pt|unc|bn|bc|pn|hd)[-/]?\d+$", re.I)
DATE = re.compile(r"^\d{1,2}/\d{1,2}(?:/\d{4})?$")


def money_amount(token, unit=""):
    m = AMOUNT.match(fold(token).replace("đ", "d"))
    if not m:
        return None
    unit = m.group(2) or unit
    raw = m.group(1)
    if unit in ("tr", "trieu", "k", "nghin"):
        value = number(raw.replace(".", ",") if raw.count(".") == 1 and len(raw.split(".")[1]) != 3 else raw)
    else:
        value = number(raw)
    if value is None:
        return None
    return int(value * UNIT_WORDS.get(unit, 1))


def money(text, projects):
    """→ {"entries": [entry]} hoặc None (không phải phiếu)."""
    body, _, content = text.partition(" - ")
    tokens = body.split()
    folded = [fold(t) for t in tokens]
    kind, used = None, 0
    for phrase, value in MONEY_KINDS:
        words = phrase.split()
        if folded[:len(words)] == words:
            kind, used = value, len(words)
            break
    if kind is None:
        return None
    entry = {"kind": kind, "party": "", "content": content.strip(), "method": "", "number": "", "project_code": "", "date": ""}
    party, amount, i = [], None, used
    codes = {p.code.casefold(): p.code for p in projects}
    while i < len(tokens):
        token, low = tokens[i], folded[i]
        nxt = folded[i + 1] if i + 1 < len(tokens) else ""
        if amount is None and AMOUNT.match(low.replace("đ", "d")):
            amount = money_amount(token, nxt if nxt in UNIT_WORDS else "")
            i += 2 if nxt in UNIT_WORDS else 1
            continue
        if token.casefold() in codes:
            entry["project_code"] = codes[token.casefold()]
        elif VOUCHER.match(token):
            entry["number"] = token.upper()
        elif low in ("ck", "uck") or (low == "chuyen" and nxt == "khoan"):
            entry["method"] = "chuyen_khoan"
            i += 1 if low != "chuyen" else 2
            continue
        elif low == "tm" or (low == "tien" and nxt == "mat"):
            entry["method"] = "tien_mat"
            i += 1 if low == "tm" else 2
            continue
        elif DATE.match(token):
            entry["date"] = day(token).isoformat()
        else:
            party.append(token)
        i += 1
    if amount is None:
        return None
    entry["amount"] = amount
    entry["party"] = " ".join(party).strip(" ,.")
    if not entry["project_code"] and len(projects) == 1 and kind != "chi_khac":
        entry["project_code"] = projects[0].code
    return {"entries": [entry]}


def period(text, today=None):
    """"hôm nay" / "tuần" / "tháng" / "15/11" / "" → (từ ngày, đến ngày)."""
    from datetime import timedelta
    today = today or timezone.localdate()
    word = fold(text)
    if not word or word in ("hom nay", "nay"):
        return today, today
    if word in ("hom qua", "qua"):
        return today - timedelta(days=1), today - timedelta(days=1)
    if word.startswith("tuan"):
        return today - timedelta(days=today.weekday()), today
    if word.startswith("thang"):
        return today.replace(day=1), today
    if DATE.match(word):
        d = day(word, today)
        return d, d
    return None
