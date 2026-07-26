"""renderer.py — วาดเฟรม 64x64 สำหรับ Pixoo 64
หัวใจความคม: draw.fontmode = "1" (ปิด anti-alias) + pixel font ขนาด native ไม่ย่อ
"""
from PIL import Image, ImageDraw, ImageFont

W = H = 64

# ---- palette หลายโทน แต่ละสีมีความหมาย (ลด brightness กันบาดตา LED) ----
PALETTE = {
    "bg":       (10, 10, 18),
    "time":     (127, 216, 232),   # cyan  — เวลา
    "date":     (200, 162, 90),    # gold  — วันที่
    "title":    (245, 176, 40),    # amber — ป้าย FR24
    "label":    (106, 116, 132),   # slate — ตัวอักษร label
    "aircraft": (90, 159, 224),    # blue  — ค่าจำนวนเครื่องบิน
    "divider":  (42, 52, 68),
}
# สีตามสุขภาพ: ใช้กับค่าเลข msg/s, กรอบ, จุดสถานะ
HEALTH = {
    "ok":         (60, 200, 90),    # เขียว
    "recovering": (230, 160, 40),   # เหลือง
    "dead":       (220, 70, 64),    # แดง
    "stale":      (150, 150, 160),  # เทา (ไม่มีข้อมูล)
}
HEALTH_WORD = {"ok": "live", "recovering": "recover", "dead": "DOWN", "stale": "?"}

# ---- fonts: Pixel Operator (วางไฟล์ .ttf ไว้ข้างๆ script) ----
# ดาวน์โหลดฟรี: https://www.dafont.com/pixel-operator.font  (OFL)
def _font(name, size):
    try:
        return ImageFont.truetype(name, size)
    except Exception:
        return ImageFont.load_default()

FONTS = {
    "big":  _font("PixelOperator-Bold.ttf", 16),  # เวลา + ค่าเลขใหญ่
    "small": _font("PixelOperator8.ttf", 8),      # label + วันที่
}


def new_frame():
    img = Image.new("RGB", (W, H), PALETTE["bg"])
    d = ImageDraw.Draw(img)
    d.fontmode = "1"          # ★ ปิด anti-alias — ตัวช่วยหลักให้ขอบคม
    return img, d


def text(d, xy, s, font, color, anchor="la"):
    """วาด text คมๆ. anchor เช่น 'mm'(กลาง) 'la'(ซ้ายบน) 'ms'(กลาง-baseline)"""
    d.text(xy, s, font=FONTS[font], fill=color, anchor=anchor)


def draw_header(d, now):
    """โซน fix: เวลา (cyan) + วันที่ (gold)"""
    text(d, (32, 1),  now.strftime("%H:%M"),      "big",   PALETTE["time"], anchor="ma")
    text(d, (32, 17), now.strftime("%a %d %b"),   "small", PALETTE["date"], anchor="ma")
    d.line([(3, 25), (60, 25)], fill=PALETTE["divider"])


def draw_status_frame(d, health):
    """กรอบรอบโซนหมุนหน้า สีตามสุขภาพ = health at-a-glance"""
    c = HEALTH.get(health, HEALTH["stale"])
    dim = tuple(v // 3 for v in c)          # กรอบใช้เวอร์ชันหม่น
    d.rectangle([(1, 27), (62, 62)], outline=dim)


def draw_bars(d, x, base_y, color):
    """signal bars พิกเซลจริง (ไม่ย่อไอคอน) = กำลังรับ feed"""
    for i, h in enumerate((4, 7, 10, 13)):
        bx = x + i * 5
        d.rectangle([(bx, base_y - h), (bx + 2, base_y)], fill=color)


def draw_plane(d, x, y, color):
    """เครื่องบินพิกเซล 7x7 วาดเอง"""
    for dx, dy in [(3,0),(3,1),(0,3),(1,3),(2,3),(3,3),(4,3),(5,3),(6,3),
                   (3,4),(3,5),(2,6),(4,6)]:
        d.point((x + dx, y + dy), fill=color)
