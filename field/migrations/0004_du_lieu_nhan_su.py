"""Dữ liệu ban đầu: danh mục nghề; cấp mã NS-0001… cho thợ có sẵn (theo thứ tự đã thêm); đơn giá cũ thành lịch sử
áp dụng từ trước (công cũ giữ nguyên giá); hồ sơ nhân sự cho giám sát (nghề Giám sát, lương tháng, liên kết tài khoản)."""
from datetime import date

from django.db import migrations

TRADES = ["Giám sát", "Thợ xây", "Thợ ốp lát", "Thợ sơn bả", "Thợ điện nước", "Thợ mộc", "Thợ thạch cao", "Lái xe"]
OLD_DAY = date(2000, 1, 1)


def forward(apps, schema_editor):
    Trade = apps.get_model("field", "Trade")
    Worker = apps.get_model("field", "Worker")
    WorkerRate = apps.get_model("field", "WorkerRate")
    User = apps.get_model("core", "User")
    Attendance = apps.get_model("field", "Attendance")
    trades = {}
    for order, name in enumerate(TRADES):
        trades[name], _ = Trade.objects.get_or_create(name=name, defaults={"order": order})
    number = 0
    for worker in Worker.objects.order_by("pk"):
        number += 1
        worker.code = f"NS-{number:04d}"
        worker.save(update_fields=["code"])
        if worker.day_rate is not None:
            WorkerRate.objects.get_or_create(worker=worker, start=OLD_DAY, defaults={"pay": worker.pay, "amount": worker.day_rate})
    staff_ids = set(Attendance.objects.filter(staff__isnull=False).values_list("staff_id", flat=True))
    for user in User.objects.filter(role="supervisor") | User.objects.filter(pk__in=staff_ids):
        if Worker.objects.filter(user=user).exists():
            continue
        number += 1
        name = " ".join(filter(None, [user.last_name, user.first_name])) or user.username
        Worker.objects.create(code=f"NS-{number:04d}", name=name[:100], trade=trades["Giám sát"], kind="tho", pay="luong_thang",
                              user=user, active=user.is_active)


class Migration(migrations.Migration):
    dependencies = [("field", "0003_nhan_su_ma_don_gia"), ("core", "0004_phai_doi_mat_khau")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
