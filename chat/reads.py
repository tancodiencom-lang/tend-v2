"""Đọc số liệu cho bot và AI — luôn chạy dưới quyền người hỏi (công trình được giao, quyền xem tiền theo vai)."""
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.utils import timezone

from asks import services as asks
from core.text import fold, vn_number, vnd
from drawings.models import Item
from field import rates
from field.models import Attendance, Worker
from field.workers import match
from overview.compute import answers, rows as progress_rows
from projects.models import projects_for


def projects(user):
    rows = projects_for(user).filter(active=True).order_by("code")
    if getattr(user, "only_project_id", None):          # trả lời trong nhóm của một công trình
        rows = rows.filter(pk=user.only_project_id)
    return list(rows)


def project(user, code):
    key = str(code or "").strip().casefold()
    found = next((p for p in projects(user) if p.code.casefold() == key), None)
    if found is None:
        raise PermissionDenied(f'Không có công trình "{code}" trong các công trình anh/chị được giao.')
    return found


def _plain(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def overview(user, proj):
    data = answers(user, proj, timezone.localdate())
    return {"project": f"{proj.code} · {proj.name}", **_plain(data)}


def items_with_percent(proj):
    today = timezone.localdate()
    return [(r.item, r.percent_at(today)) for r in progress_rows(proj)]


def find_details(user, code, query):
    proj = project(user, code)
    words = fold(query).split()
    out = []
    for item, percent in items_with_percent(proj):
        hay = fold(f"{item.code} {item.work} {item.location} {item.floor}")
        if all(w in hay for w in words):
            out.append({"code": item.code, "work": item.work, "location": item.location, "floor": item.floor,
                        "unit": item.unit, "percent": float(percent)})
    return out[:40] or [{"code": i.code, "work": i.work, "floor": i.floor} for i, _ in items_with_percent(proj)[:40]]


def find_workers(user, code=None, name=None):
    proj = project(user, code) if code else None
    if name:
        result = match(name, proj)
        found = [result["worker"]] if result.get("worker") else result.get("candidates", [])
    else:
        ids = Attendance.objects.filter(project=proj, worker__isnull=False).values_list("worker_id", flat=True) if proj else []
        found = list(Worker.objects.filter(pk__in=ids, active=True)) if proj else list(Worker.objects.filter(active=True)[:60])
    return [{"name": w.name, "team": w.team, "kind": w.get_kind_display()} for w in found]


def attendance_summary(user, code=None, name=None, start=None, end=None):
    today = timezone.localdate()
    start = date.fromisoformat(start) if start else today.replace(day=1)
    end = date.fromisoformat(end) if end else today
    allowed = projects(user)
    rows = Attendance.objects.filter(project__in=allowed, work_date__range=(start, end)).select_related("worker", "staff", "project")
    if code:
        rows = rows.filter(project=project(user, code))
    if name:
        key = fold(name)
        rows = [r for r in rows if key in fold(str(r.worker or r.staff))]
    people = defaultdict(lambda: {"days": Decimal(0), "projects": set(), "cost": Decimal(0), "missing_rate": False})
    rows = list(rows)
    rate_table = rates.table(r.worker_id for r in rows if r.worker_id)
    for r in rows:
        person = people[(r.worker.name if r.worker else str(r.staff), r.worker.team if r.worker else "Giám sát")]
        person["days"] += r.days
        person["projects"].add(r.project.code)
        if r.worker:
            pay, amount = rates.at(rate_table, r.worker, r.work_date)
            if pay == Worker.Pay.DAILY:
                if amount is None:
                    person["missing_rate"] = True
                else:
                    person["cost"] += r.days * amount
    out = {"from": start.strftime("%d/%m/%Y"), "to": end.strftime("%d/%m/%Y"), "people": []}
    for (who, team), v in sorted(people.items()):
        row = {"name": who, "team": team, "days": float(v["days"]), "projects": sorted(v["projects"])}
        if user.sees_payables:
            row["labor_cost"] = float(v["cost"])
            row["missing_rate"] = v["missing_rate"]
        out["people"].append(row)
    return out


def execute(user, name, data):
    """Công cụ cho AI."""
    if name == "list_projects":
        return {"projects": [{"code": p.code, "name": p.name} for p in projects(user)],
                "sees_money": {"payables": user.sees_payables, "receivables": user.sees_receivables, "profit": user.sees_profit}}
    if name == "project_overview":
        return overview(user, project(user, data.get("project_code")))
    if name == "find_details":
        return find_details(user, data.get("project_code"), data.get("query", ""))
    if name == "find_workers":
        return find_workers(user, data.get("project_code"), data.get("name"))
    if name == "attendance_summary":
        return attendance_summary(user, data.get("project_code"), data.get("name"), data.get("start"), data.get("end"))
    if name == "money_summary":
        return money_summary(user, data.get("project_code"), data.get("start"), data.get("end"))
    if name == "list_entries":
        return list_entries(user, data.get("project_code"), data.get("party"), data.get("start"), data.get("end"))
    if name == "find_parties":
        return find_parties(user, data.get("name", ""))
    if name == "pending_asks":
        return [{"ask_id": str(a.pk), "project": a.project.code, "note": a.note, "items": [i.code for i in a.items.all()],
                 "due": a.due.strftime("%d/%m/%Y") if a.due else ""} for a in asks.pending_for(user)]
    if name == "find_people":
        return find_people(user, data.get("name", ""))
    if name == "list_tasks":
        return list_tasks(user, data.get("name"))
    raise PermissionDenied(f"Công cụ {name} không có.")


def find_people(user, name):
    if not user.is_director:
        raise PermissionDenied("Chỉ Giám đốc tra nhân viên để giao việc.")
    from core.models import User
    key = fold(name)
    out = []
    for person in User.objects.filter(is_active=True).prefetch_related("memberships__project"):
        names = {fold(person.username), fold(person.first_name), fold(person.get_full_name())}
        if not key or any(key in n or (n and n in key) for n in names):
            out.append({"username": person.username, "name": str(person), "role": person.get_role_display(),
                        "projects": [m.project.code for m in person.memberships.all() if m.active]})
    return out[:15]


def list_tasks(user, name=None):
    from assistant import services as tasks
    now = timezone.now()
    rows = tasks.visible(user).filter(state__in=tasks.OPEN_STATES)
    if name:
        rows = [t for t in rows if fold(name) in fold(str(t.owner)) or fold(name) == fold(t.owner.username)]
    return [tasks.task_line(t, now) for t in rows][:30]


def overview_text(user):
    lines = [f"TỔNG QUAN {timezone.localdate():%d/%m/%Y}"]
    for proj in projects(user)[:15]:
        data = answers(user, proj, timezone.localdate())
        p, lab = data["progress"], data["labor"]
        row = f"\n{proj.code} · {proj.name}\n- Tiến độ: "
        if p["actual"] is not None:
            row += f"{vn_number(p['actual'])}%"
            if p["planned"] is not None:
                row += f" / kế hoạch {vn_number(p['planned'])}%"
            if p["forecast"]:
                row += f", dự kiến xong {p['forecast']:%d/%m/%Y}"
        else:
            row += p["missing"] or "chưa có dữ liệu"
        if lab["latest_date"]:
            row += f"\n- Nhân công {lab['latest_date']:%d/%m}: {vn_number(lab['total'])} công"
        if "payables" in data:
            pay = data["payables"]
            row += f"\n- Nợ NCC {vnd(pay['supplier_debt'])}; tổ thợ {vnd(pay['team_debt'])}; công nhật {vnd(pay['labor_debt'])}"
        if "customer" in data and data["customer"]["remaining"] is not None:
            row += f"\n- Khách còn phải trả {vnd(data['customer']['remaining'])}"
        if "profit" in data:
            forecast = data["profit"]["forecast"]
            row += "\n- Lãi dự báo: " + (vnd(forecast) if forecast is not None else "chưa tính được (thiếu giá vốn)")
        lines.append(row)
    lines.append(f"\nXem chi tiết: {settings.APP_URL}/tong-quan/")
    return "\n".join(lines)


def month_text(user, month=None):
    today = timezone.localdate()
    first = (month or today).replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    data = attendance_summary(user, start=first.isoformat(), end=min(last, today).isoformat())
    if not data["people"]:
        return f"Chưa có chấm công tháng {first:%m/%Y} ở các công trình anh/chị được giao."
    out = [f"CÔNG THÁNG {first:%m/%Y} (đến {data['to']})"]
    for row in data["people"]:
        line = f"• {row['name']} ({row['team'] or 'chưa có tổ'}): {vn_number(row['days'])} công · {', '.join(row['projects'])}"
        if "labor_cost" in row:
            line += f" · {vnd(row['labor_cost'])}" + (" (thiếu đơn giá)" if row["missing_rate"] else "")
        out.append(line)
    out.append(f"\nBảng công: {settings.APP_URL}/cong/?thang={first:%Y-%m}")
    return "\n".join(out)


# ---------- tiền: sổ thu chi, công nợ (theo quyền) ----------

def entries_for(user, start, end, code=None, party=None):
    from money import services as money
    from money.models import Entry
    allowed = projects_for(user)
    rows = (Entry.objects.filter(kind__in=money.visible_kinds(user), date__range=(start, end))
            .filter(models_q_project(allowed)).select_related("project", "party_ref").order_by("date", "pk"))
    if code:
        rows = rows.filter(project=project(user, code))
    if party:
        key = fold(party)
        rows = [e for e in rows if key in fold(e.party)]
    return list(rows)


def models_q_project(allowed):
    from django.db.models import Q
    return Q(project__in=allowed) | Q(project__isnull=True)


def money_totals(rows):
    from money.models import ACCRUALS, RECEIPTS
    live = [e for e in rows if not e.voided_at]
    return {"receipts": sum((e.amount for e in live if e.kind in RECEIPTS), Decimal(0)),
            "payments": sum((e.amount for e in live if e.kind not in RECEIPTS and e.kind not in ACCRUALS), Decimal(0)),
            "accruals": sum((e.amount for e in live if e.kind in ACCRUALS), Decimal(0)),
            "voided": sum(1 for e in rows if e.voided_at)}


def _range(start, end):
    today = timezone.localdate()
    return (date.fromisoformat(start) if start else today.replace(day=1)), (date.fromisoformat(end) if end else today)


def entry_line(e):
    head = f"{e.date:%d/%m} {e.number + ' ' if e.number else ''}{e.get_kind_display()} · {e.party}"
    where = f" · {e.project.code}" if e.project_id else ""
    void = f" (ĐÃ HUỶ: {e.void_reason})" if e.voided_at else ""
    return f"{head}{where}: {vnd(e.amount)}{void}"


def debts(user, proj):
    """Công nợ một công trình theo quyền (dùng lại overview.compute)."""
    data = answers(user, proj, timezone.localdate())
    out = {"project": proj.code}
    if "payables" in data:
        pay = data["payables"]
        out["suppliers"] = [{"name": r["name"], "debt": float(r["debt"])} for r in pay["suppliers"] if r["debt"]]
        out["teams"] = [{"name": r["name"], "debt": float(r["debt"])} for r in pay["teams"] if r["debt"]]
        out["labor_debt"] = float(pay["labor_debt"])
    if "customer" in data:
        out["customer_remaining"] = float(data["customer"]["remaining"]) if data["customer"]["remaining"] is not None else None
    return out


def money_summary(user, code=None, start=None, end=None):
    start, end = _range(start, end)
    rows = entries_for(user, start, end, code)
    totals = {k: (float(v) if isinstance(v, Decimal) else v) for k, v in money_totals(rows).items()}
    targets = [project(user, code)] if code else projects(user)
    return {"from": start.strftime("%d/%m/%Y"), "to": end.strftime("%d/%m/%Y"), **totals,
            "debts": [debts(user, p) for p in targets if user.sees_payables or user.sees_receivables]}


def list_entries(user, code=None, party=None, start=None, end=None):
    start, end = _range(start, end)
    return [{"id": e.pk, "date": e.date.strftime("%d/%m/%Y"), "number": e.number, "kind": e.get_kind_display(), "party": e.party,
             "project": e.project.code if e.project_id else "", "amount": float(e.amount), "voided": bool(e.voided_at)}
            for e in entries_for(user, start, end, code, party)[-60:]]


def find_parties(user, name):
    from money.parties import match
    result = match(name)
    found = [result["party"]] if result.get("party") else result.get("candidates", [])
    return [{"name": p.name, "type": p.get_type_display(), "misa_code": p.misa_code} for p in found]


def debts_text(user, code=None):
    if not (user.sees_payables or user.sees_receivables):
        return "Anh/chị không có quyền xem công nợ."
    targets = [project(user, code)] if code else projects(user)
    out = [f"CÔNG NỢ {timezone.localdate():%d/%m/%Y}"]
    for proj in targets:
        d = debts(user, proj)
        lines = [f"\n{proj.code} · {proj.name}"]
        for label, key in (("NCC", "suppliers"), ("Tổ thợ", "teams")):
            for row in d.get(key, []):
                lines.append(f"- {label} {row['name']}: {vnd(row['debt'])}")
        if d.get("labor_debt"):
            lines.append(f"- Lương công nhật còn: {vnd(d['labor_debt'])}")
        if "customer_remaining" in d:
            lines.append(f"- Khách còn phải trả: {vnd(d['customer_remaining'])}" if d["customer_remaining"] is not None
                         else "- Khách: chưa có giá trị hợp đồng")
        if len(lines) == 1:
            lines.append("- Không còn nợ")
        out += lines
    out.append(f"\nXem chi tiết: {settings.APP_URL}/bao-cao/cong-no/")
    return "\n".join(out)


def entries_text(user, start, end):
    rows = entries_for(user, start, end)
    if not rows:
        return f"Chưa có phiếu nào từ {start:%d/%m} đến {end:%d/%m/%Y}."
    totals = money_totals(rows)
    title = f"{start:%d/%m/%Y}" if start == end else f"{start:%d/%m} – {end:%d/%m/%Y}"
    out = [f"THU CHI {title}"] + [f"• {entry_line(e)}" for e in rows[-30:]]
    if len(rows) > 30:
        out.insert(1, f"(30 phiếu gần nhất trong {len(rows)} phiếu)")
    out.append(f"Tổng thu {vnd(totals['receipts'])} · tổng chi {vnd(totals['payments'])}"
               + (f" · phát sinh nợ {vnd(totals['accruals'])}" if totals["accruals"] else ""))
    out.append(f"Sổ thu chi: {settings.APP_URL}/bao-cao/thu-chi/?tu={start:%Y-%m-%d}&den={end:%Y-%m-%d}")
    return "\n".join(out)


def report_text(user, start, end):
    rows = entries_for(user, start, end)
    out = [f"BÁO CÁO {start:%d/%m} – {end:%d/%m/%Y}"]
    by_project = {}
    for e in rows:
        by_project.setdefault(e.project.code if e.project_id else "Chung", []).append(e)
    for code, items in sorted(by_project.items()):
        t = money_totals(items)
        out.append(f"• {code}: thu {vnd(t['receipts'])}, chi {vnd(t['payments'])}" + (f", phát sinh nợ {vnd(t['accruals'])}" if t["accruals"] else ""))
    if not rows:
        out.append("Chưa có phiếu nào.")
    out.append("")
    out.append(debts_text(user) if (user.sees_payables or user.sees_receivables) else "")
    return "\n".join(x for x in out if x is not None).strip()
