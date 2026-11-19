"""Ảnh nhân viên: chỉ nhận ảnh thật (JPG/PNG/WEBP ≤ 5 MB), xoay đúng chiều, thu về ≤ 512 px, bỏ EXIF (vị trí GPS)."""
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 25_000_000
SIDE = 512


def avatar_path(instance, filename):
    return f"nhan-vien/anh/{instance.pk}/{uuid4().hex}{Path(filename).suffix.lower() or '.jpg'}"


def normalize(data):
    """bytes → ContentFile JPEG đã chuẩn hoá, hoặc ValidationError."""
    if not data or len(data) > MAX_BYTES:
        raise ValidationError("Chọn ảnh tối đa 5 MB.")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > MAX_PIXELS:
                raise ValidationError("Chọn ảnh JPG, PNG hoặc WEBP.")
            image.load()
            picture = ImageOps.exif_transpose(image).convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValidationError("Tệp không phải ảnh.")
    picture.thumbnail((SIDE, SIDE))
    out = BytesIO()
    picture.save(out, "JPEG", quality=85)
    return ContentFile(out.getvalue(), name="anh.jpg")


def initials(user):
    words = (user.get_full_name() or user.username).split()
    return ((words[0][:1] + words[-1][:1]) if len(words) > 1 else (words[0][:2] if words else "?")).upper()
