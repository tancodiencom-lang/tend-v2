"""Đọc cú pháp AutoLISP để bài kiểm bắt lỗi ngoặc, chuỗi và tên hàm gõ sai (máy kiểm không có AutoCAD)."""
import re

TOKEN = re.compile(r'\s+|;[^\n]*|"(?:\\.|[^"\\])*"|\(|\)|\'|[^\s()\'";]+')


def parse(text):
    stack, top, pos = [[]], None, 0
    while pos < len(text):
        m = TOKEN.match(text, pos)
        if not m:
            raise SyntaxError(f"Ký tự lạ ở vị trí {pos}: {text[pos:pos + 20]!r}")
        token, pos = m.group(0), m.end()
        if token.isspace() or token.startswith(";"):
            continue
        if token == "(":
            stack.append([])
        elif token == ")":
            if len(stack) == 1:
                raise SyntaxError(f"Thừa ngoặc đóng ở vị trí {pos}")
            done = stack.pop()
            stack[-1].append(done)
        elif token == "'":
            stack[-1].append("'")
        else:
            stack[-1].append(token)
    if len(stack) != 1:
        raise SyntaxError("Thiếu ngoặc đóng")
    return stack[0]


def heads(forms, quoted=False):
    """Tên hàm được gọi (vị trí đầu danh sách), bỏ qua danh sách sau dấu ' và danh sách tham số của defun/lambda."""
    out = set()
    prev_quote = False
    for form in forms:
        if form == "'":
            prev_quote = True
            continue
        if isinstance(form, list) and form and not prev_quote and not quoted:
            head = form[0]
            if isinstance(head, str) and not head.startswith('"'):
                out.add(head.lower())
                body = form[1:]
                if head.lower() in ("defun", "lambda"):
                    body = form[3:] if head.lower() == "defun" else form[2:]
                elif head.lower() == "cond":
                    for clause in body:
                        if isinstance(clause, list):
                            out |= heads(clause)
                    body = []
                elif head.lower() in ("setq",):
                    body = form[2::2]
                elif head.lower() == "foreach":
                    body = form[2:]
                out |= heads(body)
            else:
                out |= heads(form)
        prev_quote = False
    return out


def defined(forms):
    return {f[1].lower() for f in forms if isinstance(f, list) and len(f) > 1 and str(f[0]).lower() == "defun"}
