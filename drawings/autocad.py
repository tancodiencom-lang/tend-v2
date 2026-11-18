"""Sinh Bộ Tend cho AutoCAD từ bộ quy chuẩn (drawings/standard.py).

- Tend-TCD.dxf: bản mẫu — đủ layer (màu cố định), thư viện block TCD-…, bảng ký hiệu. Mở rồi Save As .dwt một lần.
- Mau-Tend.dxf: một tầng vẽ mẫu đúng quy chuẩn (để học và để bài kiểm nhập thử).
- tend.lsp: lệnh TEND (bảng nút), TENDKIEMTRA, TENDXUAT, TENDCAIDAT, TENDKHONGDAU và một lệnh TEND<nút> cho mỗi nút.
  Bảng nút (DCL) nằm sẵn trong tend.lsp, tự ghi ra thư mục tạm khi chạy, nên chỉ cần nạp một tệp.

AutoCAD 2021 trở lên (LISPSYS=1) đọc được chữ có dấu trong LISP/DCL; 2018–2020 thì bảng nút và lời nhắc dùng
chữ không dấu (lệnh TENDKHONGDAU đổi qua lại). Chữ ghi vào bản vẽ luôn dùng mã \\U+XXXX nên hiện đúng dấu ở mọi bản.
"""
import io
import re
import unicodedata
import zipfile

import ezdxf

from . import standard as S

VERSION = "2026.11.18"


ASCII_SIGNS = {"·": "-", "×": "x", "–": "-", "—": "-", "‹": "<", "›": ">", "…": "..."}


def ascii_fold(text):
    """Chữ không dấu, chỉ ASCII (AutoCAD 2018–2020 đọc LISP/DCL theo bảng mã máy)."""
    text = unicodedata.normalize("NFD", text).replace("đ", "d").replace("Đ", "D")
    return "".join(c if ord(c) < 128 else ASCII_SIGNS.get(c, "") for c in text if unicodedata.category(c) != "Mn")


def acad_text(text):
    """Chữ cho bản vẽ: ký tự có dấu → \\U+XXXX (AutoCAD hiện đúng ở mọi phiên bản)."""
    return "".join(c if ord(c) < 128 else f"\\U+{ord(c):04X}" for c in unicodedata.normalize("NFC", text))


def lisp_str(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def msg(text):
    """Lời nhắc hai dạng: có dấu (2021+) và không dấu (2018–2020)."""
    return f"(tend:t {lisp_str(text)} {lisp_str(ascii_fold(text))})"


def dcl(utf8=True):
    conv = (lambda s: s) if utf8 else ascii_fold

    def q(s):
        return '"' + conv(s).replace("\\", "\\\\").replace('"', '\\"') + '"'
    lines = ["tend : dialog {", f"  label = {q('Tend · vẽ theo quy chuẩn Tân Cổ Điển')};"]
    for group, buttons in S.BUTTONS:
        lines.append(f"  : boxed_column {{ label = {q(group)};")
        for start in range(0, len(buttons), 3):
            lines.append("    : row {")
            for key, label, _ in buttons[start:start + 3]:
                lines.append(f"      : button {{ key = \"{key}\"; label = {q(label)}; width = 20; fixed_width = true; }}")
            lines.append("    }")
        lines.append("  }")
    lines += ["  : row { : spacer { } : button { key = \"cancel\"; label = " + q("Đóng") + "; is_cancel = true; width = 12; fixed_width = true; } }",
              "}"]
    return lines


def _block_defs():
    out = []
    for block in S.BLOCKS:
        parts = [f"(entmake '((0 . \"BLOCK\") (2 . \"{block.name}\") (70 . 0) (10 0.0 0.0 0.0)))"]
        for shape in block.shapes:
            if shape[0] == "circle":
                _, x, y, r = shape
                parts.append(f"(entmake '((0 . \"CIRCLE\") (8 . \"0\") (10 {x:.1f} {y:.1f} 0.0) (40 . {r:.1f})))")
            else:
                _, x1, y1, x2, y2 = shape
                parts.append(f"(entmake '((0 . \"LINE\") (8 . \"0\") (10 {x1:.1f} {y1:.1f} 0.0) (11 {x2:.1f} {y2:.1f} 0.0)))")
        parts.append("(entmake '((0 . \"ENDBLK\")))")
        out.append(f"  (if (not (tblsearch \"BLOCK\" \"{block.name}\"))\n    (progn\n      " + "\n      ".join(parts) + "))")
    return "\n".join(out)


def _button_expr(expr):
    """Chuỗi có dấu trong biểu thức nút là chữ ghi vào bản vẽ → mã \\U+XXXX (trong chuỗi LISP cần hai gạch)."""
    return re.sub(r'"([^"]*)"', lambda m: lisp_str(acad_text(m.group(1))), expr)


LISP = r''';;; Bộ Tend cho AutoCAD %%VERSION%% — sinh tự động từ bộ quy chuẩn của Tend. Không sửa tay.
;;; Lệnh: TEND (bảng nút) · TENDKIEMTRA · TENDXUAT · TENDCAIDAT · TENDKHONGDAU · TEND<nút> (vd TENDXAY110).
(vl-load-com)
(setq tend:*utf8* (= (getvar "LISPSYS") 1))
(defun tend:t (a b) (if tend:*utf8* a b))

(setq tend:*layers* '(
%%LAYERS%%))
(setq tend:*allowed* '(
%%ALLOWED%%))
(setq tend:*closed* '(%%CLOSED%%))
(setq tend:*blocks* '(%%BLOCKNAMES%%))
(setq tend:*keys* '(%%KEYS%%))
(setq tend:*dcl-utf8* '(
%%DCL_UTF8%%))
(setq tend:*dcl-ascii* '(
%%DCL_ASCII%%))

(defun tend:kind (layer / row)
  (setq row (assoc (strcase layer) tend:*layers*))
  (if row (caddr row)))

(defun tend:closed-p (d / pts)
  (or (= 1 (logand 1 (cdr (assoc 70 d))))
      (progn
        (setq pts (vl-remove-if-not '(lambda (x) (= (car x) 10)) d))
        (and (> (length pts) 2) (equal (cdr (car pts)) (cdr (last pts)) 1.0)))))

;;; Lý do đối tượng không thuộc quy chuẩn, nil nếu hợp lệ (cùng luật với máy chủ Tend).
(defun tend:why (d / layer typ kind allowed)
  (setq layer (strcase (cdr (assoc 8 d))) typ (cdr (assoc 0 d)) kind (tend:kind layer))
  (cond
    ((null kind) %%R_LAYER%%)
    ((and (setq allowed (cdr (assoc kind tend:*allowed*))) (not (member typ allowed)))
     (if (= kind "device") %%R_EXPLODE%% %%R_TYPE%%))
    ((and (member kind tend:*closed*) (= typ "LWPOLYLINE") (not (tend:closed-p d))) %%R_OPEN%%)
    ((and (= kind "device") (not (member (strcase (cdr (assoc 2 d))) tend:*blocks*))) %%R_BLOCK%%)))

(defun tend:tally (key lst / row)
  (if (setq row (assoc key lst))
    (subst (cons key (1+ (cdr row))) row lst)
    (cons (cons key 1) lst)))

(defun tend:layer (row)
  (if (and row (not (tblsearch "LAYER" (car row))))
    (entmake (list '(0 . "LAYER") '(100 . "AcDbSymbolTableRecord") '(100 . "AcDbLayerTableRecord")
                   (cons 2 (car row)) '(70 . 0) (cons 62 (cadr row)) '(6 . "Continuous")))))

(defun tend:blocks ()
%%BLOCKDEFS%%
  (princ))

(defun tend:setup ()
  (foreach row tend:*layers* (tend:layer row))
  (tend:blocks)
  (princ %%SETUP_DONE%%)
  (princ))

(defun tend:use (name)
  (tend:layer (assoc name tend:*layers*))
  (setvar "CLAYER" name))

(defun tend:p3 (p) (list (car p) (cadr p) (if (caddr p) (caddr p) 0.0)))

(defun tend:text (layer pt h s / w)
  (if (and s (/= s ""))
    (progn
      (setq w (trans (tend:p3 pt) 1 0))
      (entmake (list '(0 . "TEXT") (cons 8 layer) (cons 10 w) (cons 11 w) (cons 40 h) (cons 1 s) '(72 . 1) '(73 . 2))))))

(defun tend:pline (layer pts closed)
  (entmake (append (list '(0 . "LWPOLYLINE") '(100 . "AcDbEntity") (cons 8 layer) '(100 . "AcDbPolyline")
                         (cons 90 (length pts)) (cons 70 (if closed 1 0)))
                   (mapcar '(lambda (p) (setq p (trans (tend:p3 p) 1 0)) (list 10 (car p) (cadr p))) pts))))

(defun tend:draw-pline ()
  (command "_.PLINE")
  (while (> (logand (getvar "CMDACTIVE") 1) 0) (command pause)))

;;; Bấm trong vùng kín → đường bao trên layer; không tìm được thì vẽ tay.
(defun tend:boundary (layer pt / before)
  (tend:use layer)
  (setq before (entlast))
  (command "_.-BOUNDARY" "_A" "_I" "_N" "" pt "")
  (if (equal before (entlast))
    (progn (princ %%NO_BOUNDARY%%) (tend:draw-pline))))

;;; Chữ người dùng gõ: 2021+ hỏi ngay; bản cũ ghi chữ gốc rồi nhắc bấm đúp để sửa (AutoLISP cũ không giữ được dấu).
(defun tend:ask-suffix (layer pt h base prompt / s)
  (if tend:*utf8* (setq s (getstring T prompt)) (progn (setq s "") (princ %%EDIT_LATER%%)))
  (tend:text layer pt h (if (= s "") base (strcat base " " s))))

(defun tend:frame (/ p1 p2 f top)
  (tend:use "CH-KHUNG")
  (if (and (setq p1 (getpoint %%FRAME_P1%%)) (setq p2 (getcorner p1 %%FRAME_P2%%)))
    (progn
      (tend:pline "CH-KHUNG" (list p1 (list (car p2) (cadr p1) 0.0) p2 (list (car p1) (cadr p2) 0.0)) T)
      (setq f (strcase (getstring %%FRAME_FLOOR%%)))
      (setq top (list (/ (+ (car p1) (car p2)) 2.0) (- (max (cadr p1) (cadr p2)) 600.0) 0.0))
      (tend:text "CH-TEXT" top 350.0 (if (= f "M") %%TITLE_ROOF%% (strcat %%TITLE_FLOOR%% f)))))
  (princ))

(defun tend:wall (layer)
  (tend:use layer)
  (princ %%WALL_HELP%%)
  (tend:draw-pline)
  (princ))

(defun tend:height (/ pt h)
  (tend:use "KL-GHI")
  (if (setq pt (getpoint %%H_PT%%))
    (progn (initget 6) (if (setq h (getint %%H_VAL%%)) (tend:text "KL-GHI" pt 150.0 (strcat "H=" (itoa h))))))
  (princ))

(defun tend:room (/ pt name mat)
  (if (setq pt (getpoint %%ROOM_PT%%))
    (progn
      (tend:boundary "KL-SAN" pt)
      (if tend:*utf8*
        (progn
          (setq name (getstring T %%ROOM_NAME%%) mat (getstring T %%ROOM_MAT%%))
          (tend:text "CH-PHONG" pt 250.0 name)
          (tend:text "CH-PHONG" (polar pt (* 1.5 pi) 400.0) 150.0 mat))
        (progn
          (tend:text "CH-PHONG" pt 250.0 %%ROOM_PLACEHOLDER%%)
          (tend:text "CH-PHONG" (polar pt (* 1.5 pi) 400.0) 150.0 %%MAT_PLACEHOLDER%%)
          (princ %%EDIT_LATER%%)))))
  (princ))

(defun tend:cladding (/ pt h)
  (tend:use "KL-GHI")
  (if (setq pt (getpoint %%CLAD_PT%%))
    (progn (initget 6) (if (setq h (getint %%CLAD_H%%)) (tend:ask-suffix "KL-GHI" pt 150.0 (strcat %%OP%% " " (itoa h)) %%MAT_PROMPT%%))))
  (princ))

(defun tend:area (layer label / pt)
  (if (setq pt (getpoint %%AREA_PT%%))
    (progn (tend:boundary layer pt) (tend:ask-suffix layer pt 150.0 label %%MAT_PROMPT%%)))
  (princ))

(defun tend:door (/ p1 p2 code h w a q)
  (tend:use "KL-CUA")
  (if (and (setq p1 (getpoint %%DOOR_P1%%)) (setq p2 (getpoint p1 %%DOOR_P2%%)))
    (progn
      (setq code (strcase (getstring %%DOOR_CODE%%)))
      (initget 6)
      (if (null (setq h (getint %%DOOR_H%%))) (setq h %%DOOR_DEFAULT%%))
      (setq w (distance p1 p2) a (angle p1 p2) q (/ pi 2.0))
      (tend:pline "KL-CUA" (list (polar p1 (+ a q) 150.0) (polar p2 (+ a q) 150.0) (polar p2 (- a q) 150.0) (polar p1 (- a q) 150.0)) T)
      (tend:text "KL-CUA" (polar p1 a (/ w 2.0)) 100.0 (strcat code " " (rtos w 2 0) "x" (itoa h)))))
  (princ))

(defun tend:device (name layer / pt)
  (tend:blocks)
  (tend:use layer)
  (while (setq pt (getpoint %%DEV_PT%%))
    (entmake (list '(0 . "INSERT") (cons 2 name) (cons 8 layer) (cons 10 (trans (tend:p3 pt) 1 0))
                   '(41 . 1.0) '(42 . 1.0) '(43 . 1.0) '(50 . 0.0))))
  (princ))

(defun tend:run (layer / before s p)
  (tend:use layer)
  (setq before (entlast))
  (princ %%RUN_HELP%%)
  (tend:draw-pline)
  (if (not (equal before (entlast)))
    (progn
      (setq s (getstring T %%RUN_LABEL%%))
      (setq p (vlax-curve-getPointAtParam (entlast) 0.5))
      (if (/= s "")
        (entmake (list '(0 . "TEXT") (cons 8 layer) (cons 10 p) (cons 11 p) '(40 . 100.0) (cons 1 s) '(72 . 1) '(73 . 2))))))
  (princ))

(defun c:TENDKIEMTRA (/ ss i e why bad tally)
  (setq bad (ssadd) tally nil)
  (if (setq ss (ssget "_X" '((410 . "Model"))))
    (repeat (setq i (sslength ss))
      (setq e (ssname ss (setq i (1- i))))
      (if (setq why (tend:why (entget e)))
        (progn (ssadd e bad) (setq tally (tend:tally why tally))))))
  (if (not (ssget "_X" '((0 . "LWPOLYLINE,INSERT") (8 . "CH-KHUNG") (410 . "Model"))))
    (setq tally (tend:tally %%NO_FRAME%% tally)))
  (if (not (ssget "_X" '((0 . "TEXT,MTEXT") (8 . "CH-TEXT") (410 . "Model"))))
    (setq tally (tend:tally %%NO_TITLE%% tally)))
  (if tally
    (progn
      (princ %%HEAD_BAD%%)
      (foreach row (reverse tally) (princ (strcat "\n  - " (car row) ": " (itoa (cdr row)))))
      (if (> (sslength bad) 0) (progn (sssetfirst nil bad) (princ %%SELECTED%%))))
    (princ %%ALL_OK%%))
  (princ))

;;; Lưu DWG gốc, xoá trên bản đang mở mọi thứ ngoài quy chuẩn, lưu thành <tên>_tend.dxf, rồi mở lại DWG gốc.
(defun c:TENDXUAT (/ doc dwg out ss i e n)
  (setq doc (vla-get-ActiveDocument (vlax-get-acad-object)))
  (if (= (getvar "DWGTITLED") 0)
    (princ %%SAVE_FIRST%%)
    (progn
      (vla-Save doc)
      (setq dwg (strcat (getvar "DWGPREFIX") (getvar "DWGNAME"))
            out (strcat (getvar "DWGPREFIX") (vl-filename-base (getvar "DWGNAME")) "_tend.dxf")
            n 0)
      (if (setq ss (ssget "_X" '((410 . "Model"))))
        (repeat (setq i (sslength ss))
          (setq e (ssname ss (setq i (1- i))))
          (if (and (tend:why (entget e)) (entdel e)) (setq n (1+ n)))))
      (vla-SaveAs doc out ac2013_dxf)
      (princ (strcat %%EXPORTED%% out))
      (princ (strcat %%REMOVED%% (itoa n)))
      (princ (strcat %%REOPEN%% dwg))
      (vla-Activate (vla-Open (vla-get-Documents (vlax-get-acad-object)) dwg :vlax-false))))
  (princ))

(defun c:TENDCAIDAT () (tend:setup))

(defun c:TENDKHONGDAU ()
  (setq tend:*utf8* (not tend:*utf8*))
  (princ (if tend:*utf8* "\nTend: chữ có dấu." "\nTend: chu khong dau."))
  (princ))

(defun tend:dcl-file (/ f path)
  (setq path (strcat (getvar "TEMPPREFIX") (if tend:*utf8* "tend-co-dau.dcl" "tend-khong-dau.dcl")))
  (setq f (if tend:*utf8* (open path "w" "utf8") (open path "w")))
  (foreach line (if tend:*utf8* tend:*dcl-utf8* tend:*dcl-ascii*) (write-line line f))
  (close f)
  path)

(defun tend:run-key (k)
  (cond
%%DISPATCH%%))

(defun c:TEND (/ id act)
  (setq id (load_dialog (tend:dcl-file)))
  (if (and (>= id 0) (new_dialog "tend" id))
    (progn
      (foreach k tend:*keys* (action_tile k (strcat "(setq act \"" k "\") (done_dialog 1)")))
      (start_dialog)
      (unload_dialog id)
      (if act (tend:run-key act)))
    (princ %%DCL_FAIL%%))
  (princ))

%%COMMANDS%%

(princ %%LOADED%%)
(princ)
'''

MESSAGES = {
    "R_LAYER": S.REASONS["layer"], "R_EXPLODE": S.REASONS["explode"], "R_TYPE": S.REASONS["type"],
    "R_OPEN": S.REASONS["open"], "R_BLOCK": S.REASONS["block"],
    "SETUP_DONE": "\nTend: đã có đủ layer và block theo quy chuẩn.",
    "NO_BOUNDARY": "\nKhông tìm được đường bao kín. Vẽ đường bao (gõ C để đóng):",
    "EDIT_LATER": "\nBấm đúp vào chữ vừa ghi để sửa tên/vật liệu.",
    "FRAME_P1": "\nKhung tầng: bấm góc thứ nhất: ", "FRAME_P2": "\nBấm góc đối diện: ",
    "FRAME_FLOOR": "\nTầng số (vd 2; nhiều tầng giống nhau: 2-5; mái: M): ",
    "WALL_HELP": "\nTường: bấm các điểm theo TIM tường, Enter để xong (hoặc vẽ đường bao kín, gõ C).",
    "H_PT": "\nBấm sát bức tường cao khác tầng: ", "H_VAL": "\nChiều cao tường (mm): ",
    "ROOM_PT": "\nBấm vào bên trong phòng: ", "ROOM_NAME": "\nTên phòng (vd PHÒNG NGỦ 301): ",
    "ROOM_MAT": "\nVật liệu lát (vd Gạch 600x600, Enter bỏ qua): ",
    "CLAD_PT": "\nỐp: bấm vào trong phòng: ", "CLAD_H": "\nChiều cao ốp (mm): ",
    "MAT_PROMPT": "\nVật liệu (Enter bỏ qua): ",
    "AREA_PT": "\nBấm vào bên trong vùng: ",
    "DOOR_P1": "\nCửa: bấm mép thứ nhất của ô cửa trên tường: ", "DOOR_P2": "\nBấm mép thứ hai: ",
    "DOOR_CODE": "\nMã cửa (vd D1): ", "DOOR_H": f"\nChiều cao cửa mm <{S.DEFAULT_DOOR_HEIGHT}>: ",
    "DEV_PT": "\nBấm điểm đặt (Enter để xong): ",
    "RUN_HELP": "\nBấm theo tuyến dây/ống, Enter để xong.",
    "RUN_LABEL": "\nLoại dây hoặc đường kính ống (vd Cu 2x1,5 / D25, Enter bỏ qua): ",
    "NO_FRAME": "Chưa có khung tầng (nút Khung tầng)", "NO_TITLE": "Chưa có tiêu đề tầng ở layer CH-TEXT",
    "HEAD_BAD": "\nTend · Kiểm tra: còn đối tượng ngoài quy chuẩn (sẽ bị bỏ khi Xuất cho Tend):",
    "SELECTED": "\nĐã chọn sẵn các đối tượng này trên bản vẽ.",
    "ALL_OK": "\nTend · Kiểm tra: bản vẽ đúng quy chuẩn.",
    "SAVE_FIRST": "\nLưu bản vẽ (Save) một lần trước khi xuất.",
    "EXPORTED": "\nĐã xuất cho Tend: ", "REMOVED": "\nĐã bỏ đối tượng ngoài quy chuẩn: ",
    "REOPEN": "\nĐã mở lại bản gốc (DWG không bị sửa): ",
    "DCL_FAIL": "\nKhông mở được bảng nút Tend. Gõ TENDKHONGDAU rồi thử lại.",
    "LOADED": "\nĐã nạp Bộ Tend. Gõ TEND để mở bảng nút.",
}
DRAWING_TEXT = {"TITLE_FLOOR": "MẶT BẰNG TẦNG ", "TITLE_ROOF": "MẶT BẰNG MÁI", "OP": "ỐP",
                "ROOM_PLACEHOLDER": "TÊN PHÒNG", "MAT_PLACEHOLDER": "Vật liệu"}
PLACEHOLDERS = {DRAWING_TEXT["ROOM_PLACEHOLDER"], DRAWING_TEXT["MAT_PLACEHOLDER"]}


def lisp():
    keys = [key for _, buttons in S.BUTTONS for key, _, _ in buttons]
    own = {"KIEMTRA", "XUAT", "CAIDAT"}   # đã có lệnh TENDKIEMTRA, TENDXUAT, TENDCAIDAT
    allowed = "\n".join(f'  ("{kind}"' + "".join(f' "{t}"' for t in sorted(types or ())) + ")"
                        for kind, types in S.ALLOWED_TYPES.items())
    values = {
        "VERSION": VERSION,
        "LAYERS": "\n".join(f'  ("{l.name}" {l.color} "{l.kind}")' for l in S.LAYERS),
        "ALLOWED": allowed,
        "CLOSED": " ".join(f'"{k}"' for k in sorted(S.CLOSED_KINDS)),
        "BLOCKNAMES": " ".join(f'"{b.name}"' for b in S.BLOCKS),
        "KEYS": " ".join(f'"{k}"' for k in keys),
        "DCL_UTF8": "\n".join("  " + lisp_str(line) for line in dcl(True)),
        "DCL_ASCII": "\n".join("  " + lisp_str(line) for line in dcl(False)),
        "BLOCKDEFS": _block_defs(),
        "DISPATCH": "\n".join(f'    ((= k "{key}") {_button_expr(expr)})' for _, buttons in S.BUTTONS for key, _, expr in buttons),
        "COMMANDS": "\n".join(f"(defun c:TEND{key} () {_button_expr(expr)} (princ))"
                              for _, buttons in S.BUTTONS for key, _, expr in buttons if key not in own),
        "DOOR_DEFAULT": str(S.DEFAULT_DOOR_HEIGHT),
        **{k: msg(v) for k, v in MESSAGES.items()},
        **{k: lisp_str(acad_text(v)) for k, v in DRAWING_TEXT.items()},
    }
    return re.sub(r"%%([A-Z0-9_]+)%%", lambda m: values[m.group(1)], LISP)


# --- Bản mẫu DXF -----------------------------------------------------------------------------------------------

def _new_doc():
    doc = ezdxf.new("R2013", setup=True)
    doc.header["$INSUNITS"] = 4      # mm
    for layer in S.LAYERS:
        if layer.name not in doc.layers:
            doc.layers.add(layer.name, color=layer.color)
    for block in S.BLOCKS:
        definition = doc.blocks.new(block.name)
        for shape in block.shapes:
            if shape[0] == "circle":
                definition.add_circle((shape[1], shape[2]), shape[3])
            else:
                definition.add_line(shape[1:3], shape[3:5])
    msp = doc.modelspace()
    # Bảng ký hiệu bên trái gốc toạ độ, ngoài mọi khung tầng
    x0, y0, row = -9000, 9000, 700
    height = row * (len(S.BLOCKS) + 1) + 400
    msp.add_lwpolyline([(x0, y0), (x0 + 5000, y0), (x0 + 5000, y0 - height), (x0, y0 - height)], close=True,
                       dxfattribs={"layer": "CH-KY-HIEU"})
    msp.add_text("BẢNG KÝ HIỆU", height=250, dxfattribs={"layer": "CH-KY-HIEU"}).set_placement((x0 + 300, y0 - 500))
    for i, block in enumerate(S.BLOCKS, start=1):
        y = y0 - 400 - i * row
        msp.add_blockref(block.name, (x0 + 600, y), dxfattribs={"layer": block.layer})
        msp.add_text(f"{block.title} ({block.name})", height=150, dxfattribs={"layer": "CH-KY-HIEU"}).set_placement((x0 + 1200, y - 75))
    return doc


def template_doc():
    return _new_doc()


def sample_doc():
    """Một tầng vẽ đúng quy chuẩn. Số liệu tính tay ở drawings/tests.py."""
    doc = _new_doc()
    msp = doc.modelspace()

    def pl(layer, pts, close=True):
        msp.add_lwpolyline(pts, close=close, dxfattribs={"layer": layer})

    def tx(layer, text, at, h=150):
        msp.add_text(text, height=h, dxfattribs={"layer": layer}).set_placement(at, align=ezdxf.enums.TextEntityAlignment.MIDDLE_CENTER)

    pl("CH-KHUNG", [(0, 0), (12000, 0), (12000, 9000), (0, 9000)])
    tx("CH-TEXT", "MẶT BẰNG TẦNG 1", (6000, 8400), 350)
    pl("KT-TUONG-HT", [(800, 800), (9200, 800), (9200, 6400), (800, 6400)])
    msp.add_circle((800, 800), 150, dxfattribs={"layer": "KT-COT"})
    pl("KL-SAN", [(1000, 1000), (7000, 1000), (7000, 6000), (1000, 6000)])
    tx("CH-PHONG", "PHÒNG KHÁCH", (4000, 3600), 250)
    tx("CH-PHONG", "Gạch 600×600", (4000, 3200))
    pl("KL-SAN", [(7000, 1000), (9000, 1000), (9000, 4000), (7000, 4000)])
    tx("CH-PHONG", "WC", (8000, 2800), 250)
    tx("CH-PHONG", "Gạch 300×300", (8000, 2400))
    tx("KL-GHI", "ỐP 2400 Gạch 300×600", (8000, 1600))
    msp.add_line((7000, 1000), (7000, 4000), dxfattribs={"layer": "KL-XAY-110"})
    pl("KL-XAY-220", [(1000, 6000), (7000, 6000), (7000, 6220), (1000, 6220)])
    tx("KL-GHI", "H=3170", (4000, 6400))
    pl("KL-CUA", [(6850, 2000), (7150, 2000), (7150, 2900), (6850, 2900)])
    tx("KL-CUA", "D1 900x2200", (7000, 2450), 100)
    pl("KL-TRAN", [(1500, 1500), (6500, 1500), (6500, 5500), (1500, 5500)])
    tx("KL-TRAN", "Thạch cao khung chìm", (4000, 4600))
    for x, y in ((2000, 1200), (5000, 1200)):
        msp.add_blockref("TCD-O-CAM-DOI", (x, y), dxfattribs={"layer": "D-O-CAM"})
    msp.add_blockref("TCD-DEN-DOWNLIGHT", (4000, 4000), dxfattribs={"layer": "D-CHIEU-SANG"})
    pl("D-DAY", [(1500, 1500), (5000, 1500), (5000, 3000)], close=False)
    tx("D-DAY", "Cu 2x1,5", (3000, 1650), 100)
    return doc


def _dxf_bytes(doc):
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


GUIDE = """BỘ TEND CHO AUTOCAD (AutoCAD bản đầy đủ 2018 trở lên)

Cài một lần
1. Chép cả thư mục này vào C:\\Tend.
2. AutoCAD > Options (gõ OP) > Files > Trusted Locations > Add: C:\\Tend. (Để AutoCAD không hỏi chặn tệp LISP.)
3. Gõ APPLOAD > Startup Suite > Contents > Add > chọn C:\\Tend\\tend.lsp > Close. Từ nay mở AutoCAD là có Bộ Tend.
4. Mở Tend-TCD.dxf > Save As > AutoCAD Drawing Template (*.dwt), đặt tên "Tend TCD".
   Công trình mới: New > chọn "Tend TCD" (đã có layer, block, bảng ký hiệu).
   Bản vẽ cũ: gõ TENDCAIDAT để thêm layer và block chuẩn.

Vẽ
- Gõ TEND để mở bảng nút. Mỗi nút tự chuyển đúng layer rồi vẽ, nên không cần chọn layer.
- Mỗi mặt bằng: nút "Khung tầng" (bấm hai góc, gõ số tầng; tầng giống nhau gõ 2-5; mái gõ M).
- Tường: bấm theo TIM tường; Tend tự tính theo độ dày của nút (110/220/50). Tường cao khác tầng: nút "Ghi cao H=".
- Phòng: bấm vào trong phòng (tự lấy đường bao), gõ tên phòng và vật liệu lát.
- Ốp trong phòng: bấm vào trong phòng, gõ chiều cao ốp. Tend tính chu vi × cao, tự trừ cửa.
- Cửa: bấm hai mép ô cửa trên tường, gõ mã (D1) và chiều cao. Tend tự trừ diện tích cửa khỏi tường.
- Điện nước: nút thiết bị (đặt block, không phá khối); dây/ống vẽ theo tuyến rồi gõ loại dây/đường kính.
- AutoCAD 2018–2020: tên phòng/vật liệu ghi chữ mẫu, bấm đúp để sửa (AutoLISP bản cũ không giữ được chữ có dấu).

Trước khi gửi
- Nút "Kiểm tra" (lệnh TENDKIEMTRA): liệt kê và chọn sẵn các đối tượng ngoài quy chuẩn.
- Nút "Xuất cho Tend" (lệnh TENDXUAT): lưu bản DWG gốc, tạo <tên>_tend.dxf chỉ còn phần đúng quy chuẩn,
  rồi mở lại bản DWG gốc. Đóng tab _tend.dxf mà không cần lưu. Tải tệp _tend.dxf (nên nén .zip) lên Tend.
- Tend lọc lại một lần nữa khi nhập và báo phần bị bỏ, nên dù quên Xuất thì dữ liệu vẫn sạch.

Mau-Tend.dxf là một tầng vẽ mẫu đúng quy chuẩn để tham khảo.
"""


def kit_zip():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("Bo-Tend-AutoCAD/tend.lsp", lisp().encode("utf-8"))
        archive.writestr("Bo-Tend-AutoCAD/Tend-TCD.dxf", _dxf_bytes(template_doc()))
        archive.writestr("Bo-Tend-AutoCAD/Mau-Tend.dxf", _dxf_bytes(sample_doc()))
        archive.writestr("Bo-Tend-AutoCAD/HUONG-DAN.txt", GUIDE.replace("\n", "\r\n").encode("utf-8-sig"))
    return buffer.getvalue()
