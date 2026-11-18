"""Bộ quy chuẩn bản vẽ Tend — một nguồn duy nhất.

Từ đây sinh ra: bản mẫu AutoCAD (layer, block, bảng ký hiệu), bảng nút và lệnh Kiểm tra / Xuất cho Tend
(AutoLISP + DCL), và bộ kiểm khi nhập DXF trên máy chủ. Sửa quy chuẩn chỉ sửa tệp này; bài kiểm bắt bốn phần khớp nhau.

Đơn vị vẽ: mm. Mỗi mặt bằng nằm trong một khung (CH-KHUNG) có tiêu đề tầng (CH-TEXT, vd "MẶT BẰNG TẦNG 2").
"""
from dataclasses import dataclass, field

# Loại layer — quyết định đối tượng nào được nhận và cách tính khối lượng.
WALL = "wall"        # tường: tim tường (đường hở) hoặc đường bao kín; dày theo layer
AREA = "area"        # vùng kín: sàn, trần, ốp mặt đứng, bê tông
DOOR = "door"        # khung cửa kín, chữ mã + kích thước bên trong
NOTE = "note"        # chữ ghi chú: H=…, ỐP …
ROOM_TEXT = "room_text"
FRAME = "frame"      # khung tầng
TITLE = "title"      # tiêu đề tầng
LEGEND = "legend"    # bảng ký hiệu: không tính
DEVICE = "device"    # thiết bị là block TCD-…
RUN = "run"          # dây, ống: đường liền theo tuyến
BACKGROUND = "background"  # nền bản đồ: chỉ để xem

# Loại đối tượng DXF được nhận theo loại layer (đối tượng khác bị lọc khi xuất/nhập).
ALLOWED_TYPES = {
    WALL: {"LINE", "LWPOLYLINE", "TEXT", "MTEXT"},
    AREA: {"LWPOLYLINE", "TEXT", "MTEXT"},
    DOOR: {"LWPOLYLINE", "TEXT", "MTEXT"},
    NOTE: {"TEXT", "MTEXT"},
    ROOM_TEXT: {"TEXT", "MTEXT"},
    FRAME: {"LWPOLYLINE", "INSERT"},
    TITLE: {"TEXT", "MTEXT"},
    LEGEND: None,       # mọi loại
    DEVICE: {"INSERT"},
    RUN: {"LINE", "LWPOLYLINE", "TEXT", "MTEXT"},
    BACKGROUND: None,
}
CLOSED_KINDS = {AREA, DOOR, FRAME}


@dataclass(frozen=True)
class Layer:
    name: str
    title: str            # tên tiếng Việt hiện trên nút / báo cáo
    color: int            # màu AutoCAD (ACI)
    kind: str
    work: str = ""        # công việc của chi tiết sinh ra
    unit: str = ""
    prefix: str = ""      # tiền tố mã chi tiết (T → T2.5)
    thickness: int = 0    # tường: dày mm


LAYERS = [
    Layer("KL-XAY-110", "Tường xây 110", 1, WALL, "Xây tường 110", "m2", "T", 110),
    Layer("KL-XAY-220", "Tường xây 220", 1, WALL, "Xây tường 220", "m2", "T", 220),
    Layer("KL-XAY-50", "Tường xây 50", 1, WALL, "Xây tường 50", "m2", "T", 50),
    Layer("KL-SAN", "Phòng, lát sàn", 3, AREA, "Lát nền", "m2", "S"),
    Layer("KL-TRAN", "Trần", 4, AREA, "Trần", "m2", "TR"),
    Layer("KL-OP", "Ốp tường (mặt đứng trải)", 6, AREA, "Ốp tường", "m2", "OP"),
    Layer("KL-BT", "Bê tông", 8, AREA, "Bê tông", "m2", "BT"),
    Layer("KL-CUA", "Cửa", 2, DOOR, "Lắp cửa", "bộ", "C"),
    Layer("KL-GHI", "Ghi chú khối lượng (H=, ỐP)", 7, NOTE),
    Layer("CH-PHONG", "Tên phòng, vật liệu", 7, ROOM_TEXT),
    Layer("CH-KHUNG", "Khung tầng", 9, FRAME),
    Layer("CH-TEXT", "Tiêu đề tầng", 7, TITLE),
    Layer("CH-KY-HIEU", "Bảng ký hiệu", 9, LEGEND),
    Layer("D-O-CAM", "Ổ cắm", 30, DEVICE),
    Layer("D-CONG-TAC", "Công tắc", 30, DEVICE),
    Layer("D-CHIEU-SANG", "Chiếu sáng", 40, DEVICE),
    Layer("D-DIEU-HOA", "Điều hoà", 140, DEVICE),
    Layer("D-TU-DIEN", "Tủ điện", 30, DEVICE),
    Layer("D-DAY", "Dây điện", 30, RUN, "Đi dây điện", "m", "DD"),
    Layer("N-CAP-NUOC", "Ống cấp nước", 150, RUN, "Đi ống cấp nước", "m", "NC"),
    Layer("N-ONG-THOAT", "Ống thoát nước", 170, RUN, "Đi ống thoát nước", "m", "NT"),
    Layer("N-PHEU-THU", "Phễu thu", 150, DEVICE),
    Layer("KT-TUONG-HT", "Tường hiện trạng", 8, BACKGROUND),
    Layer("KT-COT", "Cột", 8, BACKGROUND),
    Layer("KT-CUA", "Cửa (nền)", 8, BACKGROUND),
    Layer("KT-THANG", "Cầu thang", 8, BACKGROUND),
    Layer("KT-LAN-CAN", "Lan can", 8, BACKGROUND),
    Layer("KT-TRUC", "Trục", 9, BACKGROUND),
    Layer("KT-THIET-BI", "Thiết bị", 8, BACKGROUND),
    Layer("NT-THIET-BI-VS", "Thiết bị vệ sinh", 8, BACKGROUND),
]
BY_NAME = {layer.name: layer for layer in LAYERS}


@dataclass(frozen=True)
class Block:
    name: str
    title: str
    layer: str
    shapes: tuple = field(default_factory=tuple)   # ("circle", x, y, r) | ("line", x1, y1, x2, y2), mm

    @property
    def work(self):
        return "Lắp " + self.title[0].lower() + self.title[1:]


def _box(w, h):
    a, b = w / 2, h / 2
    return (("line", -a, -b, a, -b), ("line", a, -b, a, b), ("line", a, b, -a, b), ("line", -a, b, -a, -b))


BLOCKS = [
    Block("TCD-O-CAM-DON", "Ổ cắm đơn", "D-O-CAM", (("circle", 0, 0, 100), ("line", 0, 100, 0, 200))),
    Block("TCD-O-CAM-DOI", "Ổ cắm đôi", "D-O-CAM", (("circle", 0, 0, 100), ("line", -40, 100, -40, 200), ("line", 40, 100, 40, 200))),
    Block("TCD-CONG-TAC-1", "Công tắc 1 hạt", "D-CONG-TAC", (("circle", 0, 0, 60), ("line", 40, 40, 180, 180))),
    Block("TCD-CONG-TAC-2", "Công tắc 2 hạt", "D-CONG-TAC", (("circle", 0, 0, 60), ("line", 40, 40, 180, 180), ("line", 180, 180, 220, 140))),
    Block("TCD-DEN-DOWNLIGHT", "Đèn âm trần", "D-CHIEU-SANG", (("circle", 0, 0, 100), ("line", -100, 0, 100, 0), ("line", 0, -100, 0, 100))),
    Block("TCD-DEN-OP-TRAN", "Đèn ốp trần", "D-CHIEU-SANG", (("circle", 0, 0, 150), ("circle", 0, 0, 80))),
    Block("TCD-DIEU-HOA", "Dàn lạnh điều hoà", "D-DIEU-HOA", _box(800, 200)),
    Block("TCD-TU-DIEN", "Tủ điện", "D-TU-DIEN", (*_box(400, 200), ("line", -200, -100, 200, 100))),
    Block("TCD-PHEU-THU", "Phễu thu sàn", "N-PHEU-THU", (*_box(150, 150), ("circle", 0, 0, 50))),
]
BLOCK_BY_NAME = {block.name: block for block in BLOCKS}

# Khoảng cách tối đa (mm) giữa chữ ghi chú và đối tượng nó thuộc về.
NOTE_REACH = 400
DEFAULT_DOOR_HEIGHT = 2200
DEFAULT_WALL_HEIGHT = 3300


def kind_of(layer_name):
    layer = BY_NAME.get((layer_name or "").upper())
    return layer.kind if layer else None


REASONS = {
    "layer": "Layer ngoài quy chuẩn",
    "explode": "Ký hiệu đã phá khối (Explode) hoặc vẽ nét rời",
    "type": "Loại đối tượng không dùng ở layer này",
    "open": "Đường bao chưa kín",
    "block": "Block không có trong thư viện Tend",
}


def rejection(layer_name, dxftype, closed=True, block=""):
    """Lý do đối tượng không thuộc quy chuẩn (None nếu hợp lệ). Cùng luật với lệnh TENDKIEMTRA trong AutoCAD."""
    kind = kind_of(layer_name)
    if kind is None:
        return REASONS["layer"]
    allowed = ALLOWED_TYPES[kind]
    if allowed is not None and dxftype not in allowed:
        return REASONS["explode" if kind == DEVICE else "type"]
    if kind in CLOSED_KINDS and dxftype == "LWPOLYLINE" and not closed:
        return REASONS["open"]
    if kind == DEVICE and block.upper() not in BLOCK_BY_NAME:
        return REASONS["block"]
    return None


# Bảng nút trong AutoCAD: (nhóm, [(khoá, nhãn, biểu thức LISP)]). Khoá cũng là tên lệnh TEND<khoá>.
# Chữ có dấu trong biểu thức là chữ ghi vào bản vẽ (đổi sang mã \U+XXXX khi sinh LISP).
BUTTONS = [
    ("Khung", [("KHUNG", "Khung tầng", '(tend:frame)')]),
    ("Tường xây (bấm theo tim tường)", [
        ("XAY110", "Tường 110", '(tend:wall "KL-XAY-110")'),
        ("XAY220", "Tường 220", '(tend:wall "KL-XAY-220")'),
        ("XAY50", "Tường 50", '(tend:wall "KL-XAY-50")'),
        ("CAO", "Ghi cao H=", '(tend:height)'),
    ]),
    ("Phòng, hoàn thiện", [
        ("PHONG", "Phòng, lát sàn", '(tend:room)'),
        ("TRAN", "Trần", '(tend:area "KL-TRAN" "Trần")'),
        ("OPPHONG", "Ốp trong phòng", '(tend:cladding)'),
        ("OPMATDUNG", "Ốp mặt đứng", '(tend:area "KL-OP" "Ốp")'),
        ("BETONG", "Bê tông", '(tend:area "KL-BT" "Bê tông")'),
        ("CUA", "Cửa", '(tend:door)'),
    ]),
    ("Điện, nước", [
        *[(b.name.replace("TCD-", "").replace("-", ""), b.title, f'(tend:device "{b.name}" "{b.layer}")') for b in BLOCKS],
        ("DAY", "Dây điện", '(tend:run "D-DAY")'),
        ("CAPNUOC", "Ống cấp", '(tend:run "N-CAP-NUOC")'),
        ("THOATNUOC", "Ống thoát", '(tend:run "N-ONG-THOAT")'),
    ]),
    ("Kiểm tra, xuất", [
        ("CAIDAT", "Tạo layer, block", '(tend:setup)'),
        ("KIEMTRA", "Kiểm tra", '(c:TENDKIEMTRA)'),
        ("XUAT", "Xuất cho Tend", '(c:TENDXUAT)'),
    ]),
]
