"""Bot trong nhóm Telegram nội bộ.

- Giám đốc gắn nhóm: `/nhom TN-22` (nhóm của một công trình), `/nhom chung` (nhóm chung), `/nhom tat` (thôi dùng),
  `/nhom nghe` / `/nhom khongnghe` (bật/tắt tự nhận báo cáo từ tin thường).
- Chỉ người đã kết nối Tend mới được xử lý; người lạ bị bỏ qua (nhắc kết nối một lần mỗi ngày khi họ gọi bot).
- Gọi bot (nhắc @tên_bot, trả lời tin của bot): trợ lý trả lời TRONG NHÓM nhưng không có phần tiền, chỉ công trình của nhóm;
  câu khó thì hỏi Giám đốc rồi trả lời lại trong nhóm.
- Lệnh (/viec, /xong, /tongquan…): trả lời RIÊNG người gõ (có thể có tiền theo vai của họ).
- Nghe tin thường (khi bật): ảnh lưu như tin riêng; tin rõ là báo cáo/chấm công/phiếu thì lập phiếu và GỬI RIÊNG người nhắn
  bấm Xác nhận. Không ai xác nhận hộ người khác; trợ lý không tự ghi gì.
"""
import re

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from assistant.models import Notice
from core import secretscan
from core.audit import record_save
from projects.models import Project

from . import telegram
from .models import ChatLink, Group

PREFIX = "🤖 Trợ lý: "
INTRO = ("Chào cả nhà, em là trợ lý Tend. Giám đốc nhắn /nhom <mã công trình> (vd /nhom TN-22) hoặc /nhom chung để em làm "
         "việc trong nhóm này.\n• Mỗi người cần kết nối Tend riêng với em trước (app.tend.vn › Tài khoản › Kết nối Telegram).\n"
         "• Gọi em bằng @{bot} hoặc trả lời tin của em. Báo cáo mở đầu kèm ngày (vd \"Báo cáo ngày 20/11\" hoặc \"Công việc ngày "
         "20/11\") em lập phiếu gửi RIÊNG người nhắn xác nhận, xác nhận xong em đăng tóm tắt vào nhóm; trong nhóm em không nói số "
         "tiền. Cuối ngày 18:30 em gửi tóm tắt công trình vào nhóm.\n• Câu khó em hỏi Giám đốc rồi trả lời lại trong nhóm.")


def _bot():
    return (telegram.bot_username() or "").lstrip("@")


def addressed(message, text):
    """(có gọi bot không, chữ đã bỏ @tên_bot và đuôi @bot của lệnh)."""
    bot = _bot()
    called = False
    reply = message.get("reply_to_message") or {}
    if (reply.get("from") or {}).get("is_bot") and (not bot or (reply["from"].get("username") or "").casefold() == bot.casefold()):
        called = True
    if bot and re.search(rf"@{re.escape(bot)}\b", text, re.I):
        called = True
        text = re.sub(rf"@{re.escape(bot)}\b", "", text, flags=re.I)
    command = re.match(r"^(/\w+)@(\w+)", text)
    if command:
        if bot and command.group(2).casefold() != bot.casefold():
            return False, ""                  # lệnh gửi cho bot khác trong nhóm
        text = command.group(1) + text[command.end():]
    # Giữ xuống dòng (báo cáo nhiều dòng: việc, nhân sự, dự kiến), chỉ gọn khoảng trắng trong từng dòng
    return called, "\n".join(" ".join(line.split()) for line in text.splitlines() if line.strip())


def _once(key):
    try:
        with transaction.atomic():
            Notice.objects.create(key=key[:120])
        return True
    except IntegrityError:
        return False


def link_group(link_row, chat, arg):
    if link_row is None or not link_row.user.is_director:
        return "Chỉ Giám đốc (đã kết nối Tend) gắn nhóm với công trình."
    group, _ = Group.objects.get_or_create(chat_id=str(chat["id"]), defaults={"title": (chat.get("title") or "")[:200]})
    group.title = (chat.get("title") or group.title)[:200]
    arg = arg.strip().casefold()
    if arg in ("tat", "tắt"):
        group.active = False
        record_save(group, link_row.user, "Thôi dùng trợ lý trong nhóm", channel="telegram")
        return "Đã thôi dùng trợ lý trong nhóm này. Gắn lại: /nhom TN-22 hoặc /nhom chung."
    if arg in ("nghe", "khongnghe", "khôngnghe"):
        group.listening = arg == "nghe"
        record_save(group, link_row.user, "Bật nghe tin" if group.listening else "Tắt nghe tin", channel="telegram")
        return ("Đã bật: tin báo cáo/phiếu trong nhóm được lập phiếu gửi riêng người nhắn." if group.listening
                else "Đã tắt nghe tin: em chỉ làm khi được gọi @" + _bot() + " hoặc lệnh.")
    if arg in ("", "chung"):
        group.project = None
    else:
        project = Project.objects.filter(code__iexact=arg).first()
        if project is None:
            return f"Không có công trình \"{arg.upper()}\". Gõ /nhom TN-22 hoặc /nhom chung."
        group.project = project
    group.active, group.linked_by, group.linked_at = True, link_row.user, timezone.now()
    record_save(group, link_row.user, "Gắn nhóm Telegram", channel="telegram")
    where = f"công trình {group.project.code}" if group.project_id else "nhóm chung (không gắn công trình)"
    return (f"Đã gắn nhóm với {where}. Báo cáo trong nhóm mở đầu kèm ngày (vd \"Báo cáo ngày {timezone.localdate():%d/%m}\") "
            f"em lập phiếu gửi riêng người nhắn xác nhận, xác nhận xong em đăng tóm tắt vào nhóm; gọi em bằng @{_bot()}. "
            "Tắt nghe tin: /nhom khongnghe.")


def on_member(update):
    """Bot được thêm vào / rời nhóm."""
    chat = update.get("chat") or {}
    if chat.get("type") not in ("group", "supergroup"):
        return
    status = ((update.get("new_chat_member") or {}).get("status") or "")
    if status in ("member", "administrator"):
        Group.objects.get_or_create(chat_id=str(chat["id"]), defaults={"title": (chat.get("title") or "")[:200]})
        telegram.send(str(chat["id"]), PREFIX + INTRO.format(bot=_bot()))
    elif status in ("left", "kicked"):
        Group.objects.filter(chat_id=str(chat["id"])).update(active=False)


def handle(message):
    from . import bot     # tránh vòng nhập: bot.py gọi module này
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    if message.get("migrate_to_chat_id"):        # nhóm thường nâng thành siêu nhóm: Telegram đổi mã nhóm
        Group.objects.filter(chat_id=chat_id).update(chat_id=str(message["migrate_to_chat_id"]))
        return
    raw = (message.get("text") or message.get("caption") or "").strip()
    leaked = secretscan.find(raw)
    if leaked:                    # mật khẩu/khoá trong nhóm: xoá (nếu bot là quản trị), nhắc đổi khoá, không xử lý gì thêm
        external_id, _ = bot._sender(message)
        bot.secret_alert(chat_id, message, external_id, leaked, chat.get("title") or "")
        return
    called, text = addressed(message, raw)
    if raw and not text and not called and raw.startswith("/"):
        return
    external_id, _ = bot._sender(message)
    link_row = ChatLink.objects.filter(external_id=external_id, active=True, user__is_active=True).select_related("user").first()
    command = text.split()[0].casefold() if text else ""
    if command == "/nhom":
        telegram.send(chat_id, PREFIX + link_group(link_row, chat, text[len(command):]), reply_to=message.get("message_id"))
        return
    group = Group.objects.filter(chat_id=chat_id, active=True).first()
    if group is None:
        if called or command:
            telegram.send(chat_id, PREFIX + "Nhóm này chưa được gắn với Tend. Giám đốc nhắn /nhom TN-22 hoặc /nhom chung.")
        return
    if link_row is None:
        if (called or command) and _once(f"group-unlinked:{chat_id}:{external_id}:{timezone.localdate()}"):
            telegram.send(chat_id, PREFIX + f"Anh/chị chưa kết nối Tend nên em chưa làm việc được. Nhắn riêng @{_bot()} theo "
                                            "hướng dẫn ở app.tend.vn › Tài khoản › Kết nối Telegram.", reply_to=message.get("message_id"))
        return
    user = link_row.user
    sizes = message.get("photo") or ([message["document"]] if (message.get("document") or {}).get("mime_type") in bot.IMAGE_FILE_TYPES
                                     else None)
    if sizes and (group.listening or called):
        bot._save_photo(link_row, sizes, text)   # như tin riêng: gắn vào phiếu báo cáo người này lập tiếp theo
    if not text:
        return
    try:
        if command.startswith("/"):
            if command == "/hoi":
                if user.is_director:
                    answer = "Anh là Giám đốc nên tự trả lời trong nhóm được ạ."
                else:
                    from . import task_bot
                    task_bot.escalate(user, text[len(command):], chat_id, f"nhóm \"{group.title}\"", message.get("message_id"),
                                      group.project)
                    answer = "Dạ em ghi nhận, em hỏi Giám đốc và trả lời lại ở đây."
                telegram.send(chat_id, PREFIX + answer, reply_to=message.get("message_id"))
                return
            answer, proposal = bot.handle_text(link_row, text)          # lệnh: trả lời riêng (theo quyền người gõ)
            _private(bot, link_row, answer, proposal)
            telegram.send(chat_id, PREFIX + f"Em đã trả lời riêng {user}.", reply_to=message.get("message_id"))
            return
        if not called and not group.listening:
            return
        answer, proposal = bot.handle_text(link_row, text, group=group, passive=not called, message_id=message.get("message_id"))
    except (ValidationError, PermissionDenied, ValueError) as error:
        if called:
            telegram.send(chat_id, PREFIX + bot._error_text(error), reply_to=message.get("message_id"))
        return
    if proposal is not None:
        _private(bot, link_row, None, proposal)
        telegram.send(chat_id, PREFIX + f"Em đã lập phiếu, gửi riêng {user} bấm Xác nhận.", reply_to=message.get("message_id"))
    if answer:
        telegram.send(chat_id, PREFIX + answer, reply_to=message.get("message_id"))


def _private(bot, link_row, answer, proposal):
    if proposal is not None:
        bot._proposal_reply(link_row.chat_id, proposal)
    elif answer:
        telegram.send(link_row.chat_id, answer)
