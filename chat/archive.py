"""Kho tài liệu gốc: mọi ảnh nhận qua chat (ảnh hiện trường, phiếu viết tay, ảnh chuyển khoản…) xếp theo loại và ngày
trong một thư mục đọc được bằng mắt, để đồng bộ lên Google Drive (ha-tang/dong-bo-goc.sh) và đối chiếu.

    Hiện trường/<mã công trình>/<YYYY-MM>/<YYYY-MM-DD>/<giờ>_<người gửi>_<chi tiết>_<mã>.jpg
    Chứng từ thu chi/<YYYY-MM>/<YYYY-MM-DD>/…    (phiếu thu/chi đã ghi, trừ thu tiền khách)
    Thu tiền khách/<YYYY-MM>/<YYYY-MM-DD>/…      (để chia sẻ riêng cho vai được xem tiền khách)
    Chưa ghi phiếu/<YYYY-MM>/<YYYY-MM-DD>/…      (ảnh không gắn phiếu nào, phiếu bị bỏ, hết hạn, lỗi)

Mỗi thư mục ngày có `danh-sach.csv`: giờ nhận, người gửi, công trình, nội dung tin nhắn gốc, kết quả, tên tệp; thư mục
chứng từ có cả phiếu chỉ gửi bằng chữ (tin nhắn chuyển khoản dán vào chat).

Chỉ xuất ảnh đã "chốt" (phiếu đã xác nhận / bỏ / lỗi, hoặc quá hạn phiếu 24 giờ), nên tên và chỗ của tệp không đổi về sau.
Tệp gốc giữ nguyên byte (liên kết cứng tới MEDIA_ROOT, không tốn thêm đĩa). Không xoá, không ghi đè tệp đã xuất.
"""
import csv
import os
import re
import shutil
import unicodedata
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from core.text import vnd
from drawings.models import Item
from money.models import Kind

from .models import ChatPhoto, Proposal

SETTLE = timedelta(hours=25)          # phiếu chờ quá 24 giờ là hết hạn; ảnh lẻ quá 30 phút không còn gắn được
SITE, MONEY, CUSTOMER, LOOSE = "Hiện trường", "Chứng từ thu chi", "Thu tiền khách", "Chưa ghi phiếu"
CUSTOMER_KIND = Kind.CUSTOMER_RECEIPT.value
INDEX = "danh-sach.csv"
COLUMNS = ["Giờ nhận", "Người gửi", "Công trình", "Loại", "Trạng thái", "Số tiền", "Đối tượng", "Chi tiết / số phiếu",
           "Nội dung tin nhắn", "Tệp"]


def root():
    return Path(getattr(settings, "TEND_ORIGINALS_DIR", "") or Path(settings.MEDIA_ROOT).parent / "tai-lieu-goc")


def _safe(text, limit=40):
    """Một đoạn tên tệp/thư mục: giữ chữ có dấu, bỏ ký tự cấm, gọn."""
    text = unicodedata.normalize("NFC", str(text or "")).strip()
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", text)
    text = re.sub(r"\s+", " ", text).strip(" .-")
    return text[:limit] or "-"


def _person(user):
    return _safe(user.get_full_name() or user.username, 30)


def _settled(now):
    cutoff = now - SETTLE
    return (Q(proposal__isnull=True, received_at__lt=cutoff)
            | Q(proposal__state__in=[Proposal.State.CONFIRMED, Proposal.State.CANCELLED, Proposal.State.FAILED])
            | Q(proposal__state=Proposal.State.PENDING, proposal__created_at__lt=cutoff))


def _money_row(proposal, photo_id):
    for row in (proposal.payload or {}).get("entries") or []:
        if row.get("photo_id") == photo_id:
            return row
    return None


def _item_codes(photos):
    ids = {int(p.target[5:]) for p in photos if p.target.startswith("item:") and p.target[5:].isdigit()}
    return dict(Item.objects.filter(pk__in=ids).values_list("pk", "code"))


def _place(photo, codes):
    """(thư mục loại, công trình, nhãn) của một ảnh đã chốt."""
    proposal = photo.proposal
    confirmed = proposal is not None and proposal.state == Proposal.State.CONFIRMED
    if confirmed and proposal.kind in (Proposal.Kind.MONEY, Proposal.Kind.VOID):
        row = _money_row(proposal, photo.pk) or {}
        top = CUSTOMER if row.get("kind") == CUSTOMER_KIND else MONEY
        return top, row.get("project_code") or "", row.get("number") or row.get("kind_label") or "chung-tu"
    if confirmed:
        project = proposal.project.code if proposal.project_id else ""
        if photo.target.startswith("item:"):
            label = codes.get(int(photo.target[5:]), "chi-tiet")
        else:
            label = "ảnh chung"
        return SITE, project, label
    state = "chưa gắn phiếu" if proposal is None else {
        Proposal.State.CANCELLED: "phiếu bỏ", Proposal.State.FAILED: "phiếu lỗi"}.get(proposal.state, "phiếu hết hạn")
    project = proposal.project.code if proposal is not None and proposal.project_id else ""
    return LOOSE, project, state


def _day_dir(top, project, day):
    parts = [top] + ([_safe(project or "Chưa rõ công trình")] if top == SITE else []) + [f"{day:%Y-%m}", day.isoformat()]
    return Path(*parts)


def _link(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return
    try:
        os.link(source, target)        # cùng ổ đĩa: không tốn thêm chỗ, giữ nguyên byte
    except OSError:
        shutil.copy2(source, target)


def export(now=None, log=None):
    """Xuất các ảnh đã chốt chưa xuất; viết lại danh sách các ngày liên quan. Trả số ảnh đã xuất."""
    now = now or timezone.now()
    base = root()
    photos = list(ChatPhoto.objects.filter(_settled(now), archived_at__isnull=True)
                  .select_related("link__user", "proposal__project").order_by("received_at", "pk"))
    codes = _item_codes(photos)
    days = {timezone.localdate(now) - timedelta(days=n) for n in range(3)}
    done = 0
    for photo in photos:
        source = Path(photo.image.path)
        if not source.exists():
            if log:
                log(f"Thiếu tệp ảnh #{photo.pk}: {photo.image.name}")
            continue
        top, project, label = _place(photo, codes)
        moment = timezone.localtime(photo.received_at)
        name = f"{moment:%H%M%S}_{_person(photo.link.user)}_{_safe(label, 30)}_{photo.pk}{source.suffix.lower() or '.jpg'}"
        relative = _day_dir(top, project, moment.date()) / name
        _link(source, base / relative)
        ChatPhoto.objects.filter(pk=photo.pk).update(archived_as=str(relative), archived_at=now)
        days.add(moment.date())
        done += 1
    for day in sorted(days):
        write_indexes(day, now)
    return done


# ---------- danh sách từng ngày ----------

def _status(proposal):
    if proposal is None:
        return "Chưa gắn phiếu"
    if proposal.state == Proposal.State.PENDING:
        return "Hết hạn, không ghi"
    return proposal.get_state_display()


def _photo_rows(day):
    start, end = _bounds(day)
    rows = []
    photos = (ChatPhoto.objects.filter(received_at__gte=start, received_at__lt=end).exclude(archived_as="")
              .select_related("link__user", "proposal__project").order_by("received_at", "pk"))
    codes = _item_codes(photos)
    for photo in photos:
        proposal = photo.proposal
        top, project, label = _place(photo, codes)
        money = _money_row(proposal, photo.pk) if proposal is not None else None
        rows.append((str(Path(photo.archived_as).parent), {
            "Giờ nhận": f"{timezone.localtime(photo.received_at):%d/%m/%Y %H:%M:%S}",
            "Người gửi": _person(photo.link.user),
            "Công trình": project,
            "Loại": (money or {}).get("kind_label") or (proposal.get_kind_display() if proposal else "Ảnh lẻ"),
            "Trạng thái": _status(proposal),
            "Số tiền": vnd(money["amount"]) if money and money.get("amount") else "",
            "Đối tượng": (money or {}).get("party", ""),
            "Chi tiết / số phiếu": label,
            "Nội dung tin nhắn": " ".join(((proposal.source_text if proposal else "") or photo.caption or "").split())[:1000],
            "Tệp": Path(photo.archived_as).name,
        }))
    return rows


def _text_money_rows(day, now):
    """Phiếu thu/chi gửi bằng chữ, không kèm ảnh (vd dán tin nhắn chuyển khoản): vẫn vào danh sách để đối chiếu."""
    start, end = _bounds(day)
    rows = []
    for proposal in (Proposal.objects.filter(kind=Proposal.Kind.MONEY, created_at__gte=start, created_at__lt=end)
                     .exclude(state=Proposal.State.PENDING, created_at__gte=now - SETTLE)
                     .select_related("link__user").order_by("created_at")):
        for row in (proposal.payload or {}).get("entries") or []:
            if row.get("photo_id"):
                continue
            confirmed = proposal.state == Proposal.State.CONFIRMED
            top = (CUSTOMER if row.get("kind") == CUSTOMER_KIND else MONEY) if confirmed else LOOSE
            rows.append((str(_day_dir(top, row.get("project_code"), day)), {
                "Giờ nhận": f"{timezone.localtime(proposal.created_at):%d/%m/%Y %H:%M:%S}",
                "Người gửi": _person(proposal.link.user),
                "Công trình": row.get("project_code") or "",
                "Loại": row.get("kind_label") or "",
                "Trạng thái": _status(proposal),
                "Số tiền": vnd(row["amount"]) if row.get("amount") else "",
                "Đối tượng": row.get("party") or "",
                "Chi tiết / số phiếu": row.get("number") or "",
                "Nội dung tin nhắn": " ".join((proposal.source_text or "").split())[:1000],
                "Tệp": "(chỉ có chữ)",
            }))
    return rows


def _bounds(day):
    start = timezone.make_aware(datetime.combine(day, time.min))
    return start, start + timedelta(days=1)


def write_indexes(day, now=None):
    now = now or timezone.now()
    groups = {}
    for folder, row in _photo_rows(day) + _text_money_rows(day, now):
        groups.setdefault(folder, []).append(row)
    base = root()
    for folder, rows in groups.items():
        rows.sort(key=lambda r: r["Giờ nhận"][11:])
        target = base / folder / INDEX
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(".tam")
        with open(temporary, "w", encoding="utf-8-sig", newline="") as handle:     # có BOM: Excel mở đúng chữ có dấu
            writer = csv.DictWriter(handle, fieldnames=COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        if target.exists() and target.read_bytes() == temporary.read_bytes():
            temporary.unlink()             # không đổi: giữ giờ sửa cũ để đồng bộ không chép lại
        else:
            temporary.replace(target)
    return len(groups)

