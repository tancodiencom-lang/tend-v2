"""Trợ lý kế toán: báo cáo công nợ hằng tuần (chủ dự án chốt 21/11/2026: làm trước; gửi CẢ người giữ vị trí và Giám đốc).

- Thứ Sáu 08:30 → kế toán nội bộ + Giám đốc: nợ nhà cung cấp, tổ thợ, công nhật theo hạn trả (quá hạn, đến hạn 7 ngày,
  chưa có hạn); gợi ý khoản cần trả tuần tới (kế toán rà soát, trình Giám đốc); chỉ số vị trí kế toán nội bộ.
- Thứ Tư 08:30 → kế toán tài chính + Giám đốc: công nợ khách theo đợt thanh toán (quá hạn > 3 ngày nhắc, > 15 ngày Gấp,
  đến hạn 7 ngày), công trình chưa có lịch đợt thu, dòng tiền 4 tuần tới; chỉ số vị trí kế toán tài chính.
- Mỗi người nhận bản tính theo ĐÚNG quyền của mình (projects_for, sees_payables/sees_receivables). Lệnh bot /congnotuan.
Thiếu số liệu thì ghi thiếu gì, không hiện 0 giả.
"""
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.models import Role, User
from core.text import vnd
from projects.models import projects_for

from . import aging
from .models import CASH_OUT, Entry, Kind, Party

ZERO = Decimal(0)
SHOW = 12                     # số dòng tối đa mỗi mục trong tin Telegram (đủ chi tiết ở trang Công nợ)


def _projects(user):
    return list(projects_for(user).filter(active=True).order_by("code"))


def _week(today):
    return today - timedelta(days=7), today - timedelta(days=1)


def _fit(body, tail, limit):
    """Telegram nhận tối đa ~4000 ký tự: bớt dòng cuối của phần danh sách, luôn giữ phần cuối (chỉ số vị trí, đường dẫn)."""
    body = list(body)
    more = "… còn nữa — xem đủ ở trang Công nợ (đường dẫn cuối tin)."
    while body and len("\n".join(body + tail)) > limit:
        body.pop()
        if body and body[-1] != more:
            body.append(more)
            if len("\n".join(body + tail)) > limit:
                body.pop()
    return "\n".join(body + tail)


def _pay_line(row):
    number = row["entry"].number or f"#{row['entry'].pk}"
    return f"{row['project'].code} · {row['group'].lower()} {row['name']} · {number} · còn {vnd(row['remaining'])} · {aging.label(row)}"


def internal_kpis(today):
    """Chỉ số vị trí kế toán nội bộ (7 ngày trước hôm nay)."""
    start, end = _week(today)
    entered = Entry.objects.filter(entered_by__role=Role.INTERNAL_ACCOUNTANT, voided_at__isnull=True, date__gte=start, date__lte=end)
    total = entered.count()
    on_time = sum(1 for e in entered.only("date", "created_at") if timezone.localtime(e.created_at).date() <= e.date)
    no_voucher = Entry.objects.filter(kind__in=CASH_OUT, voided_at__isnull=True, voucher="", date__gte=start, date__lte=end).count()
    used = Entry.objects.filter(voided_at__isnull=True, party_ref__isnull=False).values_list("party_ref_id", flat=True)
    no_misa = Party.objects.filter(active=True, misa_code="", pk__in=used).count()
    from assistant.schedule import workers_without_rate
    no_rate = len(workers_without_rate(timezone.now()))
    lines = [f"Chỉ số vị trí kế toán nội bộ ({start:%d/%m}–{end:%d/%m}):",
             (f"• Phiếu nhập trong ngày phát sinh: {on_time}/{total} ({on_time * 100 // total}%)" if total
              else "• Phiếu nhập trong ngày phát sinh: chưa có phiếu nào của kế toán nội bộ trong tuần"),
             f"• Phiếu chi thiếu ảnh chứng từ: {no_voucher}",
             f"• Đối tượng đang dùng mà thiếu mã MISA: {no_misa}",
             f"• Thợ công nhật chưa có đơn giá: {no_rate}",
             "• Chênh lệch quỹ cuối ngày: chưa có số liệu (Tend chưa ghi số đếm quỹ)"]
    return lines


def internal_text(user, today=None, limit=3900):
    """Báo cáo công nợ tuần phần PHẢI TRẢ (kế toán nội bộ, Giám đốc)."""
    today = today or timezone.localdate()
    if not user.sees_payables:
        return None
    projects = _projects(user)
    data = aging.payables(projects, today)
    rows = data["rows"]
    total = sum((r["remaining"] for r in rows), ZERO)
    by_group = {g: sum((r["remaining"] for r in rows if r["group"] == g), ZERO) for g in ("Nhà cung cấp", "Tổ thợ")}
    late = [r for r in rows if r["status"] == "qua_han"]
    soon = [r for r in rows if r["status"] == "sap_han"]
    no_due = [r for r in rows if r["status"] == "chua_han"]
    from overview.compute import payables as labor_payables
    labor = sum((labor_payables(p)["labor_debt"] for p in projects), ZERO)
    out = [f"💰 CÔNG NỢ PHẢI TRẢ — tuần đến {today:%d/%m/%Y}",
           f"Tổng còn nợ: {vnd(total)} (nhà cung cấp {vnd(by_group['Nhà cung cấp'])}, tổ thợ {vnd(by_group['Tổ thợ'])})"
           + (f"; lương công nhật còn {vnd(labor)}" if labor else "")]
    if late:
        out.append(f"\n▼ QUÁ HẠN: {len(late)} khoản, {vnd(sum((r['remaining'] for r in late), ZERO))}")
        out += [f"• {_pay_line(r)}" for r in late[:SHOW]] + ([f"• … và {len(late) - SHOW} khoản nữa"] if len(late) > SHOW else [])
    else:
        out.append("\n▲ Không có khoản nào quá hạn.")
    if soon:
        out.append(f"\n● ĐẾN HẠN 7 NGÀY TỚI: {len(soon)} khoản, {vnd(sum((r['remaining'] for r in soon), ZERO))}")
        out += [f"• {_pay_line(r)}" for r in soon[:SHOW]] + ([f"• … và {len(soon) - SHOW} khoản nữa"] if len(soon) > SHOW else [])
    if no_due:
        names = sorted({f"{r['name']} ({r['project'].code})" for r in no_due})
        out.append(f"\n? CHƯA CÓ HẠN TRẢ: {len(no_due)} khoản, {vnd(sum((r['remaining'] for r in no_due), ZERO))} — "
                   + ", ".join(names[:8]) + (" …" if len(names) > 8 else "")
                   + ". Đặt hạn: /hantra <số phiếu> <ngày> hoặc /hanno <đối tượng> <số ngày> ngày, hoặc trên trang Công nợ.")
    if data["advances"]:
        out.append("\nĐã trả trước nhiều hơn phát sinh: " + "; ".join(
            f"{a['name']} ({a['project'].code}) {vnd(a['amount'])}" for a in data["advances"][:6]))
    to_pay = late + soon
    if to_pay:
        out.append(f"\nGỢI Ý TRẢ TUẦN TỚI (quá hạn + đến hạn): {vnd(sum((r['remaining'] for r in to_pay), ZERO))}. "
                   "Kế toán nội bộ rà soát, lập đề xuất lịch thanh toán trình Giám đốc duyệt.")
    return _fit(out, [""] + internal_kpis(today) + [f"\nChi tiết: {settings.APP_URL}/bao-cao/cong-no/"], limit)


def finance_kpis(recv):
    late = [(p, i) for p in recv for i in p["items"] if i["status"] == "qua_han" and i["remaining"] > 0]
    amount = sum((i["remaining"] for _, i in late), ZERO)
    longest = max((i["days"] for _, i in late), default=0)
    return ["Chỉ số vị trí kế toán tài chính:",
            (f"• Tiền khách quá hạn: {vnd(amount)} ở {len(late)} đợt, lâu nhất {longest} ngày" if late
             else "• Tiền khách quá hạn: không có"),
            "• Chênh lệch MISA với Tend sau khoá sổ: chưa có số liệu (Tend chưa đọc sổ MISA)"]


def finance_text(user, today=None, limit=3900):
    """Báo cáo công nợ tuần phần PHẢI THU + dòng tiền (kế toán tài chính, Giám đốc)."""
    today = today or timezone.localdate()
    if not user.sees_receivables:
        return None
    projects = _projects(user)
    recv = aging.receivables(projects, today)
    out = [f"🏦 CÔNG NỢ KHÁCH VÀ DÒNG TIỀN — tuần đến {today:%d/%m/%Y}"]
    if not recv:
        out.append("Chưa có giá trị hợp đồng, đợt thanh toán hay phiếu thu khách nào.")
    for project in recv:
        contract = vnd(project["contract"]) if project["contract"] is not None else "chưa có giá trị hợp đồng"
        remaining = (vnd(project["contract"] - project["received"]) if project["contract"] is not None else "chưa tính được")
        out.append(f"\n{project['project'].code} · hợp đồng {contract} · đã thu {vnd(project['received'])} · còn {remaining}")
        if not project["has_plan"]:
            out.append("  ? Chưa có lịch đợt thanh toán: /dotthu " + project["project"].code + " Đợt 1 <số tiền> hạn <ngày>")
            continue
        for item in project["items"]:
            inst = item["installment"]
            if item["status"] == "da_thu":
                continue
            if item["status"] == "qua_han":
                mark = {"gap": "▼ GẤP", "nhac": "▼", "vua_den": "●"}[item["level"]] + f" quá hạn {item['days']} ngày"
            elif item["status"] == "sap_han":
                mark = "● đến hạn" + (" hôm nay" if item["days"] == 0 else f" trong {item['days']} ngày")
            else:
                mark = "○ chưa đến hạn"
            out.append(f"  {mark} · {inst.name} · hạn {inst.due_on:%d/%m/%Y} · còn {vnd(item['remaining'])}"
                       + (f" / {vnd(inst.amount)}" if item["paid"] else ""))
        paid_all = [i for i in project["items"] if i["status"] == "da_thu"]
        if paid_all:
            out.append(f"  ✓ Đã thu đủ {len(paid_all)} đợt")
        if project["ahead"] > 0:
            out.append(f"  Thu vượt lịch đợt: {vnd(project['ahead'])}")
        if project["plan_gap"] is not None:
            out.append(f"  ⚠ Tổng các đợt {vnd(project['planned'])} khác giá trị hợp đồng (chênh {vnd(project['plan_gap'])})")
    pay_rows = aging.payables(projects, today)["rows"] if user.sees_payables else []
    flow = aging.forecast(pay_rows, recv, today)
    out.append("\nDÒNG TIỀN 4 TUẦN TỚI (theo hạn đợt thu và hạn trả):")
    for week in flow["weeks"]:
        sign = "▲" if week["net"] >= 0 else "▼"
        out.append(f"• {week['start']:%d/%m}–{week['end']:%d/%m}: thu {vnd(week['inflow'])} · chi {vnd(week['outflow'])} · "
                   f"{sign} {vnd(week['net'])}")
    extra = []
    if flow["late_in"]:
        extra.append(f"khách quá hạn chưa thu {vnd(flow['late_in'])}")
    if flow["late_out"]:
        extra.append(f"nợ quá hạn chưa trả {vnd(flow['late_out'])}")
    if flow["no_due_out"]:
        extra.append(f"nợ chưa có hạn trả {vnd(flow['no_due_out'])} (không tính vào tuần nào)")
    if extra:
        out.append("Ngoài ra: " + "; ".join(extra) + ".")
    return _fit(out, [""] + finance_kpis(recv) + [f"\nChi tiết: {settings.APP_URL}/bao-cao/cong-no/"], limit)


def text_for(user, today=None):
    """Lệnh /congnotuan: bản đúng vai (Giám đốc: cả hai)."""
    builders = ([internal_text] if user.is_director or user.role == Role.INTERNAL_ACCOUNTANT else []) + (
        [finance_text] if user.is_director or user.role == Role.FINANCE_ACCOUNTANT else [])
    parts = [build(user, today, limit=3900 // max(len(builders), 1)) for build in builders]
    parts = [p for p in parts if p]
    return "\n\n".join(parts) if parts else "Báo cáo công nợ tuần dành cho Giám đốc và kế toán."


def recipients(role):
    """Người giữ vị trí + Giám đốc (chủ dự án chốt: báo cáo vị trí gửi cả người đó và Giám đốc)."""
    return list(User.objects.filter(is_active=True).filter(Q(role=role) | Q(role=Role.DIRECTOR) | Q(is_superuser=True))
                .distinct().order_by("pk"))
