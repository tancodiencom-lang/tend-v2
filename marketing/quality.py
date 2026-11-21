"""Lớp 1 — đo kỹ thuật ảnh (không tốn phí AI): độ sáng, tương phản, độ nét, lệch màu, kích thước.

Đo trên bản thu nhỏ 512 px (xám) nên nhanh và ổn định. Hai loại lỗi:
- HỎNG (không sửa được: quá tối, cháy sáng, nhoè, quá nhỏ) → ảnh không bao giờ được trợ lý chọn vào bài;
- SỬA ĐƯỢC (hơi tối, chói, bệt, lệch màu) → ảnh vẫn dùng được, lớp tự chỉnh (`enhance`) cân lại trước khi duyệt.
Ngưỡng đặt bảo thủ: chỉ loại ảnh lỗi rõ; còn lại để AI chấm điểm (lớp 2) và người duyệt quyết.
"""
from PIL import Image, ImageChops, ImageFilter, ImageStat

ANALYSIS, SHARP_SIZE = 512, 1024
VERSION = 1
# ngưỡng (giá trị trên thang 0–255, ảnh xám 512 px)
TOO_DARK, DARK = 30, 85              # độ sáng trung bình
TOO_BRIGHT, BRIGHT = 235, 205
FLAT = 32                            # độ lệch chuẩn độ sáng: dưới mức này ảnh bệt, mờ sương
BLURRY = 0.62                        # độ nhoè 0–1 (làm mờ thêm mà ảnh gần như không đổi = ảnh đã nhoè sẵn): trên mức này là nhoè
CAST = 0.07                          # lệch màu: tỉ lệ một kênh màu lệch khỏi 1/3
MIN_SHORT, MIN_LONG = 480, 720       # cạnh ngắn / dài tối thiểu (px)

HARD = ("quá tối", "cháy sáng", "nhoè", "quá nhỏ")
LABELS = {"quá tối": "▼ quá tối", "cháy sáng": "▼ cháy sáng", "nhoè": "▼ nhoè", "quá nhỏ": "▼ quá nhỏ",
          "hơi tối": "● hơi tối", "chói": "● chói", "bệt": "● bệt, thiếu tương phản", "lệch màu": "● lệch màu"}


def analysis_image(picture):
    small = picture.convert("RGB")
    small.thumbnail((ANALYSIS, ANALYSIS))
    return small


def blur(picture):
    """Độ nhoè 0–1 theo cách "làm mờ lại" (Crete 2007): so chênh lệch giữa các điểm ảnh kề nhau trước và sau khi làm mờ
    thêm; ảnh nét mất nhiều chi tiết (gần 0), ảnh đã nhoè gần như không đổi (gần 1). Không phụ thuộc nhiều vào nội dung
    (tường phẳng vẫn nét nếu mép, góc rõ)."""
    gray = picture.convert("L")
    gray.thumbnail((SHARP_SIZE, SHARP_SIZE))
    soft = gray.filter(ImageFilter.BoxBlur(4))
    worst = 0.0
    for dx, dy in ((1, 0), (0, 1)):
        sharp_diff = ImageChops.difference(gray, ImageChops.offset(gray, dx, dy))
        soft_diff = ImageChops.difference(soft, ImageChops.offset(soft, dx, dy))
        total = ImageStat.Stat(sharp_diff).sum[0]
        if total <= 0:
            return 1.0
        kept = ImageStat.Stat(ImageChops.subtract(sharp_diff, soft_diff)).sum[0]
        worst = max(worst, (total - kept) / total)
    return round(worst, 3)


def measure(picture):
    """→ số đo (dict, lưu ở Asset.metrics). `picture`: ảnh PIL đã xoay đúng chiều."""
    width, height = picture.size
    small = analysis_image(picture)
    gray = small.convert("L")
    stat = ImageStat.Stat(gray)
    red, green, blue = ImageStat.Stat(small).mean
    total = (red + green + blue) or 1
    cast = max(abs(c / total - 1 / 3) for c in (red, green, blue))
    return {"v": VERSION, "w": width, "h": height, "brightness": round(stat.mean[0], 1), "contrast": round(stat.stddev[0], 1),
            "blur": blur(picture), "cast": round(cast, 3),
            "rgb": [round(red, 1), round(green, 1), round(blue, 1)]}


def flaws(metrics):
    """Danh sách lỗi theo số đo (lỗi hỏng đứng trước)."""
    if not metrics:
        return []
    found = []
    brightness, contrast = metrics.get("brightness", 128), metrics.get("contrast", 60)
    if brightness < TOO_DARK:
        found.append("quá tối")
    elif brightness > TOO_BRIGHT:
        found.append("cháy sáng")
    if metrics.get("blur", 0) > BLURRY:
        found.append("nhoè")
    if min(metrics.get("w", 9999), metrics.get("h", 9999)) < MIN_SHORT or max(metrics.get("w", 0), metrics.get("h", 0)) < MIN_LONG:
        found.append("quá nhỏ")
    if TOO_DARK <= brightness < DARK:
        found.append("hơi tối")
    elif BRIGHT < brightness <= TOO_BRIGHT:
        found.append("chói")
    if contrast < FLAT:
        found.append("bệt")
    if metrics.get("cast", 0) > CAST:
        found.append("lệch màu")
    return sorted(found, key=lambda name: name not in HARD)


def broken(metrics):
    """Lỗi không sửa được (ảnh không được chọn vào bài)."""
    return [name for name in flaws(metrics) if name in HARD]


def labels(text):
    """Chuỗi lỗi lưu ở Asset.flaws ("nhoè, lệch màu") → nhãn có ký hiệu để hiển thị."""
    return [LABELS.get(name.strip(), name.strip()) for name in (text or "").split(",") if name.strip()]


def open_image(data):
    from io import BytesIO

    from PIL import ImageOps
    return ImageOps.exif_transpose(Image.open(BytesIO(data))).convert("RGB")
