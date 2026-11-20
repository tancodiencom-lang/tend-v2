"""Bảng công chia khối để dễ tìm, dễ soát (chủ dự án 20/11/2026: giám sát thuộc khối văn phòng, thợ thuộc khối công nhân).

    KHỐI VĂN PHÒNG   giám sát (và Giám đốc khi tự gửi báo cáo): mỗi người một dòng, lượt có mặt, ngày làm
    KHỐI CÔNG NHÂN   · Thợ công nhật — theo tổ ghi ở hồ sơ, có tên, có tiền (giá đúng ngày công)
                     · Tổ khoán của công ty — số người mỗi ngày theo báo cáo (không cần tên) + thợ khoán có tên nếu có
                     · Lương tháng (vd lái xe)
                     · Số người chưa rõ tổ — tin chỉ ghi "3 thợ 2 phụ"
    TỔ CỦA CHỦ NHÀ   số người mỗi ngày, ghi để phối hợp, không tính tiền
Mỗi nhóm có dòng cộng; mỗi khối có dòng tổng. Tính lúc đọc từ chấm công và số người theo tổ (field/crews.py).
"""
import calendar
from collections import defaultdict
from decimal import Decimal

from core.models import Role
from core.text import fold

from . import crews as crew_lookup
from . import rates
from .models import Attendance, Team, Worker

ZERO = Decimal(0)
OFFICE, WORKERS, OWNER = "van-phong", "cong-nhan", "chu-nha"


def month_days(first):
    return calendar.monthrange(first.year, first.month)[1]


def _cells(days):
    return [None] * days


def _add(cells, day, value):
    cells[day - 1] = (cells[day - 1] or ZERO) + value


def _sum_cells(rows, days):
    out = _cells(days)
    for row in rows:
        for i, value in enumerate(row["cells"]):
            if value:
                out[i] = (out[i] or ZERO) + value
    return out


def build(user, projects, start, end, search=""):
    """→ {"days": [1..n], "sections": [...], "rows": [mọi dòng người], "cost", "missing"} trong khoảng ngày (một tháng).

    sections: [{key, title, note, groups: [{title, note, rows, cells, total, unit}], cells, total, count}].
    Dòng người: {name, sub, team, staff, projects, cells, total, workdays, presence, kind?, pay?, rate?, cost?, unit}."""
    days = (end - start).days + 1
    offset = start.day
    money = user.sees_payables
    rows = Attendance.objects.filter(project__in=projects, work_date__range=(start, end)).select_related(
        "worker__trade", "staff", "project")
    rows = list(rows)
    rate_table = rates.table(r.worker_id for r in rows if r.worker_id)
    people = {}
    for r in rows:
        key = ("w", r.worker_id) if r.worker_id else ("s", r.staff_id)
        person = people.setdefault(key, {"worker": r.worker, "staff": r.staff, "cells": _cells(days), "projects": set(),
                                         "dates": set(), "presence": 0, "cost": None, "rate": None, "no_rate": False})
        _add(person["cells"], r.work_date.day - offset + 1, r.days)
        person["projects"].add(r.project.code)
        person["dates"].add(r.work_date)
        person["presence"] += 1
        if r.worker_id:
            pay, amount = rates.at(rate_table, r.worker, r.work_date)
            if pay == Worker.Pay.DAILY:
                if amount is None:
                    person["no_rate"] = True
                else:
                    person["cost"] = (person["cost"] or ZERO) + r.days * amount
                    person["rate"] = amount

    office, daily, contract, monthly = defaultdict(list), defaultdict(list), defaultdict(list), []
    titles = {}
    for (kind, _), p in people.items():
        total = sum((c for c in p["cells"] if c), ZERO)
        row = {"cells": p["cells"], "total": total, "workdays": len(p["dates"]), "presence": p["presence"],
               "projects": ", ".join(sorted(p["projects"])), "unit": "công", "staff": kind == "s"}
        worker = p["worker"]
        if kind == "s":
            role = "Giám đốc" if p["staff"].is_director else p["staff"].get_role_display()
            row.update(name=str(p["staff"]), team=role, sub=f"{role.lower()} · có mặt {p['presence']} lượt")
            office[role].append(row)
            continue
        trade = worker.trade.name if worker.trade_id else ""
        row.update(name=worker.name, team=worker.team, kind=worker.get_kind_display(), pay=worker.get_pay_display(), code=worker.code or "",
                   sub=" · ".join(x for x in (worker.code, (trade or worker.get_kind_display()).lower(), worker.get_pay_display().lower()) if x))
        if money:
            row["rate"] = p["rate"] if worker.pay == Worker.Pay.DAILY else None
            if p["cost"] is not None:
                row["cost"] = p["cost"]
            row["no_rate"] = p["no_rate"]
        if trade == "Giám sát":
            office["Giám sát"].append(row)
        elif worker.pay == Worker.Pay.DAILY:
            key = fold(worker.team)
            titles.setdefault(("d", key), worker.team)
            daily[key].append(row)
        elif worker.pay == Worker.Pay.TEAM:
            key = fold(crew_lookup.clean_name(worker.team))
            titles.setdefault(("k", key), crew_lookup.clean_name(worker.team))
            contract[key].append(row)
        else:
            monthly.append(row)

    owner, unknown = defaultdict(list), []
    for crew in crew_lookup.for_days(projects, start, end):
        team = crew.team
        if team is None:
            target, key = unknown, None
        elif team.kind == Team.Kind.OWNER:
            key = fold(team.name)
            titles.setdefault(("c", key), team.name)
            target = owner[key]
        else:
            key = fold(team.name)
            titles.setdefault(("k", key), team.name)
            target = contract[key]
        code = crew.report.project.code
        row = next((r for r in target if r.get("crew_of") == code), None)
        if row is None:
            row = {"name": f"Số người theo báo cáo · {code}", "sub": "không tên, không tính công nhật", "team": team.name if team else "",
                   "cells": _cells(days), "dates": set(), "projects": code, "unit": "người", "staff": False, "crew_of": code,
                   "work": []}
            target.append(row)
        _add(row["cells"], crew.report.work_date.day - offset + 1, Decimal(crew.total))
        row["dates"].add(crew.report.work_date)
        if crew.work:
            row["work"].append(f"{crew.report.work_date:%d/%m}: {crew.work}")
    for group in (*contract.values(), *owner.values(), unknown):
        for row in group:
            if row.get("crew_of"):
                row["total"] = sum((c for c in row["cells"] if c), ZERO)
                row["workdays"] = len(row.pop("dates"))
                row["presence"] = row["workdays"]
                row["work"] = "; ".join(row["work"][-3:])

    def group(title, members, note="", unit="công", contract_team=False):
        members = sorted(members, key=lambda r: (bool(r.get("crew_of")) is False, r["name"]))
        cells = _sum_cells(members, days)
        if contract_team:   # tổ khoán: ngày có số người theo báo cáo thì lấy số đó (đã gồm người có tên), không cộng trùng
            reported = _sum_cells([r for r in members if r.get("crew_of")], days)
            named = _sum_cells([r for r in members if not r.get("crew_of")], days)
            cells = [reported[i] if reported[i] else named[i] for i in range(days)]
        return {"title": title, "note": note, "rows": members, "cells": cells, "total": sum((c for c in cells if c), ZERO),
                "unit": unit, "people": sum(1 for r in members if not r.get("crew_of"))}

    sections = []
    groups = [group(role, members) for role, members in sorted(office.items(), key=lambda kv: (kv[0] != "Giám sát", kv[0]))]
    sections.append({"key": OFFICE, "title": "Khối văn phòng", "note": "Giám sát gửi báo cáo là có mặt tại công trình đó (một lượt "
                     "mỗi công trình/ngày). Lương tháng chia vào công trình theo ngày có mặt.", "groups": groups})
    groups = [group(f"Thợ công nhật · tổ {titles[('d', key)]}" if key else "Thợ công nhật · chưa ghi tổ", members,
                    "Có tên từng người, tiền công nhật tính theo đơn giá đúng ngày.")
              for key, members in sorted(daily.items(), key=lambda kv: (not kv[0], kv[0]))]
    groups += [group(f"Tổ khoán · {titles[('k', key)]}" if key else "Tổ khoán · chưa ghi tổ", members,
                     "Số người theo báo cáo, không cần tên; tiền nằm ở khoản khoán của tổ.", "người/công", contract_team=True)
               for key, members in sorted(contract.items(), key=lambda kv: (not kv[0], kv[0]))]
    if monthly:
        groups.append(group("Lương tháng", monthly, "Lương tháng chia vào công trình theo ngày có mặt."))
    if unknown:
        groups.append(group("Số người chưa rõ tổ", unknown, "Tin chỉ ghi \"3 thợ 2 phụ\": nhắc giám sát ghi tên tổ.", "người"))
    sections.append({"key": WORKERS, "title": "Khối công nhân", "note": "", "groups": groups})
    groups = [group(f"Tổ của chủ nhà · {titles[('c', key)]}", members, "Ghi để phối hợp tiến độ, không tính chi phí.", "người")
              for key, members in sorted(owner.items())]
    if groups:
        sections.append({"key": OWNER, "title": "Tổ của chủ nhà", "note": "Chủ nhà ký trực tiếp, không vào lãi lỗ.", "groups": groups})

    needle = fold(search)
    if needle:
        for section in sections:
            kept = []
            for g in section["groups"]:
                if needle in fold(g["title"]):
                    kept.append(g)
                    continue
                hits = [r for r in g["rows"] if needle in fold(f"{r['name']} {r.get('code', '')} {r.get('team', '')}")]
                if hits:
                    kept.append({**group(g["title"], hits, g["note"], g["unit"], g["title"].startswith("Tổ khoán")), "filtered": True})
            section["groups"] = kept
    for section in sections:
        section["cells"] = _sum_cells([{"cells": g["cells"]} for g in section["groups"]], days)
        section["total"] = sum((g["total"] for g in section["groups"]), ZERO)
        section["count"] = sum(g["people"] for g in section["groups"])
        section["teams"] = sum(1 for g in section["groups"] if any(r.get("crew_of") for r in g["rows"]))
        section["unit"] = {OFFICE: "công", WORKERS: "công + người theo tổ", OWNER: "người"}[section["key"]]
    flat = [r for s in sections for g in s["groups"] for r in g["rows"]]
    named = [r for r in flat if not r.get("crew_of")]
    return {"days": list(range(start.day, end.day + 1)), "sections": [s for s in sections if s["groups"] or s["key"] != OWNER],
            "rows": named, "cost": sum((r.get("cost") or ZERO for r in named), ZERO),
            "missing": sorted(r["name"] for r in named if r.get("no_rate"))}
