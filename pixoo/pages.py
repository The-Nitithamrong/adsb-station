"""pages.py — registry ของหน้าที่หมุนได้ในโซนล่าง
เพิ่มหน้าใหม่ = เขียน 1 ฟังก์ชัน แล้วต่อท้าย PAGES
แต่ละ page รับ (d, data) แล้ววาดในโซน y=28..61 (ในกรอบ)
"""
import renderer as R


def feeder_status(d, data):
    health = data.get("health", "stale")
    hc = R.HEALTH.get(health, R.HEALTH["stale"])   # สีค่า/สุขภาพ
    rate = data.get("msg_per_s", 0)
    ac = data.get("aircraft", 0)

    # ป้ายหน้า FR24 (amber) — วางคร่อมขอบบนกรอบซ้าย
    R.text(d, (4, 25), "FR24", "small", R.PALETTE["title"], anchor="la")

    # signal bars (สีสุขภาพ) ซ้าย + ค่า msg/s ตัวใหญ่ (สีสุขภาพ, เปลี่ยนได้)
    R.draw_bars(d, 4, 45, hc)
    R.text(d, (26, 30), str(rate), "big", hc, anchor="la")
    R.text(d, (26, 46), "msg/s", "small", R.PALETTE["label"], anchor="la")   # label เทา

    # จุดสถานะ + คำ (สีสุขภาพ) + uptime (label เทา)
    d.ellipse([(3, 55), (7, 59)], fill=hc)
    R.text(d, (10, 55), R.HEALTH_WORD.get(health, "?"), "small", hc, anchor="la")

    # ค่าที่สอง: จำนวนเครื่องบิน (น้ำเงิน) มุมขวาล่าง + ไอคอนเครื่องบิน
    R.draw_plane(d, 40, 55, R.PALETTE["aircraft"])
    R.text(d, (49, 55), str(ac), "small", R.PALETTE["aircraft"], anchor="la")


# registry — ตอนนี้มีหน้าเดียว, เพิ่มได้เรื่อยๆ
PAGES = [feeder_status]
