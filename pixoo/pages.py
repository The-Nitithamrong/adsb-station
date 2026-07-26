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


def tha_inbound(d, data):
    """หน้า THA inbound VTBS — ETA เป็นตัวเลขพระเอก (สีตามความใกล้)
    data["tha"] = {flight, eta_min, dist_nm, alt, gs} หรือ None"""
    tha = data.get("tha")

    # ป้าย THA (amber) คร่อมขอบบนกรอบ — ตำแหน่งเดียวกับ FR24 หน้า feeder
    R.text(d, (4, 25), "THA", "small", R.PALETTE["title"], anchor="la")

    if not tha:   # ไม่มี inbound → ไอคอนเทา + ข้อความ (ชิดขวาของไอคอน)
        R.draw_plane(d, 8, 41, R.PALETTE["label"])
        R.text(d, (19, 41), "no inbound", "small", R.PALETTE["label"], anchor="la")
        return

    # ขวา: ETA พระเอก (ชิดขวา) — <=15 นาที = เหลือง (ใกล้) มิฉะนั้นเขียว
    eta = int(round(tha.get("eta_min") or 0))
    ecol = R.HEALTH["recovering"] if eta <= 15 else R.HEALTH["ok"]
    R.text(d, (60, 31), str(eta), "big", ecol, anchor="ra")
    R.text(d, (60, 52), "min", "small", R.PALETTE["label"], anchor="ra")   # หน่วย ETA

    # ซ้าย: callsign (น้ำเงิน) + ระยะ + ระดับความสูง (label เทา) — ตัวอักษรน้อย ไม่ชนเลข ETA ขวา
    # เริ่ม y=34 ให้พ้นป้าย "THA" ด้านบน
    R.text(d, (4, 34), tha["flight"], "small", R.PALETTE["aircraft"], anchor="la")
    dist = int(round(tha.get("dist_nm") or 0))
    fl = (tha.get("alt") or 0) // 100
    R.text(d, (4, 44), f"{dist}nm", "small", R.PALETTE["label"], anchor="la")
    R.text(d, (4, 53), f"FL{fl:03d}", "small", R.PALETTE["label"], anchor="la")


# registry — หน้าจะหมุนตาม PAGE_HOLD; เพิ่มได้เรื่อยๆ ต่อท้าย
PAGES = [feeder_status, tha_inbound]
