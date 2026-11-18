"""Số kiểu Việt Nam: 1.250.000 ₫, 41,7%, 12,5."""
from decimal import Decimal, InvalidOperation

from django import template

register = template.Library()


def _dec(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


@register.filter
def vnd(value):
    number = _dec(value)
    if number is None:
        return ""
    sign = "-" if number < 0 else ""
    return sign + f"{int(abs(number).quantize(Decimal(1))):,}".replace(",", ".") + " ₫"


@register.filter
def pct(value):
    number = _dec(value)
    return "—" if number is None else f"{number:.1f}".replace(".", ",") + "%"


@register.filter
def num(value):
    number = _dec(value)
    if number is None:
        return ""
    text = format(number.normalize(), "f")
    whole, _, frac = text.partition(".")
    sign = "-" if whole.startswith("-") else ""
    whole = f"{int(whole.lstrip('-') or 0):,}".replace(",", ".")
    return sign + whole + ("," + frac if frac else "")


@register.filter
def absval(value):
    number = _dec(value)
    return abs(number) if number is not None else value
