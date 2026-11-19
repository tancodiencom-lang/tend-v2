"""Chữ và số kiểu Việt Nam dùng chung (bot, web)."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation


def fold(text):
    """Bỏ dấu, chữ thường, gọn khoảng trắng: "Nguyễn  Hùng" → "nguyen hung"."""
    text = unicodedata.normalize("NFD", text or "").replace("đ", "d").replace("Đ", "D")
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return " ".join(text.lower().split())


def number(text):
    """Số người dùng gõ: "1.000" là một nghìn, "4,2" là bốn phẩy hai, "0.5" (một dấu chấm, ≤ 2 số lẻ) là 0,5."""
    raw = str(text).strip().replace(" ", "").replace("₫", "").replace("đ", "")
    if not raw:
        return None
    if "," in raw:
        raw = raw.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
        raw = raw.replace(".", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def vn_number(value, places=None):
    """Decimal → "1.250.000" / "0,5"."""
    value = Decimal(str(value))
    if places is not None:
        value = value.quantize(Decimal(1).scaleb(-places))
    text = format(value.normalize() if places is None else value, "f")
    whole, _, frac = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = f"{int(whole.lstrip('-') or 0):,}".replace(",", ".")
    return sign + whole + ("," + frac if frac else "")


def vnd(value):
    return vn_number(Decimal(str(value)).quantize(Decimal(1))) + " ₫"


def phone(text):
    """"+84 912 345 678" / "84912345678" / "0912.345.678" → "0912345678" ("" nếu không có số)."""
    digits = re.sub(r"\D", "", str(text or ""))
    if digits.startswith("84") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits

