"""Tổ thợ và số người theo tổ (không tên): tìm tổ theo tên gõ trong tin, lấy số người mỗi ngày (tính lúc đọc).

Cùng công trình + ngày + tổ có nhiều báo cáo (sáng báo, chiều báo lại) thì lấy báo cáo mới nhất, không cộng trùng.
"""
import difflib
import re

from core.text import fold

from .models import Crew, Team

PREFIX = re.compile(r"^\s*(?:tổ|to)\s+", re.I)


def clean_name(label):
    """"Tổ xây Hùng:" → "xây Hùng" (tên tổ lưu không có chữ "tổ")."""
    return " ".join(PREFIX.sub("", str(label or "")).strip(" :.,;-()").split())[:100]


def match_team(label, project, teams=None):
    """→ {"team": Team} nếu chắc chắn; {"candidates": [Team…]} nếu tên gần giống; {} nếu là tổ mới."""
    key = fold(clean_name(label))
    if not key:
        return {}
    teams = list(teams if teams is not None else Team.objects.filter(project=project, active=True))
    exact = [t for t in teams if fold(t.name) == key]
    if exact:
        return {"team": exact[0]}
    close = difflib.get_close_matches(key, [fold(t.name) for t in teams], n=4, cutoff=0.8)
    found = [t for t in teams if fold(t.name) in close]
    contained = [t for t in teams if key in fold(t.name).split() or fold(t.name) in key.split()]
    found = list(dict.fromkeys(found + contained))
    return {"candidates": found[:5]} if found else {}


def latest(rows):
    """Crew (đã select_related report) → chỉ dòng của báo cáo mới nhất cho mỗi (công trình, ngày, tổ)."""
    best = {}
    for row in sorted(rows, key=lambda r: (r.report.created_at, r.pk)):
        best[(row.report.project_id, row.report.work_date, row.team_id)] = row
    return list(best.values())


def for_days(projects, start, end):
    """Số người theo tổ trong khoảng ngày (mới nhất mỗi ngày): [Crew] có report, team."""
    rows = (Crew.objects.filter(report__project__in=projects, report__work_date__range=(start, end))
            .select_related("report__project", "report__author", "team"))
    return latest(rows)


def text(crews):
    """[{team_label, kind, skilled, helpers, others}] hoặc [Crew] → "tổ xây (khoán) 5 người; tổ điện (chủ nhà) 2 người"."""
    out = []
    for c in crews:
        if isinstance(c, Crew):
            label = f"tổ {c.team.name} ({'chủ nhà' if c.team.kind == Team.Kind.OWNER else 'khoán'})" if c.team_id else "chưa rõ tổ"
            total, skilled, helpers = c.total, c.skilled, c.helpers
        else:
            label = c.get("display") or "chưa rõ tổ"
            skilled, helpers = int(c.get("skilled") or 0), int(c.get("helpers") or 0)
            total = skilled + helpers + int(c.get("others") or 0)
        parts = [f"{skilled} thợ" if skilled else "", f"{helpers} phụ" if helpers else ""]
        detail = ", ".join(p for p in parts if p)
        out.append(f"{label} {total} người" + (f" ({detail})" if detail and (skilled + helpers) != 0 and detail != f"{total} thợ" else ""))
    return "; ".join(out)
