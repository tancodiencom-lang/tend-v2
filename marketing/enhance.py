"""Lớp 3 — tự chỉnh NHẸ ảnh trước khi duyệt/đăng: cân màu (bớt ám vàng/xanh), giãn sáng tối, nâng ảnh tối, tăng nét nhẹ.

Chỉ chỉnh toàn ảnh như nút "Tự động" của điện thoại; KHÔNG thêm, xoá hay vẽ lại gì (chủ dự án 21/11/2026: ảnh đăng luôn là
ảnh thật của công trình). Ảnh gốc giữ nguyên; bản chỉnh tính lại mỗi lần từ ảnh gốc theo cùng tham số (đo trên bản 512 px)
nên bản xem trước, bản AI chấm và bản đăng giống nhau. Đổi cách chỉnh → tăng VERSION (dấu bản bài đổi → phải duyệt lại).
"""
import math

from PIL import ImageEnhance, ImageFilter, ImageStat

from .quality import analysis_image

VERSION = 1
MAX_GAIN, MIN_GAIN = 1.2, 0.85       # cân màu tối đa ±15–20% mỗi kênh
STRENGTH = 0.75                      # chỉnh tối đa 3/4 độ lệch (ảnh gỗ, tường vàng không bị xám hẳn)
CAST_START, CAST_FULL = 0.035, 0.075 # lệch màu dưới mức này là màu thật (tường kem, gỗ): KHÔNG cân; từ đây tăng dần tới đủ mức
MAX_STRETCH = 1.35                   # tăng tương phản tối đa 35%, giãn quanh độ sáng trung bình (không đẩy ảnh sáng lên)
TARGET = 118                         # ảnh tối nâng độ sáng trung bình lên mức này; ảnh đủ sáng giữ độ sáng cũ
BRIGHT_KEEP = 200                    # ảnh chói hạ nhẹ về mức này


def params(picture):
    """Tham số chỉnh tính trên bản 512 px của ảnh (đã xoay đúng chiều)."""
    small = analysis_image(picture)
    means = ImageStat.Stat(small).mean
    gray = sum(means) / 3 or 1
    cast = max(abs(m / (3 * gray) - 1 / 3) for m in means)
    strength = STRENGTH * min(1.0, max(0.0, (cast - CAST_START) / (CAST_FULL - CAST_START)))
    gains = [min(MAX_GAIN, max(MIN_GAIN, (gray / (m or 1)) ** strength)) for m in means]
    luma = small.point([min(255, int(v * gains[band])) for band in range(3) for v in range(256)]).convert("L")
    histogram = luma.histogram()
    total = sum(histogram) or 1
    low = high = None
    running = 0
    for value, count in enumerate(histogram):
        running += count
        if low is None and running >= total * 0.005:
            low = value
        if running >= total * 0.995:
            high = value
            break
    mean = ImageStat.Stat(luma).mean[0]
    stretch = max(1.0, min(MAX_STRETCH, 255 / max(1, (high or 255) - (low or 0))))
    gamma = 1.0
    if 1 < mean < TARGET - 15:
        gamma = max(0.6, math.log(TARGET / 255) / math.log(mean / 255))
    elif mean > BRIGHT_KEEP + 5:
        gamma = min(1.4, math.log(BRIGHT_KEEP / 255) / math.log(min(mean, 254) / 255))
    centers = [round(min(255.0, m * g), 2) for m, g in zip(means, gains)]      # giãn quanh trung bình TỪNG kênh: không làm ám thêm
    return {"v": VERSION, "gains": [round(g, 4) for g in gains], "centers": centers, "stretch": round(stretch, 4),
            "gamma": round(gamma, 4)}


def apply(picture, settings):
    lut = []
    stretch, gamma = settings["stretch"], settings["gamma"]
    for band in range(3):
        gain, center = settings["gains"][band], settings["centers"][band]
        for value in range(256):
            level = center + (min(255.0, value * gain) - center) * stretch
            level = min(1.0, max(0.0, level / 255))
            lut.append(int(round(255 * level ** gamma)))
    result = picture.convert("RGB").point(lut)
    result = ImageEnhance.Color(result).enhance(1.04)
    radius = max(0.8, max(result.size) / 1400)
    return result.filter(ImageFilter.UnsharpMask(radius=radius, percent=45, threshold=3))


def enhance(picture, analysed=None):
    """Ảnh đã chỉnh. `analysed`: ảnh dùng để tính tham số (mặc định chính ảnh này) — truyền ảnh gốc khi `picture` đã thu nhỏ."""
    return apply(picture, params(analysed if analysed is not None else picture))
