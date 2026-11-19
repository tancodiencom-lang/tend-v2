"""AI đọc tin nhắn: chuẩn hoá báo cáo/chấm công thành phiếu, hoặc trả lời câu hỏi bằng số liệu thật.

- Mô hình mặc định Claude Haiku 5.5 (định hướng 28/10/2026); đổi bằng TEND_AI_MODEL.
- AI chỉ có các "công cụ" do Tend định nghĩa: đọc số liệu chạy dưới đúng tài khoản người hỏi
  (quyền xem tiền giữ nguyên), và lập phiếu. AI **không ghi gì**; phiếu chỉ ghi khi người dùng bấm Xác nhận.
- Trần chi phí theo tháng (TEND_AI_MONTHLY_USD); vượt trần hoặc thiếu khoá thì trả None để bot dùng lệnh không AI.
"""
import json
import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from .models import AiUsage

log = logging.getLogger(__name__)
MAX_STEPS = 5
# Giá niêm yết USD / 1 triệu token (vào, ra). Mô hình lạ tính theo giá cao để không vượt trần.
PRICES = {'claude-haiku-5-5': (Decimal('0.10'), Decimal('0.50')), 'claude-sonnet-5-5': (Decimal(2), Decimal(10)),
          'claude-opus-5-5': (Decimal(4), Decimal(20))}
SYSTEM = """Bạn là trợ lý nhập liệu của Tend, phần mềm quản lý công trình nội thất Tân Cổ Điển. Người dùng là nhân viên
công ty nhắn qua Telegram bằng tiếng Việt, thường viết tắt, không dấu.

Việc của bạn:
1. Tin nhắn là BÁO CÁO hiện trường (đã làm gì, bao nhiêu %, khối lượng, ảnh, ai làm hôm nay): gọi find_details để lấy
   đúng mã chi tiết, rồi gọi propose_report. Chỉ dùng mã có trong kết quả find_details; không chắc thì ask_user.
   Chấm công: liệt kê từng người được nêu tên trong people (tên đúng như người dùng viết; "nửa ngày" → days 0.5;
   "4 tiếng/4h/4 giờ" → hours 4; không nói gì → cả ngày). Người báo cáo (giám sát) tự có công, không đưa vào people.
   Nếu người dùng nói tổ của người đó thì ghi team. Không tự bịa tên, không gộp người.
2. Sửa chấm công đã gửi ("sửa công Hùng hôm qua thành nửa ngày vì về sớm"): gọi propose_attendance_fix, phải có lý do;
   thiếu lý do thì ask_user.
3. Trả lời một YÊU CẦU BÁO CÁO không phải về tiến độ (xem pending_asks): gọi propose_answer.
4. CÂU HỎI về tiến độ, nhân công, chấm công, công nợ, lãi lỗ: gọi list_projects / project_overview / attendance_summary
   rồi trả lời ngắn bằng tiếng Việt có dấu, tiền dạng 1.250.000 ₫, ngày dd/mm/yyyy, số thập phân dấu phẩy. Chỉ nói số
   có trong kết quả công cụ; công cụ không trả mục nào thì nói người dùng không có quyền xem hoặc chưa có dữ liệu.
5. PHIẾU THU/CHI của kế toán (chữ hoặc ảnh phiếu thu, phiếu chi, uỷ nhiệm chi, phiếu nhập hàng): gọi propose_entries, mỗi
   phiếu một dòng: kind (thu_khach thu tiền khách; thu_khac thu khác; hang_ncc hàng nhà cung cấp đã nhận, chưa trả;
   tra_ncc trả nhà cung cấp; khoan_tinh giá trị khoán tổ thợ được tính; tra_to trả tổ thợ; tra_cong trả lương công
   nhật; chi_khac chi khác), số phiếu, ngày, đối tượng (tên NCC/tổ/khách/người nhận đúng như trên phiếu), công trình
   (mã), nội dung, số tiền (số nguyên đồng), tiền mặt hay chuyển khoản, photo_index (ảnh số mấy, nếu có ảnh). Chỉ ghi
   số đọc rõ trên ảnh/tin; không đọc được số tiền thì ask_user. Huỷ phiếu đã ghi: propose_void (số phiếu + lý do).
6. CHỈ ĐẠO GIAO VIỆC của Giám đốc (vd "nhắc Nam gửi bản vẽ điện TN-22 trước thứ Sáu", "bảo kế toán đối chiếu công nợ Cát
   Hùng Vương mai"): gọi find_people lấy đúng người, rồi propose_task: owner_name đúng tên đăng nhập từ find_people, title
   ngắn gọn bắt đầu bằng động từ, due_date YYYY-MM-DD và due_time HH:MM (không nói giờ thì 17:00; "trước thứ Sáu" là thứ
   Năm 17:00), project_code nếu có, criteria (thế nào là xong) nếu nói được. Không rõ người hoặc hạn thì ask_user. Người
   không phải Giám đốc nhờ giao việc: trả lời chỉ Giám đốc giao việc qua trợ lý. Câu hỏi "việc nào đang trễ", "Nam còn
   việc gì": gọi list_tasks.
7. Câu hỏi KHÔNG trả lời được bằng công cụ (cần quyết định, cam kết, giá, thay đổi thiết kế, thông tin không có trong Tend)
   của người không phải Giám đốc: gọi ask_director với câu hỏi viết rõ (ai hỏi, về việc gì) và bản nháp trả lời nếu có
   căn cứ. Không tự hứa, không đoán.
8. Không bịa số liệu, không tự đoán phần trăm. Thiếu công trình thì hỏi lại (trừ khi người dùng chỉ có một công trình).
Trả lời ngắn, tối đa khoảng 12 dòng. Không dùng Markdown phức tạp."""

_OBJ = {"type": "object", "properties": {}, "additionalProperties": False}
TOOLS = [
    {"name": "list_projects", "description": "Công trình người dùng được vào và quyền xem tiền của họ.", "input_schema": _OBJ},
    {"name": "project_overview", "description": "Năm câu hỏi của một công trình theo quyền người hỏi: tiến độ hiện tại, dự kiến, "
     "nhân công, công nợ NCC/tổ thợ/công nhật, công nợ khách, dự báo lãi lỗ.",
     "input_schema": {"type": "object", "properties": {"project_code": {"type": "string"}}, "required": ["project_code"],
                      "additionalProperties": False}},
    {"name": "find_details", "description": "Tìm chi tiết trên bản vẽ theo mã (vd T2.5) hoặc tên công việc/tầng/phòng. "
     "Trả mã, công việc, vị trí, tầng, đơn vị, % đã báo.",
     "input_schema": {"type": "object", "properties": {"project_code": {"type": "string"}, "query": {"type": "string"}},
                      "required": ["project_code", "query"], "additionalProperties": False}},
    {"name": "find_workers", "description": "Danh sách thợ (tên, tổ, thợ/phụ) gần với tên cần tìm, hoặc thợ hay làm ở công trình.",
     "input_schema": {"type": "object", "properties": {"project_code": {"type": "string"}, "name": {"type": "string"}},
                      "additionalProperties": False}},
    {"name": "attendance_summary", "description": "Số công theo người trong khoảng ngày (mặc định tháng này), lọc theo công trình "
     "và/hoặc tên; có tiền công nhật nếu người hỏi được xem chi phí.",
     "input_schema": {"type": "object", "properties": {"project_code": {"type": "string"}, "name": {"type": "string"},
                                                       "start": {"type": "string", "description": "YYYY-MM-DD"},
                                                       "end": {"type": "string", "description": "YYYY-MM-DD"}},
                      "additionalProperties": False}},
    {"name": "pending_asks", "description": "Các yêu cầu báo cáo Giám đốc đang chờ chính người dùng trả lời.", "input_schema": _OBJ},
    {"name": "propose_report", "description": "Lập phiếu báo cáo ngày chờ người dùng bấm Xác nhận. Mỗi dòng một chi tiết với % ước "
     "lượng HOẶC khối lượng lũy kế (tổng đã làm tới nay), không cả hai. Báo thấp hơn mức đã báo thì ghi reason nếu người dùng "
     "đã nói lý do. people: những người được chấm công hôm nay.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["project_code", "lines", "people", "note"],
                      "properties": {
                          "project_code": {"type": "string"},
                          "work_date": {"type": "string", "description": "YYYY-MM-DD; bỏ trống là hôm nay"},
                          "lines": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["code"],
                                    "properties": {"code": {"type": "string"}, "percent": {"type": "number"},
                                                   "quantity": {"type": "number"}, "note": {"type": "string"},
                                                   "reason": {"type": "string"}}}},
                          "people": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["name"],
                                     "properties": {"name": {"type": "string"}, "days": {"type": "number"},
                                                    "hours": {"type": "number"}, "team": {"type": "string"}}}},
                          "note": {"type": "string"}}}},
    {"name": "propose_attendance_fix", "description": "Lập phiếu sửa chấm công một người (đặt lại số công; 0 là bỏ công).",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["project_code", "work_date", "name", "reason"],
                      "properties": {"project_code": {"type": "string"}, "work_date": {"type": "string"},
                                     "name": {"type": "string"}, "days": {"type": "number"}, "hours": {"type": "number"},
                                     "reason": {"type": "string"}}}},
    {"name": "propose_answer", "description": "Lập câu trả lời bằng chữ cho một yêu cầu báo cáo.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["ask_id", "note"],
                      "properties": {"ask_id": {"type": "string"}, "note": {"type": "string"}}}},
    {"name": "money_summary", "description": "Thu, chi, phát sinh nợ và công nợ NCC/tổ thợ/khách theo quyền người hỏi, trong "
     "khoảng ngày (mặc định tháng này), lọc theo công trình.",
     "input_schema": {"type": "object", "properties": {"project_code": {"type": "string"}, "start": {"type": "string"},
                                                       "end": {"type": "string"}}, "additionalProperties": False}},
    {"name": "list_entries", "description": "Danh sách phiếu thu chi theo quyền người hỏi (ngày, số phiếu, loại, đối tượng, "
     "công trình, số tiền), lọc theo khoảng ngày, công trình, tên đối tượng.",
     "input_schema": {"type": "object", "properties": {"project_code": {"type": "string"}, "party": {"type": "string"},
                                                       "start": {"type": "string"}, "end": {"type": "string"}},
                      "additionalProperties": False}},
    {"name": "find_parties", "description": "Tìm đối tượng (NCC, tổ thợ, khách) gần với tên.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}},
    {"name": "propose_entries", "description": "Lập phiếu thu/chi chờ người nhập bấm Xác nhận.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["entries"], "properties": {
         "entries": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                                                "required": ["kind", "party", "amount"], "properties": {
             "kind": {"type": "string", "enum": ["thu_khach", "thu_khac", "hang_ncc", "tra_ncc", "khoan_tinh", "tra_to",
                                                 "tra_cong", "chi_khac"]},
             "project_code": {"type": "string"}, "date": {"type": "string", "description": "YYYY-MM-DD"},
             "number": {"type": "string"}, "party": {"type": "string"}, "content": {"type": "string"},
             "amount": {"type": "integer"}, "method": {"type": "string", "enum": ["tien_mat", "chuyen_khoan"]},
             "photo_index": {"type": "integer"}}}}}}},
    {"name": "propose_void", "description": "Xin huỷ một phiếu đã ghi (số phiếu hoặc #mã) kèm lý do.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["ref", "reason"],
                      "properties": {"ref": {"type": "string"}, "reason": {"type": "string"}}}},
    {"name": "find_people", "description": "Nhân viên gần với tên cần tìm (tên, tên đăng nhập, vai, công trình). Chỉ Giám đốc.",
     "input_schema": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"], "additionalProperties": False}},
    {"name": "list_tasks", "description": "Việc giao đang mở (Giám đốc: mọi người; người khác: việc của mình), có hạn và trạng thái "
     "trễ/chưa nhận/báo xong.", "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                                                  "additionalProperties": False}},
    {"name": "propose_task", "description": "Lập phiếu giao việc chờ Giám đốc bấm Xác nhận; trợ lý sẽ giao, nhắc, báo trễ.",
     "input_schema": {"type": "object", "properties": {
         "owner_name": {"type": "string", "description": "Tên đăng nhập người nhận (từ find_people)"},
         "title": {"type": "string"}, "due_date": {"type": "string", "description": "YYYY-MM-DD"},
         "due_time": {"type": "string", "description": "HH:MM, mặc định 17:00"}, "project_code": {"type": "string"},
         "criteria": {"type": "string", "description": "Tiêu chí xong"}},
         "required": ["owner_name", "title", "due_date"], "additionalProperties": False}},
    {"name": "ask_director", "description": "Chuyển câu hỏi khó cho Giám đốc; trợ lý sẽ trả lời lại đúng chỗ khi Giám đốc trả lời.",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}, "draft": {"type": "string",
                      "description": "Bản nháp trả lời để Giám đốc duyệt nhanh (để trống nếu không có căn cứ)"}},
                      "required": ["question"], "additionalProperties": False}},
    {"name": "ask_user", "description": "Hỏi lại khi thiếu thông tin hoặc không chắc chi tiết/người nào.",
     "input_schema": {"type": "object", "additionalProperties": False, "required": ["question"],
                      "properties": {"question": {"type": "string"}}}},
]
FINAL = {'propose_report', 'propose_attendance_fix', 'propose_answer', 'propose_entries', 'propose_void', 'propose_task',
         'ask_director', 'ask_user'}
# Trong nhóm: không có công cụ tiền (cả nhóm cùng đọc câu trả lời; phiếu thu chi nhập ở tin riêng)
GROUP_EXCLUDED = {'money_summary', 'list_entries', 'find_parties', 'propose_entries', 'propose_void'}


def model():
    return getattr(settings, 'TEND_AI_MODEL', 'claude-haiku-5-5')


def month_cost(today=None):
    today = today or timezone.localdate()
    totals = AiUsage.objects.filter(day__year=today.year, day__month=today.month).aggregate(
        i=Sum('input_tokens'), o=Sum('output_tokens'))
    price_in, price_out = PRICES.get(model(), (Decimal(10), Decimal(50)))
    return (Decimal(totals['i'] or 0) * price_in + Decimal(totals['o'] or 0) * price_out) / Decimal(1_000_000)


def available():
    """Có khoá AI, có thư viện và còn trong trần chi phí tháng."""
    import os
    if not os.environ.get('ANTHROPIC_API_KEY'):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return month_cost() < Decimal(str(getattr(settings, 'TEND_AI_MONTHLY_USD', 4)))


def _record(usage):
    with transaction.atomic():
        row, _ = AiUsage.objects.get_or_create(day=timezone.localdate())
        AiUsage.objects.filter(pk=row.pk).update(input_tokens=F('input_tokens') + int(getattr(usage, 'input_tokens', 0) or 0),
                                                 output_tokens=F('output_tokens') + int(getattr(usage, 'output_tokens', 0) or 0),
                                                 calls=F('calls') + 1)


def create_message(**kwargs):
    """Một lần gọi Claude. Bài kiểm thay hàm này bằng bản giả."""
    import anthropic
    client = anthropic.Anthropic(timeout=40.0, max_retries=1)
    return client.messages.create(**kwargs)


def image_blocks(images):
    """Ảnh (bytes) → khối ảnh cho Claude, thu nhỏ cạnh dài ≤ 1568 px (đủ đọc chữ trên phiếu, ít token)."""
    import base64
    from io import BytesIO

    from PIL import Image
    blocks = []
    for data in images[:4]:
        try:
            picture = Image.open(BytesIO(data))
            picture = picture.convert('RGB')
            picture.thumbnail((1568, 1568))
            out = BytesIO()
            picture.save(out, 'JPEG', quality=85)
        except Exception:   # ảnh hỏng: bỏ qua, AI vẫn đọc chữ
            continue
        blocks.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/jpeg',
                                                   'data': base64.b64encode(out.getvalue()).decode()}})
    return blocks


def group_tools():
    return [tool for tool in TOOLS if tool['name'] not in GROUP_EXCLUDED]


def run(text, history, context, execute, images=(), tools=None):
    """Chạy vòng công cụ. `execute(name, input)` do bot cung cấp, chạy dưới quyền người dùng.
    `images`: ảnh vừa gửi (bytes) theo thứ tự — ảnh số 0, 1, … — để AI đọc phiếu thu/chi.

    Trả {'type': 'final', 'tool': tên, 'input': {...}} khi AI lập phiếu/hỏi lại, {'type': 'text', 'text': ...}
    khi AI trả lời câu hỏi, hoặc None khi gọi AI lỗi.
    """
    prompt = f'{context}\n\nTin nhắn: {text}'
    blocks = image_blocks(images) if images else []
    content = [*blocks, {'type': 'text', 'text': prompt}] if blocks else prompt
    messages = [*history, {'role': 'user', 'content': content}]
    for _ in range(MAX_STEPS):
        try:
            response = create_message(model=model(), max_tokens=2000, system=[{'type': 'text', 'text': SYSTEM,
                                      'cache_control': {'type': 'ephemeral'}}], tools=tools or TOOLS, messages=messages,
                                      output_config={'effort': 'low'})
        except Exception as error:   # mạng/hết hạn mức/khoá sai: bot báo và dùng lệnh không AI
            log.warning('Gọi AI lỗi (%s)', type(error).__name__)
            return None
        _record(response.usage)
        if response.stop_reason == 'refusal':
            return {'type': 'text', 'text': 'Trợ lý không xử lý được tin này. Anh/chị nhập trên Tend giúp.'}
        uses = [block for block in response.content if block.type == 'tool_use']
        final = next((block for block in uses if block.name in FINAL), None)
        if final is not None:
            return {'type': 'final', 'tool': final.name, 'input': dict(final.input or {})}
        if not uses:
            reply = '\n'.join(block.text for block in response.content if block.type == 'text').strip()
            return {'type': 'text', 'text': reply or 'Chưa hiểu tin nhắn. Anh/chị viết rõ hơn giúp.'}
        messages.append({'role': 'assistant', 'content': response.content})
        results = []
        for block in uses:
            try:
                content, failed = json.dumps(execute(block.name, dict(block.input or {})), ensure_ascii=False, default=str), False
            except Exception as error:   # lỗi quyền/không tìm thấy trả về cho AI, không làm sập bot
                content, failed = f'Lỗi: {error}', True
            results.append({'type': 'tool_result', 'tool_use_id': block.id, 'content': content[:20000], 'is_error': failed})
        messages.append({'role': 'user', 'content': results})
    return {'type': 'text', 'text': 'Câu hỏi cần nhiều bước quá. Anh/chị hỏi ngắn hơn hoặc xem trên Tend.'}
