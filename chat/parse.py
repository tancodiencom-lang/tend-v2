"""Cú pháp ngắn khi chưa bật trợ lý AI (hoặc đã chạm trần chi phí).

    TN-22 T2.5 40% S2.1 xong. Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ
    TN-22 T2.1 58% Công Thắng thợ phụ, Thuận thợ chính      (không cần dấu hai chấm; "thợ phụ/phụ/thợ chính/thợ" sau tên)
    /suacong TN-22 17/11 Hùng 0,5 lý do về sớm
    /dongia Hùng 450000 khoán
"""
import re
from datetime import date

from django.utils import timezone

from core.text import fold, number

LINE = re.compile(r"(?<![\w.-])([A-Za-z]{1,3}[0-9M]\d*(?:\.\d+)+)\s*[:=]?\s*(?:(\d{1,3}(?:[.,]\d+)?)\s*%|(xong|hoan thanh|hoàn thành))", re.I)
CODE = re.compile(r"(?<![\w.-])([A-Za-z]{1,3}[0-9M]\d*(?:\.\d+)+)(?![\w.])")
# Bắt đầu phần chấm công: "Công:", "Công Thắng…", "Chấm công:", "Công nhân: …" (không nhầm "công trình", "công việc",
# "nhân công"…)
PEOPLE_START = re.compile(r"(?<!\w)(?<!nhân )(?<!nhan )(?<!\d )(?<!\d)(?:chấm công|cham cong|công nhân|cong nhan|công|cong)(?!\w)"
                          r"(?!\s*(?:trình|trinh|việc|viec|tác|tac|ty|nhật|nhat|đoạn|doan)(?!\w))\s*:?", re.I)
TODAY = re.compile(r"^\s*(?:hôm nay|hom nay|hn)\s*:?\s*", re.I)
TEAM = re.compile(r"^\s*(?:to|tổ)\s+([^:]{1,40}):\s*", re.I)
HALF = re.compile(r"\b(?:nua ngay|nửa ngày|1/2|0[.,]5\s*(?:cong|công)?)\s*$", re.I)
HOURS = re.compile(r"\b(\d{1,2}(?:[.,]\d+)?)\s*(?:h|gio|giờ|tieng|tiếng)\s*$", re.I)
DAYS = re.compile(r"\b(\d(?:[.,]\d+)?)\s*(?:cong|công)\s*$", re.I)
DATE_IN = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?![\d/])")

# ---------- báo cáo nhắn tự do (đợt 8a) ----------
#   Báo cáo cv 19/11
#   - Nhân sự: thợ xây: 3 thợ 2 phụ
#   - Công việc
#   + Trát thô tường phòng ngoài tầng 5.
#   + Cắt đục sàn bê tông sàn mái
#   - cv dự kiến 20/11
#   + Đánh lạnh phòng ngoài tầng 5.
# → việc không mã thành "việc ngoài bản vẽ" (không tính % tiến độ), phần "dự kiến" thành kế hoạch ngày đó (không phải việc
# đã làm), "3 thợ 2 phụ" ghi lại làm nhân sự theo tin (không tên nên không tính công).
SEGMENT = re.compile(r"\n|\s[-–]\s|[+•;]|(?<=\.)\s+(?=[A-ZÀ-Ỹ])")
BULLETED = re.compile(r"(?:^|\n)\s*[-+•*]|\s\+\s|\s[-–]\s|\n")
REPORT_HEAD = re.compile(r"^\s*(?:báo cáo|bao cao|bc|cv|công việc|cong viec|tiến độ|tien do)(?!\w)", re.I)
PLAN_START = re.compile(r"(?<!\w)(?:(?:cv|công việc|cong viec|việc|viec)\s+)?(?:dự kiến|du kien)"
                        r"(?:\s+(?:làm|lam))?(?:\s+(?:ngày mai|ngay mai|mai))?(?!\w)"
                        r"|(?:^|\n|\s[-–]\s|[+•])\s*(?:kế hoạch|ke hoach|việc ngày mai|viec ngay mai|ngày mai|ngay mai)(?!\w)", re.I)
CREW_START = re.compile(r"^\s*(?:nhân sự|nhan su|nhân công|nhan cong|nhân lực|nhan luc|lao động|lao dong)(?!\w)\s*:?\s*", re.I)
COUNT_WORDS = (r"thợ chính|tho chinh|thợ phụ|tho phu|thợ|tho|phụ|phu|người|nguoi|công nhân|cong nhan|cn|lao động|lao dong")
HEADCOUNT = re.compile(rf"(?<![\w.])\d{{1,3}}\s*(?:{COUNT_WORDS})(?!\w)", re.I)
COUNT = re.compile(rf"(?<![\w.])(\d{{1,3}})\s*({COUNT_WORDS})(?!\w)", re.I)
CREW_GROUP = re.compile(rf"(?P<label>[^\d;]*?)\s*:?\s*(?P<counts>(?<![\w.])\d{{1,3}}\s*(?:{COUNT_WORDS})(?!\w)"
                        rf"(?:\s*(?:,|\+|và|va)?\s*\d{{1,3}}\s*(?:{COUNT_WORDS})(?!\w))*)", re.I)
OWNER = re.compile(r"\(?\s*(?:của\s+|cua\s+)?(?:chủ nhà|chu nha|chủ đầu tư|chu dau tu|cđt|cdt)\s*\)?", re.I)
CREW_LABEL_NOISE = re.compile(r"(?<!\w)(?:nhân sự|nhan su|nhân công|nhan cong|nhân lực|nhan luc|lao động|lao dong|gồm|gom|có|co)(?!\w)",
                              re.I)
HEAD_WORDS = re.compile(r"(?<!\w)(?:báo cáo|bao cao|bc|cv|công việc|cong viec|việc|viec|hôm nay|hom nay|hn|ngày|ngay|tiến độ|tien do|"
                        r"chấm công|cham cong|đã làm|da lam|dự kiến|du kien|làm|lam|kế hoạch|ke hoach|mai|ngày mai|ngay mai)(?!\w)", re.I)
FLOOR = re.compile(r"(?<!\w)(?:tầng|tang|lầu|lau|t)\s*\.?\s*(\d{1,2})(?![\d.,%\w])"
                   r"|(?<!\w)(sàn mái|san mai|tầng mái|tang mai|mái|tum|tầng hầm|tang ham|hầm|trệt|tret|tầng lửng|tang lung|lửng)(?!\w)", re.I)
FLOOR_NAMES = {"san mai": "Mái", "tang mai": "Mái", "mai": "Mái", "tum": "Tum", "tang ham": "Hầm", "ham": "Hầm", "tret": "Trệt",
               "tang lung": "Lửng", "lung": "Lửng"}
DONE_WORD = re.compile(r"(?<!\w)(?:xong|hoàn thành|hoan thanh)(?!\w)", re.I)
PERCENT = re.compile(r"(\d{1,3}(?:[.,]\d+)?)\s*%")


def find_project(text, projects):
    found = next((p for p in projects if re.search(rf"(?<![\w-]){re.escape(p.code)}(?![\w-])", text, re.I)), None)
    return found or (projects[0] if len(projects) == 1 else None)


def _kind(words):
    """Chữ "thợ phụ"/"phụ" hoặc "thợ chính"/"thợ" ở CUỐI tên → (tên còn lại, "phu"|"tho"|None). Tên một chữ giữ nguyên
    (vd người tên Phú)."""
    folded = [fold(w) for w in words]
    for tail, kind in ((["tho", "phu"], "phu"), (["tho", "chinh"], "tho"), (["phu"], "phu"), (["tho"], "tho")):
        if len(words) > len(tail) and folded[-len(tail):] == tail:
            return words[:-len(tail)], kind
    return words, None


def line_pattern(known=()):
    """Dòng "mã % | xong". Mã có dấu chấm (T2.5) luôn nhận; mã khác (D179, DT12 — chép từ bản cũ) chỉ nhận khi đúng mã có
    trên bản vẽ của công trình."""
    known = [c for c in known if c and not CODE.fullmatch(c)]
    if not known:
        return LINE
    alt = "|".join(re.escape(c) for c in sorted(known, key=len, reverse=True))
    return re.compile(rf"(?<![\w.-])((?:{alt})(?![\w.])|[A-Za-z]{{1,3}}[0-9M]\d*(?:\.\d+)+)\s*[:=]?\s*"
                      r"(?:(\d{1,3}(?:[.,]\d+)?)\s*%|(xong|hoan thanh|hoàn thành))", re.I)


def code_pattern(known=()):
    known = [c for c in known if c and not CODE.fullmatch(c)]
    if not known:
        return CODE
    alt = "|".join(re.escape(c) for c in sorted(known, key=len, reverse=True))
    return re.compile(rf"(?<![\w.-])((?:{alt})(?![\w.])|[A-Za-z]{{1,3}}[0-9M]\d*(?:\.\d+)+(?![\w.]))", re.I)


def people_start(text, line_re=LINE):
    """Chỗ bắt đầu phần chấm công: dấu hiệu "công…" nằm SAU dòng báo cáo cuối cùng (tránh "báo cáo + chấm công hôm nay"
    ở đầu tin); tin chỉ có chấm công thì lấy dấu hiệu cuối."""
    marks = list(PEOPLE_START.finditer(text))
    if not marks:
        return None
    lines = list(line_re.finditer(text))
    if lines:
        after = [m for m in marks if m.start() >= lines[-1].end()]
        return after[0] if after else None
    return marks[-1]


def crew_items(text):
    """"tổ xây: 3 thợ 2 phụ, tổ điện (chủ nhà) 2 người đi dây tầng 3" → [{team, skilled, helpers, others, owner, work}].
    Không tên tổ ("3 thợ 2 phụ") thì team rỗng. Việc tổ làm chỉ lấy ở nhóm cuối (chữ sau số người)."""
    out, matches = [], list(CREW_GROUP.finditer(text))
    for index, m in enumerate(matches):
        label = m.group("label")
        owner = bool(OWNER.search(label))
        label = CREW_LABEL_NOISE.sub(" ", OWNER.sub(" ", label))
        item = {"team": " ".join(label.strip(" :.,;-+()").split())[:100], "skilled": 0, "helpers": 0, "others": 0,
                "owner": owner, "work": ""}
        for number_, word in COUNT.findall(m.group("counts")):
            word = fold(word)
            key = "helpers" if word.endswith("phu") else "skilled" if word.startswith("tho") else "others"
            item[key] += int(number_)
        if index == len(matches) - 1:
            tail = text[m.end():]
            if OWNER.search(tail):
                item["owner"] = True
                tail = OWNER.sub(" ", tail)
            tail = " ".join(tail.strip(" :.,;-+()").split())
            if _words(tail) >= 2:
                item["work"] = tail[:300]
        if item["skilled"] + item["helpers"] + item["others"]:
            out.append(item)
    return out


def merge_crews(items):
    """Gộp cùng tổ (cùng tên bỏ dấu): "tổ xây 3 thợ, 2 phụ" tách dấu phẩy thành hai nhóm."""
    merged = {}
    for item in items:
        key = fold(item["team"])
        if key in merged:
            row = merged[key]
            for k in ("skilled", "helpers", "others"):
                row[k] += item[k]
            row["owner"] = row["owner"] or item["owner"]
            row["work"] = row["work"] or item["work"]
        else:
            merged[key] = dict(item)
    return list(merged.values())


def _crew_segment(segment):
    """Một dòng là số người theo tổ: có "N thợ/phụ/người" và (ít chữ khác, hoặc mở đầu "tổ…", hoặc ghi của chủ nhà)."""
    if not HEADCOUNT.search(segment):
        return False
    return (_words(HEADCOUNT.sub(" ", segment)) <= 3 or bool(re.match(r"\s*(?:tổ|to)\s", segment, re.I))
            or bool(OWNER.search(segment)))


def names(rest, project_code=None, crews=None):
    """Phần sau dấu hiệu chấm công: "tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ" → [{name, team, days|hours, kind?}].
    Chỉ có số người ("tổ xây 3 thợ 2 phụ") thì không phải tên: thêm vào `crews` (số người theo tổ)."""
    if project_code:              # "chấm công hôm nay TN-22: Hùng…": mã công trình không phải tên người
        rest = re.sub(rf"(?<![\w-]){re.escape(project_code)}(?![\w-])\s*:?", " ", rest, flags=re.I)
    rest = TODAY.sub("", rest, count=1)
    out = []
    for group in re.split(r"[;\n]", rest):
        team = ""
        t = TEAM.match(group)
        if t:
            team, group = " ".join(t.group(1).split()), group[t.end():]
        if crews is not None and HEADCOUNT.search(group) and not re.search(r"[^\W\d_]{2,}", HEADCOUNT.sub(" ", group).replace(
                "chủ nhà", "").replace(team, "") if team else HEADCOUNT.sub(" ", group)):
            crews += crew_items(f"{team}: {group}" if team else group)   # "tổ xây: 3 thợ 2 phụ" trong phần chấm công
            continue
        for part in re.split(r",|\bva\b|\bvà\b", group):
            part = part.strip(" .:-+")
            if not part or HEADCOUNT.search(part):
                if part and crews is not None:
                    crews += crew_items(f"{team}: {part}" if team else part)
                continue
            entry = {"team": team}
            for pattern, key in ((HALF, None), (HOURS, "hours"), (DAYS, "days")):
                hit = pattern.search(part)
                if hit:
                    entry.update({"days": 0.5} if key is None else {key: float(number(hit.group(1)))})
                    part = part[:hit.start()].strip()
                    break
            words, kind = _kind(part.split())
            if words and not any(ch.isdigit() for ch in words[0]):
                entry["name"] = " ".join(words)
                if kind:
                    entry["kind"] = kind
                out.append(entry)
    return out


def people(text, project_code=None):
    """"Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ" / "Công Thắng thợ phụ, Thuận thợ chính"
    → [{name, team, days|hours, kind?}]."""
    m = people_start(text)
    return names(text[m.end():], project_code) if m else []


def floor_of(text):
    """"… tầng 5", "t5", "sàn mái", "tum" → "Tầng 5" / "Mái" / "Tum" ("" nếu không ghi)."""
    m = FLOOR.search(text or "")
    if not m:
        return ""
    if m.group(1):
        return f"Tầng {int(m.group(1))}"
    return FLOOR_NAMES.get(fold(m.group(2)), m.group(2).capitalize())


def _clean(segment):
    return " ".join(segment.strip(" \t.,:;-–+•*").split())


def _header_only(segment, project_code=None):
    rest = DATE_IN.sub(" ", segment)
    if project_code:
        rest = re.sub(rf"(?<![\w-]){re.escape(project_code)}(?![\w-])", " ", rest, flags=re.I)
    rest = HEAD_WORDS.sub(" ", rest)
    return not re.search(r"[^\W\d_]{2,}", rest)


def _date(match, today=None):
    today = today or timezone.localdate()
    d, m, y = match.groups()
    try:
        return date(int(y) if y else today.year, int(m), int(d)).isoformat()
    except ValueError:
        return None


def _work_item(segment):
    """Một gạch đầu dòng không có mã chi tiết → việc ngoài bản vẽ {floor, description, percent?}; None nếu quá ngắn."""
    text = _clean(segment)
    if _words(text) < 2:
        return None
    out = {"floor": floor_of(text), "description": text[:500]}
    pct = PERCENT.search(text)
    if pct and 0 <= float(number(pct.group(1))) <= 100:
        out["percent"] = float(number(pct.group(1)))
    elif DONE_WORD.search(text):
        out["percent"] = 100.0
    return out


def _words(text):
    return len(re.findall(r"[^\W\d_]+", text))


def plan(text, today=None, code_re=CODE):
    """Phần "cv dự kiến 20/11 + Đánh lạnh phòng ngoài tầng 5. + T2.5" → {date | None, lines: [{code?, floor, description}]}."""
    marker = PLAN_START.match(text)
    rest = text[marker.end():] if marker else text
    head = re.match(r"\s*[:(\-–]?\s*(?:ngày|ngay)?\s*(\d{1,2}/\d{1,2}(?:/\d{4})?)\)?", rest)
    if head:
        rest = rest[head.end():]
    lines = []
    for segment in SEGMENT.split(rest):
        text_ = _clean(segment)
        if not text_ or _header_only(text_):
            continue
        pieces = [_clean(x) for x in text_.split(",")]
        if len(pieces) < 2 or not all(code_re.search(x) or _words(x) >= 2 for x in pieces):
            pieces = [text_]                     # "T1.3, trát tường tầng 4" tách hai việc; "trát, bả tường" giữ một
        for piece in pieces:
            code = code_re.search(piece)
            if code:
                lines.append({"code": code.group(1), "floor": floor_of(piece), "description": piece[:500]})
            elif _words(piece) >= 2:
                lines.append({"floor": floor_of(piece), "description": piece[:500]})
    if not lines:
        return None
    return {"date": _date(DATE_IN.search(head.group(1)), today) if head else None, "lines": lines}


def sections(text, line_re=LINE):
    """Tách tin thành (phần đã làm, phần chấm công có tên, phần dự kiến). Chấm công có thể đứng trước hoặc sau phần dự kiến."""
    found = PLAN_START.search(text)
    pre, post = (text[:found.start()], text[found.start():]) if found else (text, "")
    start = people_start(pre, line_re)
    if start:
        return pre[:start.start()], pre[start.end():], post
    after = None
    for m in PEOPLE_START.finditer(post):          # "… dự kiến 20/11 + Trát tường. Công: Hùng, Nam": chấm công sau kế hoạch
        before = post[:m.start()].rstrip(" \t")
        if not before or before[-1] in ".\n;-–+•:":
            after = m
            break
    if after:
        return pre, post[after.end():], post[:after.start()]
    return pre, "", post


def report(text, projects, today=None, codes=None, strict=False):
    """Tin báo cáo → {project_code, lines, people, extra, plan, crews, work_date, unread, free, note} hoặc None.
    `codes(project)`: mã chi tiết đang dùng của công trình (để đọc mã không có dấu chấm như D179).
    `strict` (nghe tin trong nhóm): tin gạch đầu dòng chỉ là báo cáo khi mở đầu "Báo cáo…/Công việc…" hoặc có mã chi tiết.
    Không có mã công trình mà người gửi có nhiều công trình: trả {needs_project: True} để bot hỏi."""
    project = find_project(text, projects)
    code = project.code if project else None
    known = list(codes(project)) if (codes and project is not None) else []
    line_re, code_re = line_pattern(known), code_pattern(known)
    body, who_text, plan_text = sections(text, line_re)
    free = bool(REPORT_HEAD.match(text) or BULLETED.search(text.strip()))
    lines, extra, crews, unread, work_date = [], [], [], [], None
    who = names(who_text, code, crews) if who_text else []
    for raw in SEGMENT.split(body):
        segment = raw.strip(" \t-–+•*")
        if not segment:
            continue
        crew_head = CREW_START.match(segment)
        if crew_head and not line_re.search(segment):
            rest = segment[crew_head.end():]
            if HEADCOUNT.search(rest):
                crews += crew_items(rest)
            else:
                who += names(rest, code, crews)
            continue
        matches = [m for m in line_re.finditer(segment) if not code or m.group(1).casefold() != code.casefold()]
        if work_date is None:
            first = DATE_IN.search(segment)
            if first and (_header_only(segment, code) or (matches and first.start() < matches[0].start())):
                work_date = _date(first, today)
        if matches:
            for m in matches:
                lines.append({"code": m.group(1), "percent": 100.0 if m.group(3) else float(number(m.group(2)))})
            tail = " ".join(segment[matches[-1].end():].strip(" .,;").split())
            if re.search(r"[^\W\d_]{2,}", tail):
                unread.append(tail)
            continue
        if _header_only(segment, code):
            continue
        if _crew_segment(segment):
            crews += crew_items(segment)            # "thợ xây 3 thợ 2 phụ", "tổ điện (chủ nhà) 2 người" đứng riêng một dòng
            continue
        item = _work_item(segment) if free else None
        if item:
            mentioned = code_re.search(segment)
            if mentioned and (not code or mentioned.group(1).casefold() != code.casefold()):
                if item.get("percent") is not None:   # "D179 trát xong", "D207 trát tường 40%": vẫn là dòng có mã
                    lines.append({"code": mentioned.group(1), "percent": item["percent"], "note": item["description"]})
                    continue
                item["code"] = mentioned.group(1)     # có mã mà chưa có %: ghi lại, phiếu nhắc thêm %
            extra.append(item)
        elif re.search(r"[^\W\d_]{2,}", segment):
            unread.append(_clean(segment))
    planned = plan(plan_text, today, code_re) if plan_text else None
    crews = merge_crews(crews)
    if not lines and not who and not extra and not planned and not crews:
        return None
    if strict and not lines and not REPORT_HEAD.match(text):
        return None
    if project is None:
        return {"needs_project": True} if (free or lines) else None
    out = {"project_code": code, "lines": lines, "people": who, "note": text.strip()[:2000], "extra": extra}
    if planned:
        out["plan"] = planned
    if crews:
        out["crews"] = crews
    if work_date:
        out["work_date"] = work_date
    if free:
        out["free"] = True
    if not who and unread:      # còn chữ sau dòng báo cáo mà không đọc ra chấm công: báo cho người gửi, không lặng lẽ bỏ
        out["unread"] = " ".join(unread)[:200]
    return out


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
