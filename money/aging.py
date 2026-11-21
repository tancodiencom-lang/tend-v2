"""Tuổi nợ (tính lúc đọc, không lưu bảng tổng hợp).

- Phải trả (nhà cung cấp, tổ thợ): mỗi khoản nhận hàng / khoán tính là một khoản nợ; tiền đã trả cùng đối tượng, cùng công
  trình trừ dần vào khoản CŨ NHẤT trước. Khoản còn nợ có hạn (ghi riêng, hoặc ngày phiếu + số ngày được nợ của đối tượng)
  → quá hạn / đến hạn trong 7 ngày / còn hạn; không có hạn → "chưa có hạn trả" (không đoán).
- Phải thu (khách): đợt thanh toán theo hợp đồng; tiền khách đã thu trừ dần vào đợt có hạn sớm nhất. Quá hạn > 3 ngày cần
  nhắc khách, > 15 ngày là Gấp (bảng cảnh báo trong tài liệu mô tả công việc).
"""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum

from overview.compute import contract_value, party_name

from .models import Entry, Installment, Kind
from .services import effective_due

ZERO = Decimal(0)
SOON = 7
LATE, URGENT = 3, 15                     # khách: quá 3 ngày nhắc, quá 15 ngày Gấp
PAIRS = {Kind.SUPPLIER_GOODS: Kind.SUPPLIER_PAYMENT, Kind.TEAM_WORK: Kind.TEAM_PAYMENT}
GROUP = {Kind.SUPPLIER_GOODS: "Nhà cung cấp", Kind.SUPPLIER_PAYMENT: "Nhà cung cấp", Kind.TEAM_WORK: "Tổ thợ",
         Kind.TEAM_PAYMENT: "Tổ thợ"}


def _status(due, today):
    if due is None:
        return "chua_han", None
    if due < today:
        return "qua_han", (today - due).days
    if due <= today + timedelta(days=SOON):
        return "sap_han", (due - today).days
    return "con_han", (due - today).days


def label(row):
    """Ký hiệu + chữ (không chỉ màu)."""
    status, days, due = row["status"], row["days"], row["due"]
    if status == "qua_han":
        return f"▼ quá hạn {days} ngày (hạn {due:%d/%m})"
    if status == "sap_han":
        return f"● đến hạn {due:%d/%m}" + (" (hôm nay)" if days == 0 else f" (còn {days} ngày)")
    if status == "con_han":
        return f"○ hạn {due:%d/%m/%Y}"
    return "? chưa có hạn trả"


ORDER = {"qua_han": 0, "sap_han": 1, "con_han": 2, "chua_han": 3}


def payables(projects, today):
    """→ {"rows": [khoản còn nợ], "advances": [trả trước nhiều hơn phát sinh]}; rows xếp quá hạn lâu nhất trước."""
    entries = (Entry.objects.filter(voided_at__isnull=True, project__in=projects, kind__in=list(GROUP))
               .select_related("party_ref", "project").order_by("date", "pk"))
    groups = defaultdict(lambda: {"open": [], "paid": ZERO})
    for entry in entries:
        key = (entry.project, GROUP[entry.kind], party_name(entry))
        if entry.kind in PAIRS:
            groups[key]["open"].append(entry)
        else:
            groups[key]["paid"] += entry.amount
    rows, advances = [], []
    for (project, group, name), data in groups.items():
        paid = data["paid"]
        for entry in data["open"]:
            used = min(paid, entry.amount)
            paid -= used
            remaining = entry.amount - used
            if remaining <= 0:
                continue
            due = effective_due(entry)
            status, days = _status(due, today)
            rows.append({"project": project, "group": group, "name": name, "entry": entry, "remaining": remaining,
                         "due": due, "status": status, "days": days, "own_due": bool(entry.due_on)})
        if paid > 0:
            advances.append({"project": project, "group": group, "name": name, "amount": paid})
    rows.sort(key=lambda r: (ORDER[r["status"]], -(r["days"] or 0) if r["status"] == "qua_han" else (r["days"] or 0),
                             r["project"].code, r["name"]))
    return {"rows": rows, "advances": advances}


def receivables(projects, today):
    """Theo công trình: giá trị hợp đồng, đã thu, các đợt (còn phải thu, trạng thái), tiền thu vượt lịch đợt."""
    out = []
    for project in projects:
        plan = list(Installment.objects.filter(project=project, cancelled_at__isnull=True).order_by("due_on", "pk"))
        received = (Entry.objects.filter(project=project, voided_at__isnull=True, kind=Kind.CUSTOMER_RECEIPT)
                    .aggregate(s=Sum("amount"))["s"] or ZERO)
        contract = contract_value(project)
        if not plan and not received and contract is None:
            continue
        left, items = received, []
        for inst in plan:
            used = min(left, inst.amount)
            left -= used
            remaining = inst.amount - used
            status, days = _status(inst.due_on, today) if remaining > 0 else ("da_thu", None)
            level = ""
            if status == "qua_han":
                level = "gap" if days > URGENT else ("nhac" if days > LATE else "vua_den")
            items.append({"installment": inst, "remaining": remaining, "paid": used, "status": status, "days": days,
                          "due": inst.due_on, "level": level})
        planned = sum((i.amount for i in plan), ZERO)
        out.append({"project": project, "contract": contract, "received": received, "planned": planned, "items": items,
                    "has_plan": bool(plan), "ahead": left,
                    "plan_gap": (contract - planned) if (plan and contract is not None and planned != contract) else None})
    return out


def forecast(pay_rows, recv_projects, today, weeks=4):
    """Dòng tiền 4 tuần tới theo hạn: thu (đợt khách đến hạn) − chi (khoản nợ đến hạn). Quá hạn và chưa có hạn tách riêng."""
    spans = [(today + timedelta(days=7 * i), today + timedelta(days=7 * i + 6)) for i in range(weeks)]
    out = [{"start": a, "end": b, "inflow": ZERO, "outflow": ZERO} for a, b in spans]
    late_in = late_out = no_due_out = ZERO
    for row in pay_rows:
        if row["due"] is None:
            no_due_out += row["remaining"]
        elif row["due"] < today:
            late_out += row["remaining"]
        else:
            for week in out:
                if week["start"] <= row["due"] <= week["end"]:
                    week["outflow"] += row["remaining"]
    for project in recv_projects or []:
        for item in project["items"]:
            if item["remaining"] <= 0:
                continue
            if item["due"] < today:
                late_in += item["remaining"]
            else:
                for week in out:
                    if week["start"] <= item["due"] <= week["end"]:
                        week["inflow"] += item["remaining"]
    for week in out:
        week["net"] = week["inflow"] - week["outflow"]
    return {"weeks": out, "late_in": late_in, "late_out": late_out, "no_due_out": no_due_out}
