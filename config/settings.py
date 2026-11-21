"""Cấu hình Tend mới. Mọi bí mật đọc từ .env, không ghi trong mã."""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(DEBUG=(bool, False), ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
                  CSRF_TRUSTED_ORIGINS=(list, []), BEHIND_PROXY=(bool, False))
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env("SECRET_KEY")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env("CSRF_TRUSTED_ORIGINS")
BEHIND_PROXY = env("BEHIND_PROXY")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "core",
    "projects",
    "drawings",
    "field",
    "money",
    "asks",
    "overview",
    "chat",
    "assistant",
    "marketing",
    "itdesk",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.staff_views.ForcePasswordChange",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.audit.RequestMiddleware",
]

ROOT_URLCONF = "config.urls"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "core.views.shell",
    ]},
}]
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {"default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")}
DATABASES["default"]["ATOMIC_REQUESTS"] = True
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
AUTH_USER_MODEL = "core.User"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "core.validators.MinimumLength", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "overview"
LOGOUT_REDIRECT_URL = "login"

LANGUAGE_CODE = "vi"
TIME_ZONE = "Asia/Ho_Chi_Minh"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "core.storage.StaticFiles"},
}
STATIC_ROOT = Path(env("STATIC_ROOT", default=str(BASE_DIR / "staticfiles")))
MEDIA_URL = "/tep/"
MEDIA_ROOT = Path(env("MEDIA_ROOT", default=str(BASE_DIR / "media")))
# Kho tài liệu gốc xếp theo ngày (chat/archive.py), đồng bộ lên Google Drive bằng ha-tang/dong-bo-goc.sh
TEND_ORIGINALS_DIR = Path(env("TEND_ORIGINALS_DIR", default="") or MEDIA_ROOT.parent / "tai-lieu-goc")

SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = False
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
# HSTS và chuyển http → https do Caddy làm (ha-tang/app.tend.vn.caddy), không lặp ở Django.
SILENCED_SYSTEM_CHECKS = ["security.W004", "security.W008"]
if not DEBUG:
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https") if BEHIND_PROXY else None

# Ngày làm việc của trợ lý (thứ Hai = 1 … Chủ nhật = 7): ngoài các ngày này trợ lý không nhắn nhân viên
TEND_WORK_DAYS = env("TEND_WORK_DAYS", default="1,2,3,4,5,6") or "1,2,3,4,5,6"
APP_URL = env("APP_URL", default="https://app.tend.vn").rstrip("/")
TELEGRAM_TOKEN = env("TELEGRAM_TOKEN", default="")
TELEGRAM_WEBHOOK_SECRET = env("TELEGRAM_WEBHOOK_SECRET", default="")
TELEGRAM_BOT_USERNAME = env("TELEGRAM_BOT_USERNAME", default="")
TEND_AI_MODEL = env("TEND_AI_MODEL", default="claude-haiku-5-5")
TEND_AI_MONTHLY_USD = env.float("TEND_AI_MONTHLY_USD", default=4.0)
# Trợ lý nội dung (bài đăng Facebook): mô hình và trần riêng (chủ dự án chốt 21/11/2026: Sonnet 5.5, 30 USD/tháng)
TEND_MKT_AI_MODEL = env("TEND_MKT_AI_MODEL", default="claude-sonnet-5-5")
TEND_MKT_AI_MONTHLY_USD = env.float("TEND_MKT_AI_MONTHLY_USD", default=30.0)
# Trợ lý nội dung số (đợt 10a): số bài nháp mỗi ngày; thư mục ảnh Drive đã kéo về máy chủ (ha-tang/keo-anh-noi-dung.sh).
# Khoá Facebook: TEND_META_TOKEN (và TEND_META_TOKEN_<TÊN> cho tài khoản khác) — đọc thẳng từ môi trường, không qua settings.
TEND_MKT_DAILY = env.int("TEND_MKT_DAILY", default=1)
TEND_MKT_DRIVE_DIR = env("TEND_MKT_DRIVE_DIR", default="")

LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"console": {"class": "logging.StreamHandler"}},
           "root": {"handlers": ["console"], "level": "WARNING"}}
