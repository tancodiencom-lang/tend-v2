"""Mức đầy đủ của một báo cáo (thang 100, chủ dự án chốt 20/11/2026) — tính lúc đọc từ dữ kiện đã lưu, không lưu điểm.

    Mã chi tiết        25 × (số việc có mã / tổng số việc)         việc = dòng có mã + việc ngoài bản vẽ
    % hoặc khối lượng  25 × (số việc có % hoặc KL / tổng số việc)
    Ảnh                20 nếu có ít nhất một ảnh
    Tên thợ            20 nếu chấm công có ít nhất một người có tên
    Không phải hỏi lại 10 − 5 × số lần bot hỏi lại (thấp nhất 0)

Báo cáo không có việc nào (chỉ chấm công/ảnh) không chấm điểm. Trung bình tuần ≥ 80: ▲ tốt; 60–79: ● được; < 60: ▼ cần
hướng dẫn.
"""
from dataclasses import dataclass, field as dc_field
from decimal import ROUND_HALF_UP, Decimal

GOOD, FAIR = 80, 60


@dataclass
class Score:
    total: int
    coded: int
    extra: int
    valued: int
    photos: int
    named: int
    asked: int
    missing: list = dc_field(default_factory=list)

    @property
    def jobs(self):
        return self.coded + self.extra

    @property
    def label(self):
        return label(self.total)

    def text(self):
        out = f"{self.total}/100 {self.label}"
        return out + (": " + "; ".join(self.missing) if self.missing else "")


def label(total):
    if total is None:
        return "chưa có báo cáo chấm được"
    return "▲ tốt" if total >= GOOD else "● được" if total >= FAIR else "▼ cần hướng dẫn"


def compute(coded, extra, valued, photos, named, asked):
    jobs = coded + extra
    if jobs == 0:
        return None
    parts = [Decimal(25) * coded / jobs, Decimal(25) * valued / jobs, Decimal(20 if photos else 0), Decimal(20 if named else 0),
             Decimal(max(0, 10 - 5 * asked))]
    missing = []
    if coded < jobs:
        missing.append(f"thiếu mã chi tiết ({coded}/{jobs} việc có mã)")
    if valued < jobs:
        missing.append(f"thiếu % hoặc khối lượng ({valued}/{jobs} việc có)")
    if not photos:
        missing.append("không có ảnh")
    if not named:
        missing.append("không có tên thợ")
    if asked:
        missing.append(f"bot phải hỏi lại {asked} lần")
    return Score(total=int(sum(parts).quantize(Decimal(1), ROUND_HALF_UP)), coded=coded, extra=extra, valued=valued, photos=photos, named=named,
                 asked=asked, missing=missing)


def of(report):
    """Điểm của một báo cáo đã ghi (None nếu không có việc nào). Báo cáo trước đợt 8a: số người có tên lấy từ chấm công."""
    lines = list(report.lines.all())
    extra = list(report.extra_work.all())
    named = report.named
    if named is None:
        named = report.attendance.filter(worker__isnull=False).count()
    return compute(len(lines), len(extra), len(lines) + sum(1 for e in extra if e.percent is not None), len(report.photos.all()),
                   named, report.asked)
