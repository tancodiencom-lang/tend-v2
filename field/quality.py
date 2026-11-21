"""Mức đầy đủ của một báo cáo (thang 100, chủ dự án chốt 20/11/2026) — tính lúc đọc từ dữ kiện đã lưu, không lưu điểm.

    Mã chi tiết        25 × (số việc có mã / tổng số việc)         việc = dòng có mã + việc ngoài bản vẽ
    % hoặc khối lượng  25 × (số việc có % hoặc KL / tổng số việc)
    Ảnh                20 nếu có ít nhất một ảnh
    Nhân sự đủ         20 nếu có người có tên (thợ công nhật) hoặc tổ + số người (tổ khoán, tổ của chủ nhà) và không còn
                       số người chưa rõ tổ; 10 nếu chỉ ghi số người mà không rõ tổ nào ("3 thợ 2 phụ"); 0 nếu không ghi gì
                       (chủ dự án chốt 20/11/2026: tổ khoán không cần tên, thợ công nhật phải có tên)
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
    teams: int = 0          # số tổ có ghi số người (rõ tổ)
    unknown: int = 0        # số nhóm người chưa rõ tổ
    missing: list = dc_field(default_factory=list)

    @property
    def jobs(self):
        return self.coded + self.extra

    @property
    def staff(self):
        """Điểm nhân sự: 20 đủ, 10 thiếu một phần, 0 không ghi."""
        if (self.named or self.teams) and not self.unknown:
            return 20
        return 10 if (self.named or self.teams or self.unknown) else 0

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


def compute(coded, extra, valued, photos, named, asked, teams=0, unknown=0):
    jobs = coded + extra
    if jobs == 0:
        return None
    score = Score(total=0, coded=coded, extra=extra, valued=valued, photos=photos, named=named, asked=asked, teams=teams,
                  unknown=unknown)
    parts = [Decimal(25) * coded / jobs, Decimal(25) * valued / jobs, Decimal(20 if photos else 0), Decimal(score.staff),
             Decimal(max(0, 10 - 5 * asked))]
    missing = []
    if coded < jobs:
        missing.append(f"thiếu mã chi tiết ({coded}/{jobs} việc có mã)")
    if valued < jobs:
        missing.append(f"thiếu % hoặc khối lượng ({valued}/{jobs} việc có)")
    if not photos:
        missing.append("không có ảnh")
    if score.staff == 0:
        missing.append("không ghi nhân sự (tên thợ công nhật hoặc tổ + số người)")
    elif unknown:
        missing.append("có số người chưa rõ tổ nào")
    if asked:
        missing.append(f"em phải hỏi lại {asked} lần")
    score.total, score.missing = int(sum(parts).quantize(Decimal(1), ROUND_HALF_UP)), missing
    return score


def of(report):
    """Điểm của một báo cáo đã ghi (None nếu không có việc nào). Báo cáo trước đợt 8a: số người có tên lấy từ chấm công."""
    lines = list(report.lines.all())
    extra = list(report.extra_work.all())
    named = report.named
    if named is None:
        named = report.attendance.filter(worker__isnull=False).count()
    crews = list(report.crews.all())
    return compute(len(lines), len(extra), len(lines) + sum(1 for e in extra if e.percent is not None), len(report.photos.all()),
                   named, report.asked, teams=sum(1 for c in crews if c.team_id), unknown=sum(1 for c in crews if not c.team_id))
