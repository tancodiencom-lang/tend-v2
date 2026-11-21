"""Trợ lý nội dung số (đợt 10a): kho ảnh, ý tưởng, bài nháp, duyệt, đăng lên Trang Facebook.

Nguyên tắc (giữ từ bot marketing cũ trên VPS, 14/10/2026):
- Không bài nào tự đăng: người duyệt (Giám đốc, vai Marketing) bấm Duyệt trên ĐÚNG bản đã xem — duyệt gắn với `package_sha`
  (chữ + danh sách ảnh có thứ tự + kênh); sửa chữ/đổi ảnh/đổi kênh là bản mới, phải duyệt lại.
- Đăng có sổ từng bước (`PublishStep`) ghi TRƯỚC mỗi lần gọi Facebook; lỗi sau khi đã gửi lệnh tạo bài thì không tự đăng lại
  (trạng thái "cần đối soát"), chỉ đọc lại Trang để xác định.
- Khoá Facebook không lưu trong CSDL: `Channel.account` là tên khoá trong .env (TEND_META_TOKEN hoặc TEND_META_TOKEN_<TÊN>).
- Không xoá: bài bỏ là trạng thái, ảnh không dùng nữa là `Asset.blocked`.
"""
from django.conf import settings
from django.db import models

from projects.models import Project


class Channel(models.Model):
    """Một nơi đăng. Đợt 10a: Trang Facebook (nhiều Trang, nhiều tài khoản Meta). TikTok/YouTube/website đợt sau."""

    class Kind(models.TextChoices):
        FACEBOOK = "facebook", "Trang Facebook"

    kind = models.CharField("Loại", max_length=12, choices=Kind.choices, default=Kind.FACEBOOK)
    name = models.CharField("Tên hiển thị", max_length=100)
    page = models.CharField("Mã Trang (ID hoặc tên trong đường dẫn)", max_length=100,
                            help_text="vd tendct (từ facebook.com/tendct) hoặc mã số Trang")
    page_id = models.CharField("Mã số Trang (Facebook trả về khi kiểm tra)", max_length=40, blank=True)
    account = models.CharField("Tài khoản Meta (tên khoá trong .env)", max_length=30, blank=True,
                               help_text="Trống = TEND_META_TOKEN; CHINH = TEND_META_TOKEN_CHINH")
    brief = models.TextField("Giới thiệu Trang cho trợ lý viết bài", max_length=2000, blank=True,
                             help_text="Thương hiệu, dịch vụ, khách hàng, cách xưng hô, câu kêu gọi, hashtag cố định, điều cần tránh")
    default = models.BooleanField("Đăng mặc định", default=True,
                                  help_text="Trợ lý soạn bài hằng ngày cho Trang này; bài giao không chọn Trang thì đăng Trang này")
    active = models.BooleanField("Đang dùng", default=True)
    checked_at = models.DateTimeField("Kiểm tra kết nối lúc", null=True, blank=True)
    check_note = models.CharField("Kết quả kiểm tra", max_length=300, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Kênh đăng"
        verbose_name_plural = "Kênh đăng"
        ordering = ["-active", "name"]

    def __str__(self):
        return self.name


def asset_path(instance, filename):
    from pathlib import Path
    from uuid import uuid4
    return f"noi-dung/{uuid4().hex}{Path(filename).suffix.lower() or '.jpg'}"


class Asset(models.Model):
    """Một ảnh dùng được cho bài đăng. Ảnh báo cáo hiện trường (gồm ảnh gửi trong nhóm Telegram công trình, đã vào báo cáo)
    và ảnh thư mục Drive trỏ thẳng tới tệp gốc (không chép); ảnh gửi cho trợ lý nội dung lưu ở noi-dung/.
    Ảnh lẻ trong chat riêng và ảnh chứng từ thu chi KHÔNG bao giờ vào kho này."""

    class Source(models.TextChoices):
        REPORT = "bao_cao", "Ảnh báo cáo hiện trường"
        DRIVE = "drive", "Thư mục Google Drive"
        UPLOAD = "tai_len", "Gửi cho trợ lý nội dung"

    source = models.CharField("Nguồn", max_length=8, choices=Source.choices)
    project = models.ForeignKey(Project, verbose_name="Công trình", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="marketing_assets")
    image = models.FileField("Ảnh", upload_to=asset_path, max_length=400)
    sha256 = models.CharField(max_length=64, unique=True)
    taken_on = models.DateField("Ngày chụp/nhận", null=True, blank=True)
    context = models.CharField("Mô tả kèm (việc, tầng, chú thích)", max_length=500, blank=True)
    origin = models.CharField("Gốc (mã ảnh báo cáo / ảnh chat / đường dẫn Drive)", max_length=400, blank=True)
    blocked = models.BooleanField("Không dùng nữa", default=False)
    block_reason = models.CharField("Lý do không dùng", max_length=300, blank=True)
    # chất lượng (marketing/quality.py): đo khi vào kho; ảnh hỏng (tối đen, cháy sáng, nhoè, quá nhỏ) không được chọn vào bài
    metrics = models.JSONField("Số đo kỹ thuật", default=dict, blank=True)
    flaws = models.CharField("Lỗi kỹ thuật", max_length=120, blank=True)
    broken = models.BooleanField("Hỏng, không sửa được", default=False)
    ai_score = models.PositiveSmallIntegerField("Điểm AI gần nhất (1–5)", null=True, blank=True)
    passed_over = models.PositiveSmallIntegerField("Số lần người duyệt đổi ảnh này", default=0)
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ảnh nội dung"
        verbose_name_plural = "Kho ảnh nội dung"
        ordering = ["-taken_on", "-pk"]


class Idea(models.Model):
    """Ý tưởng hoặc yêu cầu bài bất chợt (Giám đốc, Marketing). Yêu cầu thì trợ lý soạn bài ngay; ý tưởng dùng cho bài hằng ngày."""

    class Kind(models.TextChoices):
        IDEA = "y_tuong", "Ý tưởng"
        REQUEST = "yeu_cau", "Giao bài ngay"

    class State(models.TextChoices):
        OPEN = "mo", "Chưa dùng"
        USED = "da_dung", "Đã thành bài"
        DROPPED = "bo", "Bỏ"

    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.IDEA)
    text = models.TextField("Nội dung", max_length=2000)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    state = models.CharField(max_length=8, choices=State.choices, default=State.OPEN)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ý tưởng nội dung"
        verbose_name_plural = "Ý tưởng nội dung"
        ordering = ["-created_at"]


class Post(models.Model):
    class State(models.TextChoices):
        DRAFT = "nhap", "Chờ duyệt"
        APPROVED = "da_duyet", "Đã duyệt, chờ đăng"
        PUBLISHING = "dang_dang", "Đang đăng"
        PUBLISHED = "da_dang", "Đã đăng"
        FAILED = "loi", "Lỗi trước khi tạo bài (thử lại được)"
        UNKNOWN = "doi_soat", "Cần đối soát (đã gửi lệnh tạo bài, chưa rõ kết quả)"
        REJECTED = "bo", "Đã bỏ"

    class Origin(models.TextChoices):
        DAILY = "hang_ngay", "Trợ lý soạn hằng ngày"
        REQUEST = "yeu_cau", "Giao bài"
        MANUAL = "tu_viet", "Tự viết"

    state = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    origin = models.CharField(max_length=10, choices=Origin.choices, default=Origin.DAILY)
    text = models.TextField("Nội dung bài", max_length=5000)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    idea = models.ForeignKey(Idea, on_delete=models.PROTECT, null=True, blank=True, related_name="posts")
    for_date = models.DateField("Bài của ngày")
    note = models.CharField("Ghi chú của trợ lý (vì sao chọn ảnh/chủ đề)", max_length=1000, blank=True)
    ai_written = models.BooleanField("AI viết", default=False)
    channels = models.ManyToManyField(Channel, related_name="posts", blank=True)
    package_sha = models.CharField("Dấu của bản hiện tại", max_length=64, blank=True)
    approved_sha = models.CharField("Dấu của bản đã duyệt", max_length=64, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    approved_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.CharField("Lý do bỏ", max_length=500, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+",
                                   help_text="Trống = trợ lý tự soạn")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bài đăng"
        verbose_name_plural = "Bài đăng"
        ordering = ["-for_date", "-pk"]

    def __str__(self):
        return f"Bài #{self.pk}"


class PostAsset(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name="items")
    asset = models.ForeignKey(Asset, on_delete=models.PROTECT, related_name="uses")
    order = models.PositiveSmallIntegerField()
    original = models.BooleanField("Dùng ảnh gốc (không tự chỉnh)", default=False)

    class Meta:
        ordering = ["post", "order"]
        constraints = [models.UniqueConstraint(fields=["post", "order"], name="post_asset_order_once")]


class PostTarget(models.Model):
    """Bài trên một kênh: kết quả đăng (mã bài, đường dẫn) hoặc lỗi."""

    class State(models.TextChoices):
        PENDING = "cho", "Chờ đăng"
        PUBLISHED = "da_dang", "Đã đăng"
        FAILED = "loi", "Lỗi trước khi tạo bài"
        UNKNOWN = "doi_soat", "Cần đối soát"

    post = models.ForeignKey(Post, on_delete=models.PROTECT, related_name="targets")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="targets")
    package_sha = models.CharField(max_length=64)
    state = models.CharField(max_length=10, choices=State.choices, default=State.PENDING)
    attempt = models.PositiveSmallIntegerField(default=1)
    remote_id = models.CharField("Mã bài trên Facebook", max_length=80, blank=True)
    permalink = models.URLField(max_length=500, blank=True)
    error = models.CharField(max_length=500, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["post", "channel"]
        constraints = [models.UniqueConstraint(fields=["post", "channel", "package_sha", "attempt"], name="post_target_once")]


class PublishStep(models.Model):
    """Sổ đăng: ghi Ý ĐỊNH trước mỗi lần gọi Facebook, ghi XONG sau khi có kết quả. Không sửa, không xoá."""

    class Step(models.TextChoices):
        UPLOAD_INTENT = "upload_intent", "Sắp tải ảnh"
        UPLOAD_DONE = "upload_done", "Đã tải ảnh (chưa đăng)"
        FEED_INTENT = "feed_intent", "Sắp tạo bài"
        FEED_DONE = "feed_done", "Đã tạo bài"
        VERIFY = "verify", "Đọc lại bài"
        RECONCILE = "reconcile", "Đối soát"
        ERROR = "error", "Lỗi"

    target = models.ForeignKey(PostTarget, on_delete=models.PROTECT, related_name="steps")
    step = models.CharField(max_length=16, choices=Step.choices)
    ordinal = models.PositiveSmallIntegerField(null=True, blank=True)
    remote_id = models.CharField(max_length=80, blank=True)
    detail = models.CharField(max_length=500, blank=True)
    at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["pk"]


class StyleSample(models.Model):
    """Bài mẫu giọng văn: bài cũ của Trang (đọc qua Facebook) hoặc bài người duyệt dán vào."""

    class Source(models.TextChoices):
        PAGE = "trang", "Bài cũ của Trang"
        MANUAL = "dan_vao", "Dán vào"
        PUBLISHED = "da_dang", "Bài đã duyệt đăng"

    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, blank=True, related_name="samples")
    source = models.CharField(max_length=8, choices=Source.choices)
    text = models.TextField(max_length=5000)
    digest = models.CharField(max_length=64, unique=True)
    remote_id = models.CharField(max_length=80, blank=True)
    active = models.BooleanField("Dùng làm mẫu", default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Bài mẫu giọng văn"
        verbose_name_plural = "Bài mẫu giọng văn"
        ordering = ["-created_at"]


class WriteJob(models.Model):
    """Việc viết bài CHẠY NỀN (soạn bài được giao, sửa bài theo ý): mô hình viết bài dài mất 1–3 phút, quá giờ chờ của web
    và Telegram. Bot/web ghi việc rồi trả lời ngay; tiến trình nền (manage.py viet_bai) làm và gửi bản duyệt."""

    class Kind(models.TextChoices):
        DRAFT = "soan", "Soạn bài"
        REVISE = "sua", "Sửa theo ý"

    class State(models.TextChoices):
        WAITING = "cho", "Chờ viết"
        RUNNING = "dang", "Đang viết"
        DONE = "xong", "Xong"
        FAILED = "loi", "Không viết được"

    kind = models.CharField(max_length=4, choices=Kind.choices)
    state = models.CharField(max_length=4, choices=State.choices, default=State.WAITING)
    idea = models.ForeignKey(Idea, on_delete=models.PROTECT, null=True, blank=True, related_name="jobs")
    post = models.ForeignKey(Post, on_delete=models.PROTECT, null=True, blank=True, related_name="jobs")
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    project = models.ForeignKey(Project, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    instruction = models.TextField("Yêu cầu", max_length=4000, blank=True)
    photos = models.JSONField("Ảnh gửi kèm (mã ảnh kho, theo thứ tự)", default=list, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    via = models.CharField(max_length=10, default="telegram")
    error = models.CharField(max_length=500, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Việc viết bài"
        verbose_name_plural = "Việc viết bài"
        ordering = ["-pk"]

