"""Bot cho kế toán: phiếu thu/chi từ chữ hoặc ảnh phiếu, đối tượng, huỷ phiếu (tự huỷ / chờ Giám đốc), tóm tắt ngày.

Phiếu chỉ ghi khi người nhập bấm ✓ Xác nhận, qua `money.services.add_entries` (một giao dịch, nhật ký, chống gửi trùng).
Nút Xác nhận chỉ hiện khi mọi dòng đã rõ đối tượng, có công trình (loại cần), và nghi trùng thì đã bấm "Vẫn ghi".
"""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from core.audit import record_save
from core.text import vnd
from money import services as money
from money.models import NEEDS_PROJECT, PARTY_TYPE, Entry, Kind, Party
from money.parties import match

from . import reads, telegram
from .models import ChatLink, Proposal

METHOD = {"tien_mat": "tiền mặt", "chuyen_khoan": "chuyển khoản", "khong": "không qua quỹ"}


def _unassigned(link_row):
    from .bot import _unassigned_photos
    return _unassigned_photos(link_row)


def _resolve_duplicate(row):
    if row.get("party_status") not in ("ok", "new_ok"):
        row["duplicate"] = ""
        return
    day = money.check_date(row["date"])
    twin = money.duplicate_of(row["kind"], row.get("number"), day, Decimal(row["amount"]), row.get("party_id"),
                              row.get("party_label") or row.get("party", ""))
    row["duplicate"] = f"{twin} ngày {twin.date:%d/%m/%Y}, {vnd(twin.amount)}" if twin else ""


def _set_party(row, party):
    row.update(party_status="ok", party_id=party.pk, party_label=party.name, candidates=[])
    _resolve_duplicate(row)


def build(link_row, data, source_text):
    user = link_row.user
    allowed = money.kinds_for(user)
    if not allowed:
        raise PermissionDenied("Anh/chị không nhập phiếu thu chi (chỉ Giám đốc và kế toán).")
    photos = _unassigned(link_row)
    rows = []
    for index, entry in enumerate(data.get("entries") or []):
        kind = entry.get("kind")
        if kind not in Kind.values:
            raise ValidationError("Không rõ loại phiếu (thu khách, thu khác, nhận hàng, khoán, trả NCC, trả tổ, trả lương, chi).")
        if kind not in allowed:
            raise PermissionDenied(f"Anh/chị không được nhập phiếu \"{Kind(kind).label}\""
                                   + (" (thu tiền khách do kế toán tài chính hoặc Giám đốc nhập)." if kind == Kind.CUSTOMER_RECEIPT else "."))
        amount = int(Decimal(str(entry.get("amount") or 0)))
        if amount <= 0:
            raise ValidationError("Phiếu thiếu số tiền.")
        project = reads.project(user, entry["project_code"]) if entry.get("project_code") else None
        day = money.check_date(entry.get("date") or None)
        name = " ".join(str(entry.get("party") or "").split())
        if not name and kind == Kind.CUSTOMER_RECEIPT and project:
            name = f"Khách {project.code}"
        if not name:
            raise ValidationError(f"Dòng {index + 1}: thiếu đối tượng (NCC, tổ thợ, người nhận…).")
        method = entry.get("method") or ("khong" if kind in (Kind.SUPPLIER_GOODS, Kind.TEAM_WORK) else "tien_mat")
        row = {"kind": kind, "kind_label": Kind(kind).label, "project_id": project.pk if project else None,
               "project_code": project.code if project else "", "date": day.isoformat(), "number": str(entry.get("number") or "")[:40],
               "party": name[:200], "content": str(entry.get("content") or "")[:500], "amount": amount, "method": method,
               "photo_id": None, "allow_duplicate": False, "duplicate": ""}
        index_ = entry.get("photo_index")
        if isinstance(index_, int) and 0 <= index_ < len(photos):
            row["photo_id"] = photos[index_].pk
        type_ = PARTY_TYPE.get(kind, Party.Type.OTHER)
        result = match(name, type_)
        if result.get("party"):
            _set_party(row, result["party"])
        elif result.get("candidates"):
            row.update(party_status="ask", candidates=[{"id": p.pk, "label": f"{p.name} ({p.get_type_display().lower()})"}
                                                       for p in result["candidates"]], new_type=type_)
        else:
            row.update(party_status="new", candidates=[], new_type=type_)
        rows.append(row)
    if not rows:
        raise ValidationError("Không thấy phiếu nào trong tin nhắn.")
    if len(rows) == 1 and rows[0]["photo_id"] is None and photos:
        rows[0]["photo_id"] = photos[0].pk
    used = {r["photo_id"] for r in rows if r["photo_id"]}
    extra = [p.pk for p in photos if p.pk not in used]
    with transaction.atomic():
        proposal = Proposal.objects.create(link=link_row, kind=Proposal.Kind.MONEY, source_text=source_text[:5000],
                                           payload={"entries": rows, "extra_photos": len(extra)})
        for photo in photos:
            if photo.pk in used:
                photo.proposal, photo.target = proposal, "voucher"
                photo.save(update_fields=["proposal", "target"])
    return proposal


def blockers(proposal):
    out = []
    rows = proposal.payload.get("entries") or []
    if any(r["party_status"] in ("ask", "new") for r in rows):
        out.append("xác định đối tượng")
    if any(r["kind"] in NEEDS_PROJECT and not r["project_id"] for r in rows):
        out.append("chọn công trình")
    if any(r.get("duplicate") and not r.get("allow_duplicate") for r in rows):
        out.append("xem phiếu nghi trùng")
    return out


def summary(proposal):
    rows = proposal.payload.get("entries") or []
    out = [f"PHIẾU THU CHI CHỜ XÁC NHẬN ({len(rows)} phiếu)"]
    for i, r in enumerate(rows, start=1):
        who = r.get("party_label") if r["party_status"] == "ok" else (
            f"{r['party']} (thêm mới: {Party.Type(r['new_type']).label.lower()})" if r["party_status"] == "new_ok" else f"{r['party']} ? chưa rõ")
        parts = [r["kind_label"], r["number"] or "chưa có số phiếu", f"{r['date'][8:10]}/{r['date'][5:7]}/{r['date'][:4]}", who,
                 r["project_code"] or ("? chưa chọn công trình" if r["kind"] in NEEDS_PROJECT else "chung công ty"),
                 METHOD.get(r["method"], r["method"]), vnd(r["amount"]), "có ảnh chứng từ" if r["photo_id"] else "không ảnh"]
        out.append(f"{i}. " + " · ".join(parts) + (f"\n   {r['content']}" if r["content"] else ""))
        if r.get("duplicate"):
            out.append(f"   ⚠ Nghi trùng: {r['duplicate']}" + (" — đã chọn vẫn ghi" if r.get("allow_duplicate") else ""))
    if proposal.payload.get("extra_photos"):
        out.append(f"Còn {proposal.payload['extra_photos']} ảnh chưa gắn phiếu nào (gửi lại kèm nội dung nếu cần).")
    blocked = blockers(proposal)
    out.append(("Còn thiếu: " + ", ".join(blocked) + ". Xong mới có nút Xác nhận.") if blocked
               else "Bấm Xác nhận để ghi vào Tend, hoặc nhắn lại để sửa.")
    return "\n".join(out)


def ask(chat_id, proposal, projects):
    """Hỏi từng chỗ còn thiếu bằng nút: đối tượng, công trình, nghi trùng. Mỗi chỗ hỏi một lần."""
    asked = set(proposal.payload.get("asked") or [])
    short = str(proposal.pk)[:8]
    for i, r in enumerate(proposal.payload.get("entries") or []):
        if r["party_status"] in ("ask", "new") and f"p{i}" not in asked:
            buttons = [[{"text": c["label"], "data": f"mp:{short}:{i}:{c['id']}"}] for c in r["candidates"][:5]]
            buttons.append([{"text": f"Thêm mới: {label}", "data": f"mp:{short}:{i}:n-{value}"}
                            for value, label in (("ncc", "NCC"), ("to", "Tổ thợ"))])
            buttons.append([{"text": f"Thêm mới: {label}", "data": f"mp:{short}:{i}:n-{value}"}
                            for value, label in (("khach", "Khách"), ("khac", "Khác"))])
            text = (f"Phiếu {i + 1}: \"{r['party']}\" là đối tượng nào?" if r["candidates"]
                    else f"Phiếu {i + 1}: \"{r['party']}\" chưa có trong danh sách đối tượng. Thêm mới là loại gì?")
            telegram.send(chat_id, text, buttons)
            asked.add(f"p{i}")
        if r["kind"] in NEEDS_PROJECT and not r["project_id"] and f"j{i}" not in asked:
            telegram.send(chat_id, f"Phiếu {i + 1} ({r['kind_label']}) thuộc công trình nào?",
                          [[{"text": p.code, "data": f"mj:{short}:{i}:{p.pk}"}] for p in projects[:12]])
            asked.add(f"j{i}")
        if r.get("duplicate") and not r.get("allow_duplicate") and f"d{i}" not in asked:
            telegram.send(chat_id, f"Phiếu {i + 1} nghi trùng: {r['duplicate']}.",
                          [[{"text": "Vẫn ghi, không trùng", "data": f"md:{short}:{i}"}]])
            asked.add(f"d{i}")
    if asked != set(proposal.payload.get("asked") or []):
        proposal.payload = {**proposal.payload, "asked": sorted(asked)}
        proposal.save(update_fields=["payload"])


@transaction.atomic
def choose(link_row, kind, short, index, choice=""):
    """Bấm nút trên phiếu: mp (đối tượng), mj (công trình), md (vẫn ghi). Trả (chữ, phiếu)."""
    from .bot import _pending
    proposal = _pending(link_row, short)
    if proposal is None or proposal.kind != Proposal.Kind.MONEY:
        return "Phiếu không còn chờ.", None
    rows = [dict(r) for r in proposal.payload.get("entries") or []]
    if not index.isdecimal() or int(index) >= len(rows):
        return "Lựa chọn không hợp lệ.", None
    row = rows[int(index)]
    if kind == "mp":
        if row["party_status"] not in ("ask", "new"):
            return "Đối tượng đã chọn.", None
        if choice.startswith("n-") and choice[2:] in Party.Type.values:
            row.update(party_status="new_ok", new_type=choice[2:], candidates=[])
            _resolve_duplicate(row)
            text = f"✓ Thêm mới {row['party']} ({Party.Type(choice[2:]).label.lower()})"
        elif choice.isdecimal() and int(choice) in {c["id"] for c in row["candidates"]}:
            _set_party(row, Party.objects.get(pk=int(choice)))
            text = f"✓ {row['party_label']}"
        else:
            return "Lựa chọn không hợp lệ.", None
    elif kind == "mj":
        project = next((p for p in reads.projects(link_row.user) if str(p.pk) == choice), None)
        if project is None:
            return "Công trình không hợp lệ.", None
        row.update(project_id=project.pk, project_code=project.code)
        text = f"✓ {project.code}"
    else:
        row["allow_duplicate"] = True
        text = "✓ Vẫn ghi phiếu này"
    rows[int(index)] = row
    proposal.payload = {**proposal.payload, "entries": rows}
    proposal.save(update_fields=["payload"])
    return text, proposal


def confirm(proposal, user):
    rows = []
    photos = {p.pk: p for p in proposal.photos.all()}
    for r in proposal.payload.get("entries") or []:
        voucher = None
        if r.get("photo_id") in photos:
            photo = photos[r["photo_id"]]
            voucher = ContentFile(photo.image.read(), name=Path(photo.image.name).name)
            photo.image.close()
        row = {"kind": r["kind"], "project_id": r["project_id"], "date": r["date"], "number": r["number"],
               "content": r["content"], "amount": r["amount"], "method": r["method"], "voucher": voucher,
               "allow_duplicate": r.get("allow_duplicate"), "party": r["party"]}
        if r["party_status"] == "new_ok":
            row["new_party"] = {"name": r["party"], "type": r["new_type"]}
        else:
            row["party_id"] = r["party_id"]
        rows.append(row)
    entries = money.add_entries(user, rows, client_key=proposal.pk, channel="telegram")
    return {"entries": [e.pk for e in entries], "url": "/bao-cao/thu-chi/"}


# ---------- huỷ phiếu ----------

def build_void(link_row, ref, reason, source_text):
    user = link_row.user
    entry = money.find_entry(user, ref)
    if entry is None:
        raise ValidationError(f"Không thấy phiếu \"{ref}\" trong phạm vi anh/chị được xem.")
    if entry.voided_at:
        raise ValidationError(f"Phiếu {entry} đã huỷ lúc {timezone.localtime(entry.voided_at):%d/%m/%Y %H:%M}.")
    if not str(reason or "").strip():
        raise ValidationError(f"Huỷ phiếu cần lý do. Ví dụ: /huy {entry.number or '#' + str(entry.pk)} nhập nhầm số tiền")
    if not money.can_request_void(user, entry):
        raise PermissionDenied("Anh/chị không được huỷ phiếu này.")
    needs = money.void_needs_director(user, entry)
    proposal = Proposal.objects.create(link=link_row, project=entry.project, kind=Proposal.Kind.VOID, source_text=source_text[:5000],
                                       payload={"entry_id": entry.pk, "label": f"{reads.entry_line(entry)}", "reason": str(reason).strip()[:400],
                                                "needs_director": needs})
    return proposal


def void_summary(proposal):
    p = proposal.payload
    head = "XIN HUỶ PHIẾU (chờ Giám đốc duyệt)" if p["needs_director"] else "HUỶ PHIẾU CHỜ XÁC NHẬN"
    return f"{head}\n{p['label']}\nLý do: {p['reason']}\nNgười xin: {proposal.link.user}"


def send_to_directors(proposal):
    sent = 0
    for row in ChatLink.objects.filter(active=True, user__is_active=True).select_related("user"):
        if row.user.is_director and row.user_id != proposal.link.user_id:
            telegram.send(row.chat_id, void_summary(proposal) + "\nPhiếu cũ hoặc đã xuất MISA nên cần anh/chị duyệt.",
                          [[{"text": "✓ Duyệt huỷ", "data": f"dv:{proposal.pk}"}, {"text": "× Không duyệt", "data": f"dx:{proposal.pk}"}]])
            sent += 1
    return sent


def confirm_void(proposal, user, approver=None):
    entry = Entry.objects.get(pk=proposal.payload["entry_id"])
    money.void(user, entry, proposal.payload["reason"], approver=approver)
    return {"entry": entry.pk, "url": "/bao-cao/thu-chi/"}


@transaction.atomic
def director_decide(link_row, proposal_id, approve):
    if not link_row.user.is_director:
        return "Chỉ Giám đốc duyệt huỷ phiếu."
    proposal = (Proposal.objects.select_for_update().filter(pk=proposal_id, kind=Proposal.Kind.VOID)
                .select_related("link__user").first())
    if proposal is None:
        return "Không thấy yêu cầu huỷ."
    if proposal.state != Proposal.State.PENDING:
        return f"Yêu cầu huỷ {proposal.get_state_display().lower()}."
    requester = proposal.link.user
    if not approve:
        proposal.state, proposal.decided_at, proposal.error = Proposal.State.CANCELLED, timezone.now(), f"{link_row.user} không duyệt"
        record_save(proposal, link_row.user, "Giám đốc không duyệt huỷ phiếu", channel="telegram")
        transaction.on_commit(lambda: telegram.send(proposal.link.chat_id, f"Giám đốc không duyệt huỷ:\n{proposal.payload['label']}"))
        return "Đã từ chối. Phiếu giữ nguyên."
    try:
        with transaction.atomic():
            result = confirm_void(proposal, requester, approver=link_row.user)
    except (ValidationError, PermissionDenied) as error:
        message = "; ".join(getattr(error, "messages", [str(error)]))
        proposal.state, proposal.error, proposal.decided_at = Proposal.State.FAILED, message[:480], timezone.now()
        record_save(proposal, link_row.user, "Duyệt huỷ nhưng không huỷ được", channel="telegram")
        return "Chưa huỷ được: " + message
    proposal.state, proposal.result, proposal.decided_at = Proposal.State.CONFIRMED, result, timezone.now()
    record_save(proposal, link_row.user, "Giám đốc duyệt huỷ phiếu", channel="telegram")
    transaction.on_commit(lambda: telegram.send(proposal.link.chat_id, f"Giám đốc đã duyệt huỷ:\n{proposal.payload['label']}"))
    return "Đã huỷ phiếu."


# ---------- tóm tắt cuối ngày cho Giám đốc ----------

def daily_text(user, day):
    rows = reads.entries_for(user, day, day)
    voided = Entry.objects.filter(voided_at__date=day, kind__in=money.visible_kinds(user)).select_related("project")
    if not rows and not voided.exists():
        return None
    t = reads.money_totals(rows)
    out = [f"TÓM TẮT THU CHI {day:%d/%m/%Y}",
           f"Thu {vnd(t['receipts'])} · chi {vnd(t['payments'])}" + (f" · phát sinh nợ {vnd(t['accruals'])}" if t["accruals"] else "")]
    out += [f"• {reads.entry_line(e)}" for e in rows if not e.voided_at][:25]
    if voided:
        out.append("Phiếu huỷ trong ngày:")
        out += [f"• {reads.entry_line(e)}" for e in voided[:10]]
    out.append(f"Sổ thu chi: {settings.APP_URL}/bao-cao/thu-chi/?tu={day:%Y-%m-%d}&den={day:%Y-%m-%d}")
    return "\n".join(out)


def send_daily(day=None):
    day = day or timezone.localdate()
    sent = 0
    for row in ChatLink.objects.filter(active=True, user__is_active=True).select_related("user"):
        if row.user.is_director:
            text = daily_text(row.user, day)
            if text:
                telegram.send(row.chat_id, text)
                sent += 1
    return sent
