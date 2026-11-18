"""Xuất phiếu Tend ra Excel theo đúng mẫu nhập khẩu MISA ASP của công ty (mẫu cột lấy từ bản cũ finance/misa.py).

Bố cục: dòng 1–6 hướng dẫn, dòng 7 nhóm cột, dòng 8 tên cột, dữ liệu từ dòng 9; trang "Lưu ý" ghi chỗ cần kiểm tra.
Hạch toán (chốt 27/10/2026, sửa tài khoản ở trang quản trị › Cài đặt kế toán):
- Thu khách: Nợ tiền mặt/tiền gửi, Có 131. Thu khác: Có 711.
- Trả NCC: Nợ 331. Trả tổ thợ, trả lương công nhật, chi khác: Nợ 154. Có tiền mặt/tiền gửi.
- Hàng NCC đã nhận: mua hàng trong nước không qua kho, Nợ 154 / Có 331, chưa thanh toán.
- Giá trị khoán tổ thợ được tính: không xuất (chỉ để Tend tính công nợ tổ).
Phiếu đã huỷ không xuất.
"""
from io import BytesIO

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from core.audit import record_save
from projects.models import projects_for

from . import services
from .models import RECEIPTS, AccountingSettings, Entry, Kind, MisaExport

CASH_IN, BANK_IN, CASH_OUT, BANK_OUT, PURCHASE = "thu_tm", "thu_tg", "chi_tm", "chi_tg", "mua_hang"
LABELS = {CASH_IN: "Phiếu thu tiền mặt", BANK_IN: "Phiếu thu tiền gửi", CASH_OUT: "Phiếu chi tiền mặt",
          BANK_OUT: "Phiếu chi tiền gửi", PURCHASE: "Mua hàng trong nước không qua kho"}
FILE_NAMES = {CASH_IN: "Phieu_thu_tien_mat", BANK_IN: "Phieu_thu_tien_gui", CASH_OUT: "Phieu_chi",
              BANK_OUT: "Phieu_chi_tien_gui", PURCHASE: "Mua_hang_trong_nuoc"}
GUIDE = ["Hướng dẫn:", "- Điền dữ liệu vào các cột tương ứng trên file này", "- Các cột có dấu (*) là những cột bắt buộc",
         "- File do Tend xuất theo mẫu nhập khẩu của MISA", "- Xem trang “Lưu ý” trước khi nhập vào MISA"]
COLUMNS = {
    CASH_IN: ("FILE MẪU PHIẾU THU ĐỂ NHẬP VÀO PHẦN MỀM ASP KẾ TOÁN", {"H": "Chi tiết hạch toán"}, [
        "Ngày hạch toán (*)", "Ngày chứng từ (*)", "Số chứng từ (*)", "Đối tượng", "Địa chỉ", "Lý do nộp",
        "Diễn giải lý do nộp", "Diễn giải (hạch toán)", "TK Nợ (*)", "TK Có (*)", "Số tiền", "Mã đối tượng (hạch toán)",
        "Số TK ngân hàng"]),
    BANK_IN: ("FILE MẪU PHIẾU THU TIỀN GỬI ĐỂ NHẬP VÀO PHẦN MỀM ASP KẾ TOÁN", {"I": "Chi tiết hạch toán"}, [
        "Ngày hạch toán (*)", "Ngày chứng từ (*)", "Số chứng từ (*)", "Đối tượng", "Nộp vào TK", "Mở tại ngân hàng",
        "Lý do thu", "Diễn giải lý do thu", "Diễn giải (hạch toán)", "TK Nợ (*)", "TK Có (*)", "Số tiền",
        "Mã đối tượng (hạch toán)"]),
    CASH_OUT: ("FILE MẪU PHIẾU CHI ĐỂ NHẬP VÀO PHẦN MỀM ASP KẾ TOÁN", {"H": "Chi tiết hạch toán", "N": "Hóa đơn"}, [
        "Ngày chứng từ (*)", "Ngày hạch toán (*)", "Số chứng từ (*)", "Đối tượng", "Địa chỉ", "Lý do chi",
        "Diễn giải lý do chi", "Diễn giải (hạch toán)", "TK Nợ (*)", "TK Có (*)", "Số tiền", "Mã đối tượng (hạch toán)",
        "Số TK ngân hàng", "Hạch toán gộp nhiều hóa đơn", "Diễn giải thuế", "Có hóa đơn", "Giá trị HHDV chưa thuế",
        "% thuế GTGT", "% thuế suất KHAC", "Tiền thuế GTGT", "TK thuế GTGT", "Ngày hóa đơn", "Số hóa đơn",
        "Nhóm HHDV mua vào", "Mã NCC", "Tên NCC", "Mã số thuế NCC"]),
    BANK_OUT: ("FILE MẪU PHIẾU CHI TIỀN GỬI ĐỂ NHẬP VÀO PHẦN MỀM ASP KẾ TOÁN", {"L": "Chi tiết hạch toán"}, [
        "Phương thức thanh toán", "Ngày hạch toán (*)", "Ngày chứng từ (*)", "Số chứng từ (*)", "Lý do chi",
        "Nội dung thanh toán", "Số tài khoản chi", "Tên ngân hàng chi ", "Đối tượng", "Số tài khoản nhận",
        "Tên ngân hàng nhận", "Diễn giải (hạch toán)", "TK Nợ (*)", "TK Có (*)", "Số tiền", "Mã đối tượng (hạch toán)",
        "Hạch toán gộp nhiều hóa đơn", "Diễn giải thuế", "Có hóa đơn", "Giá trị HHDV chưa thuế", "% thuế GTGT",
        "% thuế suất KHAC", "Tiền thuế GTGT", "TK thuế GTGT", "Ngày hóa đơn", "Số hóa đơn", "Nhóm HHDV mua vào",
        "Mã NCC", "Tên NCC", "Mã số thuế NCC"]),
    PURCHASE: ("FILE MẪU CHỨNG TỪ MUA HÀNG TRONG NƯỚC ĐỂ NHẬP VÀO PHẦN MỀM ASP KẾ TOÁN", {"Q": "Chi tiết hàng tiền"}, [
        "Hình thức mua hàng", "Phương thức thanh toán", "Nhận kèm hóa đơn", "Ngày hạch toán (*)", "Ngày chứng từ (*)",
        "Số phiếu nhập (*)", "Số chứng từ ghi nợ/Số chứng từ thanh toán", "Số hóa đơn", "Ngày hóa đơn",
        "Mã nhà cung cấp", "Tên nhà cung cấp", "Địa chỉ", "Mã số thuế", "Người giao hàng", "Số tài khoản chi",
        "Diễn giải", "Mã hàng", "Tên hàng", "Mã kho", "TK kho/TK chi phí (*)", "TK công nợ/TK tiền (*)", "ĐVT",
        "Số lượng", "Đơn giá", "Thành tiền", "Tỷ lệ CK (%)", "Tiền chiết khấu", "% thuế GTGT", "% thuế suất KHAC",
        "Tiền thuế GTGT", "TK thuế GTGT", "Phí hàng về kho/Chi phí mua hàng", "Nhóm HHDV mua vào"]),
}
KINDS_OF = {CASH_IN: (RECEIPTS, Entry.Method.CASH), BANK_IN: (RECEIPTS, Entry.Method.BANK),
            CASH_OUT: ({Kind.SUPPLIER_PAYMENT, Kind.TEAM_PAYMENT, Kind.LABOR_PAYMENT, Kind.OTHER_EXPENSE}, Entry.Method.CASH),
            BANK_OUT: ({Kind.SUPPLIER_PAYMENT, Kind.TEAM_PAYMENT, Kind.LABOR_PAYMENT, Kind.OTHER_EXPENSE}, Entry.Method.BANK),
            PURCHASE: ({Kind.SUPPLIER_GOODS}, None)}


def d(value):
    return value.strftime("%d/%m/%Y")


def short(text, limit=200):
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit - 1] + "…"


def entries_for(user, kind, date_from, date_to, project=None, include_exported=False):
    kinds, method = KINDS_OF[kind]
    kinds = set(kinds) & services.visible_kinds(user)
    rows = Entry.objects.filter(kind__in=kinds, date__range=(date_from, date_to), voided_at__isnull=True)
    if method:
        rows = rows.filter(method=method)
    rows = rows.filter(project=project) if project else rows.filter(Q(project__in=projects_for(user)) | Q(project__isnull=True))
    if not include_exported:
        rows = rows.filter(misa_export__isnull=True, misa_exported_at__isnull=True)
    return list(rows.select_related("project", "party_ref").order_by("date", "pk"))


class Builder:
    def __init__(self, kind):
        self.kind, self.s, self.notes = kind, AccountingSettings.load(), []

    def number(self, e):
        if e.number:
            return e.number
        made = f"TEND-{'T' if e.kind in RECEIPTS else 'C'}{e.pk:05d}"
        self.notes.append(f"{e}: chưa có số chứng từ, Tend tạm ghi {made}. Sửa theo số trong MISA nếu cần.")
        return made

    def party(self, e):
        party = e.party_ref
        if party is None:
            self.notes.append(f"{e}: đối tượng \"{e.party}\" chưa có trong danh sách đối tượng (không có mã MISA).")
            return "", e.party, ""
        if not party.misa_code:
            self.notes.append(f"Đối tượng {party.name}: thiếu mã trong MISA. Điền ở Tend › Báo cáo › Đối tượng rồi xuất lại.")
        return party.misa_code, party.name, party.address

    def accounts(self, e):
        cash = self.s.cash_account if e.method == Entry.Method.CASH else self.s.bank_account
        if e.kind in RECEIPTS:
            return cash, self.s.other_income_account if e.kind == Kind.OTHER_RECEIPT else self.s.receivable_account
        return (self.s.payable_account if e.kind == Kind.SUPPLIER_PAYMENT else self.s.cost_account), cash

    def description(self, e):
        where = f"{e.project.code} {e.project.name}: " if e.project_id else ""
        return short(f"{where}{e.get_kind_display().lower()} {e.party}" + (f". {e.content}" if e.content else ""))

    def row(self, e):
        code, name, address = self.party(e)
        number, desc = self.number(e), self.description(e)
        amount = int(e.amount)
        if self.kind == PURCHASE:
            return ["Mua hàng trong nước không qua kho", "Chưa thanh toán", "Không kèm hóa đơn", d(e.date), d(e.date), number, "", "", "",
                    code, name, address, e.party_ref.tax_code if e.party_ref else "", "", "", desc, "", short(e.content or "Hàng hoá", 120), "",
                    self.s.cost_account, self.s.payable_account, "", 1, amount, amount]
        debit, credit = self.accounts(e)
        if self.kind == CASH_IN:
            reason = "Thu khác" if e.kind == Kind.OTHER_RECEIPT else "Thu tiền khách hàng (không theo hóa đơn)"
            return [d(e.date), d(e.date), number, code, address, reason, desc, desc, debit, credit, amount, code, ""]
        if self.kind == BANK_IN:
            reason = "Thu khác" if e.kind == Kind.OTHER_RECEIPT else "Thu tiền khách hàng (không theo hóa đơn)"
            return [d(e.date), d(e.date), number, code, self.s.bank_number, self.s.bank_name, reason, desc, desc, debit, credit, amount, code]
        reason = "Trả tiền nhà cung cấp (không theo hóa đơn)" if e.kind == Kind.SUPPLIER_PAYMENT else "Chi khác"
        if self.kind == CASH_OUT:
            return [d(e.date), d(e.date), number, code, address, reason, desc, desc, debit, credit, amount, code, "", "Không"]
        return ["Ủy nhiệm chi", d(e.date), d(e.date), number, reason, desc, self.s.bank_number, self.s.bank_name,
                code, "", "", desc, debit, credit, amount, code, "Không"]

    def workbook(self, rows, title_note):
        title, groups, headers = COLUMNS[self.kind]
        book = Workbook()
        sheet = book.active
        sheet.title = FILE_NAMES[self.kind].replace("_", " ")[:31]
        sheet["A1"] = title
        sheet["A1"].font = Font(bold=True, size=13)
        for i, text in enumerate(GUIDE, start=2):
            sheet.cell(row=i, column=1, value=text)
        for col, text in groups.items():
            sheet[f"{col}7"] = text
            sheet[f"{col}7"].font = Font(bold=True)
        fill = PatternFill("solid", fgColor="DDEBF7")
        for i, text in enumerate(headers, start=1):
            cell = sheet.cell(row=8, column=i, value=text)
            cell.font, cell.fill, cell.alignment = Font(bold=True), fill, Alignment(wrap_text=True, vertical="center")
            sheet.column_dimensions[cell.column_letter].width = 16
        for r, row in enumerate(rows, start=9):
            for i, value in enumerate(row, start=1):
                cell = sheet.cell(row=r, column=i, value=value)
                if isinstance(value, (int, float)):
                    cell.number_format = "#,##0"
        note = book.create_sheet("Lưu ý")
        note["A1"] = f"Tend · {LABELS[self.kind]} · {title_note} · {len(rows)} dòng"
        note["A1"].font = Font(bold=True)
        if self.kind == PURCHASE:
            self.notes.append("Giá trị khoán tổ thợ được tính không xuất sang MISA (Tend chỉ dùng để tính công nợ tổ).")
        for i, text in enumerate(dict.fromkeys(self.notes or ["Không có gì cần lưu ý."]), start=3):
            note.cell(row=i, column=1, value=f"• {text}")
        note.column_dimensions["A"].width = 140
        buffer = BytesIO()
        book.save(buffer)
        return buffer.getvalue()


@transaction.atomic
def export(user, kind, date_from, date_to, project=None, include_exported=False):
    """→ (tên tệp, nội dung, số dòng, lưu ý) hoặc None khi không có phiếu. Đánh dấu phiếu đã xuất (có nhật ký)."""
    entries = entries_for(user, kind, date_from, date_to, project, include_exported)
    if not entries:
        return None
    builder = Builder(kind)
    for e in entries:
        if e.misa_export_id or e.misa_exported_at:
            builder.notes.append(f"{e}: ĐÃ XUẤT MISA trước đó, lần này xuất lại. Cẩn thận nhập trùng.")
    rows = [builder.row(e) for e in entries]
    scope = project.code if project else "các công trình được giao + khoản chung"
    content = builder.workbook(rows, f"{scope} · {d(date_from)}–{d(date_to)}")
    batch = record_save(MisaExport(kind=kind, date_from=date_from, date_to=date_to, project=project, row_count=len(rows),
                                   created_by=user), user, f"Xuất MISA {LABELS[kind]}", channel="web")
    now = timezone.now()
    for e in entries:
        e.misa_export, e.misa_exported_at = batch, now
        record_save(e, user, "Đánh dấu đã xuất MISA", channel="web")
    name = f"{FILE_NAMES[kind]}_{project.code + '_' if project else ''}{date_from:%Y%m%d}-{date_to:%Y%m%d}.xlsx"
    return name, content, len(rows), builder.notes
