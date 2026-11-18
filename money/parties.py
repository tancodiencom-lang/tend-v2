"""Tìm đối tượng theo tên gõ trong tin: đúng tên (bỏ dấu, bỏ tiền tố "NCC", "tổ", "anh/chị") thì lấy luôn; gần giống thì hỏi lại."""
import difflib
import re

from core.text import fold

from .models import Party

PREFIX = re.compile(r"^(?:ncc|nha cung cap|cty|cong ty|to|doi|anh|chi|a|c|khach|chu nha)\s+")


def key(name):
    text = fold(name)
    while True:
        stripped = PREFIX.sub("", text)
        if stripped == text:
            return text
        text = stripped


def match(name, type_=None, parties=None):
    """→ {"party": Party} chắc chắn; {"candidates": [...]} cần hỏi; {} tên mới."""
    parties = list(parties if parties is not None else Party.objects.filter(active=True))
    wanted = key(name)
    if not wanted:
        return {}
    exact = [p for p in parties if key(p.name) == wanted]
    if type_ and len(exact) > 1:
        exact = [p for p in exact if p.type == type_] or exact
    if len(exact) == 1:
        return {"party": exact[0]}
    if exact:
        return {"candidates": exact[:6]}
    names = {}
    for p in parties:
        names.setdefault(key(p.name), []).append(p)
    close = difflib.get_close_matches(wanted, list(names), n=4, cutoff=0.72)
    contains = [k for k in names if len(wanted) >= 3 and (wanted in k or k in wanted)]
    found = list(dict.fromkeys(p for k in close + contains for p in names[k]))
    if type_:
        found.sort(key=lambda p: p.type != type_)
    return {"candidates": found[:6]} if found else {}
