"""pages.py — registry ของหน้าที่หมุนได้ในโซนล่าง
เพิ่มหน้าใหม่ = เขียน 1 ฟังก์ชัน แล้วต่อท้าย PAGES
แต่ละ page รับ (d, data) แล้ววาดในโซน y=28..61 (ในกรอบ)
"""
import renderer as R


#   layout ทุกหน้า (โซนในกรอบ y28..61):
#     แถวบน y28  : ป้าย (ซ้าย) · ข้อมูลย่อย (ขวา)
#     กลาง   y38  : เลขพระเอก ตัวใหญ่ กลางจอ (สีสื่อความหมาย)
#     แถวล่าง y54 : หน่วย/label (ซ้าย) · สถานะ/ค่า (ขวา)
def feeder_status(d, data):
    health = data.get("health", "stale")
    hc = R.HEALTH.get(health, R.HEALTH["stale"])   # สีค่า/สุขภาพ
    rate = data.get("msg_per_s", 0)
    ac = data.get("aircraft", 0)

    # แถวบน: ป้าย FR24 (amber) ซ้าย · ✈ + จำนวนเครื่องบิน (น้ำเงิน) ขวา
    R.text(d, (4, 28), "FR24", "small", R.PALETTE["title"], anchor="la")
    R.draw_plane(d, 33, 28, R.PALETTE["aircraft"])
    R.text(d, (60, 28), str(ac), "small", R.PALETTE["aircraft"], anchor="ra")

    # กลาง: msg/s = เลขพระเอก (สีสุขภาพ)
    R.text(d, (32, 38), str(rate), "big", hc, anchor="ma")

    # แถวล่าง: หน่วย msg/s (ซ้าย) · สถานะ live/rcvr/DOWN (สีสุขภาพ ขวา)
    R.text(d, (4, 54), "msg/s", "small", R.PALETTE["label"], anchor="la")
    R.text(d, (60, 54), R.HEALTH_WORD.get(health, "?"), "small", hc, anchor="ra")


def tha_inbound(d, data):
    """หน้า THA inbound VTBS — ETA (นาที) เป็นเลขพระเอก สีตามความใกล้
    data["tha"] = {flight, eta_min, dist_nm, alt, gs} หรือ None"""
    tha = data.get("tha")

    R.text(d, (4, 28), "THA", "small", R.PALETTE["title"], anchor="la")

    if not tha:   # ไม่มี inbound → ไอคอนเทา + ข้อความ
        R.draw_plane(d, 6, 42, R.PALETTE["label"])
        R.text(d, (17, 42), "no inbd", "small", R.PALETTE["label"], anchor="la")
        return

    # แถวบนขวา: callsign (น้ำเงิน)
    R.text(d, (60, 28), tha["flight"], "small", R.PALETTE["aircraft"], anchor="ra")

    # กลาง: ETA พระเอก — <=15 นาที = เหลือง (ใกล้) มิฉะนั้นเขียว
    eta = int(round(tha.get("eta_min") or 0))
    ecol = R.HEALTH["recovering"] if eta <= 15 else R.HEALTH["ok"]
    R.text(d, (32, 38), str(eta), "big", ecol, anchor="ma")

    # แถวล่าง: หน่วย min (ซ้าย) · ระยะ nm (ขวา)
    dist = int(round(tha.get("dist_nm") or 0))
    R.text(d, (4, 54), "min", "small", R.PALETTE["label"], anchor="la")
    R.text(d, (60, 54), f"{dist}nm", "small", R.PALETTE["label"], anchor="ra")


# registry — หน้าจะหมุนตาม PAGE_HOLD; เพิ่มได้เรื่อยๆ ต่อท้าย
PAGES = [feeder_status, tha_inbound]
