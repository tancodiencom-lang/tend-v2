"""Kho ảnh nội dung: gom ảnh mới (báo cáo hiện trường đã xác nhận, thư mục Drive đã kéo về máy chủ), ảnh thu nhỏ để
xem/duyệt, ảnh chuẩn bị để đăng (xoay đúng chiều, bỏ thông tin EXIF/GPS — không lộ vị trí nhà khách, ≤ 2048 px, JPEG).

Chạy lại nhiều lần không thêm trùng (khoá sha256 của tệp). Ảnh chứng từ thu chi không bao giờ vào kho.
"""
import hashlib
import logging
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.audit import record_save
from core.text import fold

from .models import Asset

log = logging.getLogger(__name__)
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_BYTES = 30 * 1024 * 1024
BATCH = 300


def drive_dir():
    """Thư mục ảnh Drive đã kéo về máy chủ (ha-tang/keo-anh-noi-dung.sh), mặc định MEDIA_ROOT/noi-dung-drive."""
    raw = getattr(settings, "TEND_MKT_DRIVE_DIR", "") or ""
    return Path(raw) if raw else Path(settings.MEDIA_ROOT) / "noi-dung-drive"


def sha_of(data):
    return hashlib.sha256(data).hexdigest()


def _read(field):
    try:
        with field.open("rb") as handle:
            return handle.read(MAX_BYTES + 1)
    except (FileNotFoundError, OSError, ValueError):
        return None


def _valid_image(data):
    from PIL import Image
    try:
        Image.open(BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _add(source, data, name, **fields):
    """Thêm một ảnh (bỏ qua nếu trùng sha). `name`: đường dẫn tệp có sẵn trong MEDIA_ROOT (không chép) hoặc None để lưu mới."""
    if not data or len(data) > MAX_BYTES or not _valid_image(data):
        return None
    digest = sha_of(data)
    if Asset.objects.filter(sha256=digest).exists():
        return None
    asset = Asset(source=source, sha256=digest, **fields)
    try:
        with transaction.atomic():
            if name:
                asset.image.name = name
            else:
                asset.image.save(fields.get("origin", "anh.jpg").rsplit("/", 1)[-1] or "anh.jpg", ContentFile(data), save=False)
            return record_save(asset, fields.get("added_by"), f"Thêm ảnh nội dung ({asset.get_source_display().lower()})",
                               channel="tu_dong" if not fields.get("added_by") else "telegram")
    except IntegrityError:
        return None


def report_context(photo):
    line = photo.line
    if line is not None:
        item = line.item
        return " · ".join(x for x in (item.work, item.floor, item.location) if x)[:500]
    return "ảnh chung của báo cáo ngày"


def collect_reports(limit=BATCH):
    """Ảnh báo cáo hiện trường đã ghi (gồm ảnh gửi trong nhóm công trình). Mỗi ảnh một lần (theo origin photo:<id>)."""
    from field.models import Photo
    done = set(Asset.objects.filter(source=Asset.Source.REPORT).values_list("origin", flat=True))
    added = 0
    rows = Photo.objects.select_related("report__project", "line__item").order_by("-pk")[: limit * 3]
    for photo in rows:
        origin = f"photo:{photo.pk}"
        if origin in done:
            continue
        data = _read(photo.image)
        if _add(Asset.Source.REPORT, data, photo.image.name, project=photo.report.project, taken_on=photo.report.work_date,
                context=report_context(photo), origin=origin):
            added += 1
            if added >= limit:
                break
    return added


def _project_from_path(parts, projects):
    codes = {fold(p.code): p for p in projects}
    for part in parts:
        for token in re.split(r"[\s_]+", part):
            hit = codes.get(fold(token))
            if hit:
                return hit
    return None


def collect_drive(limit=BATCH):
    """Ảnh trong thư mục Drive đã kéo về. Thư mục con chứa mã công trình (vd "TN-22 phòng khách") thì gắn công trình đó."""
    from projects.models import Project
    root = drive_dir()
    if not root.is_dir():
        return 0
    media = Path(settings.MEDIA_ROOT).resolve()
    done = set(Asset.objects.filter(source=Asset.Source.DRIVE).values_list("origin", flat=True))
    projects = list(Project.objects.all())
    added = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXT:
            continue
        relative = path.relative_to(root).as_posix()
        origin = f"drive:{relative}"[:400]
        if origin in done:
            continue
        try:
            data = path.read_bytes() if path.stat().st_size <= MAX_BYTES else None
        except OSError:
            continue
        resolved = path.resolve()
        name = resolved.relative_to(media).as_posix() if media in resolved.parents else None
        taken = datetime.fromtimestamp(path.stat().st_mtime).date()
        if _add(Asset.Source.DRIVE, data, name, project=_project_from_path(path.relative_to(root).parts[:-1], projects),
                taken_on=taken, context=" / ".join(path.relative_to(root).parts[:-1])[:500] or path.stem[:500], origin=origin):
            added += 1
            if added >= limit:
                break
    return added


def collect():
    return {"bao_cao": collect_reports(), "drive": collect_drive()}


def add_upload(user, data, filename, project=None, context=""):
    """Ảnh gửi cho trợ lý nội dung (qua bot /baiviet hoặc tải lên web)."""
    return _add(Asset.Source.UPLOAD, data, None, project=project, taken_on=timezone.localdate(), context=context[:500],
                origin=f"upload:{filename}"[:400], added_by=user)


def _open(asset):
    from PIL import Image, ImageOps
    data = _read(asset.image)
    if not data:
        return None
    picture = ImageOps.exif_transpose(Image.open(BytesIO(data)))
    return picture.convert("RGB")


def thumbnail(asset, size=640, quality=80):
    """Ảnh nhỏ (JPEG bytes) để xem trên web, gửi bản duyệt qua Telegram, cho AI xem. None nếu tệp mất."""
    picture = _open(asset)
    if picture is None:
        return None
    picture.thumbnail((size, size))
    out = BytesIO()
    picture.save(out, "JPEG", quality=quality)
    return out.getvalue()


def for_publish(asset):
    """Ảnh để đăng: đúng chiều, KHÔNG còn EXIF (GPS, máy chụp), cạnh dài ≤ 2048 px, JPEG < 8 MiB."""
    picture = _open(asset)
    if picture is None:
        return None
    picture.thumbnail((2048, 2048))
    for quality in (90, 82, 72):
        out = BytesIO()
        picture.save(out, "JPEG", quality=quality)            # lưu mới không kèm exif=… nên không còn thông tin vị trí
        if out.tell() < 8 * 1024 * 1024:
            return out.getvalue()
    return None
