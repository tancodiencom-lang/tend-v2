"""Gọi trợ lý theo việc trong MỘT bot (chủ dự án chốt 21/11/2026: "gọi @mkt thì trợ lý marketing làm việc với tôi; gọi
@ketoannoibo thì từ đó trao đổi với kế toán nội bộ; xong thì đóng lại").

- Gõ `@mkt`, `@ketoannoibo`, `@ketoantaichinh`, `@giamsat`, `@troly` (có dấu hay không dấu, viết liền hay cách đều được)
  để gọi; `@dong` để đóng. Từ lúc gọi tới lúc đóng (hoặc gọi trợ lý khác), mọi tin và ảnh trong chat riêng đi tới trợ lý đó:
  ảnh ở bàn Marketing là ảnh bài đăng, ở bàn Kế toán là chứng từ — không lẫn sang báo cáo hiện trường.
- Không gọi trợ lý nào: bot tự nhận biết như trước. Nhân viên vai Marketing luôn ở bàn Marketing.
- Bàn tự đóng sau `TEND_DESK_IDLE_HOURS` giờ (mặc định 3) không nhắn, để hôm sau ảnh báo cáo không đi nhầm bàn.
- Chỉ chat riêng; trong nhóm "@tên_bot" vẫn là gọi bot như cũ.
"""
import re
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.models import Role
from core.text import fold

READS = {"list_projects", "project_overview"}
ASK = {"ask_user", "ask_director"}


@dataclass(frozen=True)
class Desk:
    key: str
    name: str
    icon: str
    aliases: tuple
    roles: tuple                  # vai được gọi (Giám đốc luôn được)
    focus: str                    # câu dặn AI: bàn này làm gì
    intro: str                    # tin chào khi gọi
    tools: frozenset = None       # công cụ AI được dùng; None = mọi công cụ
    money: tuple = None           # phần tiền bàn này được đọc ("payables", "receivables", "profit"); None = như người hỏi

    @property
    def label(self):
        return f"{self.icon} {self.name}"

    def allows(self, user):
        return user.is_director or user.role in self.roles


DESKS = (
    Desk("mkt", "Trợ lý Marketing", "📣", ("mkt", "marketing", "noidung", "nd"), (Role.MARKETING,),
         "bài đăng Facebook: viết bài, sửa bài, thêm ảnh",
         "• Nhắn chủ đề → trợ lý viết bài (1–3 phút).\n"
         "• Gửi ảnh → thêm vào bài đang làm, chưa có bài thì để dành cho bài sắp viết.\n"
         "• Nhắn ý sửa (bắt đầu bằng \"sửa\", \"thêm\", \"bỏ\", \"đổi\", \"viết lại\"…) → trợ lý sửa bài đang làm.\n"
         "• /noidung xem bài chờ duyệt.",
         money=()),
    Desk("ketoannoibo", "Trợ lý Kế toán nội bộ", "💰", ("ketoannoibo", "ktnb", "ketoan", "thuchi"), (Role.INTERNAL_ACCOUNTANT,),
         "phiếu thu chi, chứng từ, công nợ nhà cung cấp và tổ thợ, chi phí công trình",
         "• Gửi ảnh phiếu, hoá đơn, ảnh chuyển khoản → chứng từ (không thành báo cáo hiện trường).\n"
         "• Nhắn nội dung phiếu, vd \"trả NCC 5tr Cát Hùng Vương TN-22 PC12 ck\" → phiếu chờ Xác nhận.\n"
         "• Hỏi: \"tuần này chi bao nhiêu?\", \"còn nợ Cát Hùng Vương bao nhiêu?\"\n"
         "• Công nợ tuần: /congnotuan (tự gửi sáng thứ Sáu). Hạn trả: /hantra PC12 15/12, /hanno Cát Hùng Vương 30 ngày.",
         frozenset(READS | ASK | {"money_summary", "list_entries", "find_parties", "propose_entries", "propose_void"}),
         money=("payables",)),
    Desk("ketoantaichinh", "Trợ lý Kế toán tài chính", "🏦", ("ketoantaichinh", "kttc", "taichinh", "congno"),
         (Role.FINANCE_ACCOUNTANT,),
         "tiền khách trả, công nợ khách, lãi lỗ, dòng tiền",
         "• Gửi ảnh ủy nhiệm chi, phiếu thu của khách → chứng từ.\n"
         "• Nhắn \"thu khách 200tr TN-22 đợt 3 ck\" → phiếu thu chờ Xác nhận.\n"
         "• Hỏi: \"khách TN-22 còn nợ bao nhiêu?\", \"lãi dự báo TN-22?\"\n"
         "• Công nợ khách + dòng tiền: /congnotuan (tự gửi sáng thứ Tư). Đợt thu: /dotthu TN-22 Đợt 3 300tr hạn 15/12.",
         frozenset(READS | ASK | {"money_summary", "list_entries", "find_parties", "propose_entries", "propose_void"}),
         money=("payables", "receivables", "profit")),
    Desk("giamsat", "Trợ lý Hiện trường", "📋", ("giamsat", "gs", "baocao", "hientruong"), (Role.SUPERVISOR,),
         "báo cáo ngày, ảnh hiện trường, chấm công, tiến độ, tổ thợ",
         "• Gửi ảnh + báo cáo ngày (mã chi tiết, %, việc ngoài bản vẽ, dự kiến ngày mai) → phiếu báo cáo chờ Xác nhận.\n"
         "• Chấm công: \"Công: Hậu thợ; Thắng phụ\" hoặc \"tổ xây: 3 thợ 2 phụ\".\n"
         "• Hỏi: \"TN-22 đang chậm bao nhiêu?\", \"tháng này Hùng bao nhiêu công?\"",
         frozenset(READS | ASK | {"find_details", "find_workers", "attendance_summary", "pending_asks", "propose_report",
                                  "propose_attendance_fix", "propose_answer"}),
         money=()),
    Desk("troly", "Trợ lý Giám đốc", "🧭", ("troly", "giamdoc", "gd"),
         (Role.SUPERVISOR, Role.DESIGNER, Role.INTERNAL_ACCOUNTANT, Role.FINANCE_ACCOUNTANT, Role.MARKETING),
         "hỏi tình hình, số liệu, giao việc, theo dõi việc, hỏi Giám đốc",
         "• Hỏi tình hình: \"công trình nào đang chậm?\", \"Nam còn việc gì?\"\n"
         "• Giao việc (Giám đốc): \"nhắc Nam gửi bản vẽ điện TN-22 trước thứ Sáu\".\n"
         "• Bàn này không nhận ảnh: ảnh báo cáo gọi @giamsat, ảnh phiếu gọi @ketoannoibo, ảnh bài đăng gọi @mkt.",
         frozenset(READS | ASK | {"find_details", "find_workers", "attendance_summary", "pending_asks", "money_summary",
                                  "list_entries", "find_parties", "find_people", "list_tasks", "propose_task"})),
)
BY_KEY = {desk.key: desk for desk in DESKS}
CLOSE = ("dong", "xong", "thoat", "het")
MONEY = ("ketoannoibo", "ketoantaichinh")


def idle_hours():
    return float(getattr(settings, "TEND_DESK_IDLE_HOURS", 3) or 3)


def parse_call(text):
    """"@mkt viết bài…" → ("mkt", "viết bài…"); "@đóng" → ("dong", ""); "@abc" → ("?", ""); không mở đầu @ → (None, text).
    Tên gọi có dấu hay không, viết liền hay cách ("@kế toán nội bộ") đều nhận."""
    text = (text or "").strip()
    if not text.startswith("@"):
        return None, text
    words = text[1:].split()
    aliases = {alias: desk.key for desk in DESKS for alias in desk.aliases} | {word: "dong" for word in CLOSE}
    for count in range(min(4, len(words)), 0, -1):                 # ưu tiên tên dài nhất ("ke toan noi bo")
        joined = re.sub(r"[^a-z0-9]", "", fold(" ".join(words[:count])).replace(" ", ""))
        if joined in aliases:
            return aliases[joined], " ".join(words[count:]).strip()
    return "?", ""


def default_for(user):
    return "mkt" if user.role == Role.MARKETING else None


def current(link_row, now=None):
    """→ (bàn đang làm việc hoặc None, bàn vừa tự đóng vì lâu không nhắn hoặc None)."""
    state = link_row.state or {}
    key = state.get("desk")
    if key in BY_KEY:
        since = parse_datetime(state.get("desk_at") or "")
        if since is not None and (now or timezone.now()) - since > timedelta(hours=idle_hours()):
            return BY_KEY.get(default_for(link_row.user)), BY_KEY[key]
        return BY_KEY[key], None
    return BY_KEY.get(default_for(link_row.user)), None


def menu(user):
    rows = [f"@{desk.aliases[0]} — {desk.label}" for desk in DESKS if desk.allows(user)]
    return "Gọi trợ lý:\n" + "\n".join(f"• {row}" for row in rows) + "\n• @dong — đóng, bot tự nhận biết như bình thường"


def welcome(desk, user):
    others = " ".join(f"@{d.aliases[0]}" for d in DESKS if d.key != desk.key and d.allows(user))
    return (f"{desk.label} đang làm việc với anh/chị.\n{desk.intro}\n"
            f"Xong việc gõ @dong{', hoặc gọi trợ lý khác: ' + others if others else ''}. "
            f"Bàn tự đóng sau {idle_hours():g} giờ không nhắn.")


def viewer(user, desk):
    """Người đọc số ở bàn này: phần tiền thu hẹp theo vị trí của bàn (Giám đốc hỏi bàn Hiện trường cũng không thấy tiền)."""
    if desk is None or desk.money is None:
        return user
    from .models import GroupViewer
    return GroupViewer.of(user, None, money=desk.money)


MONEY_WORDS = {"payables": "nợ nhà cung cấp / tổ thợ / chi phí", "receivables": "tiền khách", "profit": "lãi lỗ"}


def ai_context(desk):
    out = (f"\nNgười dùng ĐANG LÀM VIỆC VỚI {desk.name.upper()} ({desk.focus}). Chỉ làm việc của trợ lý này; tin thuộc việc "
           f"khác thì trả lời ngắn và nhắc gọi đúng trợ lý ({', '.join('@' + d.aliases[0] for d in DESKS if d.key != desk.key)}).")
    if desk.money is not None:
        hidden = [MONEY_WORDS[k] for k in ("payables", "receivables", "profit") if k not in desk.money]
        if hidden:
            where = (["@ketoannoibo"] if "payables" not in desk.money else []) + (
                ["@ketoantaichinh"] if {"receivables", "profit"} - set(desk.money) else []) + ["@troly"]
            out += (f" Bàn này KHÔNG nói về: {', '.join(hidden)} (dù người hỏi là Giám đốc); hỏi phần đó thì nhắc gọi "
                    + ", ".join(where) + ".")
    return out
