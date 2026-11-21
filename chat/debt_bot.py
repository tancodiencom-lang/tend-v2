"""Lệnh bot cho trợ lý kế toán: báo cáo công nợ tuần, hạn trả nhà cung cấp / tổ thợ, đợt thanh toán của khách.

Chủ dự án chốt 21/11/2026: hạn trả do kế toán nội bộ (và Giám đốc) nhập; đợt thanh toán của khách do kế toán tài chính và
Giám đốc nhập; qua bot và web. Mỗi lệnh ghi lập một phiếu (Proposal "Hạn trả, đợt thu"), bấm ✓ Xác nhận mới ghi qua
`money.services` (giao dịch + nhật ký kênh telegram).

- /congnotuan — báo cáo công nợ tuần đúng vai (như tin tự gửi thứ Sáu / thứ Tư).
- /hantra PC12 15/12 [lý do] · /hantra #123 30 ngày — đặt / đổi hạn trả khoản nhận hàng, khoán (đổi hạn đã có phải có lý do).
- /hanno Cát Hùng Vương 30 ngày · /hanno Cát Hùng Vương bỏ — số ngày được nợ mặc định của đối tượng.
- /dotthu TN-22 — xem các đợt; /dotthu TN-22 Đợt 3 xong thô 300tr hạn 15/12 [- ghi chú] — thêm đợt.
- /huydot 12 lý do — huỷ đợt (không xoá).
"""
from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from core.text import fold, vnd
from money import aging, weekly
from money import services as money
from money.models import ACCRUALS, Installment, Party
from money.parties import match
from projects.models import projects_for

from . import parse, reads
from .models import Proposal

COMMANDS = {"/congnotuan", "/hantra", "/hanno", "/dotthu", "/huydot"}
HELP = ("• Công nợ tuần (kế toán, Giám đốc): /congnotuan. Hạn trả NCC/tổ (kế toán nội bộ): /hantra PC12 15/12 [lý do], "
        "/hanno Cát Hùng Vương 30 ngày. Đợt thu khách (kế toán tài chính): /dotthu TN-22 Đợt 3 300tr hạn 15/12, "
        "/dotthu TN-22 (xem), /huydot 12 lý do. Nhắn phiếu nhận hàng kèm \"hạn 15/12\" hoặc \"hạn 30 ngày\" cũng được.")


def _d(value):
    return f"{value:%d/%m/%Y}" if value else "chưa có"


def due_day(token, start):
    """"15/12" hoặc "15/12/2026" → ngày; không ghi năm thì lấy năm của `start`, sớm hơn `start` thì sang năm sau."""
    parts = [int(x) for x in token.split("/")]
    try:
        if len(parts) == 3:
            return date(parts[2], parts[1], parts[0])
        day = date(start.year, parts[1], parts[0])
        return day if day >= start else date(start.year + 1, parts[1], parts[0])
    except ValueError:
        raise ValidationError(f"Ngày {token} không có thật.")


def _plan_day(token, today):
    """Hạn đợt thu: không ghi năm thì năm nay, trừ khi sớm hơn hôm nay quá nửa năm (vd tháng 12 ghi "15/01")."""
    parts = [int(x) for x in token.split("/")]
    try:
        if len(parts) == 3:
            return date(parts[2], parts[1], parts[0])
        day = date(today.year, parts[1], parts[0])
        return day if (today - day).days <= 183 else date(today.year + 1, parts[1], parts[0])
    except ValueError:
        raise ValidationError(f"Ngày {token} không có thật.")


def handle(link_row, command, text):
    user = link_row.user
    words = text.split()
    if command == "/congnotuan":
        if not (user.sees_payables or user.sees_receivables):
            raise PermissionDenied("Báo cáo công nợ tuần dành cho Giám đốc và kế toán.")
        return weekly.text_for(user), None
    if command == "/hantra":
        return None, build_due(link_row, words[1:], text)
    if command == "/hanno":
        if len(words) == 1:
            return terms_list(user), None
        return None, build_terms(link_row, words[1:], text)
    if command == "/dotthu":
        if len(words) == 1:
            return "Cú pháp: /dotthu TN-22 (xem các đợt) hoặc /dotthu TN-22 Đợt 3 300tr hạn 15/12 [- ghi chú].", None
        if len(words) == 2:
            return installments_text(user, words[1]), None
        return None, build_installment(link_row, text, text)
    if len(words) < 3:
        return "Cú pháp: /huydot 12 lý do huỷ (số đợt xem ở /dotthu TN-22).", None
    return None, build_cancel(link_row, words[1], " ".join(words[2:]), text)


# ---------- hạn trả từng khoản ----------

def build_due(link_row, args, source_text):
    user = link_row.user
    if not money.can_set_due(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ đặt hạn trả.")
    if len(args) < 2:
        raise ValidationError("Cú pháp: /hantra PC12 15/12 [lý do] hoặc /hantra #123 30 ngày [lý do].")
    entry = money.find_entry(user, args[0])
    if entry is None:
        raise ValidationError(f"Không thấy phiếu {args[0]} trong phạm vi anh/chị được xem.")
    if entry.kind not in ACCRUALS:
        raise ValidationError(f"{args[0]} là phiếu {entry.get_kind_display().lower()}: chỉ đặt hạn trả cho khoản nhận hàng "
                              "nhà cung cấp hoặc giá trị khoán tổ thợ.")
    if entry.voided_at:
        raise ValidationError("Phiếu đã huỷ.")
    rest = args[1:]
    if parse.DATE.match(rest[0]):
        due, rest = due_day(rest[0], entry.date), rest[1:]
    elif rest[0].isdigit() and len(rest) > 1 and fold(rest[1]) == "ngay":
        due, rest = entry.date + timedelta(days=int(rest[0])), rest[2:]
    else:
        raise ValidationError("Ghi hạn dạng 15/12 hoặc \"30 ngày\" (tính từ ngày phiếu).")
    due = money.due_date(due, entry.date)
    reason = " ".join(rest).strip(" -:")
    if fold(reason).startswith("ly do"):
        reason = reason[5:].strip(" :")
    if entry.due_on and entry.due_on != due and not reason:
        raise ValidationError(f"Khoản này đã có hạn {_d(entry.due_on)}; đổi hạn phải ghi lý do, vd "
                              f"/hantra {args[0]} {due:%d/%m} NCC đồng ý lùi hạn.")
    label = f"{entry.number or '#' + str(entry.pk)} · {entry.get_kind_display().lower()} · {entry.party} · {vnd(entry.amount)}"
    return Proposal.objects.create(link=link_row, project=entry.project, kind=Proposal.Kind.DEBT, source_text=source_text[:5000],
                                   payload={"action": "due", "entry_id": entry.pk, "label": label,
                                            "before": entry.due_on.isoformat() if entry.due_on else "",
                                            "terms": money.effective_due(entry).isoformat() if money.effective_due(entry) else "",
                                            "due_on": due.isoformat(), "reason": reason[:300]})


# ---------- số ngày được nợ của đối tượng ----------

def terms_list(user):
    if not money.can_set_due(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ đặt số ngày được nợ.")
    rows = Party.objects.filter(active=True, terms_days__isnull=False).order_by("name")
    lines = [f"• {p.name} ({p.get_type_display().lower()}): {p.terms_days} ngày" for p in rows[:40]]
    return ("Cú pháp: /hanno <đối tượng> 30 ngày (hoặc \"bỏ\"). Hạn trả mỗi khoản = ngày nhận hàng / khoán + số ngày này, "
            "trừ khoản đã ghi hạn riêng.\n" + ("Đối tượng đã có số ngày được nợ:\n" + "\n".join(lines) if lines
                                                else "Chưa đối tượng nào có số ngày được nợ."))


def build_terms(link_row, args, source_text):
    user = link_row.user
    if not money.can_set_due(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ đặt số ngày được nợ.")
    folded = [fold(a) for a in args]
    if folded and folded[-1] in ("bo", "khong", "xoa"):
        days, name = None, args[:-1]
    elif len(args) >= 2 and folded[-1] == "ngay" and args[-2].isdigit():
        days, name = int(args[-2]), args[:-2]
    elif args and args[-1].isdigit():
        days, name = int(args[-1]), args[:-1]
    else:
        raise ValidationError("Cú pháp: /hanno Cát Hùng Vương 30 ngày (hoặc /hanno Cát Hùng Vương bỏ).")
    if days is not None and not 0 <= days <= 365:
        raise ValidationError("Số ngày được nợ từ 0 đến 365.")
    name = " ".join(name)
    result = match(name, Party.Type.SUPPLIER)
    party = result.get("party")
    if party is None:
        options = result.get("candidates") or []
        raise ValidationError(f"Không thấy đối tượng \"{name}\"."
                              + (" Có phải: " + "; ".join(p.name for p in options) + "? Gõ lại đúng tên." if options
                                 else " Xem /doituong <tên>."))
    if party.type not in (Party.Type.SUPPLIER, Party.Type.TEAM):
        raise ValidationError(f"{party.name} là {party.get_type_display().lower()}: số ngày được nợ chỉ cho nhà cung cấp, tổ thợ.")
    return Proposal.objects.create(link=link_row, kind=Proposal.Kind.DEBT, source_text=source_text[:5000],
                                   payload={"action": "terms", "party_id": party.pk,
                                            "label": f"{party.name} ({party.get_type_display().lower()})",
                                            "before": party.terms_days, "days": days})


# ---------- đợt thanh toán của khách ----------

def _project(user, code):
    if not (money.can_plan_receivables(user) or user.sees_receivables):
        raise PermissionDenied("Đợt thanh toán của khách chỉ Giám đốc và kế toán tài chính xem.")
    return reads.project(user, code)


def installments_text(user, code):
    project = _project(user, code)
    data = next(iter(aging.receivables([project], timezone.localdate())), None)
    if data is None or not data["items"]:
        return (f"{project.code} chưa có đợt thanh toán nào. Thêm: /dotthu {project.code} Đợt 1 <số tiền> hạn <ngày>"
                + (f"\nGiá trị hợp đồng: {vnd(data['contract'])}" if data and data["contract"] is not None else ""))
    out = [f"ĐỢT THANH TOÁN {project.code} · hợp đồng "
           + (vnd(data["contract"]) if data["contract"] is not None else "chưa có giá trị") + f" · đã thu {vnd(data['received'])}"]
    for item in data["items"]:
        inst = item["installment"]
        mark = {"da_thu": "✓ đã thu đủ", "qua_han": f"▼ quá hạn {item['days']} ngày", "sap_han": "● sắp đến hạn",
                "con_han": "○ chưa đến hạn"}[item["status"]]
        out.append(f"• số {inst.pk} · {inst.name} · {vnd(inst.amount)} · hạn {_d(inst.due_on)} · {mark}"
                   + (f" · còn {vnd(item['remaining'])}" if item["status"] != "da_thu" else ""))
    if data["plan_gap"] is not None:
        out.append(f"⚠ Tổng các đợt {vnd(data['planned'])} khác giá trị hợp đồng (chênh {vnd(data['plan_gap'])}).")
    out.append("Huỷ đợt: /huydot <số> lý do.")
    return "\n".join(out)


def build_installment(link_row, text, source_text):
    user = link_row.user
    if not money.can_plan_receivables(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán tài chính nhập đợt thanh toán của khách.")
    body, _, note = text.partition(" - ")
    tokens = body.split()[1:]
    project = _project(user, tokens[0])
    tokens = tokens[1:]
    folded = [fold(t) for t in tokens]
    if "han" not in folded:
        raise ValidationError(f"Thiếu hạn. Cú pháp: /dotthu {project.code} Đợt 3 300tr hạn 15/12.")
    at = folded.index("han")
    when = tokens[at + 1] if at + 1 < len(tokens) else ""
    if not parse.DATE.match(when):
        raise ValidationError("Ghi hạn dạng 15/12 hoặc 15/12/2026.")
    due = _plan_day(when, timezone.localdate())
    head = tokens[:at]
    amount, cut = None, None
    for i in range(len(head) - 1, -1, -1):                 # số tiền là cụm số cuối cùng trước chữ "hạn"
        unit = fold(head[i + 1]) if i + 1 < len(head) else ""
        value = parse.money_amount(head[i], unit if unit in parse.UNIT_WORDS else "")
        if value and value >= 1000:                         # "Đợt 3" không phải số tiền 3 ₫
            amount, cut = value, i
            break
    if not amount:
        raise ValidationError(f"Thiếu số tiền. Cú pháp: /dotthu {project.code} Đợt 3 300tr hạn 15/12.")
    name = " ".join(head[:cut]).strip(" ,.:")
    if not name:
        raise ValidationError("Ghi tên đợt trước số tiền, vd \"Đợt 3 xong phần thô\".")
    return Proposal.objects.create(link=link_row, project=project, kind=Proposal.Kind.DEBT, source_text=source_text[:5000],
                                   payload={"action": "installment", "project_id": project.pk, "code": project.code,
                                            "name": name[:100], "amount": amount, "due_on": due.isoformat(),
                                            "note": " ".join(note.split())[:300]})


def build_cancel(link_row, ref, reason, source_text):
    user = link_row.user
    if not money.can_plan_receivables(user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán tài chính huỷ đợt thanh toán của khách.")
    ref = ref.lstrip("#")
    inst = (Installment.objects.filter(pk=int(ref), project__in=projects_for(user)).select_related("project").first()
            if ref.isdigit() else None)
    if inst is None:
        raise ValidationError(f"Không thấy đợt số {ref}. Xem số đợt: /dotthu <mã công trình>.")
    if inst.cancelled_at:
        raise ValidationError("Đợt này đã huỷ.")
    return Proposal.objects.create(link=link_row, project=inst.project, kind=Proposal.Kind.DEBT, source_text=source_text[:5000],
                                   payload={"action": "cancel_installment", "installment_id": inst.pk,
                                            "label": f"{inst.project.code} · {inst.name} · {vnd(inst.amount)} · hạn {_d(inst.due_on)}",
                                            "reason": " ".join(reason.split())[:300]})


# ---------- phiếu chờ xác nhận ----------

def summary(proposal):
    p = proposal.payload
    action = p["action"]
    if action == "due":
        before = (f"hạn cũ {_d(date.fromisoformat(p['before']))}" if p["before"]
                  else f"theo số ngày được nợ: {_d(date.fromisoformat(p['terms']))}" if p.get("terms") else "chưa có hạn")
        lines = ["ĐẶT HẠN TRẢ CHỜ XÁC NHẬN", p["label"], f"Hạn trả: {before} → {_d(date.fromisoformat(p['due_on']))}"]
        if p.get("reason"):
            lines.append(f"Lý do: {p['reason']}")
    elif action == "terms":
        before = f"{p['before']} ngày" if p["before"] is not None else "chưa có"
        after = f"{p['days']} ngày" if p["days"] is not None else "bỏ (khoản không ghi hạn riêng thành chưa có hạn trả)"
        lines = ["SỐ NGÀY ĐƯỢC NỢ CHỜ XÁC NHẬN", p["label"], f"{before} → {after}",
                 "Áp cho mọi khoản nhận hàng / khoán của đối tượng này chưa ghi hạn riêng (cả khoản cũ)."]
    elif action == "installment":
        lines = ["ĐỢT THANH TOÁN CỦA KHÁCH CHỜ XÁC NHẬN",
                 f"{p['code']} · {p['name']} · {vnd(Decimal(p['amount']))} · hạn {_d(date.fromisoformat(p['due_on']))}"]
        if p.get("note"):
            lines.append(f"Ghi chú: {p['note']}")
        data = next(iter(aging.receivables([proposal.project], timezone.localdate())), None) if proposal.project else None
        planned = (data["planned"] if data else Decimal(0)) + Decimal(p["amount"])
        contract = data["contract"] if data else None
        if contract is not None and planned > contract:
            lines.append(f"⚠ Tổng các đợt sau khi thêm {vnd(planned)} lớn hơn giá trị hợp đồng {vnd(contract)}.")
        elif contract is None:
            lines.append("⚠ Công trình chưa có giá trị hợp đồng (đơn giá bán trên bảng khối lượng) để đối chiếu tổng các đợt.")
    else:
        lines = ["HUỶ ĐỢT THANH TOÁN CHỜ XÁC NHẬN", p["label"], f"Lý do: {p['reason']}"]
    lines.append("Bấm Xác nhận để ghi vào Tend, hoặc nhắn lại để sửa.")
    return "\n".join(lines)


def confirm(proposal, user):
    from money.models import Entry
    p = proposal.payload
    action = p["action"]
    if action == "due":
        money.set_due(user, Entry.objects.get(pk=p["entry_id"]), p["due_on"], p.get("reason", ""), channel="telegram")
        return {"url": "/bao-cao/cong-no/"}
    if action == "terms":
        if not money.can_set_due(user):
            raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ đặt số ngày được nợ.")
        money.set_party(user, Party.objects.get(pk=p["party_id"]), channel="telegram", terms_days=p["days"])
        return {"url": "/doi-tuong/"}
    if action == "installment":
        money.add_installment(user, proposal.project, p["name"], p["amount"], p["due_on"], p.get("note", ""), channel="telegram")
        return {"url": "/bao-cao/cong-no/"}
    money.cancel_installment(user, Installment.objects.get(pk=p["installment_id"]), p["reason"], channel="telegram")
    return {"url": "/bao-cao/cong-no/"}
