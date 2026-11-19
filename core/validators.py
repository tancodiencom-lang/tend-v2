"""Kiểm tra mật khẩu, thông báo tiếng Việt (bản dịch có sẵn của Django thiếu câu "quá ngắn")."""
from django.contrib.auth.password_validation import MinimumLengthValidator
from django.core.exceptions import ValidationError


class MinimumLength(MinimumLengthValidator):
    def validate(self, password, user=None):
        if len(password or "") < self.min_length:
            raise ValidationError(f"Mật khẩu quá ngắn: cần ít nhất {self.min_length} ký tự.", code="password_too_short")

    def get_help_text(self):
        return f"Mật khẩu cần ít nhất {self.min_length} ký tự."
