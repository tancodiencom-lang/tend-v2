"""Báo cáo Excel tải về: Tổng quan 5 câu hỏi, Báo cáo tháng theo công trình, Lãi lỗ.

Mọi số tính lúc tải từ dữ liệu gốc (overview.compute, các bảng báo cáo/chấm công/phiếu). Phần tiền theo đúng quyền vai:
giám sát/thiết kế chỉ có tiến độ và số công; kế toán nội bộ có chi phí, công nợ NCC/tổ (không giá bán, không tiền khách);
kế toán tài chính và Giám đốc có đủ. Phiếu huỷ không tính.
"""
import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from field import rates
from field.models import Attendance, Report, Worker
from money import services as money
from money.models import ACCRUALS, RECEIPTS, Entry

from .compute import answers, labor_cost, profit, progress, rows

ZERO, HUNDRED = Decimal(0), Decimal(100)
HEAD = PatternFill("solid", fgColor="DFE7DC")
MONEY, QTY, PCT, DAY = "#,##0", "#,##0.###", "0.0%", "DD/MM/YYYY"


class Sheet:
    """Một trang: tiêu đề, dòng ghi chú, bảng có kiểu số theo cột."""

    def __init__(self, book, title, heading, note=""):
        self.ws = book.create_sheet(title[:31])
        self.ws["A1"] = heading
        self.ws["A1"].font = Font(bold=True, size=13)
        if note:
            self.ws["A2"] = note
            self.ws["A2"].font = Font(italic=True, color="56635D")
        self.row = 4

    def table(self, head, rows, formats=None, widths=None):
        formats = formats or {}
        for col, text in enumerate(head, start=1):
            cell = self.ws.cell(row=self.row, column=col, value=text)
            cell.font, cell.fill, cell.alignment = Font(bold=True), HEAD, Alignment(wrap_text=True, vertical="center")
        for values in rows:
            self.row += 1
            for col, value in enumerate(values, start=1):
                if isinstance(value, Decimal):
                    value = float(value)
                cell = self.ws.cell(row=self.row, column=col, value=value)
                if head[col - 1] in formats and value is not None:
                    cell.number_format = formats[head[col - 1]]
        for col, text in enumerate(head, start=1):
            width = (widths or {}).get(text) or max(10, min(40, len(str(text)) + 4))
            self.ws.column_dimensions[get_column_letter(col)].width = width
        self.row += 2

    def pairs(self, title, items, fmt=MONEY):
        """Khối "nhãn – giá trị" (tóm tắt)."""
        self.ws.cell(row=self.row, column=1, value=title).font = Font(bold=True)
        for label, value in items:
            self.row += 1
            self.ws.cell(row=self.row, column=1, value=label)
            if isinstance(value, Decimal):
                value = float(value)
            cell = self.ws.cell(row=self.row, column=2, value=value if value is not None else "chưa có dữ liệu")
            if isinstance(value, date):
                cell.number_format = DAY
            elif isinstance(value, (int, float)):
                cell.number_format = fmt if not isinstance(fmt, dict) else fmt.get(label, MONEY)
        self.ws.column_dimensions["A"].width = max(self.ws.column_dimensions["A"].width or 0, 42)
        self.ws.column_dimensions["B"].width = max(self.ws.column_dimensions["B"].width or 0, 20)
        self.row += 2


def _book():
    book = Workbook()
    book.remove(book.active)
    return book


def _bytes(book):
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _pct(value):
    return None if value is None else float(value) / 100


# ---------- 1. Tổng quan 5 câu hỏi ----------

def overview_book(user, projects, today=None):
    today = today or timezone.localdate()
    book = _book()
    summary = Sheet(book, "Tổng quan", f"Tổng quan 5 câu hỏi · {today:%d/%m/%Y}",
                    "Tiến độ theo giá trị (khối lượng thiết kế × đơn giá bán); thiếu dữ liệu ghi rõ, không ghi 0.")
    head = ["Công trình", "Tiến độ hiện tại", "Kế hoạch đến nay", "Dự kiến xong", "Hẹn bàn giao", "Trễ (ngày)", "Công 7 ngày"]
    if user.sees_payables:
        head += ["Nợ NCC", "Nợ tổ thợ", "Nợ công nhật"]
    if user.sees_receivables:
        head += ["Khách còn phải trả"]
    if user.sees_profit:
        head += ["Lãi dự báo", "Tỷ lệ lãi"]
    data_rows, details = [], []
    for project in projects:
        data = answers(user, project, today)
        p, lab = data["progress"], data["labor"]
        row = [f"{project.code} · {project.name}", _pct(p["actual"]), _pct(p["planned"]), p["forecast"], p["target"], p["late"],
               lab["week_total"]]
        if user.sees_payables:
            pay = data["payables"]
            row += [pay["supplier_debt"], pay["team_debt"], pay["labor_debt"]]
        if user.sees_receivables:
            row += [data["customer"]["remaining"]]
        if user.sees_profit:
            row += [data["profit"]["forecast"], _pct(data["profit"]["ratio"])]
        data_rows.append(row)
        details.append((project, data))
    summary.table(head, data_rows, {"Tiến độ hiện tại": PCT, "Kế hoạch đến nay": PCT, "Dự kiến xong": DAY, "Hẹn bàn giao": DAY,
                                    "Công 7 ngày": QTY, "Nợ NCC": MONEY, "Nợ tổ thợ": MONEY, "Nợ công nhật": MONEY,
                                    "Khách còn phải trả": MONEY, "Lãi dự báo": MONEY, "Tỷ lệ lãi": PCT},
                  {"Công trình": 34})
    for project, data in details:
        sheet = Sheet(book, project.code, f"{project.code} · {project.name} · {today:%d/%m/%Y}")
        p = data["progress"]
        sheet.pairs("1–2. Tiến độ", [("Tiến độ hiện tại", _pct(p["actual"])), ("Kế hoạch đến hôm nay", _pct(p["planned"])),
                                     ("Dự kiến xong (tốc độ 14 ngày)", p["forecast"]), ("Hẹn bàn giao", p["target"]),
                                     ("Trễ so với hẹn (ngày)", p["late"]), ("Chi tiết đã có báo cáo", f"{p['reported']}/{p['item_count']}")],
                    {"Tiến độ hiện tại": PCT, "Kế hoạch đến hôm nay": PCT, "Dự kiến xong (tốc độ 14 ngày)": DAY, "Hẹn bàn giao": DAY,
                     "Trễ so với hẹn (ngày)": "0"})
        if p["missing"]:
            sheet.pairs("Thiếu dữ liệu", [(p["missing"], "")])
        if p["overdue"]:
            sheet.table(["Quá hạn kế hoạch", "Công việc", "% đã làm", "Quá (ngày)"],
                        [[x["code"], x["work"], _pct(x["percent"]), x["days"]] for x in p["overdue"]], {"% đã làm": PCT})
        if "payables" in data:
            pay = data["payables"]
            sheet.table(["Nhà cung cấp", "Hàng đã nhận", "Đã trả", "Còn nợ"],
                        [[s["name"], s["goods"], s["paid"], s["debt"]] for s in pay["suppliers"]],
                        {"Hàng đã nhận": MONEY, "Đã trả": MONEY, "Còn nợ": MONEY}, {"Nhà cung cấp": 30})
            sheet.table(["Tổ thợ (khoán)", "Được tính", "Đã trả", "Còn nợ"],
                        [[s["name"], s["work"], s["paid"], s["debt"]] for s in pay["teams"]],
                        {"Được tính": MONEY, "Đã trả": MONEY, "Còn nợ": MONEY})
            sheet.pairs("Lương công nhật", [("Ước tính (công × đơn giá)", pay["labor_accrued"]), ("Đã trả", pay["labor_paid"]),
                                           ("Còn nợ", pay["labor_debt"])])
        if "customer" in data:
            c = data["customer"]
            sheet.pairs("4. Công nợ khách", [("Giá trị hợp đồng", c["contract"]), ("Đã thu", c["received"]), ("Còn phải thu", c["remaining"])])
        if "profit" in data:
            pro = data["profit"]
            sheet.pairs("5. Dự báo lãi lỗ", [("Doanh thu (hợp đồng)", pro["revenue"]), ("Chi phí đã phát sinh", pro["incurred"]),
                                             ("Giá vốn phần việc còn lại", pro["remaining_cost"]), ("Lãi dự báo", pro["forecast"])])
    return _bytes(book)


# ---------- 2. Báo cáo tháng theo công trình ----------

def month_range(first, today=None):
    today = today or timezone.localdate()
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return first, min(last, today)


def month_book(user, project, first, today=None):
    start, end = month_range(first, today)
    before = start - timedelta(days=1)
    book = _book()
    sale, cost = user.sees_receivables, user.sees_payables
    p_end = progress(project, end)
    data = rows(project)

    # Tiến độ từng chi tiết
    items = Sheet(book, "Tiến độ", f"{project.code} · tiến độ tháng {first:%m/%Y} ({start:%d/%m}–{end:%d/%m/%Y})",
                  "% theo báo cáo mới nhất tính đến đầu/cuối kỳ; khối lượng làm trong tháng = thiết kế × (% cuối − % đầu).")
    head = ["Tầng", "Mã", "Công việc", "Vị trí", "Đơn vị", "KL thiết kế", "% đầu tháng", "% cuối tháng", "KL làm trong tháng"]
    if sale:
        head += ["Giá trị làm trong tháng (giá bán)"]
    if cost:
        head += ["Giá vốn phần làm trong tháng"]
    table, totals = [], defaultdict(lambda: ZERO)
    for r in sorted(data, key=lambda r: (r.item.floor, r.item.code)):
        item = r.item
        a, b = r.percent_at(before), r.percent_at(end)
        qty = (item.design_quantity * (b - a) / HUNDRED).quantize(Decimal("0.001"))
        row = [item.floor, item.code, item.work, item.location, item.unit, item.design_quantity, _pct(a), _pct(b), qty]
        if sale:
            value = qty * item.sale_price if item.sale_price is not None else None
            totals["sale"] += value or ZERO
            row.append(value)
        if cost:
            value = qty * item.cost_price if item.cost_price is not None else None
            totals["cost"] += value or ZERO
            row.append(value)
        table.append(row)
    items.table(head, table, {"KL thiết kế": QTY, "% đầu tháng": PCT, "% cuối tháng": PCT, "KL làm trong tháng": QTY,
                              "Giá trị làm trong tháng (giá bán)": MONEY, "Giá vốn phần làm trong tháng": MONEY},
                {"Công việc": 34, "Vị trí": 24})

    # Tóm tắt (đặt lên đầu)
    summary = Sheet(book, "Tóm tắt", f"Báo cáo tháng {first:%m/%Y} · {project.code} · {project.name}",
                    f"Từ {start:%d/%m/%Y} đến {end:%d/%m/%Y}. Lập lúc {timezone.localtime():%d/%m/%Y %H:%M}.")
    book.move_sheet(summary.ws, offset=-1)
    summary.pairs("Tiến độ", [("Tiến độ cuối kỳ", _pct(p_end["actual"])), ("Kế hoạch đến cuối kỳ", _pct(p_end["planned"])),
                              ("Dự kiến xong", p_end["forecast"]), ("Hẹn bàn giao", p_end["target"])],
                  {"Tiến độ cuối kỳ": PCT, "Kế hoạch đến cuối kỳ": PCT, "Dự kiến xong": DAY, "Hẹn bàn giao": DAY})
    if p_end["missing"]:
        summary.pairs("Thiếu dữ liệu", [(p_end["missing"], "")])
    reports = list(Report.objects.filter(project=project, work_date__range=(start, end))
                   .select_related("author").prefetch_related("lines__item", "photos").order_by("work_date", "created_at"))
    attendance = list(Attendance.objects.filter(project=project, work_date__range=(start, end)).select_related("worker", "staff"))
    worker_days = sum((a.days for a in attendance if a.worker_id), ZERO)
    summary.pairs("Hiện trường", [("Số báo cáo ngày", len(reports)), ("Số ngày có báo cáo", len({r.work_date for r in reports})),
                                  ("Tổng công thợ", worker_days),
                                  ("Lượt có mặt của giám sát", sum(1 for a in attendance if a.staff_id))], QTY)
    if sale or cost:
        money_items = []
        if sale:
            money_items.append(("Giá trị làm trong tháng (giá bán)", totals["sale"]))
        if cost:
            month_labor, _ = labor_cost(project, start, end)
            money_items += [("Giá vốn phần làm trong tháng", totals["cost"]), ("Chi phí công nhật ước tính trong tháng", month_labor)]
        summary.pairs("Giá trị trong tháng", money_items)

    # Báo cáo ngày
    log = Sheet(book, "Báo cáo ngày", f"{project.code} · các báo cáo ngày tháng {first:%m/%Y}")
    log.table(["Ngày", "Người báo", "Chi tiết", "Ảnh", "Ghi chú"],
              [[r.work_date, str(r.author), "; ".join(
                  f"{l.item.code} {float(l.percent):g}%" if l.percent is not None else f"{l.item.code} {float(l.quantity):g} {l.item.unit}"
                  for l in r.lines.all()), r.photos.count(), r.note[:500]] for r in reports],
              {"Ngày": DAY}, {"Chi tiết": 50, "Ghi chú": 50})

    # Chấm công
    people = defaultdict(lambda: {"days": ZERO, "dates": set(), "worker": None})
    attendance = list(attendance)
    rate_table = rates.table(a.worker_id for a in attendance if a.worker_id)
    for a in attendance:
        key = a.worker.name if a.worker_id else f"{a.staff} (giám sát)"
        people[key]["days"] += a.days
        people[key]["dates"].add(a.work_date)
        people[key]["worker"] = a.worker
        if a.worker is not None:
            pay, amount = rates.at(rate_table, a.worker, a.work_date)
            if pay == Worker.Pay.DAILY and amount is not None:
                people[key]["rate"] = amount
                people[key]["cost"] = (people[key].get("cost") or ZERO) + a.days * amount
    work = Sheet(book, "Chấm công", f"{project.code} · chấm công tháng {first:%m/%Y}", "8 giờ = 1 công. Thợ tổ khoán không tính tiền công nhật.")
    head = ["Người", "Tổ", "Loại", "Hình thức", "Tổng công", "Số ngày"]
    if cost:
        head += ["Đơn giá", "Thành tiền công nhật"]
    table = []
    for name, v in sorted(people.items()):
        w = v["worker"]
        row = [name, w.team if w else "", (w.trade.name if w.trade_id else w.get_kind_display()) if w else "Giám sát",
               w.get_pay_display() if w else "", v["days"], len(v["dates"])]
        if cost:
            row += [v.get("rate"), v.get("cost")]
        table.append(row)
    work.table(head, table, {"Tổng công": QTY, "Đơn giá": MONEY, "Thành tiền công nhật": MONEY}, {"Người": 28})

    # Thu chi trong tháng
    kinds = money.visible_kinds(user)
    if kinds:
        entries = list(Entry.objects.filter(project=project, date__range=(start, end), kind__in=kinds, voided_at__isnull=True)
                       .select_related("entered_by").order_by("date", "pk"))
        cash = Sheet(book, "Thu chi", f"{project.code} · thu chi tháng {first:%m/%Y}", "Phiếu đã huỷ không có trong bảng.")
        cash.table(["Ngày", "Số phiếu", "Loại", "Đối tượng", "Nội dung", "Thu", "Chi", "Phát sinh nợ", "Người nhập"],
                   [[e.date, e.number, e.get_kind_display(), e.party, e.content,
                     e.amount if e.kind in RECEIPTS else None, e.amount if e.kind not in RECEIPTS | ACCRUALS else None,
                     e.amount if e.kind in ACCRUALS else None, str(e.entered_by)] for e in entries],
                   {"Ngày": DAY, "Thu": MONEY, "Chi": MONEY, "Phát sinh nợ": MONEY}, {"Nội dung": 34, "Đối tượng": 26})
        cash.pairs("Tổng tháng", [("Thu", sum((e.amount for e in entries if e.kind in RECEIPTS), ZERO)),
                                  ("Chi", sum((e.amount for e in entries if e.kind not in RECEIPTS | ACCRUALS), ZERO)),
                                  ("Phát sinh nợ (hàng NCC, khoán)", sum((e.amount for e in entries if e.kind in ACCRUALS), ZERO))])
    return _bytes(book)


# ---------- 3. Lãi lỗ ----------

def profit_book(user, projects, today=None):
    today = today or timezone.localdate()
    book = _book()
    sheet = Sheet(book, "Lãi lỗ", f"Lãi lỗ các công trình · {today:%d/%m/%Y}",
                  "Lãi dự báo = doanh thu − chi phí đã phát sinh − giá vốn phần việc còn lại. "
                  "Lãi theo tiến độ = giá trị đã làm (giá bán × %) − chi phí đã phát sinh.")
    head = ["Công trình", "Doanh thu (hợp đồng)", "Giá trị đã làm", "Hàng NCC", "Khoán tổ thợ", "Công nhật (ước tính)",
            "Lương tháng phân bổ", "Chi khác", "Chi phí đã phát sinh", "Lãi theo tiến độ", "Giá vốn còn lại", "Lãi dự báo", "Tỷ lệ lãi dự báo", "Ghi chú"]
    table, total = [], defaultdict(lambda: ZERO)
    for project in projects:
        pro = profit(project, today)
        parts = pro["parts"]
        notes = []
        if pro["missing_cost"]:
            notes.append(f"{pro['missing_cost']} chi tiết chưa có giá vốn")
        if pro["missing_rates"]:
            notes.append("nhân sự chưa có đơn giá: " + ", ".join(pro["missing_rates"]))
        if not pro["revenue"]:
            notes.append("chưa có giá trị hợp đồng")
        no_sale = sum(1 for r in rows(project) if r.item.sale_price is None)
        if no_sale:
            notes.append(f"{no_sale} chi tiết chưa có giá bán (giá trị đã làm tính thiếu)")
        row = [f"{project.code} · {project.name}", pro["revenue"], pro["earned"], parts["goods"], parts["team"], parts["labor"],
               parts["salary"], parts["other"], pro["incurred"], pro["earned"] - pro["incurred"], pro["remaining_cost"], pro["forecast"],
               _pct(pro["ratio"]), "; ".join(notes)]
        for key, value in zip(head[1:12], row[1:12]):
            total[key] += value or ZERO
        table.append(row)
    table.append(["TỔNG"] + [total[k] for k in head[1:12]] + [None, ""])
    sheet.table(head, table, {k: MONEY for k in head[1:12]} | {"Tỷ lệ lãi dự báo": PCT}, {"Công trình": 34, "Ghi chú": 40})
    sheet.ws.cell(row=sheet.row - 2, column=1).font = Font(bold=True)
    return _bytes(book)
