"""Bot Telegram của Tend mới: kết nối, nhận tin/ảnh, lập phiếu, hỏi lại, Xác nhận/Bỏ, lệnh.

Phiếu chưa ghi gì. Bấm ✓ Xác nhận mới gọi `field.services` / `asks.services` (một giao dịch, nhật ký, chống gửi
trùng bằng mã phiếu). Phiếu chỉ hiện nút Xác nhận khi đã đủ: mọi ảnh đã rõ chi tiết, báo giảm có lý do, mọi tên
thợ đã rõ người (hoặc đã chọn thêm mới), chấm công khác số đã chấm có lý do.
"""
import hashlib
import re
import secrets
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from asks import services as asks
from asks.models import Ask
from core.audit import record_save
from core.models import User
from core.text import vn_number
from drawings.models import Item
from field import services as field
from field.models import Attendance, Worker
from field.workers import match
from projects.models import Project

from money import services as money

from . import ai, money_bot, parse, reads, telegram
from .models import ChatLink, ChatPhoto, ChatUpdate, LinkCode, Proposal

CODE_TTL = timedelta(minutes=15)
PROPOSAL_TTL = timedelta(hours=24)
PHOTO_WINDOW = timedelta(minutes=30)
IMAGE_FILE_TYPES = {"image/jpeg", "image/png", "image/webp"}
CODE_ONLY = re.compile(r"^\s*([A-Za-z]{1,3}[0-9M]\d*(?:\.\d+)+)\s*$")
HELP = ("Tend qua Telegram:\n"
        "• Báo cáo + chấm công: gửi ảnh rồi nhắn, ví dụ \"TN-22 T2.5 40%, S2.1 xong. Công: tổ xây: Hùng, Nam, Lan nửa ngày; "
        "Tâm 4 giờ\". Anh/chị gửi báo cáo là tự có công tại công trình đó. Bot lập phiếu, bấm Xác nhận mới ghi.\n"
        "• Ảnh cho từng chi tiết: chú thích ảnh ghi mã (vd T2.5); ảnh không chú thích bot sẽ hỏi.\n"
        "• Hỏi: \"TN-22 đang chậm bao nhiêu?\", \"tháng này Hùng bao nhiêu công?\"\n"
        "• Lệnh: /tongquan, /cong (công tháng này), /yeucau, /congtrinh, /suacong TN-22 17/11 Hùng 0,5 lý do …, "
        "/dongia Hùng 450000 khoán (Giám đốc, kế toán), /ngatketnoi, /trogiup\n"
        "• Kế toán: chụp ảnh phiếu hoặc nhắn \"trả NCC 5tr Cát Hùng Vương TN-22 PC12 ck - cát vàng\" (loại: thu khách, thu khác, "
        "nhận hàng, khoán, trả NCC, trả tổ, trả lương, chi). Tra cứu: /thuchi hôm nay|tuần|tháng, /congno [TN-22], /baocao tuần|tháng, "
        "/doituong <tên>. Huỷ: /huy PC12 lý do.")


# ---------- kết nối ----------

def _digest(code):
    return hashlib.sha256(f"tend-chat:{code}".encode()).hexdigest()


@transaction.atomic
def new_code(user):
    """Mã 8 chữ số, một lần, 15 phút; chỉ lưu bản băm. Lấy mã mới thì mã cũ hết hiệu lực."""
    now = timezone.now()
    LinkCode.objects.filter(user=user, used_at__isnull=True, expires_at__gt=now).update(expires_at=now)
    code = f"{secrets.randbelow(10 ** 8):08d}"
    LinkCode.objects.create(user=user, digest=_digest(code), expires_at=now + CODE_TTL)
    return code


@transaction.atomic
def link(code, external_id, chat_id, display_name):
    row = (LinkCode.objects.select_for_update(of=("self",)).filter(digest=_digest(code.strip()), used_at__isnull=True,
                                                       expires_at__gt=timezone.now()).select_related("user").first())
    if row is None or not row.user.is_active:
        raise ValidationError("Mã không đúng hoặc đã hết hạn. Lấy mã mới ở Tend › Tài khoản › Kết nối Telegram.")
    row.used_at = timezone.now()
    row.save(update_fields=["used_at"])
    for old in ChatLink.objects.select_for_update(of=("self",)).filter(active=True).filter(Q(user=row.user) | Q(external_id=str(external_id))):
        old.active = False
        record_save(old, row.user, "Thay kết nối Telegram", channel="telegram")
    return record_save(ChatLink(user=row.user, external_id=str(external_id), chat_id=str(chat_id), display_name=display_name[:200]),
                       row.user, "Kết nối Telegram bằng mã một lần", channel="telegram")


@transaction.atomic
def unlink(link_row, actor, reason="Ngắt kết nối Telegram", channel="telegram"):
    row = ChatLink.objects.select_for_update(of=("self",)).get(pk=link_row.pk)
    if row.active:
        row.active = False
        record_save(row, actor, reason, channel=channel)


def _state(link_row, **changes):
    state = dict(link_row.state or {})
    for key, value in changes.items():
        if value is None:
            state.pop(key, None)
        else:
            state[key] = value
    link_row.state = state
    ChatLink.objects.filter(pk=link_row.pk).update(state=state)


# ---------- lập phiếu báo cáo ----------

def _unassigned_photos(link_row):
    return list(link_row.photos.filter(proposal__isnull=True, received_at__gte=timezone.now() - PHOTO_WINDOW))


def _label(worker):
    return f"{worker.name} ({worker.get_kind_display().lower()}{', ' + worker.team if worker.team else ''})"


def _person(entry, project, work_date):
    """Một tên trong tin → dòng chấm công trong phiếu (rõ người / cần hỏi / người mới)."""
    name = " ".join(str(entry.get("name") or "").split())[:100]
    days = field.days_from(entry.get("days"), entry.get("hours"))
    row = {"name": name, "days": str(days), "team": str(entry.get("team") or "")[:100], "reason": ""}
    result = match(name, project)
    if result.get("worker"):
        _resolve(row, result["worker"], project, work_date)
    elif result.get("candidates"):
        row.update(status="ask", candidates=[{"id": w.pk, "label": _label(w)} for w in result["candidates"]])
    else:
        row.update(status="new")
    return row


def _resolve(row, worker, project, work_date):
    row.update(status="ok", worker_id=worker.pk, label=_label(worker), candidates=[])
    row.pop("new", None)
    existing = Attendance.objects.filter(project=project, work_date=work_date, worker=worker).first()
    if existing is not None:
        row["existing"] = str(existing.days)
        if existing.days == Decimal(row["days"]):
            row["status"] = "same"
    other = field.worker_day_total(worker, work_date, exclude_project=project)
    if other + Decimal(row["days"]) > 1:
        row["warning"] = f"đã chấm {vn_number(other)} công ở công trình khác cùng ngày"


def _work_date(data):
    raw = str(data.get("work_date") or "")[:10]
    return field._work_date(raw or None)


def build_report(link_row, data, source_text):
    user = link_row.user
    project = reads.project(user, data.get("project_code"))
    if not field.can_report(user, project):
        raise PermissionDenied("Anh/chị không được báo cáo công trình này (chỉ giám sát được giao và Giám đốc).")
    work_date = _work_date(data)
    items = {i.code.casefold(): i for i in Item.objects.filter(project=project, active=True)}
    lines, unknown = [], []
    for line in data.get("lines") or []:
        item = items.get(str(line.get("code") or "").strip().casefold())
        if item is None:
            unknown.append(str(line.get("code")))
            continue
        percent = line.get("percent")
        quantity = line.get("quantity") if percent in (None, "") else None
        if percent not in (None, "") and not 0 <= float(percent) <= 100:
            raise ValidationError(f"Phần trăm của {item.code} phải từ 0 đến 100.")
        entry = {"item_id": item.pk, "code": item.code, "work": item.work, "unit": item.unit,
                 "percent": percent if percent not in (None, "") else None, "quantity": quantity,
                 "note": str(line.get("note") or "")[:2000], "reason": str(line.get("reason") or "").strip()[:1000]}
        before = field.decrease_from(item, Decimal(str(percent)) if entry["percent"] is not None else None,
                                     Decimal(str(quantity)) if quantity is not None else None)
        if before is not None:
            entry["decrease_from"] = vn_number(before.quantize(Decimal("0.1")))
        lines.append(entry)
    if unknown:
        raise ValidationError("Không thấy chi tiết " + ", ".join(unknown) + f" trên bản vẽ {project.code}. Nhắn lại đúng mã (vd T2.5).")
    people = [_person(p, project, work_date) for p in data.get("people") or [] if str(p.get("name") or "").strip()]
    photos = _unassigned_photos(link_row)
    with transaction.atomic():
        proposal = Proposal.objects.create(link=link_row, project=project, kind=Proposal.Kind.REPORT, source_text=source_text[:5000],
                                           payload={"work_date": work_date.isoformat(), "note": str(data.get("note") or "")[:5000],
                                                    "lines": lines, "people": people})
        for photo, target in zip(photos, assign_photos(photos, lines)):
            photo.proposal, photo.target = proposal, target
            photo.save(update_fields=["proposal", "target"])
    _after_change(link_row, proposal)
    return proposal


def assign_photos(photos, lines):
    """Không đoán theo thứ tự: ảnh chú thích mã vào đúng chi tiết; ảnh không chú thích chỉ tự gán khi phiếu có một chi
    tiết hoặc chỉ còn đúng một chi tiết chưa có ảnh; còn lại "?" để người gửi chọn. Trả target theo thứ tự ảnh."""
    by_code = {line["code"].casefold(): f"item:{line['item_id']}" for line in lines}
    targets = [by_code.get(p.target[5:].casefold(), "") if p.target.startswith("code:") else None for p in photos]
    covered = {t for t in targets if t}
    missing = [f"item:{line['item_id']}" for line in lines if f"item:{line['item_id']}" not in covered]
    for index, target in enumerate(targets):
        if target is None:
            targets[index] = missing[0] if len(missing) == 1 else f"item:{lines[0]['item_id']}" if len(lines) == 1 else "?" if lines else ""
    return targets


def needs_reason(proposal):
    p = proposal.payload
    return [line["code"] for line in p.get("lines") or [] if line.get("decrease_from") and not line.get("reason")] + [
        person["label"] for person in p.get("people") or []
        if person.get("status") == "ok" and person.get("existing") and not person.get("reason")]


def unresolved_people(proposal):
    return [(i, p) for i, p in enumerate(proposal.payload.get("people") or []) if p.get("status") in ("ask", "new")]


def blockers(proposal):
    if proposal.kind == Proposal.Kind.MONEY:
        return money_bot.blockers(proposal)
    if proposal.kind != Proposal.Kind.REPORT:
        return []
    out = []
    if proposal.photos.filter(target="?").exists():
        out.append("chọn chi tiết cho ảnh")
    if unresolved_people(proposal):
        out.append("xác định người")
    if needs_reason(proposal):
        out.append("nhắn lý do")
    return out


def _after_change(link_row, proposal):
    _state(link_row, reason_for=str(proposal.pk) if needs_reason(proposal) else None)


def summary(proposal):
    if proposal.kind == Proposal.Kind.MONEY:
        return money_bot.summary(proposal)
    if proposal.kind == Proposal.Kind.VOID:
        return money_bot.void_summary(proposal) + "\nBấm Xác nhận để huỷ phiếu này."
    p = proposal.payload
    out = [f"PHIẾU CHỜ XÁC NHẬN · {proposal.project.code if proposal.project else ''}"]
    if proposal.kind == Proposal.Kind.REPORT:
        out.append(f"Báo cáo ngày {p['work_date'][8:10]}/{p['work_date'][5:7]}/{p['work_date'][:4]}")
        for line in p.get("lines") or []:
            value = (f"{vn_number(Decimal(str(line['percent'])))}% (ước lượng)" if line.get("percent") is not None
                     else f"tổng đã làm {vn_number(Decimal(str(line['quantity'])))} {line['unit']}")
            count = proposal.photos.filter(target=f"item:{line['item_id']}").count()
            out.append(f"• {line['code']} – {line['work']}: {value} · " + (f"{count} ảnh" if count else "chưa có ảnh"))
            if line.get("decrease_from"):
                out.append(f"  ⚠ Thấp hơn mức đã báo {line['decrease_from']}. Lý do: {line.get('reason') or 'chưa có'}")
        people = p.get("people") or []
        out.append(f"Chấm công: {proposal.link.user} (giám sát, có mặt)" + (f" + {len(people)} người:" if people else ""))
        for person in people:
            days = vn_number(Decimal(person["days"]))
            if person["status"] == "ok":
                line = f"• {person['label']}: {days} công"
                if person.get("existing"):
                    line += f" (đã chấm {vn_number(Decimal(person['existing']))} → sửa; lý do: {person.get('reason') or 'chưa có'})"
            elif person["status"] == "same":
                line = f"• {person['label']}: đã chấm {days} công, giữ nguyên"
            elif person["status"] == "new_ok":
                line = f"• {person['name']}: {days} công · thêm thợ mới ({person['new']['kind_label']}{', ' + person['team'] if person['team'] else ''})"
            elif person["status"] == "skip":
                line = f"• {person['name']}: bỏ qua"
            else:
                line = f"• {person['name']}: {days} công · ? chưa rõ người (bấm chọn ở tin bot hỏi)"
            if person.get("warning"):
                line += f" ⚠ {person['warning']}"
            out.append(line)
        common = proposal.photos.filter(target="").count()
        if common:
            out.append(f"Ảnh chung: {common}")
        waiting = proposal.photos.filter(target="?").count()
        if waiting:
            out.append(f"Còn {waiting} ảnh chưa chọn chi tiết: bấm nút dưới từng ảnh bot gửi lại.")
        if p.get("lines") and any(not proposal.photos.filter(target=f"item:{l['item_id']}").exists() for l in p["lines"]):
            out.append("Thêm ảnh cho chi tiết: gửi ảnh, chú thích ghi mã chi tiết (vd T2.5).")
        if needs_reason(proposal):
            out.append("Nhắn lý do (báo thấp hơn hoặc sửa công) cho: " + ", ".join(needs_reason(proposal)) + ".")
    elif proposal.kind == Proposal.Kind.FIX:
        out.append(f"Sửa chấm công {p['label']} ngày {p['work_date'][8:10]}/{p['work_date'][5:7]}: "
                   f"{vn_number(Decimal(p['before']))} → {vn_number(Decimal(p['days']))} công. Lý do: {p['reason']}")
    elif proposal.kind == Proposal.Kind.RATE:
        parts = []
        if p.get("day_rate") is not None:
            parts.append(f"đơn giá {vn_number(Decimal(p['day_rate']))} ₫/công")
        if p.get("pay"):
            parts.append("tổ khoán" if p["pay"] == "khoan" else "công nhật")
        out.append(f"Sửa thợ {p['label']}: " + ", ".join(parts))
    else:
        out.append(f"Trả lời yêu cầu: {p['note']}")
    blocked = blockers(proposal)
    out.append(("Còn thiếu: " + ", ".join(blocked) + ". Xong mới có nút Xác nhận.") if blocked
               else "Bấm Xác nhận để ghi vào Tend, hoặc nhắn lại để sửa.")
    return "\n".join(out)


def _buttons(proposal):
    cancel = {"text": "× Bỏ", "data": f"no:{proposal.pk}"}
    return [[cancel]] if blockers(proposal) else [[{"text": "✓ Xác nhận", "data": f"ok:{proposal.pk}"}, cancel]]


def ask_photos(chat_id, proposal):
    lines = proposal.payload.get("lines") or []
    for photo in proposal.photos.filter(target="?", prompt_message_id__isnull=True):
        buttons, row = [], []
        for index, line in enumerate(lines[:12]):
            row.append({"text": line["code"], "data": f"ph:{photo.pk}:{index}"})
            if len(row) == 3:
                buttons.append(row)
                row = []
        buttons.append(row + [{"text": "Ảnh chung", "data": f"ph:{photo.pk}:c"}])
        sent = telegram.send_photo(chat_id, photo.file_id, "Ảnh này của chi tiết nào?", buttons) if photo.file_id else None
        photo.prompt_message_id = (sent or {}).get("message_id") or 0
        photo.save(update_fields=["prompt_message_id"])


def ask_people(chat_id, proposal):
    asked = set((proposal.payload.get("asked") or []))
    for index, person in unresolved_people(proposal):
        if str(index) in asked:
            continue
        key = f"{str(proposal.pk)[:8]}:{index}"
        if person["status"] == "ask":
            buttons = [[{"text": c["label"], "data": f"wk:{key}:{c['id']}"}] for c in person["candidates"][:5]]
            text = f"\"{person['name']}\" là ai?"
        else:
            buttons = []
            text = f"\"{person['name']}\" chưa có trong danh sách thợ. Thêm mới" + (f" (tổ {person['team']})" if person["team"] else "") + "?"
        buttons.append([{"text": "Thêm mới: thợ", "data": f"wk:{key}:tho"}, {"text": "Thêm mới: phụ", "data": f"wk:{key}:phu"}])
        buttons.append([{"text": "Bỏ tên này", "data": f"wk:{key}:x"}])
        telegram.send(chat_id, text, buttons)
        asked.add(str(index))
    if asked != set(proposal.payload.get("asked") or []):
        proposal.payload = {**proposal.payload, "asked": sorted(asked)}
        proposal.save(update_fields=["payload"])


def _proposal_reply(chat_id, proposal):
    telegram.send(chat_id, summary(proposal), _buttons(proposal))
    if proposal.kind == Proposal.Kind.REPORT:
        ask_photos(chat_id, proposal)
        ask_people(chat_id, proposal)
    elif proposal.kind == Proposal.Kind.MONEY:
        money_bot.ask(chat_id, proposal, reads.projects(proposal.link.user))


def _pending(link_row, short_id):
    for proposal in Proposal.objects.select_for_update(of=("self",)).filter(link__user=link_row.user, state=Proposal.State.PENDING,
                                                               created_at__gte=timezone.now() - PROPOSAL_TTL):
        if str(proposal.pk).startswith(short_id):
            return proposal
    return None


@transaction.atomic
def choose_photo(link_row, photo_id, choice):
    photo = (ChatPhoto.objects.select_for_update(of=("self",)).filter(pk=photo_id, link__user=link_row.user, target="?",
                                                         proposal__state=Proposal.State.PENDING).select_related("proposal").first())
    if photo is None:
        return "Ảnh này đã được chọn hoặc phiếu không còn chờ.", None
    lines = photo.proposal.payload.get("lines") or []
    if choice == "c":
        photo.target, label = "", "ảnh chung"
    elif choice.isdecimal() and int(choice) < len(lines):
        photo.target, label = f"item:{lines[int(choice)]['item_id']}", lines[int(choice)]["code"]
    else:
        return "Lựa chọn không hợp lệ.", None
    photo.save(update_fields=["target"])
    return f"✓ Ảnh của {label}", photo.proposal


@transaction.atomic
def choose_person(link_row, short_id, index, choice):
    proposal = _pending(link_row, short_id)
    if proposal is None:
        return "Phiếu không còn chờ.", None
    people = [dict(p) for p in proposal.payload.get("people") or []]
    if not index.isdecimal() or int(index) >= len(people) or people[int(index)]["status"] not in ("ask", "new"):
        return "Tên này đã được chọn.", None
    person = people[int(index)]
    work_date = field._work_date(proposal.payload["work_date"])
    if choice == "x":
        person.update(status="skip")
        text = f"✓ Bỏ {person['name']}"
    elif choice in ("tho", "phu"):
        if not field.can_add_workers(link_row.user):
            return "Anh/chị không được thêm thợ.", None
        person.update(status="new_ok", new={"name": person["name"], "team": person["team"], "kind": choice,
                                             "kind_label": "thợ" if choice == "tho" else "phụ"})
        text = f"✓ Thêm mới {person['name']} ({person['new']['kind_label']})"
    elif choice.isdecimal() and int(choice) in {c["id"] for c in person.get("candidates") or []}:
        worker = Worker.objects.get(pk=int(choice))
        _resolve(person, worker, proposal.project, work_date)
        text = f"✓ {person['label']}"
    else:
        return "Lựa chọn không hợp lệ.", None
    people[int(index)] = person
    proposal.payload = {**proposal.payload, "people": people}
    proposal.save(update_fields=["payload"])
    _after_change(link_row, proposal)
    return text, proposal


@transaction.atomic
def add_reason(link_row, proposal_id, reason):
    proposal = Proposal.objects.select_for_update(of=("self",)).filter(pk=proposal_id, link__user=link_row.user, state=Proposal.State.PENDING).first()
    if proposal is None:
        return None
    reason = reason.strip()[:500]
    payload = dict(proposal.payload)
    payload["lines"] = [dict(l, reason=l.get("reason") or reason) if l.get("decrease_from") else l for l in payload.get("lines") or []]
    payload["people"] = [dict(p, reason=p.get("reason") or reason) if p.get("status") == "ok" and p.get("existing") else p
                         for p in payload.get("people") or []]
    proposal.payload = payload
    proposal.save(update_fields=["payload"])
    _after_change(link_row, proposal)
    return proposal


def attach_tagged_photo(link_row, photo):
    code = photo.target[5:].casefold()
    for proposal in Proposal.objects.filter(link=link_row, state=Proposal.State.PENDING, kind=Proposal.Kind.REPORT,
                                            created_at__gte=timezone.now() - PROPOSAL_TTL).order_by("-created_at"):
        line = next((l for l in proposal.payload.get("lines") or [] if l["code"].casefold() == code), None)
        if line:
            photo.proposal, photo.target = proposal, f"item:{line['item_id']}"
            photo.save(update_fields=["proposal", "target"])
            return proposal
    return None


def attach_album_photo(link_row, photo, group):
    album = (link_row.state or {}).get("album") or {}
    if not group or album.get("group") != group:
        return None
    proposal = Proposal.objects.filter(pk=album.get("proposal"), link=link_row, state=Proposal.State.PENDING).first()
    if proposal is None:
        return None
    lines = proposal.payload.get("lines") or []
    photo.proposal = proposal
    photo.target = f"item:{lines[0]['item_id']}" if len(lines) == 1 else "?" if lines else ""
    photo.save(update_fields=["proposal", "target"])
    return proposal


# ---------- phiếu sửa công, sửa thợ, trả lời ----------

def _one_worker(name, project=None):
    result = match(name, project)
    if result.get("worker"):
        return result["worker"]
    if result.get("candidates"):
        raise ValidationError(f"\"{name}\" có thể là: " + "; ".join(_label(w) for w in result["candidates"]) + ". Nhắn lại đúng tên đầy đủ.")
    raise ValidationError(f"Không có thợ tên \"{name}\" trong danh sách.")


def build_fix(link_row, data, source_text):
    user = link_row.user
    project = reads.project(user, data.get("project_code"))
    if not field.can_report(user, project):
        raise PermissionDenied("Anh/chị không được chấm công công trình này.")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise ValidationError("Sửa công cần lý do. Ví dụ: /suacong TN-22 17/11 Hùng 0,5 lý do về sớm")
    work_date = _work_date(data)
    worker = _one_worker(data.get("name"), project)
    days = field.days_from(data.get("days"), data.get("hours"))
    before = Attendance.objects.filter(project=project, work_date=work_date, worker=worker).values_list("days", flat=True).first() or 0
    if Decimal(before) == days:
        raise ValidationError(f"{_label(worker)} đã là {vn_number(days)} công ngày {work_date:%d/%m/%Y}, không cần sửa.")
    return Proposal.objects.create(link=link_row, project=project, kind=Proposal.Kind.FIX, source_text=source_text[:5000],
                                   payload={"work_date": work_date.isoformat(), "worker_id": worker.pk, "label": _label(worker),
                                            "days": str(days), "before": str(before), "reason": reason[:500]})


def build_rate(link_row, data, source_text):
    if not field.can_set_rates(link_row.user):
        raise PermissionDenied("Chỉ Giám đốc và kế toán nội bộ sửa đơn giá thợ.")
    worker = _one_worker(data.get("name"))
    return Proposal.objects.create(link=link_row, kind=Proposal.Kind.RATE, source_text=source_text[:5000],
                                   payload={"worker_id": worker.pk, "label": _label(worker),
                                            "day_rate": str(data["day_rate"]) if data.get("day_rate") is not None else None,
                                            "pay": data.get("pay")})


def build_answer(link_row, ask_id, note, source_text):
    ask = next((a for a in asks.pending_for(link_row.user) if str(a.pk) == str(ask_id)), None)
    if ask is None:
        raise ValidationError("Không thấy yêu cầu đang chờ anh/chị trả lời.")
    return Proposal.objects.create(link=link_row, project=ask.project, kind=Proposal.Kind.ANSWER, source_text=source_text[:5000],
                                   payload={"ask_id": str(ask.pk), "note": str(note)[:5000]})


# ---------- xác nhận ----------

@transaction.atomic
def decide(link_row, proposal_id, confirm):
    proposal = Proposal.objects.select_for_update(of=("self",)).filter(pk=proposal_id, link__user=link_row.user).select_related("project").first()
    if proposal is None:
        return "Không tìm thấy phiếu."
    if proposal.state != Proposal.State.PENDING:
        return f"Phiếu {proposal.get_state_display().lower()}."
    user = link_row.user
    if (link_row.state or {}).get("reason_for") == str(proposal.pk):
        _state(link_row, reason_for=None)
    if not confirm:
        proposal.state, proposal.decided_at = Proposal.State.CANCELLED, timezone.now()
        record_save(proposal, user, "Bỏ phiếu chat", channel="telegram")
        return "Đã bỏ phiếu. Không có gì được ghi vào Tend."
    if timezone.now() - proposal.created_at > PROPOSAL_TTL:
        return "Phiếu đã quá 24 giờ. Nhắn lại để lập phiếu mới theo số liệu hiện tại."
    if blockers(proposal):
        return "Phiếu còn thiếu: " + ", ".join(blockers(proposal)) + "."
    p = proposal.payload
    files = []
    try:
        with transaction.atomic():
            if proposal.kind == Proposal.Kind.REPORT:
                for photo in proposal.photos.all():
                    content = ContentFile(photo.image.read(), name=Path(photo.image.name).name)
                    photo.image.close()
                    files.append((content, int(photo.target[5:]) if photo.target.startswith("item:") else None))
                attendance = [{"worker_id": x["worker_id"], "days": x["days"], "reason": x.get("reason", "")}
                              for x in p.get("people") or [] if x["status"] == "ok"]
                attendance += [{"new": x["new"], "days": x["days"]} for x in p.get("people") or [] if x["status"] == "new_ok"]
                report = field.submit_report(user, proposal.project, {"work_date": p["work_date"], "note": p.get("note", ""),
                                                                      "lines": p.get("lines") or [], "attendance": attendance},
                                             files, client_key=proposal.pk, channel="telegram")
                result = {"report": report.pk, "url": f"/ban-ve/{proposal.project.code}/"}
            elif proposal.kind == Proposal.Kind.FIX:
                worker = Worker.objects.get(pk=p["worker_id"])
                field.correct_attendance(user, proposal.project, p["work_date"], worker, Decimal(p["days"]), p["reason"])
                result = {"url": f"/cong/?thang={p['work_date'][:7]}"}
            elif proposal.kind == Proposal.Kind.RATE:
                worker = Worker.objects.get(pk=p["worker_id"])
                field.set_worker(user, worker, day_rate=Decimal(p["day_rate"]) if p.get("day_rate") is not None else None,
                                 pay=p.get("pay"), channel="telegram")
                result = {"url": "/tho/"}
            elif proposal.kind == Proposal.Kind.MONEY:
                result = money_bot.confirm(proposal, user)
            elif proposal.kind == Proposal.Kind.VOID:
                if p.get("needs_director"):
                    raise PermissionDenied("Phiếu này cần Giám đốc duyệt huỷ.")
                result = money_bot.confirm_void(proposal, user)
            else:
                ask = Ask.objects.get(pk=p["ask_id"])
                asks.answer(user, ask, p["note"])
                result = {"url": f"/ban-ve/{ask.project.code}/"}
    except (ValidationError, PermissionDenied) as error:
        proposal.state, proposal.error, proposal.decided_at = Proposal.State.FAILED, "; ".join(
            getattr(error, "messages", [str(error)]))[:480], timezone.now()
        record_save(proposal, user, "Xác nhận nhưng Tend không nhận phiếu", channel="telegram")
        return "Chưa ghi được: " + proposal.error
    proposal.state, proposal.result, proposal.decided_at = Proposal.State.CONFIRMED, result, timezone.now()
    record_save(proposal, user, "Xác nhận phiếu chat; đã ghi", channel="telegram")
    if proposal.kind == Proposal.Kind.REPORT:
        transaction.on_commit(lambda: notify_directors(proposal, user))
    return "Đã ghi vào Tend." + (f"\nXem: {settings.APP_URL}{result['url']}" if result.get("url") else "")


def notify_directors(proposal, author):
    p = proposal.payload
    lines = ", ".join(f"{l['code']} " + (f"{vn_number(Decimal(str(l['percent'])))}%" if l.get("percent") is not None
                                          else f"{vn_number(Decimal(str(l['quantity'])))} {l['unit']}") for l in p.get("lines") or [])
    people = [x for x in p.get("people") or [] if x["status"] in ("ok", "same", "new_ok")]
    text = (f"BÁO CÁO {proposal.project.code} · {author} · {p['work_date'][8:10]}/{p['work_date'][5:7]}\n"
            + (f"Tiến độ: {lines}\n" if lines else "") + f"Chấm công: giám sát + {len(people)} người"
            + (f"\nGhi chú: {p['note'][:300]}" if p.get("note") else "")
            + f"\nXem: {settings.APP_URL}/ban-ve/{proposal.project.code}/")
    for row in ChatLink.objects.filter(active=True, user__is_active=True).exclude(user=author).select_related("user"):
        if row.user.is_director:
            telegram.send(row.chat_id, text)


def notify_ask(ask):
    """Báo người được hỏi ngay khi Giám đốc tạo yêu cầu (gọi sau khi lưu)."""
    for row in ChatLink.objects.filter(user=ask.assignee, active=True):
        buttons = None if ask.items.exists() else [[{"text": "Trả lời yêu cầu này", "data": f"ans:{ask.pk}"}]]
        telegram.send(row.chat_id, asks.text(ask) + f"\nXem bản vẽ: {settings.APP_URL}/ban-ve/{ask.project.code}/", buttons)


# ---------- nhận tin ----------

def _remember(link_row, text, answer):
    history = ((link_row.state or {}).get("history") or [])[-4:]
    history += [{"role": "user", "content": text[:1500]}, {"role": "assistant", "content": answer[:1500]}]
    _state(link_row, history=history)


def handle_text(link_row, text):
    """Trả (chữ trả lời, phiếu hoặc None)."""
    user = link_row.user
    command = text.split()[0].casefold() if text.split() else ""
    if command in ("/trogiup", "/help", "/start"):
        return HELP, None
    if command == "/tongquan":
        return reads.overview_text(user), None
    if command == "/congtrinh":
        return "Công trình được giao:\n" + "\n".join(f"• {p.code} · {p.name}" for p in reads.projects(user)), None
    if command == "/cong":
        month = None
        if len(text.split()) > 1:
            try:
                m, y = text.split()[1].split("/")
                month = timezone.localdate().replace(year=int(y), month=int(m), day=1)
            except ValueError:
                return "Gõ /cong hoặc /cong 11/2026.", None
        return reads.month_text(user, month), None
    if command == "/yeucau":
        rows = asks.pending_for(user)
        return ("\n\n".join(asks.text(a) for a in rows) or "Không có yêu cầu báo cáo nào đang chờ anh/chị."), None
    if command == "/ngatketnoi":
        unlink(link_row, user)
        return "Đã ngắt kết nối Telegram với Tend.", None
    if command == "/congno":
        return reads.debts_text(user, text.split()[1] if len(text.split()) > 1 else None), None
    if command in ("/thuchi", "/baocao"):
        arg = " ".join(text.split()[1:])
        span = parse.period(arg or ("" if command == "/thuchi" else "tuần"))
        if span is None:
            return f"Gõ {command} hôm nay | hôm qua | tuần | tháng | 15/11.", None
        return (reads.entries_text if command == "/thuchi" else reads.report_text)(user, *span), None
    if command == "/doituong":
        name = " ".join(text.split()[1:])
        rows = reads.find_parties(user, name) if name else []
        return ("\n".join(f"• {r['name']} ({r['type'].lower()}){' · MISA ' + r['misa_code'] if r['misa_code'] else ''}" for r in rows)
                or "Không thấy đối tượng nào. Gõ /doituong <tên>."), None
    if command == "/huy":
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            return "Cú pháp: /huy PC12 lý do huỷ (hoặc /huy #123 lý do).", None
        return _void(link_row, parts[1], parts[2] if len(parts) > 2 else "", text)
    if command == "/suacong":
        data = parse.fix(text)
        if data is None:
            return "Cú pháp: /suacong TN-22 17/11 Hùng 0,5 lý do về sớm (0 là bỏ công).", None
        return None, build_fix(link_row, data, text)
    if command == "/dongia":
        data = parse.rate(text)
        if data is None:
            return "Cú pháp: /dongia Hùng 450000 (thêm \"khoán\" hoặc \"công nhật\" nếu đổi hình thức trả).", None
        return None, build_rate(link_row, data, text)
    state = link_row.state or {}
    if state.get("reason_for") and not text.startswith("/"):
        proposal = add_reason(link_row, state["reason_for"], text)
        if proposal is not None:
            return None, proposal
    if state.get("answer_to") and not text.startswith("/"):
        ask_id = state["answer_to"]
        _state(link_row, answer_to=None)
        return None, build_answer(link_row, ask_id, text, text)
    if ai.available():
        photos = _unassigned_photos(link_row)
        images = []
        if money.kinds_for(user):     # kế toán, Giám đốc: AI xem ảnh để đọc phiếu thu/chi
            for photo in photos[:4]:
                images.append(photo.image.read())
                photo.image.close()
        result = ai.run(text, list(state.get("history") or []),
                        f"Hôm nay {timezone.localdate():%d/%m/%Y}. Người dùng: {user} (vai {user.get_role_display()}). "
                        f"Ảnh vừa gửi chưa gắn phiếu: {len(photos)}" + (f" (ảnh số 0–{len(images) - 1} đính kèm)." if images else "."),
                        lambda name, data: reads.execute(user, name, data), images=images)
        if result is not None:
            if result["type"] == "text":
                _remember(link_row, text, result["text"])
                return result["text"], None
            data = result["input"]
            if result["tool"] == "ask_user":
                _remember(link_row, text, data.get("question", ""))
                return data.get("question") or "Anh/chị nói rõ hơn giúp.", None
            if result["tool"] == "propose_attendance_fix":
                return None, build_fix(link_row, data, text)
            if result["tool"] == "propose_answer":
                return None, build_answer(link_row, data.get("ask_id"), data.get("note", ""), text)
            if result["tool"] == "propose_entries":
                return None, money_bot.build(link_row, data, text)
            if result["tool"] == "propose_void":
                return _void(link_row, data.get("ref", ""), data.get("reason", ""), text)
            return None, build_report(link_row, data, text)
    data = parse.money(text, reads.projects(user))
    if data:
        return None, money_bot.build(link_row, data, text)
    data = parse.report(text, reads.projects(user))
    if data:
        return None, build_report(link_row, data, text)
    return ("Chưa hiểu tin nhắn. Trợ lý AI chưa bật nên bot đọc cú pháp ngắn, ví dụ:\n"
            "\"TN-22 T2.5 40% S2.1 xong. Công: tổ xây: Hùng, Nam, Lan nửa ngày; Tâm 4 giờ\" (kèm ảnh).\n/trogiup để xem lệnh."), None


def _void(link_row, ref, reason, text):
    """Huỷ phiếu: tự huỷ được thì lập phiếu chờ Xác nhận; không thì gửi Giám đốc duyệt."""
    proposal = money_bot.build_void(link_row, ref, reason, text)
    if not proposal.payload["needs_director"]:
        return None, proposal
    sent = money_bot.send_to_directors(proposal)
    if not sent:
        return ("Phiếu cũ hoặc đã xuất MISA cần Giám đốc duyệt huỷ, nhưng chưa có Giám đốc nào kết nối Telegram. "
                "Báo Giám đốc kết nối rồi gửi lại."), None
    return "Phiếu cũ hoặc đã xuất MISA: đã gửi Giám đốc duyệt huỷ. Bot sẽ báo lại khi Giám đốc quyết định.", None


def _sender(message):
    person = message.get("from") or {}
    name = " ".join(filter(None, [person.get("first_name"), person.get("last_name")])) or person.get("username") or ""
    return str(person.get("id") or ""), name


def _save_photo(link_row, sizes, caption=""):
    best = max(sizes, key=lambda p: (p.get("file_size") or 0, p.get("width") or 0))
    if link_row.photos.filter(file_unique_id=best.get("file_unique_id", "")).exists():
        return True
    got = telegram.download(best["file_id"])
    if not got:
        return False
    data, name = got
    from PIL import Image
    try:
        Image.open(BytesIO(data)).verify()
    except Exception:
        return False
    code = CODE_ONLY.match(caption or "")
    photo = ChatPhoto(link=link_row, file_unique_id=best.get("file_unique_id", "")[:128], file_id=str(best["file_id"])[:256],
                      target=f"code:{code.group(1)}" if code else "", caption=(caption or "")[:1000])
    photo.image.save(name, ContentFile(data), save=False)
    photo.save()
    return photo


def _error_text(error):
    return "; ".join(getattr(error, "messages", [str(error)]))


def handle_message(message):
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    external_id, display_name = _sender(message)
    if not chat_id or not external_id or chat.get("type") != "private":
        return
    text = (message.get("text") or message.get("caption") or "").strip()
    found = re.fullmatch(r"/(?:start|ketnoi)\s+(\d{8})", text)
    if found:
        try:
            row = link(found.group(1), external_id, chat_id, display_name)
        except ValidationError as error:
            telegram.send(chat_id, error.messages[0])
            return
        telegram.send(chat_id, f"Đã kết nối với tài khoản Tend: {row.user}.\n\n{HELP}")
        return
    link_row = ChatLink.objects.filter(external_id=external_id, active=True).select_related("user").first()
    if link_row is None or not link_row.user.is_active:
        telegram.send(chat_id, "Tài khoản Telegram này chưa kết nối với Tend. Vào Tend › Tài khoản › Kết nối Telegram để lấy mã, "
                               "rồi nhắn: /ketnoi <mã>")
        return
    if link_row.chat_id != chat_id:
        ChatLink.objects.filter(pk=link_row.pk).update(chat_id=chat_id)
    sizes = message.get("photo")
    document = message.get("document") or {}
    if not sizes and document:
        # Ảnh gửi dạng Tệp giữ nguyên độ nét (phiếu viết tay, ảnh chuyển khoản): xử lý như ảnh, lưu đúng tệp gốc
        if str(document.get("mime_type") or "").lower() not in IMAGE_FILE_TYPES:
            telegram.send(chat_id, "Tend nhận ảnh JPG, PNG hoặc WEBP. Tệp PDF/HEIC: chụp màn hình hoặc gửi dạng Ảnh.")
            return
        sizes = [document]
    if sizes:
        photo = _save_photo(link_row, sizes, text)
        if not photo:
            telegram.send(chat_id, "Không nhận được ảnh (quá 15 MB, không phải ảnh, hoặc lỗi mạng). Gửi lại giúp.")
            return
        group = message.get("media_group_id") or ""
        if photo is not True and photo.target.startswith("code:"):
            proposal = attach_tagged_photo(link_row, photo)
            if proposal is not None:
                _proposal_reply(chat_id, proposal)
            else:
                telegram.send(chat_id, f"Đã nhận ảnh cho {photo.target[5:]}. Nhắn nội dung báo cáo để lập phiếu.")
            return
        if not text:
            proposal = attach_album_photo(link_row, photo, group) if photo is not True else None
            if proposal is not None:
                if photo.target == "?":
                    ask_photos(chat_id, proposal)
                return
            if not group or (link_row.state or {}).get("last_group") != group:
                _state(link_row, last_group=group)
                telegram.send(chat_id, "Đã nhận ảnh. Nhắn nội dung báo cáo (công trình, chi tiết, %, ai làm hôm nay) để lập phiếu.")
            return
    if not text:
        return
    try:
        answer, proposal = handle_text(link_row, text)
    except (ValidationError, PermissionDenied, ValueError) as error:
        telegram.send(chat_id, _error_text(error))
        return
    if proposal is not None:
        if message.get("media_group_id") and proposal.kind == Proposal.Kind.REPORT:
            _state(link_row, album={"group": message["media_group_id"], "proposal": str(proposal.pk)})
        _proposal_reply(chat_id, proposal)
    elif answer:
        telegram.send(chat_id, answer)


def handle_callback(callback):
    external_id, _ = _sender(callback)
    data = str(callback.get("data") or "")
    message = callback.get("message") or {}
    chat_id = str((message.get("chat") or {}).get("id") or "")
    link_row = ChatLink.objects.filter(external_id=external_id, active=True).select_related("user").first()
    if link_row is None or not link_row.user.is_active:
        telegram.answer_callback(callback.get("id", ""), "Chưa kết nối Tend.")
        return
    kind, _, value = data.partition(":")
    if kind in ("ph", "wk"):
        if kind == "ph":
            photo_id, _, choice = value.partition(":")
            text, proposal = choose_photo(link_row, photo_id, choice) if photo_id.isdecimal() else ("Lựa chọn không hợp lệ.", None)
        else:
            short_id, index, choice = (value.split(":") + ["", ""])[:3]
            text, proposal = choose_person(link_row, short_id, index, choice)
        telegram.answer_callback(callback.get("id", ""), text)
        if message.get("message_id") and text.startswith("✓"):
            (telegram.edit_caption if kind == "ph" else lambda c, m, t: telegram.edit(c, m, t))(chat_id, message["message_id"], text)
        if proposal is not None and not proposal.photos.filter(target="?").exists() and not unresolved_people(proposal):
            telegram.send(chat_id, summary(proposal), _buttons(proposal))
        return
    if kind in ("mp", "mj", "md"):
        short_id, index, choice = (value.split(":") + ["", ""])[:3]
        text, proposal = money_bot.choose(link_row, kind, short_id, index, choice)
        telegram.answer_callback(callback.get("id", ""), text)
        if message.get("message_id") and text.startswith("✓"):
            telegram.edit(chat_id, message["message_id"], text)
        if proposal is not None and not money_bot.blockers(proposal):
            telegram.send(chat_id, summary(proposal), _buttons(proposal))
        return
    if kind in ("dv", "dx"):
        text = money_bot.director_decide(link_row, value, kind == "dv")
        telegram.answer_callback(callback.get("id", ""), text)
        if message.get("message_id"):
            telegram.edit(chat_id, message["message_id"], (message.get("text") or "") + "\n\n" + text)
        return
    if kind == "ans":
        _state(link_row, answer_to=value)
        telegram.answer_callback(callback.get("id", ""))
        telegram.send(chat_id, "Nhắn câu trả lời. Bot sẽ lập phiếu để anh/chị xác nhận.")
        return
    if kind not in ("ok", "no"):
        telegram.answer_callback(callback.get("id", ""))
        return
    try:
        text = decide(link_row, value, kind == "ok")
    except (ValueError, ValidationError) as error:
        text = _error_text(error)
    telegram.answer_callback(callback.get("id", ""), text.split("\n")[0])
    if message.get("message_id"):
        telegram.edit(chat_id, message["message_id"], (message.get("text") or "") + "\n\n" + text)
    else:
        telegram.send(chat_id, text)


def handle_update(update):
    """Mỗi update_id xử lý một lần."""
    try:
        with transaction.atomic():
            ChatUpdate.objects.create(update_id=int(update["update_id"]))
    except (IntegrityError, KeyError, TypeError, ValueError):
        return
    if update.get("callback_query"):
        handle_callback(update["callback_query"])
    elif update.get("message"):
        handle_message(update["message"])
