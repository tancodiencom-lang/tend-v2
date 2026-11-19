"""Quản lý tài khoản: chỉ Giám đốc; thêm/sửa/giao công trình/khoá/mật khẩu tạm có nhật ký; bắt đổi mật khẩu tạm."""
from django.test import TestCase
from django.urls import reverse

from projects.models import Member, Project

from .models import AuditLog, Role, User

PASSWORD = "mat-khau-tam-123"


class AccountTests(TestCase):
    def setUp(self):
        self.director = User.objects.create_user("quan", password="giam-doc-12345", role=Role.DIRECTOR)
        self.site = User.objects.create_user("gs", password="giam-sat-12345", role=Role.SUPERVISOR, phone="0911111111")
        self.tn = Project.objects.create(code="TN-22", name="Thiên Nga")
        self.bh = Project.objects.create(code="BH-01", name="Biệt thự")

    def test_only_director(self):
        self.client.force_login(self.site)
        for name, args in (("staff", []), ("staff_new", []), ("staff_edit", [self.director.pk])):
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 403)
        self.client.post(reverse("staff_new"), {"username": "hack", "role": "director", "password": PASSWORD})
        self.assertFalse(User.objects.filter(username="hack").exists())

    def test_create_assign_and_forced_password_change(self):
        self.client.force_login(self.director)
        page = self.client.post(reverse("staff_new"), {"username": "Nam.KT", "first_name": "Nam", "last_name": "Lê", "role": "internal",
                                                       "phone": "+84 912 222 333", "password": "123", "cong_trinh": [self.tn.pk]}, follow=True)
        self.assertContains(page, "quá ngắn")                                  # mật khẩu yếu: không tạo
        self.assertFalse(User.objects.filter(username="nam.kt").exists())
        self.client.post(reverse("staff_new"), {"username": "Nam.KT", "first_name": "Nam", "last_name": "Lê", "role": "internal",
                                                "phone": "+84 912 222 333", "password": PASSWORD, "cong_trinh": [self.tn.pk]})
        nam = User.objects.get(username="nam.kt")
        self.assertEqual((nam.role, nam.phone, nam.must_change_password), ("internal", "0912222333", True))
        self.assertEqual(list(Member.objects.filter(user=nam, active=True).values_list("project__code", flat=True)), ["TN-22"])
        log = AuditLog.objects.get(table="core.User", object_id=str(nam.pk), action="create")
        self.assertNotIn("password", log.after)                               # không ghi mật khẩu / bản băm
        self.assertEqual(log.actor, self.director)
        dup = self.client.post(reverse("staff_new"), {"username": "khac", "role": "supervisor", "phone": "0912222333",
                                                      "password": PASSWORD}, follow=True)
        self.assertContains(dup, "đã ghi cho tài khoản khác")
        self.client.logout()
        self.assertTrue(self.client.login(username="nam.kt", password=PASSWORD))
        self.assertRedirects(self.client.get(reverse("overview")), reverse("password"))   # mật khẩu tạm: phải đổi trước
        self.client.post(reverse("password"), {"old_password": PASSWORD, "new_password1": "mat-khau-rieng-456",
                                               "new_password2": "mat-khau-rieng-456"})
        nam.refresh_from_db()
        self.assertFalse(nam.must_change_password)
        self.assertEqual(self.client.get(reverse("overview")).status_code, 200)

    def test_edit_role_projects_lock_and_reset(self):
        self.client.force_login(self.director)
        url = reverse("staff_edit", args=[self.site.pk])
        self.client.post(url, {"action": "save", "first_name": "Minh", "last_name": "", "role": "designer", "phone": "0911111111",
                               "cong_trinh": [self.tn.pk, self.bh.pk]})
        self.site.refresh_from_db()
        self.assertEqual((self.site.role, self.site.first_name), ("designer", "Minh"))
        self.client.post(url, {"action": "save", "first_name": "Minh", "role": "designer", "phone": "0911111111", "cong_trinh": [self.bh.pk]})
        self.assertEqual(set(Member.objects.filter(user=self.site, active=True).values_list("project__code", flat=True)), {"BH-01"})
        self.assertTrue(Member.objects.filter(user=self.site, project=self.tn, active=False).exists())   # thôi giao, không xoá
        page = self.client.post(url, {"action": "lock", "reason": ""}, follow=True)
        self.assertContains(page, "cần ghi lý do")
        self.client.post(url, {"action": "lock", "reason": "nghỉ việc"})
        self.site.refresh_from_db()
        self.assertFalse(self.site.is_active)
        self.client.post(url, {"action": "unlock"})
        self.client.post(url, {"action": "password", "password": PASSWORD})
        self.site.refresh_from_db()
        self.assertTrue(self.site.is_active and self.site.must_change_password and self.site.check_password(PASSWORD))
        reasons = set(AuditLog.objects.filter(table="core.User", object_id=str(self.site.pk)).values_list("reason", flat=True))
        self.assertTrue({"Khoá tài khoản: nghỉ việc", "Mở khoá tài khoản", "Giám đốc đặt mật khẩu tạm"} <= reasons)

    def test_director_cannot_lock_or_demote_self(self):
        self.client.force_login(self.director)
        url = reverse("staff_edit", args=[self.director.pk])
        page = self.client.post(url, {"action": "save", "role": "supervisor", "phone": ""}, follow=True)
        self.assertContains(page, "không tự đổi vai")
        self.client.post(url, {"action": "lock", "reason": "thử"})
        self.director.refresh_from_db()
        self.assertEqual((self.director.role, self.director.is_active), ("director", True))

    def test_locked_user_cannot_log_in(self):
        self.client.force_login(self.director)
        self.client.post(reverse("staff_edit", args=[self.site.pk]), {"action": "lock", "reason": "nghỉ việc"})
        self.client.logout()
        self.assertFalse(self.client.login(username="gs", password="giam-sat-12345"))
